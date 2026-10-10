# Fixture contract v1

The public surface is `Harness(root).run(argv, Scenario, env=None,
fault_mode="kill")`, `Result.require_pass()`, the pytest `recovery` fixture,
`Ledger`, and `Probe.hit(Boundary)`. `fault_mode="noop"` is a conformance control.
The CLI intentionally exposes only bundled synthetic workers; custom trusted
workers use argv through Python.

## Worker protocol

1. Open `Ledger(os.environ["CRASHFIXTURE_DB"])` and call `begin_attempt()` once
   per process. The harness owns initialization and the single synthetic task.
2. On a first dispatch, call `Probe.hit(BEFORE_DISPATCH)` outside a transaction.
3. Commit the synthetic destination effect. Call `hit(AFTER_EFFECT)` **after**
   the commit returns, before committing the task acknowledgement/checkpoint.
4. Commit the checkpoint. Call `hit(AFTER_CHECKPOINT)` before terminal success.
5. Mark the task succeeded. On replay, reconcile the durable destination key and
   checkpoint, skipping already committed work. Each launch still counts.

The initial attempt must present the ordered boundary prefix leading to the
selected hook. Recovery may skip earlier hooks when durable state justifies it;
reported hooks must still be ordered and unique. Hooks use dedicated pipes, a
per-launch random token, and the child PID, and block until an acknowledgement or
kill. Call each hook outside SQLite write transactions to permit parent reads.

Destination effect records are append-only through SQLite triggers. Monotonic
effect IDs and comparison with the witnessed snapshot detect lost/replaced
committed evidence on recovery. Structural corruption or unexpected row types
produce inconclusive evidence instead of an uncaught evaluator exception. A
trusted worker can still tamper with SQLite/schema/artifacts; this is not a
tamper-resistant audit system.

The example's destination key uniqueness is a property of this SQLite synthetic
destination. A real service might lack that capability. Replacing the ledger with
a live service is outside this toolkit's contract.

## Evidence gate

The parent reads the hook, correlates PID/token to its owned `Popen` object, and
reads the independent committed ledger snapshot. Only the expected boundary state
arms a kill. It then signals that child and obtains wait status `-SIGKILL` before
cleanup. Parent-owned `fault.json` is flushed to disk before recovery starts.
The snapshot and wait status together establish fixture exposure. No child's
generic `applied=true` flag is accepted.

Cleanup uses `SIGKILL` on the harness-created process group. **Its wait status
cannot supply the injection witness.** A no-op actuator plus timeout plus cleanup
therefore remains inconclusive. This distinction has an adversarial regression
test. A fresh token and another `Popen` launch establish fresh-process recovery;
PID differences alone are insufficient because the OS can reuse PIDs.

| Result | Meaning | CI behavior |
| --- | --- | --- |
| pass | Valid exposure and all declared synthetic assertions hold | exit 0 |
| fail | Valid exposure, followed by a recovery assertion failure | exit 1 / JUnit failure |
| inconclusive | Missing/mismatched exposure, protocol/observer error, unreadable ledger | exit 2 / JUnit error |

No rates, rankings, or exactly-once scores are generated. An unexposed process
that finishes or fails retains its attempt evidence and is inconclusive for
recovery. A recovery timeout after a witnessed kill is a failure. Launch or
protocol failures remain inconclusive, even if another assertion could fail.

## Bounded resources and provenance

One synchronous trial runs at a time per call. Total launches are 1–10, each
deadline is (0, 30] seconds, and protocol messages are at most 4096 bytes with at
most three ordered hooks per process. SQLite operations use a one-second busy
timeout. stdout/stderr are discarded. Parent folders isolate `HOME` and `TMPDIR`;
the caller explicitly supplies any extra environment. The worker is trusted code
running as the caller and may still access the machine; this is not containment.

Every trial uses a new directory and database, preventing stale passes. Artifacts
contain synthetic snapshots, per-launch identities, exit codes, reasons, and the
scenario. PIDs/tokens/paths vary; verdicts and fixed boundary semantics are
deterministic. v1 JSON is evidence for local regression tests, not a signed,
tamper-resistant audit format or a frozen benchmark comparison protocol.
