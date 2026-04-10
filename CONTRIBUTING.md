# Contributing

## Development setup

```
git clone https://github.com/steverice/yoto-library.git
cd yoto-library
pip install -e ".[dev]"
pre-commit install --hook-type commit-msg --hook-type pre-commit
```

## Commit conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/) with [Gitmoji](https://gitmoji.dev/). The pre-commit hook validates your message and auto-prepends the correct emoji — just write a conventional commit and the emoji is added for you.

| Type       | Emoji | Description                        | Version bump |
|------------|-------|------------------------------------|-------------|
| `feat:`    | ✨    | New feature                        | minor       |
| `fix:`     | 🐛    | Bug fix                            | patch       |
| `docs:`    | 📝    | Documentation only                 | none        |
| `style:`   | 🎨    | Formatting, no code change         | none        |
| `refactor:`| ♻️    | Code change, no new feature or fix | none        |
| `test:`    | ✅    | Adding or updating tests           | none        |
| `chore:`   | 🧹    | Maintenance, tooling, config       | none        |
| `ci:`      | 💚    | CI/CD changes                      | none        |

Include a scope when it helps: `feat(covers): add outpainting support`.

Use `BREAKING CHANGE:` in the commit body (or `!` after the type) for breaking changes — this triggers a major version bump.

Example:

```
feat: add playlist export to MP3

BREAKING CHANGE: export command now requires --format flag
```

## Running checks

```
make lint      # ruff check + ruff format --check + ty check
make test      # unit tests (excludes integration)
make check     # lint + test
```

## Pull requests

1. Create a branch from `main`
2. Make your changes with conventional commits
3. Run `make check` to verify lint and tests pass
4. Open a PR against `main` — CI validates commit messages, lint, and tests

## Releases

Releases are automated via CI and restricted to maintainers. Never run `cz bump` locally.

To publish a release:

1. Go to **Actions > Publish to PyPI > Run workflow** (or `gh workflow run publish.yml`)
2. The workflow bumps the version, updates `CHANGELOG.md`, builds, publishes to TestPyPI then PyPI, and creates a GitHub release

The version bump is determined automatically from commit history since the last tag:
- `feat:` commits → minor bump
- `fix:` commits → patch bump
- `BREAKING CHANGE` → major bump
- Other types (`docs:`, `ci:`, `chore:`, etc.) don't trigger a bump on their own
