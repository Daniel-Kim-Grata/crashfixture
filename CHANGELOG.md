# Changelog

## 0.1.0 — unreleased

- Optional actual StateGraph execution with sync checkpoints, ten crash controls,
  three two-process resume cases, parent-owned barriers, physical lock witness,
  and strict checkpoint/writer/visible-hold evidence regressions.
- Deterministic synthetic support-triage example with a real SQLite saver, separate
  mock provider ledger, stable-key and reconciliation-hold modes, sequential
  duplicate resumes, visible stalled-run evidence, and bounded negative controls.
- Offline four-case saver adoption demo with combined calibration receipts,
  injection/preflight controls.
- Optional pinned synchronous LangGraph SQLite saver example with fresh-process
  checkpoint recovery and wrong-thread/missing-evidence controls; the saver-only
  example makes no graph-runtime or upstream bug reproduction claim.
- Rename the unpublished draft from `restartcheck` to `crashfixture`, including
  its package, console command, pytest plugin, and worker protocol namespace.
- Three semantic subprocess crash boundaries with a parent-owned SIGKILL witness.
- SQLite synthetic destination effects and separate durable task checkpoints.
- Fresh-process, duplicate-effect, retry-budget, and terminal-state assertions.
- Correct and broken workers, no-op controls, pytest fixture, and JSON/JUnit.
- Original callback adapter example, CI definition, and release build workflow.
- Reproducible release sdist metadata with the standard setuptools backend and a
  small post-build normalization step using `SOURCE_DATE_EPOCH`.
- Installed-wheel console/pytest onboarding check in an isolated directory,
  included in the CI definition; launch walkthrough and public name audit.

Initial source alpha. No package release or external integration is claimed.
