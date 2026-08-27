# JobDesk release closeout evidence

Captured: 2026-08-27 Asia/Shanghai

Scope: JobDesk only, isolated remediation worktree
`codex/full-remediation-20260819-local`. This record does not authorize a
tag, publication, endpoint switch, workload, or production promotion.

## Existing published release

The live GitHub release query was:

```text
gh release view v0.7.2 --repo moxuezhuchen/jobdesk \
  --json tagName,targetCommitish,assets,url
```

It reports tag `v0.7.2` at merge commit
`f63c1ca6d24bb76d25f1df021ddfe745dc3a33a8` and exactly one asset:

```text
jobdesk-0.7.2-py3-none-any.whl
sha256:a9ef59f788a22c476d7a0558a53df286c7fc93c12ab2afef87c5c8995feb7139
```

No source distribution, SHA256 manifest, SBOM, release provenance record, or
GitHub build-attestation asset is attached to the existing release. The live
`gh attestation verify` probe returned HTTP 404.

## Fix-forward candidate

The candidate-only changes are:

- `pyproject.toml`: next patch version `0.7.3`.
- `CHANGELOG.md`: `0.7.3 - Candidate` section explicitly records that no tag
  or publication was performed.
- `.github/workflows/release.yml`: future tag-push-only release workflow with
  clean annotated-tag identity checks, an existing-release fail-closed guard,
  separate sdist/wheel builds from one tagged checkout, final wheel metadata
  validation, a source-tree-external installed-wheel smoke, CycloneDX SBOM,
  GitHub build provenance attestation, attestation/provenance JSON records,
  SHA256SUMS, workflow artifact upload, and one release publication command.
- `tests/test_release_workflow.py`: static YAML, permission, version, build,
  installed-wheel, attestation, and release-asset assertions.

The workflow is intentionally not executed here. No tag, GitHub release,
production endpoint, or external workload was changed.

## Current producer binding

The producer was subsequently published independently as ConfFlow `v2.1.6`:

```text
https://github.com/moxuezhuchen/ConfFlow/releases/tag/v2.1.6
annotated-tag peeled commit: 45bfac11f721b2152eeff5ee26e50463fcc6f657
confflow-2.1.6-py3-none-any.whl
sha256:d8fe44611ec128fece79309f42792b716c1f2f59871b5aab4024f3d136f75548
METADATA sha256:ccbdcf2dd308451f3532f21b35ff703aef8ca453edfd40f721550a00eb689afb
Requires-Python: >=3.10
```

The wheel's five control-protocol schemas are byte-identical to the retained
JobDesk `v2.1.3` snapshot. The candidate's consumer contract, chemistry input,
three Windows chemistry locks, and wheel manifest bind to this exact `v2.1.6`
filename, digest, metadata digest, and producer commit. The historical
`v2.1.3` release evidence remains unchanged.

## External publication blocker

The earlier live-repository checks reported immutable releases disabled
(`GET /immutable-releases` returned `enabled:false`) and no active tag
rulesets (`GET /rulesets` returned `[]`); those observations remain historical
evidence and were not changed by this candidate. The release workflow no
longer calls that admin-only endpoint. Instead, an authorized repository owner
must independently verify the release protection and set the repository
variable `RELEASE_IMMUTABLE_PREFLIGHT_SHA` to the exact merge commit that will
be tagged. The workflow binds that variable to `GITHUB_SHA` and both local and
remote peeled tag SHAs, and fails closed unless the post-release API reports
`isImmutable=true`. This evidence does not authorize writing settings,
variables, secrets, tags, or releases.

## Local verification

The test-first sequence was:

1. Before the workflow/version changes,
   `python -m pytest tests/test_release_workflow.py -q --basetemp
   .pytest_tmp_release_workflow` returned four expected failures.
2. After the changes, the same command returned `4 passed`.
3. `python -m ruff check tests/test_release_workflow.py` passed.
4. `python -m black --check tests/test_release_workflow.py` passed.
5. `git diff --check` passed (only the expected CRLF normalization warning for
   the edited TOML file was emitted by Git).
6. Local candidate package builds passed:

   ```text
   python -m build --sdist --outdir .build_release_candidate
   python -m build --wheel --outdir .build_release_candidate
   ```

   Produced artifacts:

   ```text
   jobdesk-0.7.3-py3-none-any.whl
   sha256:f1a2ada64cdde2459e503b619019ec64983f776bd7f92b896173a67c701179a3
   jobdesk-0.7.3.tar.gz
   sha256:08affb681c4c6de64abe9d557fd1ceb919ccdc0384118df4daa0c38e05275dfd
   ```

7. The wheel was installed with `--no-deps` into the candidate-local
   `.venv-release-verify` and probed from `.pytest_tmp_release_install`; it
   reported package version `0.7.3` and an import path under
   `site-packages`, outside the source tree.
8. The formal ConfFlow `v2.1.6` wheel was independently downloaded, hashed,
   and inspected: the wheel SHA-256, `METADATA` SHA-256, `Requires-Python`,
   and `Requires-Dist` values match the checked-in manifest; all five wheel
   control schemas are byte-identical to the retained `v2.1.3` snapshot.
9. `powershell -ExecutionPolicy Bypass -File scripts/compile_chem_locks.ps1
   -Check` passed after regenerating the three Windows chemistry locks and
   manifest from that exact wheel.
10. The release/contract regression set passed on Python 3.11, 3.12, and
    3.13 with the formal wheel installed: `87 passed, 1 skipped` on each
    matrix entry. The full non-integration Python 3.11 gate passed with
    `2237 passed, 36 skipped, 12 deselected`.
11. Ruff and Black passed for all changed Python files; MyPy passed for 189
    source files; YAML parsing and `git diff --check` passed. The local
    matrix environments initially contained the protected production-compatible
    ConfFlow `2.0.0`; that expected environment mismatch was not counted as a
    code failure, and the gate was rerun after installing the formal `2.1.6`
    wheel as CI does.

## Remaining authorization gates

The release workflow prepares the next candidate but does not itself prove
the v0.7.2 release retroactively. A future authorized v0.7.3 release still
needs a clean tagged commit, remote CI/compatibility gates, side-by-side
non-compute acceptance, and (if desired) separately authorized real-launcher
and production-promotion gates.
