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


class LocalActions(unittest.TestCase):
    def test_local_composite_action_pins_node(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / ".github/workflows").mkdir(parents=True)
            (tmp / ".github/actions/setup-env").mkdir(parents=True)
            (tmp / ".github/actions/setup-env/action.yml").write_text("runs:\n  using: composite\n  steps:\n    - uses: actions/setup-node@v4\n")
            wf = tmp / ".github/workflows/ci.yml"
            wf.write_text("jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ./.github/actions/setup-env\n      - run: npm test\n")
            ch = rf.diff_images(rf.parse_readme(OLD), rf.parse_readme(NEW))
            job = rf.split_jobs(wf)[0]
            rf.analyze_job(job, ch, tmp)
            self.assertTrue(all(f.severity == "info" for f in job.findings), job.findings)
            # without the action file, the conventional name is enough
            job2 = rf.Job("ci.yml", "u", 1, "ubuntu-latest", True, "steps:\n  - uses: ./.github/actions/setup-nodejs\n  - run: node x.js\n")
            rf.analyze_job(job2, ch, None)
            self.assertTrue(all(f.severity == "info" for f in job2.findings), job2.findings)
        finally:
            shutil.rmtree(tmp)


class LocalActionDeltas(unittest.TestCase):
    def test_comments_nesting_traversal_names(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            (tmp / "repo/.github/actions/a").mkdir(parents=True)
            (tmp / "repo/.github/actions/b").mkdir(parents=True)
            (tmp / "outside/x").mkdir(parents=True)
            (tmp / "repo/.github/actions/a/action.yml").write_text("runs:\n  steps:\n    - uses: ./.github/actions/b\n")
            (tmp / "repo/.github/actions/b/action.yml").write_text("runs:\n  steps:\n    - uses: actions/setup-node@v4\n")
            (tmp / "outside/x/action.yml").write_text("SECRET-OUTSIDE uses: actions/setup-node@v4\n")
            repo = tmp / "repo"
            nested = rf.inline_local_actions("steps:\n  - uses: ./.github/actions/a # setup\n", repo)
            self.assertIn("setup:node.js", rf.tokens_used(nested))
            self.assertNotIn("SECRET-OUTSIDE", rf.inline_local_actions("steps:\n  - uses: ./../outside/x\n", repo))
            self.assertNotIn("SECRET-OUTSIDE", rf.inline_local_actions("steps:\n  - uses: ./.github/actions/../../../outside/x\n", repo))
            commented = rf.inline_local_actions("steps:\n  # - uses: ./.github/actions/b\n", repo)
            self.assertNotIn("setup:node.js", rf.tokens_used(commented))
            self.assertNotIn("setup:go", rf.tokens_used(rf.inline_local_actions("  - uses: ./.github/actions/setup-google-cloud\n", None)))
            self.assertIn("setup:node.js", rf.tokens_used(rf.inline_local_actions("  - uses: ./.github/actions/setup-nodejs\n", None)))
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


class ImageNotices(unittest.TestCase):
    def job(self, runs_on, text):
        return rf.Job("w.yml", "j", 1, runs_on, runs_on == "ubuntu-latest", text)

    def test_2204_and_go_default(self):
        j = self.job("ubuntu-22.04", "steps:\n - run: go test ./...\n")
        rf.analyze_job(j, {}, None)
        msgs = " ".join(f.message for f in j.findings)
        self.assertIn("2027-04-17", msgs)
        self.assertIn("1.24 → 1.26", msgs)
        pinned = self.job("ubuntu-24.04", "steps:\n - run: go build\n")       # what --pin produces
        rf.analyze_job(pinned, {}, None)
        self.assertEqual([f.severity for f in pinned.findings], ["medium"])
        ok = self.job("ubuntu-24.04", "steps:\n - uses: actions/setup-go@v5\n   with: {go-version: '1.24'}\n - run: go build\n")
        rf.analyze_job(ok, {}, None)
        self.assertEqual(ok.findings, [])
        msys = self.job("windows-latest", "steps:\n - uses: msys2/setup-msys2@v2\n   with:\n     install: >\n       mingw-w64-x86_64-go\n - run: go build\n")
        rf.analyze_job(msys, {}, None)
        self.assertEqual([f for f in msys.findings if "1.24" in f.message], [])   # brings its own Go
        word = self.job("ubuntu-24.04", "name: Let's go\nsteps:\n - run: echo ready, go\n")
        rf.analyze_job(word, {}, None)
        self.assertEqual(word.findings, [])                                      # 'go' as a word, not a command
        task = self.job("ubuntu-24.04", "steps:\n - run: brew install go-task\n - run: go build ./...\n")
        rf.analyze_job(task, {}, None)
        self.assertEqual(len(task.findings), 1)                                  # go-task is not Go
        matrix = self.job("${{ matrix.os }}", "strategy:\n  matrix:\n    os: [ubuntu-22.04, macos-15]\nsteps:\n - run: make\n")
        rf.analyze_job(matrix, {}, None)
        self.assertIn("2027-04-17", " ".join(f.message for f in matrix.findings))
        mac = self.job("macos-15", "steps:\n - run: go build\n")                 # not in the announced set
        rf.analyze_job(mac, {}, None)
        self.assertEqual(mac.findings, [])


class WindowsNov30(unittest.TestCase):
    def test_refs_and_arm_workspace(self):
        j = rf.Job("w.yml", "j", 1, "windows-2025", False, "steps:\n - run: fsutil 8dot3name set D: 1\n")
        rf.analyze_job(j, {}, None)
        self.assertIn("ReFS", " ".join(f.message for f in j.findings))
        a = rf.Job("w.yml", "j", 1, "windows-11-arm", False, "steps:\n - run: cd C:\\a\\repo\\repo\n")
        rf.analyze_job(a, {}, None)
        self.assertIn("Arm64 moves to `D:`", " ".join(f.message for f in a.findings))
        c = rf.Job("w.yml", "j", 1, "windows-2025", False, "steps:\n - run: fsutil quota track C:\n")
        rf.analyze_job(c, {}, None)
        self.assertEqual(c.findings, [])                                            # fsutil on C: is fine
        exe = rf.Job("w.yml", "j", 1, "windows-2025", False, "steps:\n - run: fsutil.exe 8dot3name set D: 1\n")
        rf.analyze_job(exe, {}, None)
        self.assertEqual(len(exe.findings), 1)
        v = rf.Job("w.yml", "j", 1, "windows-11-vs2026-arm", False, "steps:\n - run: cd C:\\a\\x\n")
        rf.analyze_job(v, {}, None)
        self.assertEqual(len(v.findings), 1)
        sh = rf.Job("w.yml", "j", 1, "[self-hosted, windows]", False, "steps:\n - run: fsutil quota track D:\n")
        rf.analyze_job(sh, {}, None)
        self.assertEqual(sh.findings, [])
        ok = rf.Job("w.yml", "j", 1, "windows-2025", False, "steps:\n - run: dir C:\\PROGRA~1\n")   # C: stays NTFS
        rf.analyze_job(ok, {}, None)
        self.assertEqual(ok.findings, [])


class MacOpenSSL(unittest.TestCase):
    def test_openssl4_on_macos26_only(self):
        for ro, n in (("macos-latest", 1), ("macos-26-intel", 1), ("macos-15", 0), ("ubuntu-latest", 0)):
            j = rf.Job("w.yml", "j", 1, ro, ro == "ubuntu-latest", "steps:\n - run: openssl req -new -x509 -nodes\n")
            rf.analyze_job(j, {}, None)
            self.assertEqual(sum("OpenSSL 4" in f.message for f in j.findings), n, ro)
        three = rf.Job("w.yml", "j", 1, "macos-latest", False,
                       "steps:\n - run: export PATH=\"$(brew --prefix openssl@3)/bin:$PATH\"\n - run: openssl req -new\n")
        rf.analyze_job(three, {}, None)
        self.assertEqual(three.findings, [])                                        # already pins OpenSSL 3
        quoted = rf.Job("w.yml", "j", 1, "macos-26", False, "steps:\n - run: sh -c \"openssl pkeyutl -sign\"\n")
        rf.analyze_job(quoted, {}, None)
        self.assertEqual(len(quoted.findings), 1)
        w = rf.Job("w.yml", "j", 1, "macos-latest", False, "steps:\n - run: echo use openssl later\n")
        rf.analyze_job(w, {}, None)
        self.assertEqual(w.findings, [])


class PullRequestTarget(unittest.TestCase):
    def test_trigger_forms(self):
        y = rf.uses_pull_request_target
        self.assertTrue(y("on: pull_request_target\njobs:\n  a:\n"))
        self.assertTrue(y("on: [push, pull_request_target]\njobs:\n"))
        self.assertTrue(y("on:\n  pull_request_target:\n    types: [opened]\njobs:\n"))
        self.assertFalse(y("on: pull_request\njobs:\n  a:\n    steps:\n      - run: echo pull_request_target\n"))
        self.assertFalse(y("on:\n  # pull_request_target:\n  pull_request:\njobs:\n"))
        self.assertTrue(y("on:\n  - push\n  - pull_request_target\njobs:\n"))                # block list
        self.assertTrue(y('name: "x"\non:\n- pull_request_target\n\npermissions:\n  contents: read\njobs:\n'))  # unindented (cobra)
        self.assertTrue(y("on: {pull_request_target: {types: [opened]}}\njobs:\n"))           # flow mapping
        self.assertTrue(y("on: ['pull_request_target']\n"))                                     # quoted
        self.assertTrue(y('"on":\n  "pull_request_target":\n    types: [opened]\n'))           # quoted keys
        self.assertTrue(y("true:\n  pull_request_target:\n"))                                  # YAML 1.1 'on'
        self.assertTrue(y("on: [push, pull_request_target]\r\njobs:\r\n"))                    # CRLF
        self.assertFalse(y("on:\n  push:\n    branches: [pull_request_target]\njobs:\n"))     # a value, not an event
        self.assertFalse(y("on:\n  workflow_run:\n    workflows: [pull_request_target]\n"))

    def test_once_per_file(self):
        with tempfile.TemporaryDirectory() as d:
            wf = Path(d) / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "l.yml").write_text("on: pull_request_target\njobs:\n  a:\n    runs-on: ubuntu-24.04\n    steps: []\n"
                                      "  b:\n    runs-on: ubuntu-24.04\n    steps: []\n")
            jobs = [j for f in rf.find_workflows(Path(d)) for j in rf.split_jobs(f)]
            rf.trigger_notices(jobs)
        self.assertEqual([len(j.findings) for j in jobs], [1, 0])
        with tempfile.TemporaryDirectory() as d:                                   # no jobs: block at all
            wf = Path(d) / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "r.yml").write_text("on:\n  pull_request_target:\n  workflow_call:\n")
            files = rf.find_workflows(Path(d))
            jobs = [j for f in files for j in rf.split_jobs(f)]
            rf.trigger_notices(jobs, files)
        self.assertEqual([(j.name, len(j.findings)) for j in jobs], [("(workflow)", 1)])


class RunsOnParsing(unittest.TestCase):
    def test_comments_and_azure(self):
        with tempfile.TemporaryDirectory() as d:
            wf = Path(d) / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "a.yml").write_text("jobs:\n  b:\n    # runs-on: ubuntu-22.04\n    runs-on: ubuntu-24.04 # moved off ubuntu-22.04\n"
                                      "    steps:\n      - run: make\n")
            (Path(d) / "azure-pipelines.yml").write_text("pool:\n  vmImage: 'ubuntu-22.04'\nsteps:\n- script: make\n")
            jobs = [j for f in rf.find_workflows(Path(d)) for j in rf.split_jobs(f)]
        ro = sorted(j.runs_on for j in jobs)
        self.assertEqual(ro, ["'ubuntu-22.04'", "ubuntu-24.04"])


class ReadmeFormat(unittest.TestCase):
    def test_unparseable_readme_exits_2(self):
        real = rf.fetch_readme
        rf.fetch_readme = lambda v, refresh=False: "# Ubuntu\nSoftware list moved elsewhere."
        try:
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(SystemExit) as cm:
                    rf.main([d])
            self.assertEqual(cm.exception.code, 2)
        finally:
            rf.fetch_readme = real


class DockerStore(unittest.TestCase):
    def test_containerd_store_rule(self):
        rx = [k for k in rf.KNOWN if "containerd" in k[2]][0][0]
        for yes in ("docker save app -o a.tar", "docker image inspect app", "docker buildx build --platform linux/arm64 .",
                    "docker images --digests"):
            self.assertTrue(rx.search(yes), yes)
        for no in ("docker run app", "docker compose up", "docker build -t app .", "echo dockerimages"):
            self.assertFalse(rx.search(no), no)


if __name__ == "__main__":
    unittest.main()
