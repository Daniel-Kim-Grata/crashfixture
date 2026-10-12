# Stop a queued job from duplicating its output on retry

A queue can redeliver a job if its worker dies after committing output but before
acknowledging completion. This example models a weekly report job. Its output is
a synthetic SQLite row; it creates no report file and uses no queue, network,
credentials, or customer data.

The unsafe worker uses a new output key for each attempt. At the `after_effect`
boundary, the first output is already committed when the harness kills the
process. Retry then creates a second row. The stable worker uses the logical job
ID for both attempts and reconciles the destination before acknowledging the job.

Follow the [clean-wheel quickstart](../../docs/quickstart.md), then copy this
directory's `worker.py`, `test_retry_job.py`, and `requirements.txt` into that fresh
consumer directory.
Install the test dependency from the pinned manifest and run:

```sh
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements.txt
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -I -m pytest -p crashfixture.pytest_plugin -q -s test_retry_job.py --basetemp evidence/retry-job --junitxml evidence/retry-job.xml
```

Expected output includes:

```text
unsafe after_effect: fail; 2 committed output rows
stable_key after_effect: pass; 1 committed output row
stable_key noop: inconclusive; missing fault witness
5 passed
```

The five passing pytest tests assert three successful stable-key recoveries, one
expected duplicate-output failure, and one inconclusive no-fault control. Four
trials witness SIGKILL and restart a fresh interpreter. The unsafe recovery itself
does not pass. Each trial retains its fault, attempts, result, and SQLite ledger
under the chosen evidence directory. Use a fresh directory for a repeated run.

In a real integration, place `Probe.hit(Boundary.AFTER_EFFECT)` immediately after
the trusted synthetic destination commit and before the worker saves its
acknowledgement. Retain the unsafe regression and missing-witness control, then
test the actual adapter using the [integration contract](../../docs/contract.md).

`Ledger` models a destination with `UNIQUE(task_id, effect_key)` and committed
rows. A stable key cannot add deduplication to a destination that does not support
it. Concurrent workers, remote provider behavior, crashes inside a transaction,
and host power loss are outside this example. It establishes no general
exactly-once or production queue guarantee.
