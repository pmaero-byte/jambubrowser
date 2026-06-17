#!/usr/bin/env python3
"""
Browser-app Efficiency Benchmark — measures the build / test / size / dep
footprint of the Tauri v2 + Vite 7 + React 19 frontend (browser-app/).

What it measures (each is a printed sub-report, machine-greppable):

  SUB-A  Source size
          - TypeScript / TSX line count + file count
          - React components (under src/components/)
          - Stores (under src/store/)
          - Rust line count (under src-tauri/src/)

  SUB-B  Dependency surface
          - runtime + dev dependency count from package.json
          - transitive count from package-lock.json (top-level only)

  SUB-C  Install footprint
          - node_modules size (bytes + MB)
          - package count inside node_modules

  SUB-D  Lint pass
          - npm run lint timing
          - 0 / non-zero error count
          - file linted

  SUB-E  Typecheck pass
          - npm run typecheck timing
          - 0 / non-zero error count

  SUB-F  Test pass
          - npm test (vitest run) timing
          - test file count + per-file test count
          - pass / fail

  SUB-G  Build
          - npm run build timing
          - dist/ size (HTML + CSS + JS)
          - per-asset size breakdown
          - chunk-size advisory if any chunk > 500kB

Run (NOT via pytest):

    cd browser-app && npm install  # one-time
    python3 tests/bench_browser.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BROWSER_APP = REPO_ROOT / "browser-app"
SRC_TAURI = BROWSER_APP / "src-tauri"

RESULTS: list[tuple[str, str, object]] = []


def _record(sub: str, metric: str, value) -> None:
    RESULTS.append((sub, metric, value))
    print(f"  [BENCH/{sub}] {metric} = {value}")


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _ok(msg: str) -> None:
    print(f"    \033[32m✓\033[0m {msg}")


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str, float]:
    """Run a command, return (exit_code, stdout, elapsed_seconds)."""
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or BROWSER_APP,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed = time.time() - t0
        return result.returncode, result.stdout + result.stderr, elapsed
    except subprocess.TimeoutExpired:
        return -1, f"timeout after {timeout}s", time.time() - t0


def _loc_for_extensions(root: Path, exts: tuple[str, ...]) -> tuple[int, int]:
    """Return (total_lines, file_count) for files with the given extensions under root."""
    total_lines = 0
    file_count = 0
    for ext in exts:
        for p in root.rglob(f"*{ext}"):
            if "node_modules" in p.parts or "target" in p.parts or "dist" in p.parts:
                continue
            try:
                total_lines += sum(1 for _ in p.open())
                file_count += 1
            except (OSError, UnicodeError):
                pass
    return total_lines, file_count


# ---------------------------------------------------------------------------
# SUB-A: Source size
# ---------------------------------------------------------------------------

def sub_a_source_size() -> None:
    _section("SUB-A: source size")
    ts_loc, ts_files = _loc_for_extensions(BROWSER_APP / "src", (".ts", ".tsx"))
    css_loc, css_files = _loc_for_extensions(BROWSER_APP / "src", (".css",))
    rust_loc, rust_files = _loc_for_extensions(SRC_TAURI / "src", (".rs",))

    # Component count: files under src/components/
    comp_dir = BROWSER_APP / "src" / "components"
    components = [p for p in comp_dir.rglob("*.tsx") if p.is_file()]
    store_dir = BROWSER_APP / "src" / "store"
    stores = [p for p in store_dir.glob("*.ts") if p.is_file() and not p.name.startswith("__")]

    _record("A", "ts_loc", ts_loc)
    _record("A", "ts_files", ts_files)
    _record("A", "css_loc", css_loc)
    _record("A", "css_files", css_files)
    _record("A", "rust_loc", rust_loc)
    _record("A", "rust_files", rust_files)
    _record("A", "components", len(components))
    _record("A", "stores", len(stores))
    _ok(f"{ts_files} TS files ({ts_loc} lines), {len(components)} components, {len(stores)} stores, {rust_files} Rust files ({rust_loc} lines)")


# ---------------------------------------------------------------------------
# SUB-B: Dependency surface
# ---------------------------------------------------------------------------

def sub_b_deps() -> None:
    _section("SUB-B: dependency surface")
    pkg = json.loads((BROWSER_APP / "package.json").read_text())
    deps = pkg.get("dependencies", {})
    dev = pkg.get("devDependencies", {})
    _record("B", "runtime_deps", len(deps))
    _record("B", "dev_deps", len(dev))

    # Transitive top-level: count entries in node_modules
    nm = BROWSER_APP / "node_modules"
    transitive = 0
    if nm.exists():
        # Count @scoped packages by their parent directory entries
        transitive = sum(1 for p in nm.iterdir() if p.is_dir() and not p.name.startswith("."))
    _record("B", "transitive_packages", transitive)
    _ok(f"{len(deps)} runtime + {len(dev)} dev deps; {transitive} packages installed")


# ---------------------------------------------------------------------------
# SUB-C: Install footprint
# ---------------------------------------------------------------------------

def sub_c_footprint() -> None:
    _section("SUB-C: install footprint")
    nm = BROWSER_APP / "node_modules"
    if not nm.exists():
        _record("C", "node_modules_size_bytes", 0)
        _record("C", "node_modules_size_mb", 0.0)
        _record("C", "package_count", 0)
        _ok("node_modules not installed (run `npm install` first)")
        return

    total_bytes = sum(
        sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
        for p in nm.iterdir() if p.is_dir()
    )
    pkg_count = sum(1 for p in nm.iterdir() if p.is_dir() and not p.name.startswith("."))
    _record("C", "node_modules_size_bytes", total_bytes)
    _record("C", "node_modules_size_mb", f"{total_bytes / 1024 / 1024:.1f}")
    _record("C", "package_count", pkg_count)
    _ok(f"node_modules: {pkg_count} packages, {total_bytes / 1024 / 1024:.1f} MB")


# ---------------------------------------------------------------------------
# SUB-D: Lint pass
# ---------------------------------------------------------------------------

def sub_d_lint() -> None:
    _section("SUB-D: lint (npm run lint)")
    code, out, elapsed = _run(["npm", "run", "lint", "--silent"], timeout=180)
    _record("D", "exit_code", code)
    _record("D", "time_seconds", f"{elapsed:.2f}")
    # Extract error/warning counts from eslint output
    err_count = len(re.findall(r"^\s*\d+:\d+\s+error", out, re.MULTILINE))
    warn_count = len(re.findall(r"^\s*\d+:\d+\s+warning", out, re.MULTILINE))
    _record("D", "errors", err_count)
    _record("D", "warnings", warn_count)
    if code == 0:
        _ok(f"lint clean in {elapsed:.2f}s ({err_count} errors, {warn_count} warnings)")
    else:
        _ok(f"lint FAILED exit={code} in {elapsed:.2f}s ({err_count} errors, {warn_count} warnings)")
        # Last 20 lines for context (capped)
        for line in out.strip().splitlines()[-20:]:
            print(f"      {line}")


# ---------------------------------------------------------------------------
# SUB-E: Typecheck pass
# ---------------------------------------------------------------------------

def sub_e_typecheck() -> None:
    _section("SUB-E: typecheck (npm run typecheck)")
    code, out, elapsed = _run(["npm", "run", "typecheck", "--silent"], timeout=180)
    _record("E", "exit_code", code)
    _record("E", "time_seconds", f"{elapsed:.2f}")
    err_count = out.count("error TS")
    _record("E", "errors", err_count)
    if code == 0:
        _ok(f"typecheck clean in {elapsed:.2f}s")
    else:
        _ok(f"typecheck FAILED exit={code} in {elapsed:.2f}s ({err_count} TS errors)")
        for line in out.strip().splitlines()[-20:]:
            print(f"      {line}")


# ---------------------------------------------------------------------------
# SUB-F: Test pass
# ---------------------------------------------------------------------------

def sub_f_test() -> None:
    _section("SUB-F: test (npm test / vitest run)")
    code, out, elapsed = _run(["npm", "test", "--silent", "--", "--reporter=verbose"], timeout=300)
    _record("F", "exit_code", code)
    _record("F", "time_seconds", f"{elapsed:.2f}")

    # Vitest output: "Test Files  N passed (M)"
    test_files_match = re.search(r"Test Files\s+(\d+)\s+passed", out)
    test_files_failed_match = re.search(r"Test Files\s+\d+\s+passed\s+\|\s+(\d+)\s+failed", out)
    tests_passed_match = re.search(r"Tests\s+(\d+)\s+passed", out)
    tests_failed_match = re.search(r"Tests\s+\d+\s+passed\s+\|\s+(\d+)\s+failed", out)

    n_test_files = int(test_files_match.group(1)) if test_files_match else 0
    n_tests = int(tests_passed_match.group(1)) if tests_passed_match else 0
    n_files_failed = int(test_files_failed_match.group(1)) if test_files_failed_match else 0
    n_tests_failed = int(tests_failed_match.group(1)) if tests_failed_match else 0

    _record("F", "test_files_passed", n_test_files)
    _record("F", "test_files_failed", n_files_failed)
    _record("F", "tests_passed", n_tests)
    _record("F", "tests_failed", n_tests_failed)

    if code == 0 and n_tests_failed == 0:
        _ok(f"tests pass: {n_test_files} files, {n_tests} tests in {elapsed:.2f}s")
    else:
        _ok(f"tests FAILED exit={code} in {elapsed:.2f}s: {n_tests} pass, {n_tests_failed} fail")


# ---------------------------------------------------------------------------
# SUB-G: Build
# ---------------------------------------------------------------------------

def sub_g_build() -> None:
    _section("SUB-G: build (npm run build)")
    # Clean dist first to get a true cold-build timing
    dist = BROWSER_APP / "dist"
    if dist.exists():
        import shutil
        shutil.rmtree(dist)

    code, out, elapsed = _run(["npm", "run", "build", "--silent"], timeout=600)
    _record("G", "exit_code", code)
    _record("G", "time_seconds", f"{elapsed:.2f}")

    if code != 0 or not dist.exists():
        _ok(f"build FAILED exit={code} in {elapsed:.2f}s")
        for line in out.strip().splitlines()[-15:]:
            print(f"      {line}")
        return

    # Asset breakdown
    html = list(dist.glob("*.html"))
    css = list(dist.glob("assets/*.css"))
    js = list(dist.glob("assets/*.js"))
    total_html = sum(p.stat().st_size for p in html)
    total_css = sum(p.stat().st_size for p in css)
    total_js = sum(p.stat().st_size for p in js)
    total = total_html + total_css + total_js
    _record("G", "html_count", len(html))
    _record("G", "css_count", len(css))
    _record("G", "js_count", len(js))
    _record("G", "html_bytes", total_html)
    _record("G", "css_bytes", total_css)
    _record("G", "js_bytes", total_js)
    _record("G", "total_bytes", total)
    _record("G", "total_kb", f"{total / 1024:.1f}")

    # Largest JS chunk
    if js:
        largest = max(js, key=lambda p: p.stat().st_size)
        _record("G", "largest_js_chunk_bytes", largest.stat().st_size)
        _record("G", "largest_js_chunk_kb", f"{largest.stat().st_size / 1024:.1f}")
        _record("G", "largest_js_chunk_name", largest.name)
        if largest.stat().st_size > 500_000:
            _record("G", "chunk_size_advisory", "LARGEST_JS_CHUNK_OVER_500KB")

    # Extract Vite's "built in X.XXs" line
    built_in = re.search(r"built in (\d+(?:\.\d+)?)\s*s", out)
    if built_in:
        _record("G", "vite_reported_time", f"{built_in.group(1)}s")

    _ok(f"build OK in {elapsed:.2f}s — {len(html)} HTML + {len(css)} CSS + {len(js)} JS = {total / 1024:.1f} KB")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
    print("=" * 70)
    print("Browser-app Benchmark — 7 sub-reports")
    print("=" * 70)
    print(f"Target: {BROWSER_APP}")

    subs = [
        ("SUB-A source size", sub_a_source_size),
        ("SUB-B deps", sub_b_deps),
        ("SUB-C footprint", sub_c_footprint),
        ("SUB-D lint", sub_d_lint),
        ("SUB-E typecheck", sub_e_typecheck),
        ("SUB-F tests", sub_f_test),
        ("SUB-G build", sub_g_build),
    ]
    failed = 0
    for name, fn in subs:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [BENCH/FAIL] {name}: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print(f"COMPLETE: {len(RESULTS)} metrics, {failed} sub-report failure(s)")
    print("=" * 70)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    if not BROWSER_APP.exists():
        print(f"browser-app not found at {BROWSER_APP}", file=sys.stderr)
        sys.exit(2)
    sys.exit(_run_all())
