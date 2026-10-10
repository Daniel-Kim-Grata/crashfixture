# Complementary integration plan

Public source inspection on 2026-10-10 found a broad reliability benchmark in
[OpenHands issue #764](https://github.com/OpenHands/benchmarks/issues/764) and
[PR #765](https://github.com/OpenHands/benchmarks/pull/765). The proposal covers
agent tasks, matched runs, infrastructure adapters, faults, and scoring. Its issue
discussion distinguishes attempted injection from runtime witnesses. We implement
original subprocess conformance fixtures rather than copy its benchmark core.

The executable [adapter worker](../examples/adapter_worker.py) and
[pytest integration](../examples/test_adapter.py) are an illustrative generic
subprocess example, not a validated OpenHands adapter. They demonstrate the shape
without requiring OpenHands, an SDK, a model, a container, or access tokens. Run
them with:

```sh
python -m pytest -q examples/test_adapter.py
```

## Proposed upstream seam, subject to upstream review

An agent benchmark adapter could launch a synthetic tool worker as a subprocess
and translate its existing semantic callbacks to `Probe.hit()` at the three local
boundaries. The synthetic destination remains `Ledger`; the benchmark keeps task
selection, model configuration, runtime policy, grading, and publication rules.
`Harness.run()` then supplies a local conformance trial. An adapter could consume
these fields from `Result.to_dict()`:

| Local evidence | Candidate use by the host benchmark |
| --- | --- |
| `scenario.boundary` | Correlate with its own scheduled semantic trigger |
| `fault.pid`, `fault.token`, `fault.sent_signal` | Actuator target correlation |
| `fault.wait_returncode`, `fault.snapshot` | Runtime witness and committed synthetic effect |
| `attempts` | Fresh launches, terminal exits, and bounded attempt history |
| `status`, `reasons` | Conformance gate; propagate inconclusive without scoring |

This is a plan, not a tested integration with #765's evolving interfaces. A
future bridge must retain the host's fault/cell identities and validation policy,
map invalid evidence explicitly, and avoid double-injecting the same worker.
Version-pin the upstream seam and add host-owned contract tests before claiming
compatibility. The MVP intentionally does not import or subclass upstream models.

[mcp-chaos-monkey](https://github.com/alexey-tyurin/mcp-chaos-monkey) supports
transport interception and fault scenarios in Python/TypeScript. A process-backed
MCP test server could use these fixtures to test durable replay after actual
death, while that project's transport faults stay in its own tests. This MVP has
no MCP protocol adapter, network proxy, or production fault injector.

## LangGraph synchronous saver example

The opt-in [SQLite saver fixture](../examples/langgraph_sqlite/README.md) now uses
the real public `SqliteSaver.put()` / `get_tuple()` interface, pinned to SQLite
saver 3.1.1 and checkpoint 4.1.0. It checks an original single-action worker at
all three subprocess boundaries, independently verifies SIGKILL, and compares
checkpoint reads from the killed and restarted processes with a final parent
read. A wrong-thread negative control proves that destination effect deduplication
alone cannot establish checkpoint recovery. Missing saver evidence is inconclusive.

This is a tested synchronous persistence seam, not a graph-runtime adapter or
upstream reproducer. The example uses public APIs and its own synthetic code;
no graph/model, async scheduler, pending writes, or copied upstream test is used.

## LangGraph pending-write ordering remains unvalidated

[Issue #8039](https://github.com/langchain-ai/langgraph/issues/8039) already contains
a crash/restart reproducer for pending-write/checkpoint ordering. Its proposed
[fix #8055](https://github.com/langchain-ai/langgraph/pull/8055) has regression tests
and was closed for missing assignment. Do not submit a competing cold fix.

Public PyPI metadata confirms `langgraph==1.2.4` and
`langgraph-checkpoint-sqlite==3.1.0` both support Python 3.10+. A future original
single-task experiment could gate saver writes with threading events, kill at
checkpoint entry, and compare synthetic effect counts after a fresh-process
resume. To attribute that ordering, it must also retain a witness of the actual
pending writes and checkpoint state. Our one-task ledger alone does not provide
that LangGraph-specific witness. The optional synchronous saver dependency has
now been installed and tested as described above. This ordering reproducer remains unrun; the saver example does not establish
graph-runtime compatibility. The separate [full graph demo](../examples/support_triage/GRAPH.md)
now covers a finite synchronous StateGraph workflow and two local concurrent
resumes; it does not reproduce this upstream pending-write issue.

The possible contribution is a reusable bounded fixture with a missing-injection
control, rather than the already-covered core bug fix. This seam has not been
confirmed absent from current upstream tests; inspect those tests and agree on
scope before any pitch. It is deferred beyond the finite launch MVP.

## Adoption sequence

1. Run the hosted CI matrix before tagging an alpha. Actions are initially
   disabled; local checks do not establish hosted compatibility.
2. Make the two-minute broken/correct/no-op demo and pytest adapter the primary
   entry points. Ask early users for a synthetic reproducer of a real regression.
3. After separately approved recipient and purpose, offer one focused fixture
   integration to a relevant upstream project. Do not advertise untested seams.
4. Maintain issues, compatibility tests, release notes, and a short support
   matrix. Track actual integrations, retained use, and bugs caught as evidence
   of usefulness. External adoption has not been observed.

Eligibility for an open-source support program is a possible later outcome of
genuine maintenance and impact, not a fixture feature or promised star count.
