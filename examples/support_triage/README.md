# Synthetic support-triage example

One original deterministic workflow routes the synthetic ticket
`fixture-ticket-001`, text `refund status delayed`, to `billing`, then asks a local
mock provider to create a follow-up. It uses the existing crashfixture harness
and the real pinned synchronous LangGraph SQLite saver. No CRM, email, model API,
customer data, credentials, or live effect is involved. This is a local synthetic
example with no production recovery guarantee.
For actual four-node StateGraph execution and a separate bounded concurrent-resume
experiment, see [the full graph demo](GRAPH.md).

## Run

From this source checkout on Linux/macOS and Python 3.10+, reuse the
[saver example's optional environment](../langgraph_sqlite/README.md#run-the-pinned-example),
or create it with these same hash-pinned requirements:

```sh
python3 -m venv artifacts/saver-venv
artifacts/saver-venv/bin/python -m pip install --only-binary=:all: --require-hashes -r examples/langgraph_sqlite/requirements.txt
artifacts/saver-venv/bin/python -m pip install --no-deps .
artifacts/saver-venv/bin/python examples/support_triage/pilot.py --output artifacts/support-triage
```

The executable checks exactly `langgraph-checkpoint-sqlite==3.1.1` and
`langgraph-checkpoint==4.1.0`. Dependency installation is separate; the pilot's
workers and parent make no network calls. Every invocation retains a fresh
`run-*` directory. `latest.json` is set to running/false before preflight, then
records all eleven cases and both sequential duplicate-resume checks.

Exit **0** means all calibration expectations matched, including intentional
failures and a missing-fault negative control. Exit **1** means at least one
expectation missed. Exit **2** means an execution/dependency error. Neither exit
0 nor a core `pass` means every ticket completed: a reconciliation hold has
`business_complete=false` and needs human action.

## Before and after evidence

Three modes run at each real crash boundary. The first process is killed before
effect dispatch, after the provider effect commits but before acknowledgement,
or after the saver acknowledgement is durably written and separately read.
The parent verifies SIGKILL signal/wait status `9/-9` and launches a fresh Python
interpreter. It independently reads the actual saver and provider SQLite database.

| Mode and boundary | Effects at fault → after restart | Visible phase | Fixture verdict |
| --- | --- | --- | --- |
| Unsafe, before dispatch | 0 → 1 | acknowledged | pass |
| Unsafe, after effect before checkpoint | 1 → 2 | acknowledged | fail: duplicate |
| Unsafe, after checkpoint | 1 → 1 | acknowledged | pass |
| Stable key, before dispatch | 0 → 1 | acknowledged | pass |
| Stable key, after effect before checkpoint | 1 → 1 | acknowledged | pass |
| Stable key, after checkpoint | 1 → 1 | acknowledged | pass |
| No lookup/dedup, before dispatch | 0 → 0 | reconciliation_hold | pass for expected hold; incomplete |
| No lookup/dedup, after effect before checkpoint | 1 → 1 | reconciliation_hold | pass for expected hold; incomplete |
| No lookup/dedup, after checkpoint | 1 → 1 | acknowledged | pass |

The unsafe mode uses a per-launch request key and repeats the same business
operation after the effect/acknowledgement gap. Stable-key mode submits
`fixture-ticket-001:create-followup:v1` every time. The mock provider's unique
operation-key constraint models a provider that supports request deduplication;
the second dispatch returns without creating another row. This result depends
on that modeled capability, not on a general exactly-once property.

No-lookup mode models a provider offering neither lookup nor request deduplication.
The worker records a durable dispatch intent before the first boundary. A restarted
worker seeing unresolved intent does **not** inspect the provider ledger or retry.
It records a hold, finishes the synthetic task with the expected `failed` state,
and writes `workflow-status.json` with `automatic_retry_allowed=false` and
`action_required="reconcile provider outcome before retry"`. Even the pre-effect
case holds: the observer knows zero effects occurred, but the worker lacks that
evidence. The parent's provider read is test instrumentation, not a lookup API
available to the worker. No reconciliation resolver or manual retry is implemented.

The tenth case deliberately stalls the second process after checkpoint recovery.
It writes visible `phase="stalled"`, then parks without spinning; the harness
times it out after two seconds and kills its own child group. The verdict fails
with recovery timeout/terminal-state evidence while the visible status remains
available. The eleventh case disables injection and must stay inconclusive.
Cleanup cannot manufacture the required first-process fault witness.

Two additional fresh interpreters sequentially resume each of a completed
stable-key case and a held no-lookup case. They use the same terminal guard as
the harness restart. The parent checks exact child receipts and unchanged
provider rows, checkpoint ID, core task state, and visible status. These checks
cover sequential duplicate delivery after terminal/hold state, not concurrent
owners, leases, or scheduling races.

## Evidence and tests

Each trial retains `provider.sqlite3` (mock effects), `saver.sqlite3` (real saver),
`ledger.sqlite3` (core task accounting and cooperative effect mirror), fault and
launch receipts, saver reads, visible status, and `workflow-result.json` with both
gates. The mock provider commits before the mirror and the named after-effect hook.
The parent compares provider operation keys with the mirror. Crashes inside that
two-commit interval, during saver/SQLite transactions, or between uninstrumented
instructions are outside this finite demo. Receipts come from trusted synthetic
workers; they are not a malicious-worker security boundary.

Run the six opt-in tests without collecting them into the dependency-free core
suite:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 artifacts/saver-venv/bin/python -m pytest -p crashfixture.pytest_plugin -q examples/support_triage/pilot_checks.py --basetemp artifacts/support-triage-tests --junitxml=artifacts/support-triage-tests.xml
```

The tests check the full demo, absent fault evidence, a missing child receipt,
changed physical provider data, integer completion masquerading as a boolean,
and changed saved business identity blocking duplicate resume.
Missing/malformed evidence is inconclusive; a well-observed incorrect effect is
a failure. Use a fresh task-owned `--basetemp` path to retain prior runs: pytest
clears its selected directory.

This manual saver pilot's local validation is macOS with Python 3.10.7 only.
StateGraph and concurrent resumes are tested separately in the finite
[full graph demo](GRAPH.md). Linux, other Python versions, hosted CI,
async/pending writes, multiple tickets, Postgres, power loss, external provider
semantics, and production recovery remain unvalidated. Checkpoint version
increments and channel shapes here are a tiny
synthetic saver example, not a general application adapter.

## Example use

Use a separate task-owned output directory for each run. All effects are local
and synthetic; the original fixture code and examples are licensed under MIT.
