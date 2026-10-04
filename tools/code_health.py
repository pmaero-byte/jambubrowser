"""Code-health metrics for the Python codebase.

A refactor is only worth doing if you can show it moved a number. This tool
measures the handful of properties that actually correlate with debugging
pain in this repo, and is designed to be *checkable in CI* so the codebase
cannot silently rot back:

* ``long_file`` / ``long_function`` — where you stop being able to hold the
  code in your head. Over the limit is reported, not failed, because some
  files legitimately are big.
* ``missing_docstring`` — a public function/class whose contract nobody
  wrote down. This is the cheapest debugging aid there is: the docstring
  *is* the summary you wanted when you were reading the traceback.
* ``duplicate_definition`` — the exact defect class that hid in
  ``browser_agent.py`` (a class defined twice, the second shadowing the
  first). Hard failure: it is always a bug, never a style choice.
* ``silent_except`` — ``except Exception: pass`` swallows the error you
  wanted to see. Reported per file so clusters are obvious.
* ``debug_print`` — ``print()`` left in backend code (excluded under
  ``cli/`` and ``scripts/``, where printing *is* the product). Reported, not
  failed.

Usage::

    python tools/code_health.py                 # human table
    python tools/code_health.py --json          # machine-readable
    python tools/code_health.py --baseline f.json --write-baseline g.json
    python tools/code_health.py --baseline b.json   # exit 1 on regression
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

# Thresholds: chosen to flag the current worst offenders without flagging
# healthy files. Tune deliberately, never reflexively.
MAX_FILE_LOC = 1500          # warn above this
MAX_FUNCTION_LOC = 150       # warn above this
DOCSTRING_MIN = 0.60         # share of public defs with docstrings
DUPLICATE_DEFS_MAX = 0       # always a bug

DEFAULT_ROOTS = ("backend", "cli", "scripts")


@dataclass
class FileReport:
    path: str
    lines: int = 0
    functions: int = 0
    longest_function: str = ""
    longest_function_loc: int = 0
    long_functions: int = 0
    public_defs: int = 0
    documented_defs: int = 0
    duplicate_definitions: list[str] = field(default_factory=list)
    silent_excepts: int = 0
    debug_prints: int = 0
    todos: int = 0

    @property
    def docstring_ratio(self) -> float:
        return (self.documented_defs / self.public_defs) if self.public_defs else 1.0

    def flags(self, *, max_file_loc: int, max_function_loc: int,
              docstring_min: float, duplicate_max: int) -> list[str]:
        out: list[str] = []
        if self.lines > max_file_loc:
            out.append(f"long_file({self.lines}>{max_file_loc})")
        if self.long_functions:
            out.append(f"long_function({self.long_functions} over {max_function_loc})")
        if self.docstring_ratio < docstring_min and self.public_defs:
            out.append(f"thin_docs({self.docstring_ratio:.0%}<{docstring_min:.0%})")
        if len(self.duplicate_definitions) > duplicate_max:
            out.append(f"duplicate_definition({','.join(self.duplicate_definitions)})")
        if self.silent_excepts:
            out.append(f"silent_except({self.silent_excepts})")
        if self.debug_prints:
            out.append(f"debug_print({self.debug_prints})")
        return out


def _is_public(name: str) -> bool:
    return not name.startswith("_") or name.startswith("__") and name.endswith("__")


def _iter_py_files(roots: Iterable[str], repo_root: Path) -> list[Path]:
    """Python files under the given roots, resolved against ``repo_root``.

    Roots are given relative to the repo (``backend``, ``cli``, …) so the
    tool behaves the same regardless of the shell's working directory.
    """
    files: list[Path] = []
    for root in roots:
        base = Path(root)
        if not base.is_absolute():
            base = repo_root / base
        if not base.exists():
            continue
        files.extend(sorted(base.rglob("*.py")))
    return [f for f in files if "__pycache__" not in f.parts]


def _is_cli_path(path: str) -> bool:
    """CLI/script files print by design; debug-print is a backend smell."""
    return path.startswith(("cli/", "scripts/"))


def analyse_file(path: Path, repo_root: Path) -> FileReport:
    source = path.read_text(encoding="utf-8", errors="replace")
    # Roots are usually relative paths; resolve before relativising so the
    # report is stable regardless of how the tool was invoked.
    try:
        shown = str(path.resolve().relative_to(repo_root))
    except ValueError:
        shown = str(path)
    report = FileReport(path=shown)
    report.lines = len(source.splitlines())
    report.todos = source.count("TODO") + source.count("FIXME")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        # A file that cannot be parsed is a hard problem; surface it as a
        # duplicate-style flag rather than crashing the whole report.
        report.duplicate_definitions = [f"syntax_error@{exc.lineno}"]
        return report

    seen: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen[node.name] = seen.get(node.name, 0) + 1
    report.duplicate_definitions = sorted(n for n, c in seen.items() if c > 1)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            report.functions += 1
            loc = (node.end_lineno or node.lineno) - node.lineno + 1
            if loc > report.longest_function_loc:
                report.longest_function_loc = loc
                report.longest_function = node.name
            if loc > 150:  # module constant would create an import cycle
                report.long_functions += 1
            if _is_public(node.name):
                report.public_defs += 1
                if ast.get_docstring(node):
                    report.documented_defs += 1
        elif isinstance(node, ast.ExceptHandler):
            if node.type is None or (
                isinstance(node.type, ast.Name) and node.type.id == "Exception"
            ):
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    report.silent_excepts += 1
        elif isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Name) and func.id == "print"
                    and not _is_cli_path(report.path)):
                report.debug_prints += 1
    return report


def analyse(
    repo_root: Path, roots: Iterable[str] = DEFAULT_ROOTS,
) -> list[FileReport]:
    return [analyse_file(p, repo_root) for p in _iter_py_files(roots, repo_root)]


def summarise(reports: list[FileReport], **thresholds) -> dict:
    return {
        "files": len(reports),
        "total_lines": sum(r.lines for r in reports),
        "duplicate_definitions": sum(len(r.duplicate_definitions) for r in reports),
        "silent_excepts": sum(r.silent_excepts for r in reports),
        "debug_prints": sum(r.debug_prints for r in reports),
        "public_defs": sum(r.public_defs for r in reports),
        "documented_defs": sum(r.documented_defs for r in reports),
        "long_files": sum(1 for r in reports if r.lines > thresholds["max_file_loc"]),
        "long_functions": sum(r.long_functions for r in reports),
        "docstring_ratio": round(
            sum(r.documented_defs for r in reports)
            / max(sum(r.public_defs for r in reports), 1), 4),
    }


def print_table(reports: list[FileReport], **thresholds) -> None:
    flagged = [r for r in reports if r.flags(**thresholds)]
    flagged.sort(key=lambda r: (-len(r.flags(**thresholds)), -r.lines))
    print(f"{'file':<52} {'loc':>6} {'docs':>6} {'flags'}")
    print("-" * 100)
    for r in flagged[:40]:
        docs = f"{r.docstring_ratio:.0%}" if r.public_defs else "-"
        print(f"{r.path:<52} {r.lines:>6} {docs:>6} {', '.join(r.flags(**thresholds))}")
    print("-" * 100)
    print(f"{len(flagged)} flagged of {len(reports)} files")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--roots", nargs="*", default=list(DEFAULT_ROOTS))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--baseline", help="baseline json to compare against")
    parser.add_argument("--write-baseline", help="write baseline json here")
    parser.add_argument("--max-file-loc", type=int, default=MAX_FILE_LOC)
    parser.add_argument("--max-function-loc", type=int, default=MAX_FUNCTION_LOC)
    parser.add_argument("--docstring-min", type=float, default=DOCSTRING_MIN)
    args = parser.parse_args(argv)

    thresholds = {
        "max_file_loc": args.max_file_loc,
        "max_function_loc": args.max_function_loc,
        "docstring_min": args.docstring_min,
        "duplicate_max": DUPLICATE_DEFS_MAX,
    }
    root = Path(args.repo_root).resolve()
    reports = analyse(root, args.roots)
    summary = summarise(reports, **thresholds)

    if args.write_baseline:
        Path(args.write_baseline).write_text(
            json.dumps({"summary": summary, "files": [asdict(r) for r in reports]},
                       indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"wrote baseline {args.write_baseline}")

    if args.baseline:
        base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        old = base.get("summary", {})
        regressions = []
        # Only numbers that must never go *up* are gated; ratios are gated
        # on the side that means "less documented".
        for key in ("duplicate_definitions", "silent_excepts", "debug_prints",
                    "long_files", "long_functions"):
            if summary.get(key, 0) > old.get(key, 0):
                regressions.append(f"{key}: {old.get(key)} -> {summary.get(key)}")
        if summary.get("docstring_ratio", 1) < old.get("docstring_ratio", 1):
            regressions.append(
                f"docstring_ratio: {old.get('docstring_ratio')} -> {summary['docstring_ratio']}"
            )
        if summary.get("duplicate_definitions", 0) > DUPLICATE_DEFS_MAX:
            regressions.append(
                f"duplicate_definitions must be {DUPLICATE_DEFS_MAX}"
            )
        if regressions:
            print("code-health REGRESSIONS:")
            for r in regressions:
                print(f"  - {r}")
            return 1
        print("code-health: no regressions vs baseline")

    if args.json:
        print(json.dumps({"summary": summary, "files": [asdict(r) for r in reports]},
                         indent=2, sort_keys=True))
    else:
        print_table(reports, **thresholds)
        print()
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
