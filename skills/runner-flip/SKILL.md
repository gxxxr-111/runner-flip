---
name: runner-flip
description: Check GitHub Actions or Azure Pipelines workflows for breakage from the ubuntu-latest switch to Ubuntu 26.04 (Nov 2026). Reports which tools each ubuntu-latest job uses that changed version or were removed (Java 25, Node 24, Python 3.14, CMake 4, Docker Compose 5, Helm 4, …), plus sudo-rs, runner-user and pinned-apt issues, and can pin jobs to ubuntu-24.04. Use when CI started failing on ubuntu-latest, when a user asks whether the runner change affects them, or before the migration deadline.
---

```bash
python3 <this-skill-dir>/scripts/runnerflip.py <repo>          # report
python3 <this-skill-dir>/scripts/runnerflip.py <repo> --pin    # only with the user's go-ahead
```

For each high finding, prefer pinning the tool with its setup action (`actions/setup-java` with `java-version: 17`, and so on) over pinning the whole job, and explain the trade-off. `--pin` is a temporary escape that 24.04's own deprecation will end.
