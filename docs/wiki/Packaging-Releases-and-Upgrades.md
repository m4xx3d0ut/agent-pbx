# Packaging, Releases, and Upgrades

> **Applies to:** Agent PBX 2.1.x  
> **Last reviewed:** 2026-10-10

Agent PBX publishes a Python wheel and source distribution through PyPI Trusted
Publishing. GitHub Releases carry the checksummed Linux wheelhouse, installer,
dependency manifest, and SPDX SBOM. TestPyPI and PyPI are separate package
indexes with separate project registration and Trusted Publisher configuration;
their project names should match the package identity even though their
registrations and credentials do not.

## Local release qualification

```bash
scripts/test_full.sh
python -m pytest -q tests/test_release_packaging.py tests/test_release_validation.py
python scripts/check_docs.py
git diff --check
```

Build and inspect distributions without publishing:

```bash
python -m build --sdist --wheel --outdir dist/pypi
python -m twine check --strict dist/pypi/*
```

Version, tag, changelog heading, release document, archive metadata, and GitHub
Release must agree. Published package-index files are immutable; a defective
release is yanked and replaced with a new patch version rather than deleted and
reused.

## Upgrade transaction

Create and retain a reviewable backup before stopping the daemon:

```bash
agent-pbx doctor
agent-pbx migrate dry-run
agent-pbx migrate backup --retention-days 7
agent-pbx mcp stop
agent-pbx migrate apply --retention-days 7
agent-pbx migrate verify
agent-pbx mcp start
agent-pbx doctor
```

Starred Agents, root Operators, active forks, session mappings, and rollback
evidence are preserved by the migration contract. Backup retention is a policy,
not an automatic deletion grant: preview and explicitly confirm pruning after
the reviewed retention period.

## Documentation and wiki release

The repository README is the package-index long description, so it uses
absolute public image URLs and static architecture media. Mermaid source stays
versioned under `docs/diagrams`. Wiki source stays under `docs/wiki` and is
published only after link, command, public-data, and rendered-page review.

See the complete [release procedure](../releasing.md),
[installation and rollback guide](../install-v2.md), and
[test qualification](../testing.md).

