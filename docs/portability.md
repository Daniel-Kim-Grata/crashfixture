# Released alpha portability checks

On 2026-10-12, the published `v0.1.0a1` source at
`90a93643ecb8ce2ef2421808a4a5fabcb310e41c` and its downloaded release wheel were
checked on additional local platforms. The tag and original five release assets
remain unchanged. [The machine-readable summary](portability-20261012.json)
records the exact source, hashes, interpreter versions, commands, and limits.

| Environment | Canonical source checks | Published-wheel onboarding |
| --- | --- | --- |
| Linux/aarch64, CPython 3.11.15, local container | Ruff, 84 tests, wheel/sdist build passed | 14 control verdicts matched; 3 fixture tests passed |
| macOS/arm64, CPython 3.14.7 | Ruff, 84 tests, wheel/sdist build passed | 14 control verdicts matched; 3 fixture tests passed |
| macOS/arm64, CPython 3.13.14 | Not run | 14 control verdicts matched; 3 fixture tests passed |

Each canonical suite had zero failures, errors, or skips. Onboarding imports the
downloaded wheel from an isolated consumer working directory, rejects an editable
install, invokes the console entry point, and checks the registered pytest
fixture. Each calibration retains four expected passes, four deliberately broken
failures, and six inconclusive controls. Those negative outcomes must match;
they do not count as passing recovery assertions.

The downloaded wheel and sdist match the release's `SHA256SUMS`:

```text
44ff7885625b43162344d162120c5ff468364372d1af8a44a3feb41aa414eb4a  crashfixture-0.1.0a1-py3-none-any.whl
6ae7da007839620426158168503baf152674800f9a72f0e4aa7faf39fb91f8a6  crashfixture-0.1.0a1.tar.gz
```

The Linux/3.11 and macOS/3.14 source builds produced those same bytes with
`SOURCE_DATE_EPOCH=1791752011` and the hash-pinned development dependencies. This
is a comparison of these specific builds, not a guarantee for arbitrary
interpreters or build tools. Checksums do not provide a signature.

## Repeat the checks

From the frozen release source, create a virtual environment and install the
development tools with `--require-hashes -r requirements-dev.txt`. Install the
source with `--no-deps --no-build-isolation -e .`, then run:

```sh
python scripts/verify.py
```

For a source tree without Git metadata, set `SOURCE_DATE_EPOCH=1791752011` before
building. In a separate environment with pytest 8.4.2 and the downloaded alpha
wheel installed, run the frozen source's checker with that environment's Python:

```sh
python scripts/check_onboarding.py --python /absolute/consumer/bin/python --outdir artifacts/onboarding
```

The Linux check used the existing official Python image
`python@sha256:db3ff2e1800a8581e2c48a27c3995339d47bdf046da21c7627accd3d51053a93`,
limited to two CPUs and 1 GiB RAM. It ran only public synthetic source and release
artifacts, exposed no ports, and used no provider credentials.

## Remaining scope

GitHub Actions remain disabled. The prepared CI definition now includes
Linux/macOS with Python 3.10, 3.13, and 3.14, but none of those hosted jobs have
run. These local checks do not establish Linux/x86_64 or the full runner matrix.
Optional LangGraph/saver/charge examples were not repeated on the additional
platforms. Their original macOS/Python 3.10.7 release evidence is separate.

There is no new version or PyPI publication, upstream bug reproduction, external
adoption, or general exactly-once guarantee. All effects remain synthetic local
SQLite rows. The original alpha's recovery and durability limits still apply.
