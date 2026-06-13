"""Tests: tools/calculator.py — safe AST-based expression evaluator."""
import pytest


class TestCalculatorArithmetic:
    def test_simple_addition(self):
        from tools.calculator import run
        assert run(expr="2 + 3") == {"result": 5}

    def test_simple_subtraction(self):
        from tools.calculator import run
        assert run(expr="10 - 4") == {"result": 6}

    def test_simple_multiplication(self):
        from tools.calculator import run
        assert run(expr="6 * 7") == {"result": 42}

    def test_division(self):
        from tools.calculator import run
        assert run(expr="10 / 4") == {"result": 2.5}

    def test_floor_division(self):
        from tools.calculator import run
        assert run(expr="10 // 3") == {"result": 3}

    def test_modulo(self):
        from tools.calculator import run
        assert run(expr="10 % 3") == {"result": 1}

    def test_power(self):
        from tools.calculator import run
        assert run(expr="2 ** 10") == {"result": 1024}

    def test_unary_negation(self):
        from tools.calculator import run
        assert run(expr="-5 + 3") == {"result": -2}

    def test_unary_positive(self):
        from tools.calculator import run
        assert run(expr="+5") == {"result": 5}

    def test_operator_precedence(self):
        from tools.calculator import run
        assert run(expr="2 + 3 * 4") == {"result": 14}

    def test_parentheses(self):
        from tools.calculator import run
        assert run(expr="(2 + 3) * 4") == {"result": 20}

    def test_nested_expressions(self):
        from tools.calculator import run
        assert run(expr="((1 + 2) * (3 + 4)) / 7") == {"result": 3.0}


class TestCalculatorConstants:
    def test_pi(self):
        from tools.calculator import run
        result = run(expr="pi")
        assert abs(result["result"] - 3.141592653589793) < 1e-10

    def test_e(self):
        from tools.calculator import run
        result = run(expr="e")
        assert abs(result["result"] - 2.718281828459045) < 1e-10

    def test_tau(self):
        from tools.calculator import run
        result = run(expr="tau")
        assert abs(result["result"] - 6.283185307179586) < 1e-10

    def test_inf(self):
        from tools.calculator import run
        assert run(expr="inf") == {"result": float("inf")}


class TestCalculatorFunctions:
    def test_sqrt(self):
        from tools.calculator import run
        assert run(expr="sqrt(16)") == {"result": 4.0}

    def test_sin(self):
        from tools.calculator import run
        result = run(expr="sin(0)")
        assert abs(result["result"] - 0.0) < 1e-10

    def test_cos(self):
        from tools.calculator import run
        result = run(expr="cos(0)")
        assert abs(result["result"] - 1.0) < 1e-10

    def test_log(self):
        from tools.calculator import run
        assert run(expr="log10(100)") == {"result": 2.0}

    def test_abs(self):
        from tools.calculator import run
        assert run(expr="abs(-7)") == {"result": 7}

    def test_floor(self):
        from tools.calculator import run
        assert run(expr="floor(3.7)") == {"result": 3}

    def test_ceil(self):
        from tools.calculator import run
        assert run(expr="ceil(3.2)") == {"result": 4}

    def test_round(self):
        from tools.calculator import run
        assert run(expr="round(3.5)") == {"result": 4}

    def test_pow(self):
        from tools.calculator import run
        assert run(expr="pow(2, 8)") == {"result": 256.0}

    def test_nested_function_call(self):
        from tools.calculator import run
        assert run(expr="sqrt(abs(-16))") == {"result": 4.0}


class TestCalculatorSecurity:
    def test_rejects_eval(self):
        from tools.calculator import run
        result = run(expr="__import__('os').system('echo pwned')")
        assert "error" in result
        assert "result" not in result

    def test_rejects_import(self):
        from tools.calculator import run
        result = run(expr="import os")
        assert "error" in result

    def test_rejects_lambda(self):
        from tools.calculator import run
        result = run(expr="(lambda x: x)(42)")
        assert "error" in result

    def test_rejects_function_def(self):
        from tools.calculator import run
        result = run(expr="def foo(): pass")
        assert "error" in result

    def test_rejects_string_literal(self):
        from tools.calculator import run
        result = run(expr="'hello'")
        assert "error" in result

    def test_rejects_list(self):
        from tools.calculator import run
        result = run(expr="[1, 2, 3]")
        assert "error" in result

    def test_rejects_dict(self):
        from tools.calculator import run
        result = run(expr="{'a': 1}")
        assert "error" in result

    def test_rejects_attribute_access(self):
        from tools.calculator import run
        result = run(expr="(1).bit_length()")
        assert "error" in result

    def test_rejects_subscript(self):
        from tools.calculator import run
        result = run(expr="pi[0]")
        assert "error" in result

    def test_rejects_comparison(self):
        from tools.calculator import run
        result = run(expr="1 < 2")
        assert "error" in result

    def test_rejects_boolean(self):
        from tools.calculator import run
        result = run(expr="True")
        assert "error" in result

    def test_rejects_none(self):
        from tools.calculator import run
        result = run(expr="None")
        assert "error" in result

    def test_rejects_unknown_function(self):
        from tools.calculator import run
        result = run(expr="open('/etc/passwd')")
        assert "error" in result

    def test_rejects_unknown_variable(self):
        from tools.calculator import run
        result = run(expr="x + 1")
        assert "error" in result

    def test_rejects_dunder_access(self):
        from tools.calculator import run
        result = run(expr="__builtins__")
        assert "error" in result

    def test_rejects_walrus(self):
        from tools.calculator import run
        result = run(expr="(x := 5)")
        assert "error" in result

    def test_rejects_starred(self):
        from tools.calculator import run
        result = run(expr="*[1,2,3]")
        assert "error" in result


class TestCalculatorEdgeCases:
    def test_empty_string(self):
        from tools.calculator import run
        assert run(expr="") == {"result": 0}

    def test_whitespace_only(self):
        from tools.calculator import run
        assert run(expr="   ") == {"result": 0}

    def test_non_string_input(self):
        from tools.calculator import run
        assert run(expr=42) == {"result": 0}
        assert run(expr=None) == {"result": 0}

    def test_no_expr_kwarg(self):
        from tools.calculator import run
        assert run() == {"result": 0}

    def test_syntax_error(self):
        from tools.calculator import run
        result = run(expr="2 +")
        assert "error" in result

    def test_division_by_zero(self):
        from tools.calculator import run
        result = run(expr="1 / 0")
        assert "error" in result

    def test_complex_number(self):
        from tools.calculator import run
        result = run(expr="(1+2j)")
        assert result["result"] == 1 + 2j

    def test_large_number(self):
        from tools.calculator import run
        assert run(expr="999999999 * 999999999") == {"result": 999999998000000001}

    def test_negative_result(self):
        from tools.calculator import run
        assert run(expr="-10 + 3") == {"result": -7}
