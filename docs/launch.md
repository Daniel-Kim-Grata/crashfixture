# Launch walkthrough and readiness

The project is a source alpha. No public package, external users, or upstream
compatibility is claimed. The runnable artifact is the original synthetic
subprocess fixture, with no model calls or live effects.

## A short demo

From a checkout with the package installed, run:

```sh
python -m crashfixture demo --json artifacts/demo.json --junit artifacts/demo.xml
```

Use this 45-second narration alongside the output:

> A worker records a synthetic effect in SQLite. We kill its actual process
> before it acknowledges the effect, then start a fresh interpreter. The broken
> worker records the effect twice. The correct worker recognizes the same logical
> action and records it once. When the injector does nothing, evidence is missing
> and the result is inconclusive. The complete demo checks 14 expected verdicts;
> its zero exit means the controls matched, not that broken workers passed.

The useful contrast is:

```text
duplicate.after_effect.kill: fail duplicate_synthetic_effects
correct.after_effect.kill: pass
correct.after_effect.noop: inconclusive missing_fault_witness
```

A useful first contribution is a synthetic crash/retry regression that needs
these boundaries, with a reproducible failing case and retained evidence.

## Reproduce installed-wheel onboarding

Use Python 3.10+ on Linux or macOS. From the source checkout:

```sh
python3 -m venv artifacts/onboarding-venv
artifacts/onboarding-venv/bin/python -m pip install --require-hashes -r requirements-dev.txt
artifacts/onboarding-venv/bin/python -m pip install --no-deps --no-build-isolation .
artifacts/onboarding-venv/bin/python scripts/check_onboarding.py --python artifacts/onboarding-venv/bin/python
```

This uses the pinned development/test tools and a noneditable wheel installation.
The checker performs no installs or network calls. It rejects editable installs,
checks the installed module and entry points, runs the actual console command
from a temporary directory, and creates three pytest cases there. Those cases
use the registered `recovery` fixture at all three boundaries and require a
witnessed kill. Python's isolated mode and a minimal environment prevent ambient
`PYTHONPATH`, pytest configuration, or checkout tests from supplying the package.

Success prints the three contrast lines followed by:

```text
onboarding: matched 14 controls; 3 installed-fixture tests passed
```

The latest receipt is saved in `artifacts/onboarding/receipt.json`. It points to
a fresh `run-*` directory containing demo JSON/JUnit, pytest JUnit, logs, and
per-trial ledgers. Previous reports cannot supply missing evidence; every named
demo verdict must match exactly once. Use the checker's exit code for the
onboarding gate. Demo JUnit deliberately retains four failures and six errors;
it is evidence for negative controls, not a green CI test suite.

CI installs the built wheel after source verification and runs this same check.
The prepared Linux/macOS matrix covers Python 3.10, 3.13, and 3.14; Actions remain
disabled and hosted execution is unrun. The release's original evidence covers
macOS/Python 3.10.7. Later [portability checks](portability.md) cover the exact
released wheel on macOS/Python 3.13.14 and 3.14.7, plus Linux/aarch64/Python 3.11.15.

## Name, license, and provenance

The chosen provisional name is `crashfixture`: reusable fixtures for real
subprocess crash/restart tests. Public metadata checked on 2026-10-10:

- The exact-name [PyPI JSON endpoint](https://pypi.org/pypi/crashfixture/json) and
  [simple-index endpoint](https://pypi.org/simple/crashfixture/) returned HTTP 404.
- Public GitHub repository search for `crashfixture in:name is:public` returned
  no matches. Exact-name public web searches surfaced no named project; generic
  mentions of crash fixtures do not establish a competing project namespace.
- These are limited point-in-time observations, not name reservation, guaranteed
  registry acceptance, or trademark clearance. Recheck the approved destination
  and registries immediately before a package release.

The earlier unpublished draft name `restartcheck` had search ambiguity with
[alicecm11/restartcheck](https://github.com/alicecm11/restartcheck) and Salt's
[`salt.modules.restartcheck` module](https://docs.saltproject.io/en/latest/ref/modules/all/salt.modules.restartcheck.html).
`restartwitness` is already a different public
[scientific restart-contract project](https://github.com/loaff123/restartwitness).
No old-name package/CLI alias is retained: this draft has no public release or
users requiring migration. The package, console command, pytest entry point,
worker protocol, and output namespace all use `crashfixture`.

The MIT text, `pyproject.toml` license identifier, and wheel license file agree.
The original code and examples are licensed under MIT with the included
contributor attribution. Runtime dependencies are empty; pytest is an optional dependency and the `pytest11` entry point is
exercised by onboarding. The pytest classifier describes a real plugin.

Code and fixtures were authored for this project using synthetic records. Public
issue discussions informed the scope; no upstream implementation was copied.
The bounded source scan found no secret/private-project markers. That scan is
supporting evidence, not a comprehensive provenance or legal clearance guarantee.

## Release checklist

1. Review the source pin, package file lists, license, and bounded source scan.
   Keep the README's alpha, synthetic-only, and illustrative-integration limits.
2. Require the hosted CI matrix and installed-wheel onboarding check before an
   alpha tag. Actions are initially disabled; hosted checks have not run.
3. Follow [release instructions](releases.md), which build review artifacts and
   do not auto-publish a package. Tags and package publication need a separately
   approved destination and reviewed artifact hashes.
4. Retain synthetic regression reports and real fixture integrations before
   expanding the toolkit. Maintain compatibility tests and useful bug reports.
   No adoption, star target, or program eligibility is promised.
