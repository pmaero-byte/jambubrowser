# Publishing `jambu` — PyPI + Homebrew runbook

`jambu` ships as the `jambubrowser` PyPI package (console script
`jambu = cli.jambu:main`) and a Homebrew formula that installs it.
The release checklist keeps the three version sources in sync; the
`tests/test_packaging.py` suite enforces it in CI.

## Version bump checklist

1. Bump the version in **all three** places (they must match):
   - `pyproject.toml` → `version`
   - `backend/__init__.py` → `__version__`
   - `browser-app/package.json` → `version`
2. `git commit`, tag `vX.Y.Z`, push the tag.
3. The `release.yml` workflow (or the manual steps below) builds and uploads.

## Manual PyPI release (TestPyPI first)

```bash
# One-time: trusted publishing on PyPI, or an API token in ~/.pypirc
.venv/bin/pip install -U build twine

rm -rf dist/
.venv/bin/python -m build
.venv/bin/python -m twine check dist/*

# Dry run against TestPyPI
.venv/bin/python -m twine upload --repository testpypi dist/*

# Verify in a clean venv, then release for real
.venv/bin/python -m twine upload dist/*
```

Verify the install reports the right version and entry point:

```bash
pipx install jambubrowser==X.Y.Z   # or: pip install jambubrowser
jambu --help
jambu health
```

## Post-install setup the user still needs

PyPI delivers the Python; the browser driver and LLM are environment steps
the README covers and the formula echoes as caveats:

```bash
python -m playwright install chromium
ollama serve   # or configure a cloud provider key
```

## Homebrew

The formula lives at `packaging/homebrew/jambu.rb` (template — update the
`url`/`sha256` per release). Publishing options, easiest first:

1. **Tap (recommended):** push the formula to
   `github.com/pmaero-byte/homebrew-jambu`, then:
   `brew install pmaero-byte/jambu/jambu`.
2. **Core:** submit to homebrew-core once the PyPI release has traction.

Update the formula per release:

```bash
SHA=$(sha256sum dist/jambubrowser-X.Y.Z.tar.gz | cut -d' ' -f1)
# paste url + sha256 into packaging/homebrew/jambu.rb
brew audit --strict --new --online pmaero-byte/jambu/jambu  # in the tap
brew test pmaero-byte/jambu/jambu
```

## What is intentionally NOT automated here

Actual uploads need human credentials (PyPI token / Trusted Publisher,
Apple notarization for the desktop `.dmg`, tap push rights). This runbook
plus the packaging tests make the release itself a 10-minute, checklisted
operation instead of archaeology.
