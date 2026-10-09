# Agent PBX release publishing

Agent PBX publishes its Python wheel and source distribution through GitHub
Actions and PyPI Trusted Publishing. GitHub does not store a PyPI password or
API token. The production publish job receives a short-lived OpenID Connect
token only after exact-tag validation, the release test matrix, package
inspection, clean-install smoke testing, and approval through the protected
`pypi` environment.

The release artifacts have separate destinations:

- PyPI receives one `py3-none-any` wheel and one source distribution;
- the GitHub Release receives the checksummed Linux wheelhouse, installer,
  dependency manifest, and SPDX SBOM produced by `build_wheelhouse.sh`.

## Trust configuration

Configure the existing `agent-pbx` project on PyPI with this Trusted Publisher:

```text
Owner:        m4xx3d0ut
Repository:   agent-pbx
Workflow:     publish-pypi.yml
Environment:  pypi
```

Configure the TestPyPI project separately:

```text
Owner:        m4xx3d0ut
Repository:   agent-pbx
Workflow:     publish-testpypi.yml
Environment:  testpypi
```

The workflow filenames and environment names are part of the publisher
identity. Update the package-index publisher before renaming either value.

Both GitHub environments are restricted to release tags. Each has a required
reviewer and no secrets. A single-maintainer repository must leave self-review
enabled until a second trusted reviewer is available; otherwise the only
maintainer cannot approve the deployment.

## Release identity

Production releases use all of these matching values:

```text
src/agent_pbx/__init__.py       2.1.1
Git tag                         v2.1.1
CHANGELOG.md heading            ## v2.1.1 - YYYY-MM-DD
Release document                docs/releases/v2.1.1.md
GitHub Release tag              v2.1.1
```

TestPyPI rehearsals use a strict release-candidate form:

```text
src/agent_pbx/__init__.py       2.1.1rc1
Git tag                         v2.1.1rc1
CHANGELOG.md heading            ## v2.1.1rc1 - YYYY-MM-DD
Release document                docs/releases/v2.1.1rc1.md
```

The validator rejects other tag forms, mismatched versions, an untagged
checkout, a tag commit outside `dev`, missing release documents, or a version
already present on the target package index.

## TestPyPI rehearsal

1. Prepare and commit the prerelease version, changelog, and release document.
2. Run the full local test suite and release validation.
3. Create and push an annotated prerelease tag:

   ```bash
   git tag -a v2.1.1rc1 -m "Agent PBX 2.1.1rc1"
   git push github v2.1.1rc1
   ```

4. In GitHub Actions, open **publish Agent PBX to TestPyPI**.
5. Choose the prerelease tag in the **Run workflow** ref selector. Running the
   workflow from a branch is deliberately rejected.
6. Review the exact tag, commit, version, archive metadata, and hashes.
7. Approve the `testpypi` deployment.
8. Confirm that TestPyPI reports Trusted Publishing and attestations and that
   the verification job installs the exact version in a clean environment.

TestPyPI supplies Agent PBX while the verification environment obtains normal
dependencies from production PyPI. The production workflow is not reachable
from this manual rehearsal.

## Production release

1. Confirm `dev` is clean and synchronized with both Git remotes.
2. Run:

   ```bash
   scripts/test_full.sh
   python -m pytest -q tests/test_release_packaging.py tests/test_release_validation.py
   git diff --check
   ```

3. Set the stable version in `src/agent_pbx/__init__.py`.
4. Move the reviewed changelog entries from `Unreleased` into the matching
   version section and add `docs/releases/v<version>.md`.
5. Commit and push the release metadata to `dev`.
6. Wait for `v2 release gate` to pass on the exact commit.
7. Create and push an annotated `vMAJOR.MINOR.PATCH` tag.
8. Draft a GitHub Release targeting that tag and verify the target commit and
   generated release notes.
9. Publish the GitHub Release. A draft or tag push alone does not publish to
   PyPI.
10. Wait for release validation, all four test lanes, the isolated build,
    Twine metadata check, archive inspection, and wheel smoke installation.
11. Review the workflow summary and approve the `pypi` environment.
12. Confirm the PyPI verification job matches the published file hashes and
    installs the exact version from the public index.
13. Confirm the final job attached the wheelhouse archive, checksum, and
    installer to the GitHub Release.

The production workflow has no manual-dispatch trigger. The publish job is the
only job with `id-token: write`, and it publishes the immutable artifact built
by the unprivileged quality workflow. `skip-existing` remains disabled.

## Local release checks

Build and inspect the public distributions without publishing:

```bash
python -m build --sdist --wheel --outdir dist/pypi
python -m twine check --strict dist/pypi/*
python scripts/validate_release.py archives \
  --dist dist/pypi \
  --version "$(python -c 'import agent_pbx; print(agent_pbx.__version__)')"
python scripts/smoke_install_release.py \
  dist/pypi/*.whl \
  --version "$(python -c 'import agent_pbx; print(agent_pbx.__version__)')"
```

Build isolation is intentional. The build backend requirements in
`pyproject.toml` should be resolved in a clean PEP 517 environment rather than
inheriting the workstation virtual environment.

## Failure and recovery

PyPI versions and files are immutable. Never delete and reuse a public version.

- A failure before publication requires a corrected commit and tag. No package
  index rollback is needed.
- If the publisher fails without uploading a file, correct the environment or
  Trusted Publisher identity and rerun the failed job.
- If only part of a release reaches PyPI, stop and publish a new version after
  diagnosis; do not enable `skip-existing` to conceal the partial state.
- If a published release is defective, yank it, annotate the GitHub Release,
  fix the defect, and publish a new patch version.
- If publisher integrity is in doubt, remove the Trusted Publisher from PyPI,
  disable the workflow, and inspect repository access and workflow history.

After the first successful production Trusted Publishing release, revoke any
legacy PyPI token used for manual GitHub uploads. Keep account recovery codes
outside the repository and require two-factor authentication for package
maintainers.
