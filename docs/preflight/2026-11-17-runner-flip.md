# Design brief: runner-flip

> Date: 2026-11-17 · Tier: 1 · Status: built (MVP) · Deadline-driven: rollout ends 2026-11-19

**Evidence (primary, checked 2026-11-17):** [actions/runner-images#14748](https://github.com/actions/runner-images/issues/14748): open, created 2026-09-17, 282 reactions, 152 comments; vendor rollout 2026-10-19 → 2026-11-19. Breakages reported in its comments: sudo-rs `sudo -E`, the runner user change from vsts to runner, Java default 25 breaking Gradle, `switch_root` missing, cross-compilers.

**Finding during the build:** the announcement table names Java as the only real version change. The official readmes show 26 major changes (Node 22→24, CMake 3→4, Compose 2→5, Helm 3→4, PostgreSQL 16→18, Clang 18→22, …), language-level changes (Python 3.12→3.14, PHP 8.3→8.5, Ruby 3.2→3.3), and 18 removals.

**Prior art:** actionlint (4.4k★), zizmor (7.1k★): workflow linting and security, no image-change awareness. No tool found that maps runner-image diffs onto a repo's jobs.

| Decision | Choice | Why |
|---|---|---|
| Data | Official image readmes, fetched live and cached | Primary, always current |
| Parsing | Line-based, no YAML dependency | Zero install; good enough for job and step text |
| Fix | `--pin` to 24.04 + advice to pin tools with setup actions | Reversible; the user chooses |

**Verification:** 6 offline tests; a fixture workflow with known issues; 3 real repos (Flask, spring-petclinic, jq), with one false positive (Docker Compose flagged without compose use) found and fixed.
