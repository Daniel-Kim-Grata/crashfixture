# Clean-wheel quickstart

Use Python 3.10+ on Linux or macOS, plus `curl`. This installs the existing GitHub
alpha wheel into a new directory. No source checkout, editable install, container,
account login, or supplied secret is required. There is no PyPI release.

```sh
set -eu
mkdir crashfixture-demo
cd crashfixture-demo
python3 -m venv .venv
curl --fail --location --silent --show-error https://github.com/Daniel-Kim-Grata/crashfixture/releases/download/v0.1.0a1/crashfixture-0.1.0a1-py3-none-any.whl --output crashfixture-0.1.0a1-py3-none-any.whl
python3 -c "import hashlib,pathlib,sys; actual=hashlib.sha256(pathlib.Path('crashfixture-0.1.0a1-py3-none-any.whl').read_bytes()).hexdigest(); sys.exit(0 if actual=='44ff7885625b43162344d162120c5ff468364372d1af8a44a3feb41aa414eb4a' else 'wheel SHA256 mismatch')"
.venv/bin/python -m pip install --no-index --no-deps crashfixture-0.1.0a1-py3-none-any.whl
.venv/bin/python -I -m crashfixture demo --workdir evidence/core --json evidence/core.json --junit evidence/core.xml
```

The console prints 14 trial outcomes and exits zero when all expected verdicts
match: four pass, four deliberately broken failures, and six inconclusive
controls. The useful contrast is:

```text
duplicate.after_effect.kill: fail duplicate_synthetic_effects
correct.after_effect.kill: pass
correct.after_effect.noop: inconclusive missing_fault_witness
```

Read `evidence/core.json` for the named trial, reasons, committed state, fault
witness, and recovery attempts. The JUnit file deliberately retains negative
controls; it is not a green ordinary test suite. For one recovery assertion with
normal CI exit behavior:

```sh
.venv/bin/python -I -m crashfixture run --boundary after_effect --json evidence/recovery.json --junit evidence/recovery.xml
```

`run` exits 0 for pass, 1 for regression, and 2 for inconclusive evidence.

## Retain a concrete job regression

The [queued report job example](../examples/retry_job/README.md) shows the concrete
failure: committed output followed by a crash before acknowledgement, then
duplicate output on retry. It includes a small standalone worker and five pytest
checks that keep the unsafe case and no-fault control alongside the stable-key fix.
Those example files are source files to copy into this consumer directory; they
are not contained in the existing alpha wheel.

The wheel's runtime has no dependencies. The example needs pytest, installed
using its supplied hash-pinned `requirements.txt`. It imports the installed wheel
and can run outside any source checkout. All effects remain local synthetic rows.
This is a bounded crash/retry regression pattern, not a production queue or
exactly-once guarantee.
