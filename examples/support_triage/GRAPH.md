# Full graph and two concurrent resumes

This optional local demo executes a real `StateGraph` with the pinned synchronous
SQLite saver, using the original synthetic ticket and mock provider from the
[saver-only pilot](README.md). Four nodes route the ticket, save dispatch intent,
commit the provider effect, and finalize acknowledgement. The worker uses
`graph.invoke(..., durability="sync")`; a fresh interpreter resumes with
`graph.invoke(None, ...)`. The parent independently reads `graph.get_state`,
provider rows, task accounting, child receipts, and visible outcome.

The runtime owns graph checkpoints. This example does not construct checkpoints
with `empty_checkpoint` or write them directly with `saver.put`. See LangGraph's
[checkpoint documentation](https://docs.langchain.com/oss/python/langgraph/checkpointers)
for the synchronous durability and state inspection APIs used here.

## Run locally

Python 3.10+ and POSIX process groups/file locks are required. The dependency-free
core does not install LangGraph. From this checkout, create a separate environment:

```sh
python3 -m venv artifacts/graph-venv
artifacts/graph-venv/bin/python -m pip install --only-binary=:all: --require-hashes -r examples/support_triage/graph-requirements.txt
artifacts/graph-venv/bin/python -m pip install --no-deps .
artifacts/graph-venv/bin/python examples/support_triage/graph_pilot.py --output artifacts/graph-pilot
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 artifacts/graph-venv/bin/python -m pytest -p crashfixture.pytest_plugin -q examples/support_triage/graph_checks.py --basetemp artifacts/graph-tests --junitxml=artifacts/graph-tests.xml
```

The lock retains all 44 dependencies of the saver environment and adds three
prebuilt Python packages: `langgraph==1.2.14`, `langgraph-prebuilt==1.1.0`, and
`langgraph-sdk==0.4.6`. The actual runtime, checkpoint, and saver pins are checked
before execution. Installation needs public package access; execution uses no
model, network call, credentials, service, container, or external effect.

Each invocation creates a fresh `run-*` directory and starts `latest.json` with
`calibration_passed=false`. Exit 0 means all thirteen expected outcomes matched;
exit 1 means a calibration mismatch; exit 2 means a dependency/execution error.
Expected broken cases remain failures in the JSON. A passed hold remains business
incomplete. Pytest JUnit records the twelve regression tests, including assertions
that broken trials fail and missing evidence stays inconclusive; it does not
turn those trial verdicts into successful recovery claims.

## Crash cases

Nine trials cross three modes with the three real SIGKILL boundaries. At the
before-dispatch and after-effect boundaries, the actual graph's saved phase is
`intent` and `next=["effect"]`. The after-checkpoint boundary is inside finalize,
after sync durability has saved `acknowledged` with `next=["finalize"]`. The parent
checks the exact saved thread, namespace, checkpoint ID, writer identity, and
empty task errors. The restart must load that same checkpoint; completion must
match the separately read terminal graph, with `next=[]`.

The same before/after table as the saver-only pilot applies. Unsafe retry after
an effect creates two rows and fails. Stable-key retry depends on the mock
provider's unique request-key capability and leaves one row. No-lookup retry
from unresolved intent records a reconciliation hold without dispatching again,
even when the observer knows the initial process created zero effects. Its
visible outcome is `business_complete=false`, `automatic_retry_allowed=false`,
and `action_required="reconcile provider outcome before retry"`.

The tenth trial disables injection. It must remain inconclusive because no real
fault was witnessed. Missing or malformed graph/visible receipts also remain
inconclusive. Well-observed wrong identity, effect, or outcome is a failure.
These are trusted synthetic workers, not a malicious-worker security boundary.

## Concurrent cases

Three additional cases first retain one witnessed crash, then start exactly two
fresh resume processes against the saved thread. The seed uses a one-launch
budget and its core failure is deliberate; that verdict is retained separately
and cannot certify the concurrent outcome. Parent-owned pipes bind barrier
messages to exact PIDs and random launch tokens; the twelve-second resume deadline
bounds both workers together. Cleanup targets only the process groups it created.

| Case | Evidence before release | Result |
| --- | --- | --- |
| Unsafe, unguarded | Both resume workers park inside the effect node against the same seeded checkpoint | Two effects; expected failure |
| Stable key, guarded | Both admitted; one owns the local lock and the contender is blocked | One effect; completed |
| No lookup, guarded | Same ownership/contention evidence, starting after the committed effect | One effect; reconciliation hold, incomplete |

The first case uses explicit node barriers, avoiding a sleep-based scheduling
assumption. In guarded cases the parent additionally attempts a nonblocking lock
on its own descriptor while the owner is parked; only actual contention counts
as the lock witness. Claimed ownership without this independent observation is
inconclusive. The owner must resume the seeded checkpoint; after acquiring the
lock, the contender must read and preserve the owner's terminal checkpoint.
Completed receipt writers must belong to the observed resume processes.

The guard is an application-level POSIX `flock` around this workflow's entire
resume. It is a single-host serialization example, not a distributed lease or a
LangGraph concurrency guarantee. Stable-key behavior remains dependent on the
modeled provider capability. There is no general exactly-once claim.

## Finite validation boundary

The pilot has 13 cases, 12 witnessed initial SIGKILLs, 28 total subprocess
launches, and two independent lock-contention witnesses. The twelve opt-in checks
exercise this calibration plus wrong saved scope/task errors, missing receipts,
strict boolean outcomes, unobserved writers, wrong contender checkpoint, wrong
visible ticket, lost reconciliation action, absent physical lock evidence, and
exhaustive owned-group/descriptor cleanup after a mocked reap failure.
All effects are local SQLite rows for one synthetic ticket.

Local execution is validated only on macOS/Python 3.10.7 with these pins. Linux,
other Python versions, hosted runners, Postgres and other savers, async/delta
checkpoints, pending writes, parallel nodes, subgraphs, multiple tickets, power
loss, external provider semantics, and crashes inside SQLite transactions or
between provider commit and the cooperative mirror remain unrun. This is not a
reproduction of an upstream bug or a general framework conformance suite.
