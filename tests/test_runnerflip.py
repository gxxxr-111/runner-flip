"""Offline tests with small synthetic image readmes. Run: python3 -m unittest discover tests"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runnerflip as rf  # noqa: E402

OLD = """### Language and Runtime
- Node.js 22.23.4
- Python 3.12.3
- Clang: 16.0.6, 17.0.6, 18.1.3
### Tools
- CMake 3.31.6
- Pulumi 3.274.1
- Docker Compose 2.38.2
### Java
| Version | Environment Variable |
| ------- | -------------------- |
| 17.0.21+9 (default) | JAVA_HOME_17_X64 |
### Installed apt packages
| Name | Version |
|------|---------|
| curl | 8.5.0-2ubuntu10 |
"""
NEW = """### Language and Runtime
- Node.js 24.22.2
- Python 3.14.4
- Clang: 20.1.8, 21.1.8, 22.1.2
### Tools
- CMake 3.31.9
- Docker Compose 5.1.3
### Java
| Version | Environment Variable |
| ------- | -------------------- |
| 25.0.5+8 (default) | JAVA_HOME_25_X64 |
### Installed apt packages
| Name | Version |
|------|---------|
| curl | 8.18.0-1ubuntu1 |
"""
FIXTURE = Path(__file__).parent / "fixture"


class ImageDiff(unittest.TestCase):
    def setUp(self):
        self.ch = rf.diff_images(rf.parse_readme(OLD), rf.parse_readme(NEW))

    def test_parse_and_classify(self):
        self.assertEqual(self.ch["node.js"][2], "major")
        self.assertEqual(self.ch["python"][2], "feature")
        self.assertEqual(self.ch["cmake"][2], "minor")
        self.assertEqual(self.ch["pulumi"][2], "removed")
        self.assertEqual(self.ch["java"][:2], ("17.0.21+9 (default)", "25.0.5+8 (default)"))
        self.assertEqual(self.ch["apt:curl"][2], "minor")
        self.assertEqual(self.ch["docker compose"][2], "major")


class Workflows(unittest.TestCase):
    def setUp(self):
        self.ch = rf.diff_images(rf.parse_readme(OLD), rf.parse_readme(NEW))
        self.jobs = {j.name: j for j in rf.split_jobs(FIXTURE / ".github/workflows/ci.yml")}
        for j in self.jobs.values():
            rf.analyze_job(j, self.ch)

    def msgs(self, name):
        return " ".join(f.severity + ":" + f.message for f in self.jobs[name].findings)

    def test_jobs_and_latest_detection(self):
        self.assertEqual(set(self.jobs), {"build", "web", "legacy", "matrix"})
        self.assertFalse(self.jobs["legacy"].latest)
        self.assertTrue(self.jobs["matrix"].latest, "ubuntu-latest via matrix")

    def test_known_breakages_and_versions(self):
        m = self.msgs("build")
        self.assertIn("sudo-rs", m)
        self.assertIn("Pinned apt", m)
        self.assertIn("high:`java`", m)
        self.assertIn("high:`docker compose`", m)
        self.assertNotIn("`cmake`", m, "patch-level change is not reported")

    def test_setup_action_pins_bundled_tools(self):
        self.assertTrue(all(f.severity == "info" for f in self.jobs["web"].findings))

    def test_untouched_job_has_no_findings(self):
        self.assertEqual(self.jobs["legacy"].findings, [])

    def test_pin_rewrites_in_place(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copytree(FIXTURE, tmp / "r")
            wf = tmp / "r/.github/workflows/ci.yml"
            n = rf.pin_file(wf, [])
            text = wf.read_text()
            self.assertEqual(n, 3)
            self.assertNotIn("ubuntu-latest", text)
            self.assertIn("os: [ubuntu-24.04, macos-latest]", text)
        finally:
            shutil.rmtree(tmp)


class ReviewRegressions(unittest.TestCase):
    def test_comment_between_jobs_and_sudo_remedy(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            wf = tmp / "ci.yml"
            wf.write_text("jobs:\n  a:\n    runs-on: ubuntu-24.04\n# ---- next ----\n  b:\n    runs-on: ubuntu-latest\n"
                          "    steps:\n      - run: sudo --preserve-env=FOO make\n")
            jobs = rf.split_jobs(wf)
            self.assertEqual([(j.name, j.latest) for j in jobs], [("a", False), ("b", True)])
            rf.analyze_job(jobs[1], {})
            self.assertFalse(any("sudo-rs" in f.message for f in jobs[1].findings))
        finally:
            shutil.rmtree(tmp)

    def test_pin_block_matrix_duplicates_and_crlf(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            wf = tmp / "ci.yml"
            wf.write_bytes(b"jobs:\r\n  t:\r\n    strategy:\r\n      matrix:\r\n        os:\r\n          - ubuntu-latest\r\n"
                           b"          - windows-latest\r\n    runs-on: ${{ matrix.os }} # was ubuntu-latest\r\n"
                           b"  u:\r\n    strategy: { matrix: { os: [ubuntu-latest, ubuntu-latest] } }\r\n")
            self.assertEqual(rf.pin_file(wf, []), 3)
            data = wf.read_bytes()
            self.assertNotIn(b"- ubuntu-latest", data)
            self.assertIn(b"# was ubuntu-latest", data, "comments untouched")
            self.assertEqual(data.count(b"\r\n"), 10, "CRLF preserved")
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
