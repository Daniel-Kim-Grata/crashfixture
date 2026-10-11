# Release procedure

The release workflow builds and uploads artifacts for review; it does **not**
publish to PyPI or create a GitHub release. Version tags, package publication,
and hosted workflow execution require separate authorization. Actions are
initially disabled. The original code and examples use the included MIT license
and contributor attribution.

1. Update the source version and changelog. Verify that the name is available at
   the chosen package/repository destination; the provisional name is not reserved.
2. Run `python scripts/verify.py` and the two-minute demo. Review every regression
   and inconclusive control, not just the command's exit status.
3. Obtain independent review. For a locally validated GitHub alpha, keep Actions
   disabled and explicitly disclose the tested platform/interpreter and unrun
   hosted matrix. A broader release needs hosted CI on Linux and macOS at the
   chosen commit; tests on one local Mac do not establish that matrix.
4. After a version tag is separately authorized, create the reviewed tag. The
   `release-build` workflow checks tag/version agreement, runs the same checks,
   uses the commit timestamp as `SOURCE_DATE_EPOCH`, and uploads wheel/sdist plus
   checksums. It requests only read access to repository contents.
5. Install the wheel in a clean environment, run the CLI and pytest fixture smoke
   tests with `scripts/check_onboarding.py` as described in the
   [launch walkthrough](launch.md), and review package contents for unintended files. Publish only the
   reviewed artifacts through the separately approved account/process. No PyPI
   tokens, signing keys, or auto-publishing are part of this MVP.

The release build command is `python scripts/build_release.py` after installing
the hash-pinned development tools. It uses the standard setuptools backend, then
normalizes the sdist's gzip/tar metadata: fixed epoch, sorted members, numeric
owner/group zero, stable permissions, and regenerated PAX headers. It preserves
file contents and executable intent, extracts nothing, and replaces the archive
only after successful normalization. This follows the
[Reproducible Builds archive guidance](https://reproducible-builds.org/docs/archives/).

Use the same `SOURCE_DATE_EPOCH` and toolchain to compare builds. In a checkout,
the helper defaults to the latest commit timestamp; outside Git, provide the
epoch explicitly. `scripts/verify.py` and the release workflow use this helper.
Raw `python -m build` remains supported but bypasses sdist normalization. No
cross-toolchain byte identity or provenance signature is claimed. Keep alpha
release notes explicit about schema/API changes and unsupported cases.
