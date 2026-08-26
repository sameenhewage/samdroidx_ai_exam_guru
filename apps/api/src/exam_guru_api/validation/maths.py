"""Bounded exact arithmetic checks for the first Grade-school Mathematics slice."""

from __future__ import annotations

import ast
import io
import re
import tokenize
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from exam_guru_api.validation.domain import (
    FindingEvidence,
    FindingStatus,
    ValidationFinding,
)
from exam_guru_api.validation.subject import (
    SubjectFindingCode,
    SubjectValidationContext,
)

MAX_EXPRESSION_CHARACTERS = 256
MAX_EXPRESSION_TOKENS = 64
MAX_AST_NODES = 64
MAX_AST_DEPTH = 12
MAX_OPERATORS = 16
MAX_NUMERIC_DIGITS = 24
MAX_NUMERIC_MAGNITUDE = 1_000_000_000_000
MAX_FRACTION_COMPONENT = 1_000_000_000_000_000

_QUESTION_WRAPPER = re.compile(
    r"^\s*(?:(?:what|how much)\s+is|calculate|compute|find(?:\s+the\s+value\s+of)?)\s+"
    r"(?P<expression>.+?)\s*\??\s*$",
    re.IGNORECASE,
)
_PERCENT_OF = re.compile(
    r"^\s*(?P<percent>[+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*%\s+of\s+"
    r"(?P<amount>.+?)\s*$",
    re.IGNORECASE,
)
_PERCENT_LITERAL = re.compile(r"(?<![\w.])([+-]?(?:\d+(?:\.\d+)?|\.\d+))\s*%")
# The marking capture uses the same bounded numeric grammar accepted by the parser.
_MARKING_ANSWER = re.compile(
    r"(?:\banswer\s+is\b|\bresult\s+is\b|\bequals?\b|=)\s*"
    r"(?P<value>[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:/\d+)?)",
    re.IGNORECASE,
)
_UNIT_WORD = re.compile(
    r"(?:^|\s)(?:mm|cm|m|km|mg|g|kg|ml|l|litre|liter|second|minute|hour)s?(?:\s|$)",
    re.IGNORECASE,
)
_TRANSLATION = str.maketrans(
    {"\u00d7": "*", "\u00f7": "/", "\u2212": "-", "\u2013": "-", "\u2014": "-"}
)


class _UnsupportedExpressionError(ValueError):
    pass


class _UnitExpressionError(_UnsupportedExpressionError):
    pass


@dataclass(slots=True)
class _ParseBudget:
    nodes: int = 0
    operators: int = 0


@dataclass(frozen=True, slots=True)
class _ParsedOption:
    option_id: str
    text: str
    value: Fraction


def _bounded_fraction(value: Fraction) -> Fraction:
    if (
        abs(value) > MAX_NUMERIC_MAGNITUDE
        or abs(value.numerator) > MAX_FRACTION_COMPONENT
        or value.denominator > MAX_FRACTION_COMPONENT
    ):
        raise _UnsupportedExpressionError("numeric magnitude exceeds the supported bound")
    return value


def _decimal_fraction(token: str) -> Fraction:
    unsigned = token.removeprefix("+").removeprefix("-")
    if "e" in unsigned.casefold() or len(unsigned.replace(".", "")) > MAX_NUMERIC_DIGITS:
        raise _UnsupportedExpressionError("numeric literal exceeds the supported grammar")
    try:
        return _bounded_fraction(Fraction(Decimal(token)))
    except (InvalidOperation, ValueError, ZeroDivisionError) as error:
        raise _UnsupportedExpressionError("numeric literal is malformed") from error


def _normalise_expression(value: str, *, question: bool) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_EXPRESSION_CHARACTERS:
        raise _UnsupportedExpressionError("expression is blank or oversized")
    normalised = value.translate(_TRANSLATION).strip()
    if question:
        match = _QUESTION_WRAPPER.fullmatch(normalised)
        if match is None:
            if _UNIT_WORD.search(normalised):
                raise _UnitExpressionError("unit expression is not supported")
            raise _UnsupportedExpressionError(
                "question does not use a supported exact-expression form"
            )
        normalised = match.group("expression").strip()
    if _UNIT_WORD.search(f" {normalised} "):
        raise _UnitExpressionError("unit expression is not supported")

    percent_of = _PERCENT_OF.fullmatch(normalised)
    if percent_of is not None:
        normalised = f"({percent_of.group('percent')} / 100) * ({percent_of.group('amount')})"
    else:
        normalised = _PERCENT_LITERAL.sub(r"(\1 / 100)", normalised)
    if len(normalised) > MAX_EXPRESSION_CHARACTERS:
        raise _UnsupportedExpressionError("normalised expression is oversized")
    return normalised


def _validate_tokens(expression: str) -> None:
    try:
        tokens = tuple(tokenize.generate_tokens(io.StringIO(expression).readline))
    except (IndentationError, tokenize.TokenError) as error:
        raise _UnsupportedExpressionError("expression tokens are malformed") from error
    if len(tokens) > MAX_EXPRESSION_TOKENS:
        raise _UnsupportedExpressionError("expression has too many tokens")
    allowed = {
        tokenize.NUMBER,
        tokenize.OP,
        tokenize.NEWLINE,
        tokenize.NL,
        tokenize.ENDMARKER,
    }
    if any(token.type not in allowed for token in tokens):
        raise _UnsupportedExpressionError("expression contains unsupported tokens")
    allowed_operators = {"+", "-", "*", "/", "(", ")"}
    if any(token.type == tokenize.OP and token.string not in allowed_operators for token in tokens):
        raise _UnsupportedExpressionError("expression contains an unsupported operator")


def _evaluate_node(
    node: ast.AST,
    expression: str,
    *,
    depth: int,
    budget: _ParseBudget,
) -> Fraction:
    budget.nodes += 1
    if budget.nodes > MAX_AST_NODES or depth > MAX_AST_DEPTH:
        raise _UnsupportedExpressionError("expression AST exceeds its bound")
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body, expression, depth=depth + 1, budget=budget)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise _UnsupportedExpressionError("expression literal is unsupported")
        source = ast.get_source_segment(expression, node)
        if source is None:
            raise _UnsupportedExpressionError("expression literal source is unavailable")
        return _decimal_fraction(source)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        budget.operators += 1
        if budget.operators > MAX_OPERATORS:
            raise _UnsupportedExpressionError("expression has too many operators")
        value = _evaluate_node(node.operand, expression, depth=depth + 1, budget=budget)
        return value if isinstance(node.op, ast.UAdd) else _bounded_fraction(-value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Sub | ast.Mult | ast.Div):
        budget.operators += 1
        if budget.operators > MAX_OPERATORS:
            raise _UnsupportedExpressionError("expression has too many operators")
        left = _evaluate_node(node.left, expression, depth=depth + 1, budget=budget)
        right = _evaluate_node(node.right, expression, depth=depth + 1, budget=budget)
        if isinstance(node.op, ast.Add):
            result = left + right
        elif isinstance(node.op, ast.Sub):
            result = left - right
        elif isinstance(node.op, ast.Mult):
            result = left * right
        else:
            if right == 0:
                raise _UnsupportedExpressionError("division by zero is unsupported")
            result = left / right
        return _bounded_fraction(result)
    raise _UnsupportedExpressionError("expression AST contains an unsupported node")


def parse_exact_expression(value: str, *, question: bool = False) -> Fraction:
    """Parse a bounded arithmetic expression without executing generated code."""

    expression = _normalise_expression(value, question=question)
    _validate_tokens(expression)
    try:
        tree = ast.parse(expression, mode="eval")
    except (MemoryError, RecursionError, SyntaxError, ValueError) as error:
        raise _UnsupportedExpressionError("expression syntax is unsupported") from error
    return _evaluate_node(tree, expression, depth=0, budget=_ParseBudget())


def _candidate_mapping(context: SubjectValidationContext, field_name: str) -> dict[str, object]:
    value = context.candidate.get(field_name)
    return dict(value) if isinstance(value, dict | Mapping) else {}


def _finding(
    validator: MathematicsSubjectValidator,
    *,
    code: SubjectFindingCode,
    status: FindingStatus,
    message: str,
    location: str,
    expected: str,
    observed: str,
) -> ValidationFinding:
    return ValidationFinding(
        validator_id=validator.validator_id,
        validator_version=validator.validator_version,
        code=code,
        status=status,
        message=message,
        evidence=(
            FindingEvidence(
                location=location,
                expected=expected[:1_024],
                observed=observed[:1_024],
            ),
        ),
    )


@dataclass(frozen=True, slots=True)
class MathematicsSubjectValidator:
    subject_codes: frozenset[str] = frozenset({"MATHEMATICS", "MATHS", "MATH"})
    validator_id: str = "grade-school-mathematics"
    validator_version: str = "1.0.0"

    def validate(self, context: SubjectValidationContext) -> tuple[ValidationFinding, ...]:
        stem = context.candidate.get("stem")
        if not isinstance(stem, str):
            return self._unsupported("$.candidate.stem", "stem is not text")
        try:
            expected_value = parse_exact_expression(stem, question=True)
        except _UnitExpressionError:
            return (
                _finding(
                    self,
                    code=SubjectFindingCode.MATH_UNIT_MISMATCH,
                    status=FindingStatus.WARN,
                    message=(
                        "Unit conversion or dimensional consistency requires a supported unit rule."
                    ),
                    location="$.candidate.stem",
                    expected="a supported exact arithmetic expression without unresolved units",
                    observed="unit-bearing expression requires review",
                ),
            )
        except _UnsupportedExpressionError:
            return self._unsupported(
                "$.candidate.stem",
                "expression is outside the bounded grammar",
            )

        raw_options = context.candidate.get("options")
        if not isinstance(raw_options, tuple | list):
            return self._unsupported("$.candidate.options", "options are malformed")
        parsed_options: list[_ParsedOption] = []
        try:
            for raw_option in raw_options:
                if not isinstance(raw_option, Mapping):
                    raise _UnsupportedExpressionError("option is not an object")
                option_id = raw_option.get("option_id")
                text = raw_option.get("text")
                if not isinstance(option_id, str) or not isinstance(text, str):
                    raise _UnsupportedExpressionError("option identity or text is malformed")
                parsed_options.append(
                    _ParsedOption(
                        option_id=option_id,
                        text=text,
                        value=parse_exact_expression(text),
                    )
                )
        except _UnsupportedExpressionError:
            return self._unsupported(
                f"$.candidate.options[{len(parsed_options)}]",
                "not every option is a supported exact expression",
            )

        answer = _candidate_mapping(context, "answer")
        correct_option_id = answer.get("correct_option_id")
        correct_options = tuple(
            option for option in parsed_options if option.value == expected_value
        )
        selected = next(
            (option for option in parsed_options if option.option_id == correct_option_id),
            None,
        )
        answer_matches = selected is not None and selected.value == expected_value
        answer_finding = _finding(
            self,
            code=SubjectFindingCode.MATH_ANSWER_MISMATCH,
            status=FindingStatus.PASS if answer_matches else FindingStatus.FAIL,
            message=(
                "The proposed answer matches the independently computed exact result."
                if answer_matches
                else "The proposed answer does not match the independently computed exact result."
            ),
            location="$.candidate.answer.correct_option_id",
            expected="an option mathematically equivalent to the independently computed result",
            observed=(
                f"selected={correct_option_id};equivalent={answer_matches};"
                f"correct_option_count={len(correct_options)}"
            ),
        )
        exactly_one = len(correct_options) == 1
        multiple_finding = _finding(
            self,
            code=SubjectFindingCode.MATH_MULTIPLE_CORRECT_OPTIONS,
            status=FindingStatus.PASS if exactly_one else FindingStatus.FAIL,
            message=(
                "Exactly one option is mathematically correct."
                if exactly_one
                else "The MCQ does not contain exactly one mathematically correct option."
            ),
            location="$.candidate.options",
            expected="exactly one option equivalent to the computed result",
            observed="equivalent_option_ids="
            + (",".join(option.option_id for option in correct_options) or "none"),
        )

        equivalent_groups: dict[Fraction, list[str]] = {}
        for option in parsed_options:
            equivalent_groups.setdefault(option.value, []).append(option.option_id)
        duplicates = tuple(
            tuple(option_ids) for option_ids in equivalent_groups.values() if len(option_ids) > 1
        )
        duplicate_finding = _finding(
            self,
            code=SubjectFindingCode.MATH_DUPLICATE_EQUIVALENT_OPTIONS,
            status=FindingStatus.PASS if not duplicates else FindingStatus.FAIL,
            message=(
                "No mathematically equivalent duplicate options were found."
                if not duplicates
                else "Separate MCQ options are mathematically equivalent."
            ),
            location="$.candidate.options",
            expected="pairwise mathematically distinct option values",
            observed=(
                "equivalent_groups=none"
                if not duplicates
                else "equivalent_groups=" + ";".join(",".join(group) for group in duplicates)
            ),
        )
        marking_finding = self._validate_marking(context, expected_value)
        return (answer_finding, multiple_finding, duplicate_finding, marking_finding)

    def _validate_marking(
        self,
        context: SubjectValidationContext,
        expected_value: Fraction,
    ) -> ValidationFinding:
        marking = _candidate_mapping(context, "marking")
        raw_criteria = marking.get("criteria")
        statements: list[tuple[int, Fraction]] = []
        if isinstance(raw_criteria, tuple | list):
            for index, raw_criterion in enumerate(raw_criteria):
                if not isinstance(raw_criterion, Mapping):
                    continue
                description = raw_criterion.get("description")
                if not isinstance(description, str):
                    continue
                for match in _MARKING_ANSWER.finditer(description):
                    token = match.group("value")
                    try:
                        statements.append((index, parse_exact_expression(token)))
                    except _UnsupportedExpressionError:
                        continue
        inconsistent = tuple(index for index, value in statements if value != expected_value)
        return _finding(
            self,
            code=SubjectFindingCode.MARKING_ANSWER_INCONSISTENT,
            status=FindingStatus.FAIL if inconsistent else FindingStatus.PASS,
            message=(
                "An explicit marking answer conflicts with the independently computed result."
                if inconsistent
                else "No explicit marking answer conflicts with the computed result."
            ),
            location="$.candidate.marking.criteria",
            expected="any explicit numeric marking answer to equal the computed result",
            observed=(
                "inconsistent_criteria=" + ",".join(str(index) for index in inconsistent)
                if inconsistent
                else f"checked_explicit_answers={len(statements)}"
            ),
        )

    def _unsupported(self, location: str, observed: str) -> tuple[ValidationFinding, ...]:
        return (
            _finding(
                self,
                code=SubjectFindingCode.MATH_UNSUPPORTED_EXPRESSION,
                status=FindingStatus.WARN,
                message=(
                    "The Maths expression is unsupported or underspecified; "
                    "human review is required."
                ),
                location=location,
                expected=(
                    "bounded grade-school arithmetic using numbers, fractions, decimals, "
                    "percentages, parentheses, and + - * /"
                ),
                observed=observed,
            ),
        )
