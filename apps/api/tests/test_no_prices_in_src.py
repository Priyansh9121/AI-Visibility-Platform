"""No price is written into production code — U1, 2026-10-04.

The founder's rule: production logs counts and never a dollar figure, and a rate lives in
`scripts/verify_e2e.py`, where it carries the date it was read and the page it came from.
Two things enforce it, at two strengths.

* `services/usage.py`, which exists to count what a scan used, holds no dollar figure ANYWHERE:
  not in code, not in a comment, not in a docstring, and no name in it is a price, a rate or a
  cost.
* Everything under `src` holds none in CODE: no string literal (an f-string's literal parts
  included) with a `$` before a digit, and no dict literal shaped like a rate table, a model's
  id mapped to numbers.

What the second does NOT cover, on purpose, is prose. Some comments and docstrings under `src`
quote a dated vendor price, a measured scan cost or the plan's own price as the reason a choice
was made (`billing.py`'s plan price and its history, a "cents" in `cocitation.py` and
`fix_generator.py`; the provider figures that `engines.py`, `extraction.py` and `scan_allowance.py`
carried were replaced by a pointer to north-star section 5.1, 2026-10-06): rationale written
beside a choice, read by nobody's code, and
the cost skill allows a price "with a vendor URL and the date it was read". A figure in prose
is not a figure in production code, and a guard on prose would fail every time the plan's price
or a vendor's rate is discussed where it belongs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import avp_api

SRC = Path(avp_api.__file__).resolve().parent
USAGE = SRC / "services" / "usage.py"
DOLLAR = re.compile(r"\$\s?\d")
# A model id, as a key of a table.
MODEL_ID = re.compile(r"^(claude|gpt|gemini|perplexity|sonar|o\d)[-/.\w]*$", re.I)
# The vocabulary of money, as whole words of a name (split on `_`) or, for the two `per_` forms,
# anywhere in it: `generate` and `feed` contain `rate` and `fee` and are not about money.
# `billed` is not on the list: `serpapi_billed_or_unknown` counts the searches that may have been
# billed, which is a count and not an amount.
PRICE_WORDS = frozenset({
    "usd", "dollar", "dollars", "price", "prices", "priced", "pricing", "cents", "mtok",
    "rate", "rates", "cost", "costs", "fee", "fees",
})
PRICE_PHRASES = ("per_million", "per_mtok")


def is_money_word(name: str) -> bool:
    lowered = name.lower()
    return bool(PRICE_WORDS & set(lowered.split("_"))) or any(p in lowered for p in PRICE_PHRASES)


def python_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def docstring_nodes(tree: ast.AST) -> set[int]:
    """Identity of every node that is a docstring: prose, not a value the code uses."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(id(first.value))
    return found


def string_literals_with_a_dollar_figure(source: str) -> list[tuple[int, str]]:
    """Each string literal in CODE (docstrings and comments are not) with `$` before a digit."""
    tree = ast.parse(source)
    skip = docstring_nodes(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and DOLLAR.search(node.value)
    ]


def _is_number(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, int | float) and not isinstance(node.value, bool)
    if isinstance(node, ast.UnaryOp):
        return _is_number(node.operand)
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Decimal"
    )


def _holds_numbers(node: ast.AST) -> bool:
    if _is_number(node):
        return True
    if isinstance(node, ast.Tuple | ast.List):
        return any(_holds_numbers(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        return any(_holds_numbers(v) for v in node.values)
    return False


def rate_table_shapes(source: str) -> list[int]:
    """Lines of dict literals that map a model's id to numbers: what a rate table looks like."""
    shapes: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and MODEL_ID.match(key.value)
                and _holds_numbers(value)
            ):
                shapes.append(node.lineno)
                break
    return shapes


def names_in(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.arg) or isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
    return names


class TestTheUsageModule:
    def test_it_exists_to_be_checked(self) -> None:
        assert USAGE.is_file()

    def test_it_holds_no_dollar_figure_anywhere_not_even_in_prose(self) -> None:
        text = USAGE.read_text()
        assert DOLLAR.findall(text) == [], DOLLAR.findall(text)

    def test_no_name_in_it_is_a_price_a_rate_or_a_cost(self) -> None:
        offenders = {name for name in names_in(USAGE.read_text()) if is_money_word(name)}
        # `ok`, `retry` and the like are fine; the vocabulary of money is what is refused.
        assert offenders == set(), offenders

    def test_it_holds_no_string_that_looks_like_a_rate_table(self) -> None:
        assert rate_table_shapes(USAGE.read_text()) == []


class TestAllOfSrc:
    def test_no_code_holds_a_dollar_figure(self) -> None:
        offenders = {
            str(path.relative_to(SRC)): found
            for path in python_files()
            if (found := string_literals_with_a_dollar_figure(path.read_text()))
        }
        assert offenders == {}, offenders

    def test_no_code_holds_a_rate_table(self) -> None:
        offenders = {
            str(path.relative_to(SRC)): lines
            for path in python_files()
            if (lines := rate_table_shapes(path.read_text()))
        }
        assert offenders == {}, offenders

    def test_the_walk_actually_reaches_the_tree(self) -> None:
        """A guard over an empty list passes. These are files this one must be looking at."""
        names = {str(p.relative_to(SRC)) for p in python_files()}
        assert {"services/engines.py", "services/serp.py", "services/usage.py",
                "services/scan_executor.py", "config.py"} <= names
        assert len(names) > 60


class TestTheGuardsCatchWhatTheyAreFor:
    """A guard that has never failed has not been shown to work."""

    def test_a_dollar_figure_in_a_string_is_found_and_one_in_a_comment_is_not(self) -> None:
        source = (
            '"""A module docstring may say $5 a million."""\n'
            "# and so may a comment: $25 a million\n"
            'PRICE = "$5 per million"\n'
            'LABEL = f"fee is $0.25 each {unit}"\n'
            "def f():\n"
            '    """So may this one: $1."""\n'
            '    return "no figure here"\n'
        )
        found = string_literals_with_a_dollar_figure(source)
        assert [line for line, _ in found] == [3, 4]

    def test_a_table_of_models_and_numbers_is_found_and_a_table_of_names_is_not(self) -> None:
        assert rate_table_shapes('RATES = {"claude-opus-5": (5, 25)}') == [1]
        assert rate_table_shapes('RATES = {"gpt-5.5": {"in": Decimal("5"), "out": 30}}') == [1]
        assert rate_table_shapes('RATES = {"perplexity/sonar": [0.25, 2.5]}') == [1]
        assert rate_table_shapes('WHO = {"claude-opus-5": "anthropic"}') == []
        assert rate_table_shapes('LIMITS = {"requests": (5, 25)}') == []

    def test_a_price_word_in_a_name_is_found(self) -> None:
        names = names_in("def total_cost(rate_usd):\n    return rate_usd * price_per_million")
        flagged = {n for n in names if is_money_word(n)}
        assert flagged == {"total_cost", "rate_usd", "price_per_million"}
        assert not [n for n in ("generate", "feed", "separate", "accurate", "input_tokens", "ok")
                    if is_money_word(n)]
