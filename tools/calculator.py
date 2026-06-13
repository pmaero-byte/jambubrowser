"""Calculator tool — safe arithmetic expression evaluator.

Uses AST-based evaluation instead of eval() to prevent RCE.
Only allows: numbers, arithmetic operators, math functions.
"""
import ast
import math
import operator
from typing import Any, Optional

# Mapped operators for safe evaluation
_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Whitelisted math names accessible in expressions
_SAFE_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}
_SAFE_FUNCTIONS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "log": math.log, "log10": math.log10, "log2": math.log2,
    "exp": math.exp, "abs": abs, "floor": math.floor, "ceil": math.ceil,
    "round": round, "trunc": math.trunc, "degrees": math.degrees, "radians": math.radians,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "hypot": math.hypot, "pow": pow, "gcd": math.gcd, "factorial": math.factorial,
}


def _safe_eval(node: ast.AST) -> Any:
    """Recursively evaluate an AST node with strict whitelisting."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)):
            return node.value
        raise ValueError(f"Unsupported constant: {node.value!r}")

    if isinstance(node, ast.UnaryOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_safe_eval(node.operand))

    if isinstance(node, ast.BinOp):
        op = _OPERATORS.get(type(node.op))
        if op is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op(_safe_eval(node.left), _safe_eval(node.right))

    if isinstance(node, ast.Call):
        func_name = _get_func_name(node.func)
        if func_name is None:
            raise ValueError("Only named functions are supported")
        fn = _SAFE_FUNCTIONS.get(func_name)
        if fn is None:
            raise ValueError(f"Function '{func_name}' is not allowed")
        args = [_safe_eval(arg) for arg in node.args]
        return fn(*args)

    if isinstance(node, ast.Name):
        name = node.id
        if name in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[name]
        raise ValueError(f"Unknown variable: '{name}'")

    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)

    raise ValueError(f"Unsupported syntax: {type(node).__name__}")


def _get_func_name(node: ast.AST) -> Optional[str]:
    """Extract function name from a Call node's func."""
    if isinstance(node, ast.Name):
        return node.id
    return None


def run(**kwargs: Any) -> dict:
    """Evaluate a mathematical expression safely.

    Args:
        expr: Mathematical expression string (e.g. "2 + 2 * pi")

    Returns:
        dict with 'result' key containing the numeric result.

    Raises:
        ValueError: If the expression is invalid or uses disallowed features.
    """
    expr = kwargs.get("expr", "0")

    if not isinstance(expr, str) or not expr.strip():
        return {"result": 0}

    try:
        tree = ast.parse(expr.strip(), mode="eval")
        if not isinstance(tree, ast.Expression):
            return {"error": "Invalid expression"}
        result = _safe_eval(tree)
        return {"result": result}
    except (SyntaxError, ValueError, TypeError) as e:
        return {"error": str(e)}
