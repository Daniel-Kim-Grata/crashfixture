# Contributing

The MVP has one job: demonstrate a witnessed process crash and deterministic
recovery assertions over a synthetic ledger. Prefer a small reproducible failure
over a new generalized framework. No live endpoints, credentials, proprietary
code, or copied private fixtures belong in this repository.

## Local checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python scripts/verify.py
```

Verification runs Ruff, the entire pytest suite (including the adapter example),
and a normalized wheel/sdist release build. It is sequential and bounded.
pytest's only loaded plugin is this project's fixture, preventing ambient plugins
from changing the run.
Keep checks in `scripts/verify.py` synchronized with workflows.

For a recovery assertion change, add a deliberately broken worker or a minimal
synthetic reproducer that fails with valid exposure. For instrumentation changes,
add a negative control that would falsely pass without the fix. Never fix a test
by counting an inconclusive result as a pass or shrinking the trial set.

Document reason-code or JSON changes in the changelog. Alpha versions can change
the API; schema changes must be explicit. State the affected platforms and exact
checks in a pull request. Do not claim cross-platform validation from one host.

Dependency updates belong in a separate change: edit `requirements-dev.in`,
regenerate the hash-pinned file with `uv pip compile --generate-hashes`, and run
the full suite. Keep Python 3.10 compatibility. No global installs are needed.

No automatic PyPI publishing credentials are configured. See
[release instructions](docs/releases.md) for the reviewable release path.
