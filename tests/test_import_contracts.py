"""Contract test: every `from backend.* import Name` in the codebase resolves.

This is the guard that would have caught the dead-endpoint class of bugs:
function-level imports of names that never existed (e.g. ``get_mlx_provider``,
``get_notification_manager``) only execute when an endpoint is called. The
routes catch ``Exception`` and return HTTP 500, so the suite stayed green
while ~25 endpoints were dead in production.

The test statically collects every import-from statement whose module is
inside ``backend.*`` and asserts the imported name actually exists on the
module (or resolves as a submodule).
"""
import ast
import importlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"


def _iter_python_files():
    for path in sorted(BACKEND.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _collect_backend_imports(path: Path):
    """Yield (lineno, module, name) for every backend.* import-from."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("backend.")
        ):
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, node.module, alias.name


def _import(module_name: str):
    """Import a module, returning the module or the exception."""
    try:
        return importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 — import failure is the finding
        return exc


@pytest.mark.parametrize(
    "path",
    list(_iter_python_files()),
    ids=lambda p: str(p.relative_to(REPO_ROOT)),
)
def test_backend_imports_resolve(path: Path):
    problems = []
    for lineno, module_name, name in _collect_backend_imports(path):
        module = _import(module_name)
        if isinstance(module, Exception):
            problems.append(
                f"{path.relative_to(REPO_ROOT)}:{lineno}: "
                f"cannot import '{module_name}': {module}"
            )
            continue
        if hasattr(module, name):
            continue
        # `from backend.modules import foo` may reference a submodule that
        # isn't imported yet — a successful module import is also valid.
        if isinstance(_import(f"{module_name}.{name}"), Exception):
            problems.append(
                f"{path.relative_to(REPO_ROOT)}:{lineno}: "
                f"'{module_name}' has no attribute '{name}'"
            )
    assert not problems, "Broken backend imports:\n" + "\n".join(problems)
