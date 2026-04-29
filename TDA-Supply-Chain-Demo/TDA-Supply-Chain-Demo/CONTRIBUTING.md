# Contributing to TDA Supply Chain

Thank you for your interest in contributing. This guide covers the full development workflow.

---

## Development Setup

```bash
git clone https://github.com/Chege-N/tda-supply-chain.git
cd tda-supply-chain
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install ruff mypy pytest pytest-cov
```

## Branching Strategy

We follow **GitHub Flow**:

| Branch | Purpose |
|--------|---------|
| `main` | Always deployable — protected, requires PR + CI green |
| `develop` | Integration branch for feature work |
| `feat/<name>` | New feature (branch from `develop`) |
| `fix/<name>` | Bug fix (branch from `main` for hotfixes, `develop` otherwise) |
| `docs/<name>` | Documentation only |
| `chore/<name>` | Maintenance, dependency bumps |

```bash
git checkout develop
git checkout -b feat/ripser-acceleration
```

## Commit Message Convention

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

[optional body]

[optional footer: Closes #issue]
```

**Types:** `feat` · `fix` · `docs` · `test` · `refactor` · `perf` · `chore` · `ci`

**Scopes:** `tda` · `detector` · `pipeline` · `api` · `datagen` · `viz` · `bench` · `deps`

### Examples
```
feat(tda): add ripser backend for GPU-accelerated persistence

Replaces the Python Z/2 reducer with ripser when available,
falling back to the pure-Python implementation automatically.

Closes #42
```
```
fix(detector): correct essential-class tracking after paired simplices

Essential classes were previously overcounted when killed_indices
overlapped with the zero-boundary simplex set. Fixes FPR spike on
sparse complexes with < 5 nodes.

Closes #38
```
```
perf(tda): cap Wasserstein diagram size at 50 top-persistence points

Reduces W2 computation from O(n²) to O(50²) for large complexes
with no measurable AUC degradation on benchmark suite.
```

## Testing

```bash
# Full suite
pytest tests/ -v --tb=short

# With coverage report
pytest tests/ --cov=src --cov-report=html
open htmlcov/index.html

# Single test
pytest tests/test_tda_engine.py::TestPersistentHomology -v
```

All PRs must maintain **test coverage ≥ 70%** and pass the FPR smoke test.

## Code Style

```bash
ruff check src/ tests/ --fix      # lint + auto-fix
ruff format src/ tests/            # format
mypy src/core/ --ignore-missing-imports
```

## Pull Request Process

1. Branch from `develop` (or `main` for hotfixes)
2. Write tests for your change
3. Ensure `pytest` and `ruff` pass locally
4. Open a PR using the template — fill every section
5. Request review from a maintainer
6. Squash-merge after approval

## Mathematical Contributions

If your PR changes the TDA algorithm (simplex construction, reduction, distance metric):
- Cite the relevant paper in the docstring
- Add a unit test that verifies the topological invariant (e.g., Euler characteristic)
- Update `CHANGELOG.md` under `[Unreleased]`

## Reporting Security Issues

Do **not** open a public issue. Email `chegenganga08@gmail,com` with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

We will respond within 72 hours.
