# Kill a LangGraph worker after its effect commits

This walkthrough runs a real, synchronous four-node `StateGraph` with a real
`SqliteSaver`. The ticket and provider are original synthetic data. It needs no
model key, network service, container, or real side effect after installation.
The alpha was validated locally on macOS with Python 3.10.7; other platforms and
Python versions remain untested for this release.

The useful failure happens between a committed provider effect and the graph's
saved acknowledgement. The fresh process resumes the saved `effect` node:

```text
route -> intent -> effect -> finalize
                   |
              commit local effect
              SIGKILL before return
                   |
             fresh process resumes effect
```

| Case | Physical provider rows | Verdict | Business complete |
| --- | ---: | --- | --- |
| Unsafe retry after effect | 2 | expected fail | true, but duplicated |
| Stable request key after effect | 1 | pass | true |
| No provider lookup after effect | 1 | pass for the hold assertion | false |
| Fault injection disabled | 1 | inconclusive | true, without crash proof |

The stable-key case relies on the modeled destination enforcing a unique request
key. A checkpoint alone cannot undo or deduplicate a separately committed effect.
The no-lookup case keeps `automatic_retry_allowed=false` and an explicit
reconciliation action. Its passing test does not claim completed work.

## Install as a clean consumer

Use Python 3.10 or newer. Start in a directory where neither `crashfixture` nor
`crashfixture-consumer` already exists. The source supplies the example files;
the package itself is installed from the release wheel. The optional graph
dependencies are hash pinned and are installed separately.

```sh
git clone --branch v0.1.0a1 --depth 1 https://github.com/Daniel-Kim-Grata/crashfixture.git
cd crashfixture
mkdir ../crashfixture-consumer
cp examples/support_triage/*.py ../crashfixture-consumer/
cp examples/support_triage/graph-requirements.txt ../crashfixture-consumer/requirements.txt
cd ../crashfixture-consumer
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements.txt
curl -fLO https://github.com/Daniel-Kim-Grata/crashfixture/releases/download/v0.1.0a1/crashfixture-0.1.0a1-py3-none-any.whl
curl -fLO https://github.com/Daniel-Kim-Grata/crashfixture/releases/download/v0.1.0a1/crashfixture-0.1.0a1.tar.gz
curl -fLO https://github.com/Daniel-Kim-Grata/crashfixture/releases/download/v0.1.0a1/SHA256SUMS
shasum -a 256 -c SHA256SUMS
.venv/bin/python -m pip install --no-index --no-deps crashfixture-0.1.0a1-py3-none-any.whl
.venv/bin/python -I -c 'import crashfixture, importlib.metadata as m; print(m.version("crashfixture")); print(crashfixture.__file__)'
```

The probe must print `0.1.0a1` and a path under this consumer's
`.venv/.../site-packages/crashfixture/`. It must not import from the source
checkout. Checksums detect byte mismatch against the published manifest; they
are not a signature or independent trust attestation.

## Execute the graph and inspect the crash

Run from `crashfixture-consumer`:

```sh
env -u PYTHONPATH -u PYTHONHOME .venv/bin/python graph_pilot.py --output evidence/graph
env -u PYTHONPATH -u PYTHONHOME PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest -p crashfixture.pytest_plugin -q graph_checks.py --basetemp evidence/graph-tests --junitxml=evidence/graph-tests.xml
```

The pilot exits zero only when all 13 named expectations match: 10 pass verdicts,
two deliberately unsafe failures, and one missing-fault inconclusive verdict.
The three passes for no-lookup modes assert a retained reconciliation hold.
The regression command has 12 passing tests, including damaged-evidence controls.
The pilot runs 28 owned subprocess launches, witnesses 12 initial SIGKILLs, and
independently observes two physical local lock-contention events.

Inspect the retained evidence rather than relying on the pilot's exit code:

```sh
.venv/bin/python - <<'PY'
import json
import sqlite3
from pathlib import Path

report = json.loads(Path("evidence/graph/latest.json").read_text())
for name in ("unsafe.after_effect.kill", "stable_key.after_effect.kill",
             "no_lookup.after_effect.kill", "stable_key.after_checkpoint.noop"):
    case = next(c for c in report["graph_cases"] if c["name"] == name)
    core = case["core"]
    print(name, case["graph"])
    print("fault:", core["fault"])
    print("fresh launch tokens:", [a["token"] for a in core["attempts"]])
    root = Path(core["artifact_dir"])
    with sqlite3.connect(f"{(root / 'provider.sqlite3').as_uri()}?mode=ro", uri=True) as db:
        print("physical effects:", db.execute("SELECT operation_key, business_key FROM effects").fetchall())
    if len(core["attempts"]) == 2:
        first, resumed = core["attempts"]
        before = json.loads((root / f"graph-{first['token']}-intent.json").read_text())["read"]
        loaded = json.loads((root / f"graph-{resumed['token']}-start.json").read_text())["read"]
        print("resumed exact checkpoint:", before == loaded)
        print("saved phase / next:", loaded["values"]["phase"], loaded["next"])
PY
```

For each after-effect kill, `sent_signal=9`, `wait_returncode=-9`, and
`snapshot_valid=true` satisfy the harness's fault witness. Two distinct launch
tokens bind the killed and restarted processes. The resumed checkpoint matches
the original intent receipt, with `phase="intent"` and `next=["effect"]`.
The parent also reads the terminal graph through a separate saver connection and
checks exact thread, namespace, checkpoint, writer, task errors, and visible status.
The no-op case has no witnessed kill and remains inconclusive.

The concurrency examples compare two unguarded resumptions with two resumptions
serialized by an application-level POSIX `flock`. The latter is a single-host
guard around the entire resume; it establishes no distributed lease or framework
concurrency guarantee. See the [graph evidence contract](../examples/support_triage/GRAPH.md).

## Adapt the fixture to your worker

Place `Probe.hit(Boundary.AFTER_EFFECT)` after the destination commit has returned
and before acknowledgement is saved. Keep the stable logical operation identity
across retries. Read the destination separately in the parent and compare the
saved identity and outcome, as this example does. If the destination supplies no
lookup or deduplication capability, retain uncertainty and require reconciliation.
Missing injection or missing evidence must remain inconclusive.

This finite local fixture does not establish general exactly-once delivery.
Power loss, async durability, other savers, distributed execution, crashes inside
transactions or between provider commit and the cooperative mirror, and real
provider behavior are outside its tested boundary. No upstream LangGraph or
AutoGPT bug reproduction is claimed.
