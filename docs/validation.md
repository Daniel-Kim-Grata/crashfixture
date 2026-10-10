# Local alpha validation

The local verification command is `python scripts/verify.py`. The baseline alpha
at `cc8f213351b7fa68ed28f2acc482ca882fd1b505`, on macOS with Python 3.10.7, passed:

- Ruff check and format check.
- 84 pytest tests, including the three-boundary custom subprocess adapter,
  15 archive normalization regressions, and seven onboarding producer controls.
- Wheel and source distribution build.
- Independent review of the recovery contract and release normalization.
  Review findings have permanent regressions for malformed rows, replaced
  witnessed effects, corrupted gzip data, and sparse archive members.
- A clean wheel install without runtime dependencies ran all 14 demo trials.
- A clean wheel plus pytest passed all three example integration tests, using
  the installed pytest entry point.
- The launch onboarding script ran the wheel's console entry point and three
  generated fixture tests from outside the checkout. Its editable-install
  negative control rejected before running trials. CI/release definitions now
  include the same installed-wheel check; hosted execution remains unrun.
- Correct release tag accepted; mismatched tag rejected.
- Original-source inspection and a bounded secret/private-marker pattern scan
  found no matches. This is not a comprehensive secret detection guarantee.

The demo retains four expected passes, four expected recovery failures, and six
expected inconclusive controls. A zero demo exit means those expectations match;
it does not mean the deliberately broken workers passed. JSON retains every
trial. JUnit represents inconclusive trials as errors with no skipped cases.

With a fixed `SOURCE_DATE_EPOCH` and the pinned local toolchain, two release builds
produced byte-identical wheels and normalized sdists. Rebuilding a wheel from the
normalized sdist produced the same wheel bytes. The release helper normalizes
gzip/tar metadata after the standard setuptools build; raw `python -m build`
bypasses that normalization. Cross-toolchain byte identity is not claimed.

The generic subprocess adapter example is illustrative. It has not been
validated against OpenHands or MCP interfaces, and supplies no compatibility proof.

The later opt-in [LangGraph SQLite saver example](../examples/langgraph_sqlite/README.md)
passed 16 tests on the same local Python/platform, with three saver pass verdicts,
three expected failure verdicts, and ten inconclusive controls. Thirteen trials
recorded independently witnessed SIGKILL; three no-op injections were inconclusive.
Independent review caught an acknowledgement type false pass; integer/float worker
variants and strict nested evidence validation now cover it. The final parent read
was independently challenged with a malformed acknowledgement too.

For that addition, 62 selected core checks passed and default collection remained
84 tests without optional packages. Missing saver dependencies caused an explicit
collection error. A targeted sdist check included all six example files and
confirmed unchanged runtime source; the full unchanged packaging/reproducibility
suite was not repeated. Saver dependencies remain isolated and optional.

The optional [full graph demo](../examples/support_triage/GRAPH.md) adds actual
StateGraph execution for one four-node synthetic ticket and two fresh concurrent
resumes. Its 13-case calibration retains expected unsafe failures, incomplete
reconciliation holds, and a missing-fault inconclusive control. Twelve opt-in
regressions check checkpoint/writer identity, typed visible outcomes, and an
independent physical lock witness. This is separate from the dependency-free
84-test core suite. At a frozen source commit, run `scripts/verify.py`, all optional
saver/adoption/triage/graph checks, installed-wheel onboarding, normalized repeat
builds, and a source/package audit; retain the exact commit and check receipts.
No historical baseline alone certifies the current source.

Unrun: hosted GitHub CI/release workflows, the Linux/other-Python matrix, real
OpenHands or MCP integration, LangGraph async/pending-write or upstream-bug reproduction,
and public package installation. The source alpha has no package release.
No external adoption is claimed.
