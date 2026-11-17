# runner-flip

> v0.1. Standard library only. Read-only unless you pass `--pin`.

**GitHub is moving `ubuntu-latest` from Ubuntu 24.04 to 26.04 between 2026-10-19 and 2026-11-19** ([actions/runner-images#14748](https://github.com/actions/runner-images/issues/14748)). runner-flip tells you what that changes for **your** workflows.

The announcement's table lists only Java among the version changes. The official image readmes show **26 major version changes and 18 removals**, including:

| Tool | 24.04 | 26.04 |
|---|---|---|
| Java (default) | 17 | 25 |
| Node.js | 22 | 24 |
| Python | 3.12 | 3.14 |
| PHP | 8.3 | 8.5 |
| CMake | 3.31 | 4.4 |
| Docker Compose | 2.38 | 5.1 |
| Helm | 3.22 | 4.3 |
| PostgreSQL | 16 | 18 |
| Clang | 16–18 | 20–22 |
| Removed | | Miniconda, Pulumi, Julia, fastlane, Mercurial, Lerna, Parcel, newman … |

*(Computed from the [24.04](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md) and [26.04](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2604-Readme.md) readmes on 2026-11-17. Run `--image-diff --refresh` for the current list.)*

## What it does
1. Finds every job in `.github/workflows/*.yml` (and `azure-pipelines.yml`) that runs on `ubuntu-latest`, directly or through a matrix.
2. Detects which tools each job calls (including through local composite actions, `uses: ./.github/actions/…`) (`./gradlew`, `cmake`, `docker compose`, `helm`, `conda` …) and reports those whose version changed or were removed. Tools fixed by a setup action (`setup-node`, `setup-java`, `setup-python` …) are marked as safe.
3. Flags behavior changes reported on the announcement issue: `sudo -E` under sudo-rs, the runner user changing from `vsts` to `runner`, pinned `apt install pkg=1.2.3`, hard-coded Ubuntu codenames, and `switch_root`.
4. `--pin` rewrites `ubuntu-latest` to `ubuntu-24.04` as a quick escape. Review the diff and commit it.

```bash
python3 runnerflip.py path/to/repo          # report; exit 1 on high-risk findings
python3 runnerflip.py path/to/repo --pin    # rewrite ubuntu-latest -> ubuntu-24.04
python3 runnerflip.py --image-diff          # just the 24.04 -> 26.04 software diff
```

**How common is this?** Across the 30 most-starred GitHub repos pushed since 2026-10-01, scanned 2026-11-17: 21 have workflows, **all 21 run jobs on `ubuntu-latest`**, and **8 (38%) have high-risk findings**, mostly Node 22→24 and Docker 28→29 in jobs that don't pin them.

Real repos, scanned 2026-11-17: spring-petclinic → Helm 3→4 in its cluster job (its Gradle and Maven jobs are safe, because of `setup-java`). jq → Docker 28→29 in its docker job. Flask → 8 jobs, all safe.


**Also checked, on any runner label:** jobs on `ubuntu-22.04` (medium: retires 2027-04-17, with brownouts from March 2027; [announcement](https://github.com/actions/runner-images/issues/14254)), and jobs that use the preinstalled `go` without `actions/setup-go`, whose default changes from 1.24 to 1.26 on Ubuntu 22.04/24.04 and Windows from 2026-11-18 ([announcement](https://github.com/actions/runner-images/issues/15253)). That second change also hits jobs pinned to `ubuntu-24.04`, so `--pin` alone doesn't freeze Go. Jobs that bring their own Go (msys2/apt/brew packages, a `golang` container, version managers) are skipped. On Windows, from 2026-11-30 the `D:` drive becomes ReFS ([#15315](https://github.com/actions/runner-images/issues/15315)); runner-flip flags the NTFS-only features Microsoft lists that are visible in a workflow (8.3 short names on `D:`, `fsutil` quota/objectid/transaction) and, on Windows 11 Arm64, hard-coded `C:\a\` workspace paths ([#15314](https://github.com/actions/runner-images/issues/15314)). Most ReFS incompatibilities live inside tools, so these rules are narrow by design. Workflows triggered by `pull_request_target` are flagged too: from **2026-12-02** GitHub blocks that event by default in public repositories unless an Actions event policy allows it ([changelog](https://github.blog/changelog/2026-11-02-pull_request_target-blocked-by-default-december-2)); runner-flip can't tell whether your repository is public, so the finding says so.

## Limits
- Line-based, with no YAML parser: it finds tools by command name, so it can miss tools invoked indirectly through scripts or Makefiles.
- A version change isn't necessarily a break. It's where to look first.

## Tests
`python3 -m unittest discover tests`, offline, with synthetic readmes and a fixture workflow.

MIT license. Not affiliated with GitHub.
