# Release checklist — Jambubrowser v3.4.0

Verified on macOS arm64, 2026-11-10. Version `3.4.0` is aligned in
`pyproject.toml`, `backend/__init__.py`, `browser-app/package.json`,
`browser-app/src-tauri/tauri.conf.json`, and `browser-app/src-tauri/Cargo.toml`.

## Verified gates

| Gate | Command | Result |
|---|---|---|
| Backend core tests | `make test-backend` | 117 passed |
| Frontend tests | `npx vitest run src/components/browser/ src/components/layout/ src/store/appStore.test.ts` | 109 passed |
| Typecheck / lint | `npm run typecheck`, eslint on touched files | clean |
| Rust tests | `cargo test --lib` (in `browser-app/src-tauri`) | 37 passed |
| Frontend build | `npm run build` | passes |
| Engine sidecar | `scripts/build_engine_sidecar.sh` | real Mach-O binary, smoke test OK (348 routes, browser session created) |
| Tauri app | `npm run tauri -- build --debug --no-bundle` | builds, launches, engine `/health` online, Chromium starts on a free port even when 9222 is taken |
| Docker image | `docker build -t jambubrowser-engine .` | builds (CPU-only torch; no CUDA wheels) |
| Docker runtime | `docker run -p 18001:8001 jambubrowser-engine:release-test` | container `healthy`, runs as user `jambu`, `/health` serves v3.4.0, Playwright Chromium present |
| Compose | `docker-compose config` | valid |

Live Chromium verification also confirmed the CDP history fix:
`Page.getNavigationHistory` returned entry IDs `[15, 17, 44]` for
indices `0, 1, 2`, and navigating by `entries[1].id` landed correctly.

## Fresh-checkout bootstrap

```bash
make install     # .venv + requirements-dev.txt + npm ci
make test        # backend core + frontend gates
```

Production installs use `requirements.txt` only. Development and CI
use `requirements-dev.txt` (runtime + pytest/ruff/mypy toolchain).
`pyproject.toml` is the canonical dependency source.

## Packaging

1. Build the frozen engine sidecar (required by Tauri `externalBin`):
   `scripts/build_engine_sidecar.sh`
2. Build the desktop app: `cd browser-app && npm run tauri build`
3. The Docker engine image is a separate artifact:
   `docker build -t jambubrowser-engine .`

The sidecar under `browser-app/src-tauri/binaries/` is gitignored;
build it on each release machine for its architecture.

## Still requires a human

- Commit review and the `v3.4.0` tag.
- macOS signing/notarisation and Windows signing credentials.
- PyPI / Homebrew / registry publishing credentials
  (see `docs/PUBLISHING.md`).

## Known notes

- `backend/modules/semantic.py` (`get_document` on the semantic and
  hybrid indexes, used by the bench CLI) is intentionally not part of
  the release-fix commit; commit it separately.
- The engine depends on the user's installed Chrome for the desktop
  shell, so Chromium security updates follow that install.
- Host disk space matters for release builds: the Docker build and
  Tauri target directory each need several free GB.
