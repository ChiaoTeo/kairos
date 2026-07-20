# PyPI Release Guide

Kairos uses two public names:

- PyPI distribution name: `kairospy`
- Python import package and CLI command: `kairospy`

## PyPI Trusted Publisher

On PyPI, add a pending GitHub trusted publisher with these fields:

| PyPI field | Value |
| --- | --- |
| PyPI Project Name | `kairospy` |
| Owner | `ChiaoTeo` |
| Repository name | `kairospy` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

The repository URL currently configured as `origin` is `https://github.com/ChiaoTeo/kairospy.git`, so the trusted publisher must use `ChiaoTeo / kairospy`.

## GitHub Environment

Create a GitHub Actions environment named `pypi`:

1. Open the GitHub repository settings.
2. Go to Environments.
3. Create an environment named `pypi`.
4. Add required reviewers if release publishing should need manual approval.

The workflow in `.github/workflows/release.yml` declares `environment: pypi`, so the environment name must match exactly if it is configured on PyPI.

## Release Flow

1. Ensure `pyproject.toml` has the intended version.
2. Commit and push the release changes to GitHub.
3. Create and push a version tag, for example `v0.1.0`.
4. GitHub Actions runs `.github/workflows/release.yml` from the tag push.
5. The workflow builds the package and publishes `kairospy` to PyPI through OIDC.

No PyPI API token is required for this flow.

## Local Static Check

Before releasing, run:

```bash
./scripts/check_naming_static.sh
```

This checks the current naming boundary without importing Python or building a wheel.
It depends only on shell, `git`, `rg`, `find`, `awk`, `cut`, and `head`.
