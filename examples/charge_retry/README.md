# Synthetic charge, crash, retry

An original local SQLite model shows how a retry can duplicate a charge after a
worker dies before saving acknowledgement. All charges are rows for one fictional
order, with a synthetic amount of 700 cents. No payment API, money, credentials,
customer data, or upstream implementation is used. This is not an executed
AutoGPT reproduction or evidence about any production payment integration.

From a source checkout with a wheel installed and pytest available:

```sh
python examples/charge_retry/demo.py --output artifacts/charge-retry
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p crashfixture.pytest_plugin -q examples/charge_retry/charge_checks.py --junitxml=artifacts/charge-tests.xml
```

Run these checks separately from the other optional example suites, since the
standalone examples use sibling module names such as `worker` and `demo`.
For an isolated consumer, copy this example's three `.py` files into a separate
directory and run with the installed wheel's interpreter.

Seven controls cover unsafe and stable-key workers at all three crash boundaries,
plus a no-op injector. The after-effect crash yields two charges / 1400 synthetic
cents for unsafe retry, and one charge / 700 synthetic cents with a stable request
key. Six controls witness a real SIGKILL and restart. The no-op remains
inconclusive. Exit zero means the calibration matched, including the unsafe
failure; it does not certify that every trial recovered correctly.

Five regression tests run the calibration and challenge a passing core result
with a missing charge database, wrong amount, wrong business operation, and a
request key inconsistent with the cooperative mirror. The parent reads physical
charges separately, so a core pass cannot hide those mismatches.

The model's `UNIQUE(request_key)` represents a destination supporting request
deduplication. Merely adding a key to a worker cannot provide that capability.
The charge commit and mirror are separate transactions; their intervening gap,
crashes inside transactions, remote payment behavior, distributed consumers, and
general exactly-once semantics are untested. This is a small regression example,
not a payment design recommendation.
