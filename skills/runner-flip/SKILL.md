---
name: runner-flip
description: Check GitHub Actions or Azure Pipelines workflows for breakage from the ubuntu-latest switch to Ubuntu 26.04 (Nov 2026). Reports which tools each ubuntu-latest job uses that changed version or were removed (Java 25, Node 24, Python 3.14, CMake 4, Docker Compose 5, Helm 4, …), plus sudo-rs, runner-user, pinned-apt and Docker 29 containerd-image-store issues, and can pin jobs to ubuntu-24.04. Also flags other dated GitHub-hosted image changes on any label: default Go 1.24→1.26 (Nov 18, also on ubuntu-24.04), ubuntu-22.04 retirement and brownouts, Windows D: drive becoming ReFS and the Arm64 workspace moving (Nov 30), OpenSSL 4 as default on macos-latest, and pull_request_target workflows blocked by default in public repos from Dec 2. Use when CI started failing on a GitHub/Azure hosted runner, when a user asks whether a runner image change affects them, or before these deadlines.
---

```bash
python3 <this-skill-dir>/scripts/runnerflip.py <repo>          # report
python3 <this-skill-dir>/scripts/runnerflip.py <repo> --pin    # only with the user's go-ahead
```

For each high finding, prefer pinning the tool with its setup action (`actions/setup-java` with `java-version: 17`, and so on) over pinning the whole job, and explain the trade-off. `--pin` is a temporary escape that 24.04's own deprecation will end.
