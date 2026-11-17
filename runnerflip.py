#!/usr/bin/env python3
"""runner-flip: what does the `ubuntu-latest` switch to Ubuntu 26.04 change for *your* workflows?

GitHub moves `ubuntu-latest` from Ubuntu 24.04 to 26.04 between 2026-10-19 and
2026-11-19 (actions/runner-images#14748). This scans .github/workflows (and
azure-pipelines.yml), finds jobs on `ubuntu-latest`, and reports which tools
those jobs use whose version changed or that were removed. The changes are computed
live from the official image readmes. It also checks known behavior changes
reported on the announcement issue. Optionally pins jobs to ubuntu-24.04.

Standard library only. Exit code 1 when any job has a high-risk finding; 2 when an image readme can't be parsed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ISSUE = "https://github.com/actions/runner-images/issues/14748"
RAW = "https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/Ubuntu{v}-Readme.md"
CACHE = Path(os.environ.get("RUNNER_FLIP_CACHE", Path.home() / ".cache" / "runner-flip"))

# Behavior changes reported on the announcement issue (primary source), beyond version bumps.
KNOWN = [
    (re.compile(r"\bsudo\s+(?:-E(?![\w=])|--preserve-env(?!=))"), "high",
     "26.04 defaults to sudo-rs; `sudo -E` / `--preserve-env` behaves differently. Pass variables explicitly "
     "(`sudo env VAR=$VAR cmd`) or use `--preserve-env=VAR1,VAR2`.", ISSUE),
    (re.compile(r"/home/vsts|\bvsts\b"), "high",
     "The runner user changed from `vsts` to `runner` on the new image; hard-coded paths or ownership "
     "can cause permission-denied errors.", ISSUE),
    (re.compile(r"\bswitch_root\b"), "medium",
     "`switch_root` is no longer in the default util-linux package on 26.04 (package split).", ISSUE),
    (re.compile(r"apt(-get)?\s+install[^\n]*=\s*\d"), "high",
     "Pinned apt package versions (`pkg=1.2.3`) are almost never in the 26.04 archive.", ISSUE),
    (re.compile(r"\b(focal|jammy|noble)\b"), "medium",
     "Hard-coded Ubuntu codename. 26.04 is `resolute`; repository lines for noble/jammy may break.", ISSUE),
    (re.compile(r"\bdocker\s+(?:image\s+)?(?:save|load|inspect|images|ls|history)\b|\bdocker\s+(?:buildx\s+)?build\b[^\n]*--platform"),
     "medium",
     "Docker 29 on the 26.04 image uses the **containerd image store** by default for fresh installs, which changes "
     "image IDs, `docker save` output and multi-platform handling; users reported breakage on 2026-11-15. Docker's "
     "storage docs control it with the `containerd-snapshotter` feature in /etc/docker/daemon.json (setting it to "
     "false should restore the classic store), or pin the job to `ubuntu-24.04`.",
     "https://docs.docker.com/engine/release-notes/29/"),
]
# Command tokens -> readme tool names (lowercase first word of the readme entry).
ALIASES = {
    "java": "java", "javac": "java", "gradle": "gradle", "./gradlew": "java", "gradlew": "java", "mvn": "maven",
    "python": "python", "python3": "python", "pip": "pip", "pip3": "pip3", "pipx": "pipx",
    "node": "node.js", "npm": "npm", "npx": "npm", "yarn": "yarn", "pnpm": "pnpm",
    "php": "php", "composer": "composer", "ruby": "ruby", "gem": "rubygems", "bundle": "bundler",
    "go": "go", "gcc": "gnu", "g++": "gnu", "gfortran": "gnu", "clang": "clang", "clang++": "clang",
    "clang-format": "clang-format", "clang-tidy": "clang-tidy", "cmake": "cmake", "ninja": "ninja",
    "dotnet": ".net", "docker": "docker", "docker-compose": "docker", "kubectl": "kubectl", "helm": "helm",
    "minikube": "minikube", "terraform": "terraform", "ansible": "ansible", "bazel": "bazel",
    "swift": "swift", "kotlin": "kotlin", "rustc": "rust", "cargo": "rust", "perl": "perl",
    "psql": "postgresql", "mysql": "mysql", "pwsh": "powershell", "az": "azure", "aws": "aws",
    "gcloud": "google", "firefox": "firefox", "google-chrome": "google", "chromedriver": "chromedriver",
    "sqlite3": "sqlite3", "git": "git", "git-lfs": "git", "jq": "jq", "yq": "yq", "zstd": "zstd",
    "conda": "miniconda", "pulumi": "pulumi", "julia": "julia", "fastlane": "fastlane", "hg": "mercurial",
    "newman": "newman", "lerna": "lerna", "parcel": "parcel", "podman": "podman", "mediainfo": "mediainfo",
}
# A setup action also fixes the tools bundled with that runtime.
PINS = {"node.js": {"node.js", "npm", "yarn"}, "python": {"python", "pip", "pip3", "pipx"},
        "ruby": {"ruby", "rubygems", "bundler"}, "java": {"java"}, "go": {"go"}, "php": {"php", "composer"},
        ".net": {".net"}, "gradle": {"gradle"}}
# For these, a change in the second number (3.12 -> 3.14) is a real language-level change.
MINOR_MATTERS = {"python", "ruby", "go", "php", "perl", "swift", "kotlin", "rust", "bash"}
SETUP_ACTIONS = {"java": "actions/setup-java", "python": "actions/setup-python", "node.js": "actions/setup-node",
                 "go": "actions/setup-go", "ruby": "ruby/setup-ruby", "php": "shivammathur/setup-php",
                 ".net": "actions/setup-dotnet", "gradle": "gradle/actions/setup-gradle"}


@dataclass
class Finding:
    severity: str
    message: str
    source: str = ""


@dataclass
class Job:
    file: str
    name: str
    line: int
    runs_on: str
    latest: bool
    text: str
    findings: list[Finding] = field(default_factory=list)


# ---------------------------------------------------------------- image readmes

MIN_TOOLS = 20


def fetch_readme(v: str, refresh=False) -> str:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"Ubuntu{v}-Readme.md"
    if p.exists() and not refresh:
        return p.read_text()
    req = urllib.request.Request(RAW.format(v=v), headers={"User-Agent": "runner-flip/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
    p.write_text(text)
    return text


def parse_readme(md: str) -> dict[str, str]:
    """Map lowercase tool key -> version text, from bullet lists and two-column tables."""
    tools: dict[str, str] = {}
    section = ""
    for line in md.splitlines():
        if line.startswith("#"):
            section = line.lstrip("#").strip().lower()
            continue
        m = re.match(r"^- ([^:\d(][^:]*?)(?::\s*|\s+)(v?\d[^\n]*)$", line.strip())
        if m:
            name, ver = m.group(1).strip(), m.group(2).strip()
            tools.setdefault(name.lower(), ver)
            continue
        if line.startswith("|") and not re.match(r"^\|\s*-", line):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and re.search(r"\d", cells[1]) and cells[0].lower() not in ("name", "version"):
                key = cells[0].lower()
                if section == "java" and re.match(r"\d", cells[0]):
                    if "default" in cells[0]:
                        tools["java"] = cells[0]
                    continue
                if section.startswith("installed apt"):
                    key = "apt:" + key
                tools.setdefault(key, cells[1])
    return tools


def major(ver: str) -> str:
    m = re.search(r"\d+", ver or "")
    return m.group(0) if m else ""


def major_minor(ver: str) -> str:
    m = re.search(r"\d+\.\d+", ver or "")
    return m.group(0) if m else major(ver)


def diff_images(old: dict[str, str], new: dict[str, str]) -> dict[str, tuple[str, str, str]]:
    """key -> (old, new, kind): removed / major / feature (x.Y changed, where that matters) / minor."""
    out = {}
    for k, ov in old.items():
        nv = new.get(k)
        if nv is None:
            out[k] = (ov, "", "removed")
        elif nv != ov:
            if major(ov) != major(nv):
                kind = "major"
            elif k.split()[0] in MINOR_MATTERS and major_minor(ov) != major_minor(nv):
                kind = "feature"
            else:
                kind = "minor"
            out[k] = (ov, nv, kind)
    for k, nv in new.items():
        if k not in old:
            out[k] = ("", nv, "added")
    return out


def lookup(changes: dict, tool: str):
    """Find change entries whose readme name starts with the tool key."""
    hits = []
    for k, v in changes.items():
        base = k.removeprefix("apt:")
        if base == tool or base.split()[0] == tool or base.startswith(tool + " "):
            hits.append((k, v))
    return hits


# ---------------------------------------------------------------- workflows

def find_workflows(root: Path) -> list[Path]:
    files = sorted((root / ".github" / "workflows").glob("*.y*ml")) if (root / ".github" / "workflows").exists() else []
    files += [p for p in (root / "azure-pipelines.yml", root / "azure-pipelines.yaml") if p.exists()]
    return files


def split_jobs(path: Path) -> list[Job]:
    """Line-based: find top-level `jobs:` children by indentation (no YAML dependency)."""
    lines = path.read_text(errors="replace").splitlines()
    jobs, in_jobs, indent, cur, start = [], False, None, None, 0
    for i, line in enumerate(lines + ["__end__:"]):
        if re.match(r"^jobs:\s*$", line):
            in_jobs = True
            continue
        if re.match(r"^\s*(#|$)", line):   # comments and blank lines never end the jobs block
            continue
        if in_jobs and re.match(r"^\S", line):  # left the jobs block
            in_jobs = False
            if cur:
                jobs.append((cur, start, lines[start:i]))
                cur = None
            continue
        if not in_jobs:
            continue
        m = re.match(r"^(\s+)([\w.-]+):\s*$", line)
        if m and (indent is None or len(m.group(1)) == indent):
            indent = len(m.group(1))
            if cur:
                jobs.append((cur, start, lines[start:i]))
            cur, start = m.group(2), i
    out = []
    whole = "\n".join(lines)
    if not jobs and re.search(r"vmImage:\s*['\"]?[\w.-]", whole):  # Azure Pipelines, no jobs: block
        jobs = [("pipeline", 0, lines)]
    for name, ln, block in jobs:
        text = "\n".join(block)
        ro = re.search(r"(?m)^[\s-]*(?:runs-on|vmImage):\s*(.+)", _strip_yaml_comments(text))
        runs_on = ro.group(1).strip() if ro else ""
        latest = "ubuntu-latest" in runs_on or (
            "matrix" in runs_on and re.search(r"ubuntu-latest", text) is not None)
        out.append(Job(str(path), name, ln + 1, runs_on, latest, text))
    return out


LOCAL_SETUP = {"node": "node.js", "nodejs": "node.js", "python": "python", "java": "java", "go": "go",
               "ruby": "ruby", "php": "php", "dotnet": ".net", "gradle": "gradle"}


def _strip_yaml_comments(text: str) -> str:
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        out.append(re.sub(r"\s+#.*$", "", line))
    return "\n".join(out)


LOCAL_USES = re.compile(r"""^\s*-?\s*uses:\s*['"]?\./([\w./-]*?)/?['"]?\s*$""", re.M)
LOCAL_SETUP_NAME = re.compile(r"setup[-_]?(nodejs|node|python|java|go|ruby|php|dotnet|gradle)"
                              r"(?:[-_](?:env|environment|toolchain|sdk|runtime|version))?$", re.I)


def inline_local_actions(text: str, repo: Path | None, _seen: set | None = None, _depth: int = 0) -> str:
    """Append the steps of local composite actions (`uses: ./path`, recursively, inside the repo only)."""
    seen = _seen if _seen is not None else set()
    text = _strip_yaml_comments(text)
    extra = []
    for m in LOCAL_USES.finditer(text):
        path = m.group(1)
        if path.endswith((".yml", ".yaml")) and "/workflows/" in "/" + path:
            continue   # reusable workflow: a job of its own, not step text
        found = False
        if repo is not None and _depth < 4:
            root = repo.resolve()
            for name in ("action.yml", "action.yaml"):
                f = (repo / path / name).resolve()
                if not f.is_relative_to(root):
                    break   # never read outside the repository
                if f.is_file() and f not in seen:
                    seen.add(f)
                    extra.append(inline_local_actions(f.read_text(errors="replace"), repo, seen, _depth + 1))
                    found = True
                    break
        if not found:   # file not available: infer only from an exact conventional name like setup-nodejs
            last = path.rstrip("/").split("/")[-1] if path else ""
            mm = LOCAL_SETUP_NAME.fullmatch(last)
            if mm:
                extra.append(f"uses: local-setup/{mm.group(1).lower()}")
    return text + ("\n" + "\n".join(extra) if extra else "")


def tokens_used(text: str) -> set[str]:
    used = set()
    for t in re.findall(r"(?<![\w/.-])(\.?/?[A-Za-z][\w.+-]*)", text):
        if t in ALIASES:
            used.add(ALIASES[t])
    for m in re.finditer(r"uses:\s*([\w.-]+/[\w./-]+)", text):
        for tool, action in SETUP_ACTIONS.items():
            if m.group(1).startswith(action):
                used.add("setup:" + tool)
        lm = re.match(r"local-setup/(\w+)", m.group(1))
        if lm and lm.group(1) in LOCAL_SETUP:
            used.add("setup:" + LOCAL_SETUP[lm.group(1)])
    return used


UBUNTU_2204 = "https://github.com/actions/runner-images/issues/14254"
OWN_GO = re.compile(r"mingw-w64-[\w-]*-go(?![\w-])|apt(?:-get)?\s+install[^\n]*\bgolang(?:-go|-1\.\d+(?:-go)?)?(?![\w-])|"
                    r"brew\s+install[^\n]*\bgo(?:@[\d.]+)?(?![\w-])|"
                    r"image:\s*['\"]?golang|container:\s*['\"]?golang|docker://golang|\bgoenv\b|asdf\s+install\s+golang|"
                    r"\bgvm\s+use\b")
GO_DEFAULT = "https://github.com/actions/runner-images/issues/15253"
REFS = "https://github.com/actions/runner-images/issues/15315"
ARM_WS = "https://github.com/actions/runner-images/issues/15314"
# NTFS features ReFS lacks (Microsoft ReFS overview): short 8.3 names, quotas, object IDs, transactions
NTFS_ONLY = re.compile(r"\bfsutil\s+(?:8dot3name|quota|objectid|transaction)\b|\b[Dd]:[\\/][^\s'\"]*~\d")
ARM_C_WORKSPACE = re.compile(r"\b[Cc]:[\\/]+a[\\/]")


GO_CMD = re.compile(r"(?:^|[\s;&|(`])go\s+(?:build|test|run|install|mod|vet|generate|get|work|env|version|fmt|list|tool)\b"
                    r"|languages?:\s*\[?\s*['\"]?go\b")   # CodeQL builds Go with the PATH toolchain


def _image_text(job: Job) -> str:
    """runs-on, or for a matrix job the job text too (`runs-on: ${{ matrix.os }}` with `os: [ubuntu-22.04, …]`)."""
    ro = job.runs_on.lower()
    return ro + "\n" + _strip_yaml_comments(job.text).lower() if "${{" in ro else ro


def image_notices(job: Job) -> None:
    """Dated changes that also hit jobs NOT on ubuntu-latest (including ones pinned by runner-flip --pin)."""
    img = _image_text(job)
    if "ubuntu-22.04" in img:
        job.findings.append(Finding("medium",
            "`ubuntu-22.04` is deprecated and retires on **2027-04-17** (jobs then fail). Scheduled brownouts fail jobs "
            "on March 23, March 30, April 6 and April 13 (14:00–24:00 UTC; the announcement omits the year, 2027 "
            "implied by the retirement date). Move to `ubuntu-24.04` or `ubuntu-26.04` before the first brownout.",
            UBUNTU_2204))
    code = _strip_yaml_comments(job.text)
    if "windows" in img and NTFS_ONLY.search(code):
        job.findings.append(Finding("medium",
            "From **2026-11-30** the `D:` drive on Windows images is ReFS, not NTFS. This job uses an NTFS-only "
            "feature (8.3 short names, quotas, object IDs or transactions per Microsoft's ReFS overview) "
            "on or via `D:`.", REFS))
    if "windows-11-arm" in img and ARM_C_WORKSPACE.search(code):
        job.findings.append(Finding("medium",
            "From **2026-11-30** the workspace on Windows 11 Arm64 moves to `D:`; this job hard-codes a `C:\\a\\…` "
            "path. Use `${{ github.workspace }}` / `GITHUB_WORKSPACE`.", ARM_WS))
    uses_go = GO_CMD.search(code) is not None
    on_affected = any(k in img for k in ("ubuntu-22.04", "ubuntu-24.04", "windows")) or job.latest
    if uses_go and "setup:go" not in tokens_used(job.text) and not OWN_GO.search(code) and on_affected:
        job.findings.append(Finding("medium",
            "Builds with the preinstalled Go (no `actions/setup-go`): from **2026-11-18** the default Go changes "
            "1.24 → 1.26 on Ubuntu 22.04/24.04 and Windows images (26.04 already defaults to 1.26), and 1.24 leaves "
            "the tool cache. Pinning the job to `ubuntu-24.04` does not freeze it; add `actions/setup-go` with an "
            "explicit `go-version`.", GO_DEFAULT))


def analyze_job(job: Job, changes: dict, repo: Path | None = None):
    job.text = inline_local_actions(job.text, repo)
    image_notices(job)
    if not job.latest:
        return
    for rx, sev, msg, src in KNOWN:
        if rx.search(job.text):
            job.findings.append(Finding(sev, msg, src))
    used = tokens_used(job.text)
    pinned_tools = {t for s_ in used if s_.startswith("setup:") for t in PINS.get(s_[6:], {s_[6:]})}
    for tool in sorted(u for u in used if not u.startswith("setup:")):
        pinned = tool in pinned_tools
        for key, (ov, nv, kind) in lookup(changes, tool):
            if kind == "added" or ("compose" in key and "compose" not in job.text):
                continue
            if pinned:
                job.findings.append(Finding("info", f"`{key}` changes ({ov or '—'} → {nv or 'removed'}), "
                                            "but this job pins it with a setup action.", ""))
                continue
            if kind == "minor":
                continue
            sev = "high" if kind in ("removed", "major") else "medium"
            what = "removed from the image" if kind == "removed" else f"{ov} → {nv}"
            job.findings.append(Finding(sev, f"`{key}`: {what}. Pin it with a setup action, or pin the job "
                                        "to `ubuntu-24.04`.", ""))
    if not job.findings:
        job.findings.append(Finding("info", "No tools with changed versions detected in this job's steps.", ""))


# ---------------------------------------------------------------- pin

def pin_file(path: Path, jobs: list[Job]) -> int:
    """Rewrite every non-comment `ubuntu-latest` (runs-on, vmImage, flow or block matrices),
    preserving the file's line endings."""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        text = f.read()
    out, n = [], 0
    for line in text.splitlines(keepends=True):
        code, sep, comment = line.partition("#")
        if "ubuntu-latest" in code:
            n += code.count("ubuntu-latest")
            line = code.replace("ubuntu-latest", "ubuntu-24.04") + sep + comment
        out.append(line)
    if n:
        with open(path, "w", newline="", encoding="utf-8") as f:
            f.write("".join(out))
    return n


# ---------------------------------------------------------------- CLI

def render(jobs: list[Job], changes: dict, root: Path) -> str:
    latest = [j for j in jobs if j.latest]
    high = sum(1 for j in jobs for f in j.findings if f.severity == "high")
    L = ["# runner-flip report", "",
         f"`ubuntu-latest` → Ubuntu 26.04, rollout 2026-10-19 → 2026-11-19 ([announcement]({ISSUE})).",
         f"Scanned {len({j.file for j in jobs})} workflow files, {len(jobs)} jobs; "
         f"**{len(latest)} on ubuntu-latest**, {high} high-risk findings. Image changes computed from the "
         f"official readmes: {sum(1 for v in changes.values() if v[2] == 'major')} major version changes, "
         f"{sum(1 for v in changes.values() if v[2] == 'removed')} removals.", ""]
    for j in [j for j in jobs if j.latest or j.findings]:
        rel = os.path.relpath(j.file, root)
        L.append(f"## {rel} · job `{j.name}` (line {j.line}) · runs-on `{j.runs_on}`")
        for f in sorted(j.findings, key=lambda f: {"high": 0, "medium": 1, "low": 2, "info": 3}[f.severity]):
            src = f" ([source]({f.source}))" if f.source else ""
            L.append(f"- **{f.severity}** · {f.message}{src}")
        L.append("")
    if latest:
        L += ["**Quick escape:** `runner-flip --pin` rewrites `ubuntu-latest` to `ubuntu-24.04` in these files "
              "(review the diff, commit, and plan a real upgrade; 24.04 stays supported for now)."]
    elif not any(j.findings for j in jobs):
        L.append("No jobs use `ubuntu-latest`, and no other dated image change applies. Nothing changes for you.")
    else:
        L.append("No jobs use `ubuntu-latest`; the findings above come from other dated runner-image changes.")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="runnerflip", description=__doc__.split("\n\n")[0])
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--from", dest="old", default="2404")
    ap.add_argument("--to", dest="new", default="2604")
    ap.add_argument("--refresh", action="store_true", help="re-download image readmes")
    ap.add_argument("--pin", action="store_true", help="rewrite ubuntu-latest to ubuntu-24.04 in place")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--image-diff", action="store_true", help="just print the image software diff")
    a = ap.parse_args(argv)
    root = Path(a.repo).resolve()
    old_tools, new_tools = parse_readme(fetch_readme(a.old, a.refresh)), parse_readme(fetch_readme(a.new, a.refresh))
    for v, tools in ((a.old, old_tools), (a.new, new_tools)):
        if len(tools) < MIN_TOOLS:   # the readmes list hundreds; a handful means the format changed, not the image
            print(f"error: parsed only {len(tools)} tools from the Ubuntu {v} image readme; its format may have changed, "
                  "so results would be a false all-clear. Check the readme or update runner-flip.", file=sys.stderr)
            sys.exit(2)
    changes = diff_images(old_tools, new_tools)
    if a.image_diff:
        for k, (ov, nv, kind) in sorted(changes.items(), key=lambda kv: (kv[1][2], kv[0])):
            if kind != "minor":
                print(f"{kind:8} {k}: {ov or '—'} → {nv or '—'}")
        return
    jobs = [j for p in find_workflows(root) for j in split_jobs(p)]
    for j in jobs:
        analyze_job(j, changes, root)
    if a.json:
        print(json.dumps([{**{k: v for k, v in j.__dict__.items() if k != "text"},
                           "findings": [f.__dict__ for f in j.findings]} for j in jobs], indent=1))
    else:
        print(render(jobs, changes, root))
    if a.pin:
        n = sum(pin_file(p, jobs) for p in find_workflows(root))
        print(f"pinned {n} occurrence(s) of ubuntu-latest to ubuntu-24.04")
    sys.exit(1 if any(f.severity == "high" for j in jobs for f in j.findings) else 0)


if __name__ == "__main__":
    main()
