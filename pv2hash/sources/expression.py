"""Reusable safe arithmetic expressions over named measurements.

Tokens are persisted as JSON, never Python code. Registered variables use stable IDs.
Supported: + - * / parentheses and finite numeric constants. No arbitrary eval.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

MAX_TOKENS = 96
_OPERATORS = {'+': 1, '-': 1, '*': 2, '/': 2}


def compile_expression(tokens: Sequence, available_ids: set[str]) -> tuple[tuple, set[str]]:
    if not isinstance(tokens, list) or not 1 <= len(tokens) <= MAX_TOKENS:
        raise ValueError('Formel: ungültige Länge (1–96 Elemente).')
    output: list[tuple] = []
    operators: list[str] = []
    references: set[str] = set()
    expects_operand = True
    depth = 0
    for token in tokens:
        if isinstance(token, dict):
            if not expects_operand:
                raise ValueError('Formel: Operator zwischen zwei Werten fehlt.')
            if token.get('type') in {'variable', 'register'}:
                rid = token.get('id')
                if not isinstance(rid, str) or rid not in available_ids:
                    raise ValueError('Formel: unbekannte Registerkennung.')
                references.add(rid)
                output.append(('register', rid))
            elif token.get('type') == 'number':
                number = token.get('value')
                if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
                    raise ValueError('Formel: ungültige numerische Konstante.')
                output.append(('number', float(number)))
            else:
                raise ValueError('Formel: unbekannter Baustein.')
            expects_operand = False
        elif token == '(':
            if not expects_operand:
                raise ValueError('Formel: vor einer Klammer fehlt ein Operator.')
            operators.append('(')
            depth += 1
            if depth > 12:
                raise ValueError('Formel: zu viele verschachtelte Klammern.')
        elif token == ')':
            if expects_operand or depth == 0:
                raise ValueError('Formel: ungültige schließende Klammer.')
            while operators and operators[-1] != '(':
                output.append(('operator', operators.pop()))
            operators.pop()
            depth -= 1
            expects_operand = False
        elif isinstance(token, str) and token in _OPERATORS:
            if expects_operand:
                raise ValueError('Formel: Operator ohne vorherigen Wert.')
            while operators and operators[-1] in _OPERATORS and _OPERATORS[operators[-1]] >= _OPERATORS[token]:
                output.append(('operator', operators.pop()))
            operators.append(token)
            expects_operand = True
        else:
            raise ValueError('Formel: nicht unterstützter Ausdruck.')
    if expects_operand or depth:
        raise ValueError('Formel: unvollständig oder Klammer nicht geschlossen.')
    while operators:
        output.append(('operator', operators.pop()))
    return tuple(output), references


def evaluate_expression(compiled: Sequence[tuple], values: Mapping[str, float]) -> float:
    stack: list[float] = []
    for kind, value in compiled:
        if kind == 'register':
            if value not in values:
                raise ValueError(f'Formel: Messwert {value} fehlt.')
            number = float(values[value])
            if not math.isfinite(number):
                raise ValueError('Formel: ungültiger Messwert.')
            stack.append(number)
        elif kind == 'number':
            stack.append(value)
        elif kind == 'operator':
            if len(stack) < 2:
                raise ValueError('Formel: Operanden fehlen.')
            right, left = stack.pop(), stack.pop()
            try:
                result = {'+': lambda: left + right, '-': lambda: left - right,
                          '*': lambda: left * right, '/': lambda: left / right}[value]()
            except ZeroDivisionError as exc:
                raise ValueError('Formel: Division durch 0.') from exc
            if not math.isfinite(result):
                raise ValueError('Formel: Ergebnis nicht endlich.')
            stack.append(result)
    if len(stack) != 1:
        raise ValueError('Formel: Auswertung fehlgeschlagen.')
    return stack[0]
