"""Tests for the code-health tool.

The tool is a gate, so the gate itself needs tests: a metric that silently
stops counting would let the codebase rot back without anyone noticing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.code_health import (
    DEFAULT_ROOTS,
    analyse_file,
    analyse,
    main,
    summarise,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


SAMPLE = '''
"""Module docstring."""


def documented():
    """Has a docstring."""


def undocumented():
    return 1


def _private_helper():
    return 2


async def documented_async():
    """Async docstring."""


class Thing:
    """A class."""

    def method(self):
        return 3


def quiet():
    try:
        return 1
    except Exception:
        pass


def loud():
    print("debug leftover")
'''


DUPLICATED = '''
class Widget:
    pass


class Widget:
    pass
'''


@pytest.fixture()
def sample_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample.py"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


class TestMetrics:
    def test_counts_and_flags(self, sample_file: Path, tmp_path: Path):
        r = analyse_file(sample_file, tmp_path)
        assert r.path == "sample.py"
        assert r.functions == 7  # 5 module-level + method + one nested-free async
        # public = documented, undocumented, documented_async, method, loud, quiet
        assert r.public_defs >= 4
        assert r.docstring_ratio < 1.0
        assert r.silent_excepts == 1
        assert r.debug_prints == 1
        assert r.duplicate_definitions == []

    def test_private_helpers_are_not_public(self, sample_file: Path, tmp_path: Path):
        r = analyse_file(sample_file, tmp_path)
        # _private_helper must not count towards the documentation ratio
        assert r.docstring_ratio < 1.0

    def test_duplicate_definitions_detected(self, tmp_path: Path):
        p = tmp_path / "dup.py"
        p.write_text(DUPLICATED, encoding="utf-8")
        r = analyse_file(p, tmp_path)
        assert r.duplicate_definitions == ["Widget"]

    def test_syntax_error_is_reported_not_raised(self, tmp_path: Path):
        p = tmp_path / "broken.py"
        p.write_text("def (:\n", encoding="utf-8")
        r = analyse_file(p, tmp_path)
        assert any("syntax_error" in d for d in r.duplicate_definitions)

    def test_cli_paths_exempt_from_debug_print(self, tmp_path: Path):
        cli = tmp_path / "cli"
        cli.mkdir()
        p = cli / "tool.py"
        p.write_text('def main():\n    print("hello")\n', encoding="utf-8")
        r = analyse_file(p, tmp_path)
        assert r.debug_prints == 0

    def test_longest_function_is_tracked(self, tmp_path: Path):
        p = tmp_path / "long.py"
        body = "\n".join(f"    x{i} = {i}" for i in range(60))
        p.write_text(f"def big():\n{body}\n", encoding="utf-8")
        r = analyse_file(p, tmp_path)
        assert r.longest_function == "big"
        assert r.longest_function_loc >= 60


class TestCli:
    def test_json_output_and_baseline_roundtrip(self, tmp_path: Path, capsys):
        root = tmp_path / "repo"
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "m.py").write_text(SAMPLE, encoding="utf-8")
        base = tmp_path / "base.json"

        assert main(["--repo-root", str(root), "--roots", "pkg",
                     "--write-baseline", str(base), "--json"]) == 0
        data = json.loads(base.read_text(encoding="utf-8"))
        assert data["summary"]["files"] == 1

        # Same tree again against its own baseline must not regress.
        assert main(["--repo-root", str(root), "--roots", "pkg",
                     "--baseline", str(base), "--json"]) == 0

    def test_baseline_detects_regression(self, tmp_path: Path):
        root = tmp_path / "repo"
        (root / "pkg").mkdir(parents=True)
        (root / "pkg" / "clean.py").write_text('def f():\n    """Doc."""\n', encoding="utf-8")
        base = tmp_path / "base.json"
        assert main(["--repo-root", str(root), "--roots", "pkg",
                     "--write-baseline", str(base), "--json"]) == 0
        # Introduce a duplicate definition and a debug print.
        (root / "pkg" / "bad.py").write_text(
            'class A:\n    pass\n\n\nclass A:\n    pass\n\n\n'
            'def p():\n    print("x")\n',
            encoding="utf-8",
        )
        assert main(["--repo-root", str(root), "--roots", "pkg",
                     "--baseline", str(base), "--json"]) == 1

    def test_repo_has_no_swallowed_exceptions(self):
        """The repo reached zero `except Exception: pass`; keep it there.

        A silent handler is how an engine shutdown ends up hiding a leaked
        browser, so the count is pinned at zero rather than merely reported.
        The baseline comparison already fails if this number goes *up*; this
        test states the invariant so the intent survives a baseline rewrite.
        """
        reports = analyse(REPO_ROOT, list(DEFAULT_ROOTS))
        offenders = {
            r.path: r.silent_excepts for r in reports if r.silent_excepts
        }
        assert offenders == {}

    def test_summarise_aggregates(self, tmp_path: Path):
        p = tmp_path / "x.py"
        p.write_text(SAMPLE, encoding="utf-8")
        reports = analyse(tmp_path, ["."])
        s = summarise(reports, max_file_loc=1500, max_function_loc=150,
                      docstring_min=0.6, duplicate_max=0)
        assert s["files"] >= 1
        assert s["public_defs"] >= 1
