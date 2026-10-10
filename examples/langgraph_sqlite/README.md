# Synchronous LangGraph SQLite saver fixture

This original example uses the public `SqliteSaver.put()` and `get_tuple()` APIs
from `langgraph-checkpoint-sqlite==3.1.1` and `langgraph-checkpoint==4.1.0`.
It validates one synthetic action across a real subprocess SIGKILL and fresh
interpreter restart. It runs no graph, model, network request, or live effect.
The optional packages do not become crashfixture runtime dependencies.

## Run the pinned example

From the source checkout on Python 3.10+ and Linux/macOS:

```sh
python3 -m venv artifacts/saver-venv
artifacts/saver-venv/bin/python -m pip install --only-binary=:all: --require-hashes -r examples/langgraph_sqlite/requirements.txt
artifacts/saver-venv/bin/python -m pip install --no-deps .
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 artifacts/saver-venv/bin/python -m pytest -p crashfixture.pytest_plugin -q examples/langgraph_sqlite/recovery_checks.py --basetemp artifacts/saver-tests --junitxml=artifacts/saver-tests.xml
```

Use a new task-owned `--basetemp` path when retaining earlier results: pytest
clears that directory. `recovery_checks.py` is deliberately opt-in, so the default
core suite does not collect it or require these packages. Its explicit run fails
on missing packages or mismatched saver/checkpoint versions; it never substitutes
a passing skip. The hash-pinned environment includes upstream transitive
dependencies, although the example uses only local synchronous saver calls.

Expected: **16 tests pass**, checking these verdicts:

| Case | Core ledger gate | Additional saver gate |
| --- | --- | --- |
| Correct worker at each of three boundaries | pass | pass |
| Duplicate worker after effect commit | fail | fail |
| Wrong thread ID after durable checkpoint | pass | fail |
| No-op injection at each boundary | inconclusive | inconclusive |
| Missing/malformed/wrong-child/invalid-read receipt | pass | inconclusive |
| Checkpoint removed after recovery | pass | fail |
| Unreadable checkpoint database | pass | inconclusive |
| Integer/float acknowledgement from an original worker variant | pass | inconclusive |

The deliberately broken workers are application mistakes authored here, not
upstream bugs. A passing test suite means these positive and negative controls
matched their expected verdicts. Per-trial `saver-result.json` preserves both
gates; the normal fixture artifacts retain signal receipt, ledger, and launches.
`saver.sqlite3` holds the actual upstream saver state.

## Run the adoption demo

After the pinned setup above, run four cases without pytest:

```sh
artifacts/saver-venv/bin/python examples/langgraph_sqlite/demo.py --output artifacts/saver-demo
```

Expected console verdicts: `correct: core=pass, saver=pass`,
`wrong_thread: core=pass, saver=fail`, `duplicate: core=fail, saver=fail`, and
`noop: core=inconclusive, saver=inconclusive`. Exit **0** means these calibration
controls matched, not that all workers recovered. Exit **1** means at least one
control missed its expected verdict/evidence; exit **2** means an execution or
dependency/version error. A kill case needs an actual signal witness, two fresh
launch tokens, the expected effect count, and terminal exit 0. Disabled injection
cannot produce a passing calibration for those cases.

`artifacts/saver-demo/latest.json` holds both gates and each calibration check.
Each invocation creates a new `run-*` directory with `demo-result.json`, ledger,
saver database, signal receipt, and child receipts. It replaces any previous
latest success before preflight. Run directories are retained; remove only your
own artifacts when finished. Evidence is local and may contain local file paths.
No model or network call occurs during the demo.

Its three opt-in tests exercise the real four-case run, an injector forced to
no-op, and preflight failure replacing a stale success:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 artifacts/saver-venv/bin/python -m pytest -q examples/langgraph_sqlite/demo_checks.py
```

## What the evidence establishes

The worker uses a stable synthetic effect key in the separate destination ledger.
Before dispatch and after effect commit, no saver acknowledgement exists yet.
After effect commit it writes an acknowledgement with the public saver, closes
the writing connection, then reads it through another connection. Only after that
read does it park at `after_checkpoint`. The core harness verifies the child's
SIGKILL wait status independently and starts a fresh interpreter.

The fresh process reads the saver using the same `thread_id` and namespace. At
`after_checkpoint`, the checkpoint ID, channel values, and writer token must
match the killed process's committed receipt. At the two earlier boundaries, the
fresh read must be empty and the restarted process must write the final checkpoint.
The parent reads the final saver through its public API and compares it too.
Start/commit receipts are fsynced, and each is bound to the harness's exact child
PID, launch token, and attempt number. They are cooperative instrumentation for
trusted synthetic workers, not an independent graph-runtime witness.

The wrong-thread control demonstrates why the ledger alone is insufficient:
idempotency keeps one destination effect, but the fresh process reads a different
thread and never recovers the original checkpoint. The separate saver gate catches
that error. Missing/invalid instrumentation or unreadable state is inconclusive.
Read evidence checks nested config/channel shape and exact boolean acknowledgement
types; Python's `1 == True` or `1.0 == True` cannot satisfy this gate.

This covers the pinned synchronous SQLite saver seam on the tested local platform.
Local validation used macOS and Python 3.10.7; Linux/other Python versions remain unrun.
It does not validate StateGraph execution, asynchronous/background writes,
pending-write ordering, multiple nodes/threads, serializer conformance, power loss,
or upstream issue #8039. There is no exactly-once or upstream acceptance claim.

## Public interface sources

Inspected on 2026-10-10:

- [Official checkpointer guide](https://docs.langchain.com/oss/python/langgraph/checkpointers)
  describes the saver contract and distinguishes checkpoints from pending writes.
- [SqliteSaver reference](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver)
  describes its lightweight synchronous scope.
- The reference links to
  [source at b2926a0](https://github.com/langchain-ai/langgraph/blob/b2926a0ff9589c28c7e01fe7cdbb337b86d5a4b4/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py).
  Its `put()` commits through the cursor context before returning. The installed
  pinned distribution's actual interface was also inspected and exercised.
- [SQLite saver 3.1.1 metadata](https://pypi.org/pypi/langgraph-checkpoint-sqlite/3.1.1/json)
  and [checkpoint 4.1.0 metadata](https://pypi.org/pypi/langgraph-checkpoint/4.1.0/json)
  identify the tested package versions. No upstream implementation was copied
  into this example.

Use the named cases as a starting point for an original synthetic regression.
No external usage or upstream acceptance is claimed.
