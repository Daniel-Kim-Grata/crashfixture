# crashfixture

**Catch recovery bugs with a real subprocess kill and a synthetic side-effect ledger.**

`crashfixture` is a small Python/pytest toolkit for a specific regression: a worker
commits an effect, dies before saving its acknowledgement, then repeats the effect
on restart. It parks the worker at a named boundary, sends `SIGKILL`, records the
child's wait status, and starts a fresh process against the same SQLite ledger.

Python 3.10+, Linux or macOS. No runtime dependencies, containers, model calls, or
live external effects. Name and API are provisional; this is an alpha.
See the [launch walkthrough](docs/launch.md) for a short demo, clean-wheel
onboarding commands, and the remaining release gates.
Start without a checkout using the [clean-wheel quickstart](docs/quickstart.md),
then retain the [queued report job regression](examples/retry_job/README.md).
The [portability receipt](docs/portability.md) records source and released-wheel
checks on Linux/aarch64 and newer macOS Python versions, plus the successful
2026-10-12 hosted Ubuntu/macOS matrix at `3454cd5` for Python 3.10, 3.13, and 3.14.
Hosted checks cover the core and built-wheel onboarding; optional LangGraph
examples were not run in that matrix.

**Try the [clean consumer LangGraph walkthrough](docs/langgraph-walkthrough.md):**
install the alpha wheel, kill a real graph after its effect commits, and inspect
the duplicate, stable-key recovery, reconciliation hold, and no-op control.
The [synthetic charge/retry example](examples/charge_retry/README.md) shows the
same failure window using fictional charge rows. No upstream reproduction is claimed.

If this helps you catch a recovery bug, consider starring the repository so
other developers can find it. Useful reports include the failing boundary and
sanitized evidence; never upload credentials or customer data.

## Two-minute demo

From a source checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m crashfixture demo --json artifacts/demo.json
```

The last command checks 14 deterministic trials and exits zero **only when their
expected verdicts match**. Deliberately broken trials remain failures in the JSON.
The same crash window shows the before/after:

```text
duplicate.after_effect.kill: fail duplicate_synthetic_effects
correct.after_effect.kill: pass
correct.after_effect.noop: inconclusive missing_fault_witness
```

The broken worker chooses a new destination key on every attempt, producing two
synthetic effects. The correct worker reconciles the stable logical key in the
destination ledger, producing one. Both receive a real kill and launch a fresh
process. If the injector silently does nothing, the result is inconclusive.

To check one trial with ordinary CI exit semantics:

```sh
.venv/bin/python -m crashfixture run --boundary after_effect --json artifacts/trial.json --junit artifacts/trial.xml
```

`run` exits `0` for pass, `1` for regression, `2` for inconclusive evidence. Each
trial retains its ledger, scenario, attempts, signal receipt, and result under
`crashfixture-artifacts/`. The CLI prints each verdict; JSON contains the evidence.

## Use in pytest

Installing `.[test]` registers the `recovery` fixture:

```python
import sys
import pytest
from crashfixture import Boundary, Scenario

@pytest.mark.parametrize("boundary", list(Boundary))
def test_recovery(recovery, boundary):
    result = recovery.run(
        [sys.executable, "-m", "crashfixture.workers", "correct"],
        Scenario(boundary, max_attempts=3),
    )
    result.require_pass()
```

`require_pass()` raises on both failure and inconclusive evidence; neither becomes
a passing pytest skip. With pytest plugin autoload disabled, explicitly load
`-p crashfixture.pytest_plugin`. [The adapter example](examples/test_adapter.py)
shows an original worker using `Ledger` and `Probe` directly.

An optional [LangGraph SQLite saver example](examples/langgraph_sqlite/README.md)
tests the actual pinned synchronous checkpoint API across process death, including
a wrong-thread restart that destination deduplication alone would miss.
The [synthetic support-triage example](examples/support_triage/README.md) reuses this
seam to compare unsafe retry, provider-supported stable keys, and an explicit
reconciliation hold, with sequential duplicate-resume and visible stall controls.
Its separate [full graph demo](examples/support_triage/GRAPH.md) executes four
StateGraph nodes and two simultaneous local resumes, with observed lock contention
and an unsafe concurrent duplicate-effect control.

| Kill boundary | Independently observed state before signaling |
| --- | --- |
| `before_dispatch` | Running task, zero effects, no checkpoint |
| `after_effect` | One committed synthetic effect, no checkpoint |
| `after_checkpoint` | One effect and a committed checkpoint, task still running |

`Scenario.max_attempts` caps **total launches**, including the killed attempt.
Timeouts bound each process; semantic hooks determine injection timing. The
evaluator checks destination effect count, preservation of witnessed effects,
durable attempt accounting, checkpoint state, terminal outcome, and a fresh
process after the fault. A failed terminal outcome can be expected explicitly
for a retry-exhaustion test.

## Scope and limits

This fixture tests one task and at most one expected synthetic effect per trial.
Workers must cooperate with three hooks and use the supplied ledger. The parent
witness proves the selected child died by `SIGKILL` at the parked hook, with the
observed committed state; it does not certify an arbitrary application, real
provider, or malicious adapter. Correctness in this fixture is **not an exactly-once
delivery guarantee**. The core harness does not model host power loss, network
partitions, or concurrent workers. The optional graph example adds a bounded
single-host resume experiment. Killing Python cannot prove hardware durability.

Only run trusted synthetic adapters. The harness is not a security sandbox. It
cleans up the process groups it creates, while deliberately detached descendants
are outside its cleanup contract. It does not inherit the parent's environment;
pass required synthetic configuration with `env=`. stdout/stderr go to the null
device to bound capture; evidence uses separate pipes. See [the contract](docs/contract.md).

## Existing work

[OpenHands issue #764](https://github.com/OpenHands/benchmarks/issues/764) and
[PR #765](https://github.com/OpenHands/benchmarks/pull/765) cover the broader
adapter-backed agent reliability benchmark. This project supplies a reusable
local subprocess fixture, not another agent scoring core. The adapter example is
illustrative; it has not been validated with OpenHands. An
[integration plan](docs/integration.md) explains the proposed evidence seam; no
upstream compatibility or adoption is claimed.
[mcp-chaos-monkey](https://github.com/alexey-tyurin/mcp-chaos-monkey) targets
transport faults; crashfixture targets local process death and durable replay.
[crashpoint](https://github.com/mstevens843/crashpoint) already provides named
crash barriers, external-effect evidence, and experiments across multiple engines,
including LangGraph. The adoption hypothesis here is a small retained application
CI fixture. Equivalent maintained coverage is a reason to reuse existing work;
no globally new recovery benchmark or upstream defect is claimed.

## Contribute

Start with [CONTRIBUTING.md](CONTRIBUTING.md). The bounded verification command is
`python scripts/verify.py` after installing the hash-pinned development tools.
[Release instructions](docs/releases.md) keep builds and checks reproducible.
The original code and examples are licensed under MIT. All workers and
fixtures were authored for this project and use synthetic data.
