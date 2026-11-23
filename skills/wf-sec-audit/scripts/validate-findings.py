#!/usr/bin/env python3
"""Validate findings.json against report-schema.json.

Usage: python3 validate-findings.py <path-to-findings.json>

Prints `PASS: N findings valid` and exits 0, or prints each problem as `ERROR: ...` on stderr and
exits 1. This is a dependency-free interpreter for the JSON Schema keywords that report-schema.json
uses, plus the finding-specific checks that are clearer in code. The schema is read from the
directory of this script's real path.

Port of validate-findings.cjs from cloudflare/security-audit-skill (MIT License, Copyright (c)
2025-2026 Cloudflare, Inc.) with the same checks, limits, messages and function order. Where Python
and JavaScript differ, the port keeps the JavaScript behaviour: JSON.parse numbers, UTF-16 string
lengths, slices and ordering, Object.keys order, Unicode property escapes and ECMAScript `pattern`s.
"""

from __future__ import annotations

import bisect
import errno
import functools
import json
import math
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path

SUPPORTED_TYPES = frozenset(('object', 'array', 'string', 'integer', 'number', 'boolean', 'null'))
SUPPORTED_KEYWORDS = frozenset((
    '$comment',
    'additionalProperties',
    'const',
    'description',
    'enum',
    'items',
    'minimum',
    'minItems',
    'minLength',
    'oneOf',
    'pattern',
    'properties',
    'required',
    'type',
    'uniqueItems',
    'visibleContent',
))
SEVERITY_RANK = {'informational': 0, 'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
LIMITS = {
    'input_bytes': 5 * 1024 * 1024,
    'nesting_depth': 64,
    'array_items': 1000,
    'canonical_key_bytes': 1024 * 1024,
    'unique_set_bytes': 5 * 1024 * 1024,
    'validation_errors': 100,
}
MAX_DIAGNOSTIC_STRING_LENGTH = 256

# Upstream's \p{Default_Ignorable_Code_Point} and \p{White_Space} as Node 24 (ICU 78) applies them:
# Unicode 17.0 DerivedCoreProperties.txt and PropList.txt. The general categories Cc, Cf, Cs, Zl and
# Zp come from unicodedata, so they follow the running Python's Unicode database.
DEFAULT_IGNORABLE_CODE_POINTS = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
WHITE_SPACE_CODE_POINTS = frozenset((
    0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20, 0x85, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x2028, 0x2029,
    0x202F, 0x205F, 0x3000,
))
# ECMAScript WhiteSpace and LineTerminator: the set of /\s/ and String.prototype.trim.
JS_WHITESPACE = (
    '\t\n\v\f\r \xa0            '
    '    　﻿'
)
# Upstream matches these names with the `iu` flags, which compare simple case foldings
# (CaseFolding.txt C+S). The only non-ASCII code point folding onto one of their letters is
# U+212A KELVIN SIGN (to k), so the segment is folded first and matched case-sensitively.
WINDOWS_RESERVED_COMPONENT = re.compile(
    '(?:con|prn|aux|nul|clock\\$|conin\\$|conout\\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\\.|\\Z)')
SIMPLE_CASE_FOLD = {**{code: code + 0x20 for code in range(0x41, 0x5B)}, 0x212A: 0x6B}

_DEFAULT_IGNORABLE_STARTS = tuple(start for start, _ in DEFAULT_IGNORABLE_CODE_POINTS)
_ASCII_CONTROL = re.compile('[\x00-\x1f\x7f]')
_SURROGATE = re.compile('[\ud800-\udfff]')
_IDENTIFIER = re.compile('[A-Za-z_][A-Za-z0-9_]*')
_ARRAY_INDEX = re.compile('0|[1-9][0-9]{0,9}')
_DRIVE_PREFIX = re.compile('[A-Za-z]:')
_STRING_REST = re.compile(r'[^"\\]*(?:\\[\s\S]?[^"\\]*)*(")?')
_STRUCTURAL_CHARACTER = re.compile(r'["\[\]{},]')


class JsonStructureError(Exception):
    """The JSON text exceeds a structural limit."""


class SafeInputError(Exception):
    """The input file cannot be read safely."""


class _CanonicalKeyError(Exception):
    pass


class _Undefined:
    """JavaScript's undefined: a missing property, distinct from JSON null (None)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return 'undefined'


_UNDEFINED = _Undefined()


# --- JavaScript semantics -------------------------------------------------------------------------

def _get(value, key):
    """`value[key]` for an own property of a JSON object, else undefined."""
    if isinstance(value, dict) and key in value:
        return value[key]
    return _UNDEFINED


def _truthy(value) -> bool:
    if value is None or value is _UNDEFINED or isinstance(value, bool):
        return value is True
    if isinstance(value, (int, float)):
        return value == value and value != 0
    if isinstance(value, str):
        return value != ''
    return True


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_double(value) -> float:
    if isinstance(value, float):
        return value
    try:
        return float(value)
    except OverflowError:
        return math.inf if value > 0 else -math.inf


def _is_integer(value) -> bool:
    """Number.isInteger."""
    return _is_number(value) and _to_double(value).is_integer()


def _is_finite_number(value) -> bool:
    return _is_number(value) and math.isfinite(_to_double(value))


def _number_to_string(value) -> str:
    """Number.prototype.toString for a double: shortest round-trip digits in ECMAScript layout."""
    number = _to_double(value)
    if number != number:
        return 'NaN'
    if math.isinf(number):
        return 'Infinity' if number > 0 else '-Infinity'
    if number == 0:
        return '0'
    mantissa, _, exponent = repr(abs(number)).partition('e')
    whole, _, fraction = mantissa.partition('.')
    digits = (whole + fraction).lstrip('0')
    point = len(whole) + int(exponent or 0) - (len(whole + fraction) - len(digits))
    digits = digits.rstrip('0')
    count = len(digits)
    if count <= point <= 21:
        text = digits + '0' * (point - count)
    elif 0 < point <= 21:
        text = f'{digits[:point]}.{digits[point:]}'
    elif -6 < point <= 0:
        text = '0.' + '0' * -point + digits
    else:
        shift = point - 1
        text = (digits[0] + (f'.{digits[1:]}' if count > 1 else '')
                + ('e+' if shift >= 0 else 'e-') + str(abs(shift)))
    return ('-' if number < 0 else '') + text


def _utf16_length(value: str) -> int:
    return len(value) if value.isascii() else len(value.encode('utf-16-le', 'surrogatepass')) // 2


def _utf16_slice(value: str, end: int) -> str:
    """value.slice(0, end) in UTF-16 code units; a split pair leaves its high surrogate."""
    return value.encode('utf-16-le', 'surrogatepass')[:2 * end].decode('utf-16-le', 'surrogatepass')


def _utf16_code_units(value: str) -> str:
    """The string with each supplementary code point split into its surrogate pair."""
    if value.isascii() or max(value) <= '￿':
        return value
    return ''.join(
        character if character <= '￿' else
        chr(0xD800 + ((ord(character) - 0x10000) >> 10)) + chr(0xDC00 + ((ord(character) - 0x10000) & 0x3FF))
        for character in value)


def _utf16_order(value: str) -> bytes:
    """Sort key for JavaScript string comparison, which compares UTF-16 code units."""
    return value.encode('utf-16-be', 'surrogatepass')


def _utf8_length(value: str) -> int:
    """Buffer.byteLength: a lone surrogate counts as its three-byte replacement character."""
    return len(value) if value.isascii() else len(value.encode('utf-8', 'surrogatepass'))


def _json_string(value: str) -> str:
    """JSON.stringify for a string: lone surrogates are escaped, other non-ASCII stays literal."""
    return _SURROGATE.sub(lambda match: '\\u%04x' % ord(match.group()), json.dumps(value, ensure_ascii=False))


def _object_keys(value: dict) -> list:
    """Object.keys order: array-index keys ascending, then the other keys in insertion order."""
    keys = list(value)
    indexes = [key for key in keys
               if isinstance(key, str) and _ARRAY_INDEX.fullmatch(key) and int(key) < 0xFFFFFFFF]
    if not indexes:
        return keys
    index_set = set(indexes)
    return sorted(indexes, key=int) + [key for key in keys if key not in index_set]


def _parse_int(text: str):
    # JSON.parse yields a double; keep exactly representable integers as int.
    return int(text) if len(text.lstrip('-')) <= 15 else float(text)


def _parse_constant(name: str):
    raise ValueError(f'{name} is not valid JSON')


def _json_parse(text: str):
    """JSON.parse: NaN and Infinity are rejected, numbers are doubles, the last duplicate key wins."""
    return json.loads(text, parse_int=_parse_int, parse_constant=_parse_constant)


def _is_default_ignorable(code_point: int) -> bool:
    index = bisect.bisect_right(_DEFAULT_IGNORABLE_STARTS, code_point) - 1
    return index >= 0 and code_point <= DEFAULT_IGNORABLE_CODE_POINTS[index][1]


def _is_visible_character(character: str) -> bool:
    """VISIBLE_CONTENT: [^\\p{White_Space}\\p{Cc}\\p{Cf}\\p{Default_Ignorable_Code_Point}]."""
    code_point = ord(character)
    if code_point < 0x80:
        return 0x20 < code_point < 0x7F
    return (code_point not in WHITE_SPACE_CODE_POINTS and not _is_default_ignorable(code_point)
            and unicodedata.category(character) not in ('Cc', 'Cf'))


def _is_path_forbidden_character(character: str) -> bool:
    """PATH_FORBIDDEN_CHARACTER: [\\p{Cc}\\p{Cf}\\p{Zl}\\p{Zp}\\p{Default_Ignorable_Code_Point}]."""
    code_point = ord(character)
    if code_point < 0x80:
        return code_point < 0x20 or code_point == 0x7F
    return _is_default_ignorable(code_point) or unicodedata.category(character) in ('Cc', 'Cf', 'Zl', 'Zp')


def _is_unsafe_diagnostic_character(character: str) -> bool:
    """UNSAFE_DIAGNOSTIC_CHARACTER: PATH_FORBIDDEN_CHARACTER plus \\p{Cs}."""
    code_point = ord(character)
    if code_point < 0x80:
        return code_point < 0x20 or code_point == 0x7F
    return (_is_default_ignorable(code_point)
            or unicodedata.category(character) in ('Cc', 'Cf', 'Cs', 'Zl', 'Zp'))


def _has_visible_content(value: str) -> bool:
    return any(map(_is_visible_character, value))


def _contains_path_forbidden_character(value: str) -> bool:
    if value.isascii():
        return _ASCII_CONTROL.search(value) is not None
    return any(map(_is_path_forbidden_character, value))


def _is_windows_reserved_component(segment: str) -> bool:
    return WINDOWS_RESERVED_COMPONENT.match(segment.translate(SIMPLE_CASE_FOLD)) is not None


def _js_trim(value: str) -> str:
    return value.strip(JS_WHITESPACE)


# ECMAScript RegExp sources (no flags, so not unicode mode) translated to Python patterns.
_WORD_CLASS = 'A-Za-z0-9_'
_SPACE_CLASS = '\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff'
_SHORTHAND_CLASSES = {'d': '0-9', 'w': _WORD_CLASS, 's': _SPACE_CLASS}
_CONTROL_ESCAPES = {'t': 0x09, 'n': 0x0A, 'v': 0x0B, 'f': 0x0C, 'r': 0x0D}
_WORD_BOUNDARY = '(?:(?<=[A-Za-z0-9_])(?![A-Za-z0-9_])|(?<![A-Za-z0-9_])(?=[A-Za-z0-9_]))'
_NOT_WORD_BOUNDARY = '(?:(?<=[A-Za-z0-9_])(?=[A-Za-z0-9_])|(?<![A-Za-z0-9_])(?![A-Za-z0-9_]))'
_QUANTIFIER = re.compile('\\{[0-9]+(?:,[0-9]*)?\\}')
_HEX2 = re.compile('[0-9A-Fa-f]{2}')
_HEX4 = re.compile('[0-9A-Fa-f]{4}')
_NAMED_GROUP = re.compile('\\(\\?<(?![=!])')


def _literal(code_point: int) -> str:
    return '\\u%04x' % code_point


def _translate_escape(source: str, index: int, named_groups: bool, in_class: bool):
    """Translate the escape at source[index] ('\\'); return (kind, value, next index).

    kind is 'char' (a code point), 'class' (Python class content), 'negated' (a Python class) or
    'pattern' (Python pattern text outside a class).
    """
    if index + 1 >= len(source):
        raise re.error('\\ at end of pattern')
    character = source[index + 1]
    after = index + 2
    if character in _SHORTHAND_CLASSES:
        return 'class', _SHORTHAND_CLASSES[character], after
    if character in 'DWS':
        return 'negated', f'[^{_SHORTHAND_CLASSES[character.lower()]}]', after
    if character == 'b':
        return ('char', 0x08, after) if in_class else ('pattern', _WORD_BOUNDARY, after)
    if character == 'B' and not in_class:
        return 'pattern', _NOT_WORD_BOUNDARY, after
    if character in _CONTROL_ESCAPES:
        return 'char', _CONTROL_ESCAPES[character], after
    if character == '0' and not '0' <= source[after:after + 1] <= '9':
        return 'char', 0, after
    if '0' <= character <= '9':
        raise re.error('backreferences and legacy octal escapes are not supported')
    if character == 'c':
        letter = source[after:after + 1]
        if letter.isascii() and (letter.isalpha() or (in_class and (letter.isdigit() or letter == '_'))):
            return 'char', ord(letter) % 32, after + 1
        return 'char', 0x5C, index + 1
    if character == 'x' and _HEX2.match(source, after):
        return 'char', int(source[after:after + 2], 16), after + 2
    if character == 'u' and _HEX4.match(source, after):
        return 'char', int(source[after:after + 4], 16), after + 4
    if character == 'k' and named_groups:
        raise re.error('named backreferences are not supported')
    return 'char', ord(character), after


def _translate_class(source: str, index: int, named_groups: bool):
    """Translate the class at source[index] ('['); return (Python pattern text, next index)."""
    index += 1
    negated = source[index:index + 1] == '^'
    if negated:
        index += 1
    atoms = []
    while True:
        if index >= len(source):
            raise re.error('unterminated character class')
        character = source[index]
        if character == ']':
            index += 1
            break
        if character == '\\':
            kind, value, index = _translate_escape(source, index, named_groups, True)
            atoms.append((kind, value, False))
        else:
            atoms.append(('char', ord(character), character == '-'))
            index += 1
    content, extras, position = [], [], 0
    while position < len(atoms):
        kind, value, _ = atoms[position]
        if position + 2 < len(atoms) and atoms[position + 1][2]:
            end_kind, end_value, _ = atoms[position + 2]
            if kind == 'char' and end_kind == 'char':
                if value > end_value:
                    raise re.error('range out of order in character class')
                content.append(f'{_literal(value)}-{_literal(end_value)}')
                position += 3
                continue
        if kind == 'char':
            content.append(_literal(value))
        elif kind == 'class':
            content.append(value)
        else:
            extras.append(value)
        position += 1
    alternatives = ([f'[{"".join(content)}]'] if content else []) + extras
    if negated:
        return ('(?:(?!' + '|'.join(alternatives) + ')(?s:.))' if alternatives else '(?s:.)'), index
    if not alternatives:
        return '(?!)', index
    return (alternatives[0] if len(alternatives) == 1 else '(?:' + '|'.join(alternatives) + ')'), index


@functools.lru_cache(maxsize=64)
def _ecmascript_pattern(source: str):
    """Compile an ECMAScript RegExp source as a Python pattern over UTF-16 code units.

    Backreferences, legacy octal escapes and group syntax ECMAScript lacks raise re.error, so they
    count as invalid rather than being matched with a different meaning.
    """
    source = _utf16_code_units(source)
    named_groups = _NAMED_GROUP.search(source) is not None
    parts, index, can_repeat = [], 0, False
    while index < len(source):
        character = source[index]
        quantifier = _QUANTIFIER.match(source, index) if character == '{' else None
        if character in '*+?' or quantifier:
            if not can_repeat:
                raise re.error('nothing to repeat')
            text = quantifier.group() if quantifier else character
            index += len(text)
            if source[index:index + 1] == '?':
                text += '?'
                index += 1
            parts.append(text)
            can_repeat = False
        elif character == '\\':
            kind, value, index = _translate_escape(source, index, named_groups, False)
            if kind == 'char':
                parts.append(_literal(value))
            elif kind == 'class':
                parts.append(f'[{value}]')
            else:
                parts.append(value)
            can_repeat = kind != 'pattern'
        elif character == '[':
            text, index = _translate_class(source, index, named_groups)
            parts.append(text)
            can_repeat = True
        elif character == '(':
            if source.startswith(('(?:', '(?=', '(?!'), index):
                parts.append(source[index:index + 3])
                index += 3
            elif source.startswith(('(?<=', '(?<!'), index):
                parts.append(source[index:index + 4])
                index += 4
            elif source.startswith('(?<', index):
                end = source.find('>', index)
                if end < 0 or not source[index + 3:end].isidentifier():
                    raise re.error('invalid capture group name')
                parts.append(f'(?P<{source[index + 3:end]}>')
                index = end + 1
            elif source.startswith('(?', index):
                raise re.error('invalid group')
            else:
                parts.append('(')
                index += 1
            can_repeat = False
        else:
            if character == ')':
                parts.append(')')
            elif character == '|':
                parts.append('|')
            elif character == '^':
                parts.append('^')
            elif character == '$':
                parts.append('\\Z')
            elif character == '.':
                parts.append('[^\\n\\r\\u2028\\u2029]')
            else:
                parts.append(_literal(ord(character)))
            can_repeat = character not in '|^$'
            index += 1
    return re.compile(''.join(parts))


def _pattern_test(source: str, value: str) -> bool:
    """new RegExp(source).test(value)."""
    return _ecmascript_pattern(source).search(_utf16_code_units(value)) is not None


# --- Validator ------------------------------------------------------------------------------------

def escape_unsafe_diagnostic_characters(value) -> str:
    def escape(character: str) -> str:
        code_point = ord(character)
        return '\\u%04x' % code_point if code_point <= 0xFFFF else '\\u{%x}' % code_point

    text = str(value)
    if text.isascii():
        return _ASCII_CONTROL.sub(lambda match: escape(match.group()), text)
    return ''.join(escape(character) if _is_unsafe_diagnostic_character(character) else character
                   for character in text)


def safe_quote(value) -> str:
    if isinstance(value, str):
        clipped = (f'{_utf16_slice(value, MAX_DIAGNOSTIC_STRING_LENGTH)}...'
                   if _utf16_length(value) > MAX_DIAGNOSTIC_STRING_LENGTH else value)
        serialized = _json_string(clipped)
    elif value is None or isinstance(value, bool):
        serialized = {None: 'null', True: 'true', False: 'false'}[value]
    elif _is_finite_number(value):
        serialized = _number_to_string(value)
    else:
        serialized = f'"<{"array" if isinstance(value, list) else _js_typeof(value)}>"'
    return escape_unsafe_diagnostic_characters(serialized)


def property_path(base: str, key: str) -> str:
    return f'{base}.{key}' if _IDENTIFIER.fullmatch(key) else f'{base}[{safe_quote(key)}]'


class _ErrorList(list):
    """createErrorList: push() keeps at most LIMITS['validation_errors'] escaped messages."""

    def push(self, *messages) -> int:
        remaining = LIMITS['validation_errors'] - len(self)
        if remaining > 0:
            self.extend(escape_unsafe_diagnostic_characters(message) for message in messages[:remaining])
        return len(self)


def create_error_list() -> _ErrorList:
    return _ErrorList()


def type_of(value) -> str:
    if isinstance(value, list):
        return 'array'
    if value is None:
        return 'null'
    return _js_typeof(value)


def _js_typeof(value) -> str:
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, str):
        return 'string'
    if _is_number(value):
        return 'number'
    if value is _UNDEFINED:
        return 'undefined'
    return 'object' if isinstance(value, (dict, list)) or value is None else type(value).__name__


def deep_equal(left, right) -> bool:
    kind = type_of(left)
    if kind != type_of(right):
        return False
    if kind == 'array':
        return left is right or (len(left) == len(right)
                                 and all(deep_equal(value, right[index]) for index, value in enumerate(left)))
    if kind == 'object':
        if left is right:
            return True
        left_keys = _object_keys(left)
        return len(left_keys) == len(right) and all(
            key in right and deep_equal(left[key], right[key]) for key in left_keys)
    if kind == 'number':
        return _to_double(left) == _to_double(right)
    return left == right if kind in ('string', 'boolean', 'null') else left is right


def code_point_length(value: str) -> int:
    # json.loads joins escaped surrogate pairs, so each lone surrogate is one code point, as upstream counts it.
    return len(value)


def has_valid_unicode_scalar_values(value: str) -> bool:
    return _SURROGATE.search(value) is None


def has_visible_prose(value) -> bool:
    return isinstance(value, str) and has_valid_unicode_scalar_values(value) and _has_visible_content(value)


def canonical_key(value):
    chunks = []
    size = 0

    def append(chunk: str) -> None:
        nonlocal size
        size += _utf8_length(chunk)
        if size > LIMITS['canonical_key_bytes']:
            raise _CanonicalKeyError(f'canonical key exceeds {LIMITS["canonical_key_bytes"]} byte limit')
        chunks.append(chunk)

    def encode(item) -> None:
        kind = type_of(item)
        if kind == 'null':
            append('null')
        elif kind == 'string':
            append(f'string:{_json_string(item)}')
        elif kind == 'number':
            append(f'number:{_number_to_string(item)}')
        elif kind == 'boolean':
            append(f'boolean:{"true" if item else "false"}')
        elif kind == 'array':
            append('array:[')
            for index, entry in enumerate(item):
                if index > 0:
                    append(',')
                encode(entry)
            append(']')
        elif kind == 'object':
            append('object:{')
            for index, key in enumerate(sorted(_object_keys(item), key=_utf16_order)):
                if index > 0:
                    append(',')
                append(_json_string(key))
                append(':')
                encode(item[key])
            append('}')
        else:
            append(f'{kind}:{item}')

    encode(value)
    return ''.join(chunks), size


def collect_data_limit_errors(value, location: str = '$data') -> list:
    stack = [(value, location, 1 if isinstance(value, (dict, list)) else 0)]
    seen = set()

    while stack:
        current, current_location, depth = stack.pop()
        if not isinstance(current, (dict, list)):
            continue
        if depth > LIMITS['nesting_depth']:
            return [escape_unsafe_diagnostic_characters(
                f'{current_location}: exceeds {LIMITS["nesting_depth"]} level nesting depth limit')]
        if id(current) in seen:
            return [escape_unsafe_diagnostic_characters(
                f'{current_location}: input must not contain repeated or cyclic object references')]
        seen.add(id(current))

        if isinstance(current, list):
            if len(current) > LIMITS['array_items']:
                return [escape_unsafe_diagnostic_characters(
                    f'{current_location}: exceeds {LIMITS["array_items"]} item array limit')]
            children = [(child, f'{current_location}[{index}]') for index, child in enumerate(current)
                        if isinstance(child, (dict, list))]
        else:
            children = [(current[key], property_path(current_location, key)) for key in _object_keys(current)
                        if isinstance(current[key], (dict, list))]
        stack.extend((child, child_location, depth + 1) for child, child_location in reversed(children))

    return []


def collect_schema_errors(schema, location: str = 'schema') -> list:
    errors = create_error_list()

    def check(node, p: str) -> None:
        if not isinstance(node, dict):
            errors.push(f'{p}: schema must be an object')
            return

        for key in _object_keys(node):
            if key not in SUPPORTED_KEYWORDS:
                errors.push(f'{p}: unsupported schema keyword {safe_quote(key)}')

        if '$comment' in node and not isinstance(node['$comment'], str):
            errors.push(f'{p}.$comment: expected string')
        if 'description' in node and not isinstance(node['description'], str):
            errors.push(f'{p}.description: expected string')
        if 'type' in node and not (isinstance(node['type'], str) and node['type'] in SUPPORTED_TYPES):
            errors.push(f'{p}.type: unsupported type {safe_quote(node["type"])}')
        if 'properties' in node:
            if not isinstance(node['properties'], dict):
                errors.push(f'{p}.properties: expected object')
            else:
                for key in _object_keys(node['properties']):
                    check(node['properties'][key], property_path(f'{p}.properties', key))
        if 'required' in node:
            required = node['required']
            if not isinstance(required, list) or any(not isinstance(key, str) for key in required):
                errors.push(f'{p}.required: expected an array of strings')
            elif len(set(required)) != len(required):
                errors.push(f'{p}.required: entries must be unique')
        if 'additionalProperties' in node and not isinstance(node['additionalProperties'], bool):
            errors.push(f'{p}.additionalProperties: only boolean values are supported')
        if 'enum' in node:
            if not isinstance(node['enum'], list) or len(node['enum']) == 0:
                errors.push(f'{p}.enum: expected a non-empty array')
            else:
                seen = set()
                for value in node['enum']:
                    try:
                        key = canonical_key(value)[0]
                    except Exception as error:
                        errors.push(f'{p}.enum: {error}')
                        break
                    if key in seen:
                        errors.push(f'{p}.enum: entries must be unique')
                        break
                    seen.add(key)
        if 'items' in node:
            check(node['items'], f'{p}.items')
        for keyword in ('minItems', 'minLength'):
            if keyword in node and (not _is_integer(node[keyword]) or node[keyword] < 0):
                errors.push(f'{p}.{keyword}: expected a non-negative integer')
        if 'minimum' in node and not _is_finite_number(node['minimum']):
            errors.push(f'{p}.minimum: expected a finite number')
        if 'pattern' in node:
            if not isinstance(node['pattern'], str):
                errors.push(f'{p}.pattern: expected string')
            else:
                try:
                    _ecmascript_pattern(node['pattern'])
                except Exception:
                    errors.push(f'{p}.pattern: invalid regular expression')
        if 'uniqueItems' in node and not isinstance(node['uniqueItems'], bool):
            errors.push(f'{p}.uniqueItems: expected boolean')
        if 'visibleContent' in node:
            if not isinstance(node['visibleContent'], bool):
                errors.push(f'{p}.visibleContent: expected boolean')
            elif node['visibleContent'] is True and _get(node, 'type') != 'string':
                errors.push(f'{p}.visibleContent: requires type "string"')
        if 'oneOf' in node:
            if not isinstance(node['oneOf'], list) or len(node['oneOf']) == 0:
                errors.push(f'{p}.oneOf: expected a non-empty array')
            else:
                for index, branch in enumerate(node['oneOf']):
                    check(branch, f'{p}.oneOf[{index}]')

    check(schema, location)
    return errors


def find_discriminator(schema):
    if 'properties' not in schema or not isinstance(schema['properties'], dict):
        return None
    for key in _object_keys(schema['properties']):
        sub_schema = schema['properties'][key]
        if isinstance(sub_schema, dict) and 'const' in sub_schema:
            return key, sub_schema['const']
    return None


def validate(value, schema: dict, p: str, errors: _ErrorList) -> None:
    if len(errors) >= LIMITS['validation_errors']:
        return
    if 'oneOf' in schema:
        results = [collect_unchecked(value, branch, p) for branch in schema['oneOf']]
        passing_indexes = [index for index, branch_errors in enumerate(results) if len(branch_errors) == 0]

        if len(passing_indexes) != 1:
            errors.push(f'{p}: must match exactly one schema in oneOf; matched {len(passing_indexes)}')
            if len(passing_indexes) == 0 and isinstance(value, dict):
                matching_discriminators = []
                for index, branch in enumerate(schema['oneOf']):
                    discriminator = find_discriminator(branch)
                    if discriminator and discriminator[0] in value and deep_equal(value[discriminator[0]], discriminator[1]):
                        matching_discriminators.append(index)
                if len(matching_discriminators) == 1:
                    errors.push(*results[matching_discriminators[0]])

    if 'const' in schema and not deep_equal(value, schema['const']):
        errors.push(f'{p}: must equal {safe_quote(schema["const"])}, got {safe_quote(value)}')
    if 'enum' in schema and not any(deep_equal(value, allowed) for allowed in schema['enum']):
        allowed = ', '.join(safe_quote(entry) for entry in schema['enum'])
        errors.push(f'{p}: invalid value {safe_quote(value)} (expected one of {allowed})')

    if 'type' in schema and type_of(value) != schema['type'] and not (
            schema['type'] == 'integer' and type_of(value) == 'number' and _is_integer(value)):
        errors.push(f'{p}: expected {schema["type"]}, got {type_of(value)}')
        return

    if isinstance(value, dict):
        for required in schema['required'] if 'required' in schema else []:
            if required not in value:
                errors.push(f'{p}: missing required field {safe_quote(required)}')
        for key in _object_keys(value):
            if 'properties' in schema and key in schema['properties']:
                validate(value[key], schema['properties'][key], property_path(p, key), errors)
            elif schema.get('additionalProperties', None) is False:
                errors.push(f'{p}: unexpected field {safe_quote(key)}')

    if isinstance(value, list):
        if 'minItems' in schema and len(value) < schema['minItems']:
            errors.push(f'{p}: must have at least {_number_to_string(schema["minItems"])} item(s), got {len(value)}')
        if schema.get('uniqueItems', None) is True:
            seen = set()
            set_bytes = 0
            for index, item in enumerate(value):
                try:
                    key, size = canonical_key(item)
                except Exception as error:
                    errors.push(f'{p}[{index}]: {error}')
                    break
                if key in seen:
                    errors.push(f'{p}: items must be unique; duplicate at index {index}')
                    continue
                set_bytes += size
                if set_bytes > LIMITS['unique_set_bytes']:
                    errors.push(f'{p}: canonical uniqueness set exceeds {LIMITS["unique_set_bytes"]} byte limit')
                    break
                seen.add(key)
        if 'items' in schema:
            for index, item in enumerate(value):
                validate(item, schema['items'], f'{p}[{index}]', errors)

    if isinstance(value, str):
        if 'minLength' in schema and code_point_length(value) < schema['minLength']:
            errors.push(f'{p}: must have at least {_number_to_string(schema["minLength"])} character(s)')
        if schema.get('visibleContent', None) is True:
            if not has_valid_unicode_scalar_values(value):
                errors.push(f'{p}: must contain only valid Unicode scalar values')
            elif not _has_visible_content(value):
                errors.push(f'{p}: must contain a visible character')
        if 'pattern' in schema and not _pattern_test(schema['pattern'], value):
            errors.push(f'{p}: must match pattern {_json_string(schema["pattern"])}')

    if _is_number(value) and 'minimum' in schema and value < schema['minimum']:
        errors.push(f'{p}: must be at least {_number_to_string(schema["minimum"])}, got {_number_to_string(value)}')


def collect_unchecked(value, schema: dict, p: str) -> _ErrorList:
    errors = create_error_list()
    validate(value, schema, p, errors)
    return errors


def collect(value, schema: dict, path: str = '$data') -> list:
    """Schema errors for one value against a (sub)schema, after the data limits."""
    limit_errors = collect_data_limit_errors(value, path)
    if limit_errors:
        return limit_errors
    return collect_unchecked(value, schema, path)


def _is_posix_absolute(value: str) -> bool:
    return value.startswith('/')


def _is_win32_absolute(value: str) -> bool:
    if not value:
        return False
    if value[0] in '/\\':
        return True
    return (len(value) > 2 and value[0].isascii() and value[0].isalpha() and value[1] == ':'
            and value[2] in '/\\')


def is_safe_relative_source_path(value) -> bool:
    if (not isinstance(value, str) or len(value) == 0 or not has_valid_unicode_scalar_values(value)
            or _js_trim(value) != value or _contains_path_forbidden_character(value) or '\\' in value
            or ':' in value):
        return False
    if (_is_posix_absolute(value) or _is_win32_absolute(value) or _DRIVE_PREFIX.match(value)
            or value.startswith('~')):
        return False
    return all(
        segment != '' and
        segment != '.' and
        segment != '..' and
        not segment.endswith((' ', '.')) and
        not _is_windows_reserved_component(segment)
        for segment in value.split('/'))


def collect_finding_semantic_errors(findings) -> list:
    errors = create_error_list()
    if not isinstance(findings, list):
        return errors

    fingerprints = {}
    previous_fingerprint = None
    for index, finding in enumerate(findings):
        if len(errors) >= LIMITS['validation_errors']:
            break
        if not isinstance(finding, dict):
            continue
        base = f'$[{index}]'

        if isinstance(finding.get('fingerprint'), str):
            fingerprint = finding['fingerprint']
            if fingerprint in fingerprints:
                errors.push(f'{base}.fingerprint: duplicate of $[{fingerprints[fingerprint]}].fingerprint')
            else:
                fingerprints[fingerprint] = index
            if previous_fingerprint is not None and _utf16_order(previous_fingerprint) > _utf16_order(fingerprint):
                errors.push(f'{base}.fingerprint: findings must be sorted lexicographically')
            previous_fingerprint = fingerprint

        for field in ('trace', 'evidence'):
            if not isinstance(finding.get(field), list):
                continue
            for entry_index, entry in enumerate(finding[field]):
                if not isinstance(entry, dict):
                    continue
                if 'line' in entry and (not _is_integer(entry['line']) or entry['line'] < 1):
                    errors.push(f'{base}.{field}[{entry_index}].line: must be a positive integer')
                if 'file' in entry and not is_safe_relative_source_path(entry['file']):
                    errors.push(f'{base}.{field}[{entry_index}].file: must be a safe repository-relative source path')
        remediation = _get(finding, 'remediation')
        if _truthy(remediation) and isinstance(_get(remediation, 'code_changes'), list):
            for change_index, change in enumerate(remediation['code_changes']):
                if (isinstance(change, dict) and 'file_name' in change
                        and not is_safe_relative_source_path(change['file_name'])):
                    errors.push(f'{base}.remediation.code_changes[{change_index}].file_name: '
                                'must be a safe repository-relative source path')

        trace = _get(finding, 'trace')
        if isinstance(trace, list) and len(trace) == 1:
            kind = _get(trace[0], 'kind') if _truthy(trace[0]) else trace[0]
            if kind != 'entrypoint' and kind != 'sink':
                errors.push(f'{base}.trace[0].kind: a one-line trace must be "entrypoint" or "sink"')
        elif isinstance(trace, list) and len(trace) > 1:
            last = len(trace) - 1
            if _truthy(trace[0]) and _get(trace[0], 'kind') != 'entrypoint':
                errors.push(f'{base}.trace[0].kind: must be "entrypoint", got {safe_quote(_get(trace[0], "kind"))}')
            if _truthy(trace[last]) and _get(trace[last], 'kind') != 'sink':
                errors.push(f'{base}.trace[{last}].kind: must be "sink", got {safe_quote(_get(trace[last], "kind"))}')
            for trace_index in range(1, last):
                if _truthy(trace[trace_index]) and _get(trace[trace_index], 'kind') != 'propagation':
                    errors.push(f'{base}.trace[{trace_index}].kind: must be "propagation", '
                                f'got {safe_quote(_get(trace[trace_index], "kind"))}')

        verdict = _get(finding, 'verdict')
        if verdict == 'confirmed':
            for forbidden in ('claimed_root_cause', 'blockers', 'validation_plan', 'reason'):
                if forbidden in finding:
                    errors.push(f'{base}: confirmed finding must not contain {safe_quote(forbidden)}')
            execution = _get(finding, 'execution')
            if not isinstance(execution, (dict, list)) or not has_visible_prose(_get(execution, 'observed_result')):
                errors.push(f'{base}: confirmed finding requires a visible execution observed_result')
            remediation = _get(finding, 'remediation')
            if not isinstance(remediation, (dict, list)) or not has_visible_prose(_get(remediation, 'strategy')):
                errors.push(f'{base}: confirmed finding requires visible remediation')
            severity = _get(finding, 'severity')
            overall = _get(severity, 'overall_severity') if _truthy(severity) else severity
            impact = _get(severity, 'impact') if _truthy(severity) else severity
            impact = _get(impact, 'score') if _truthy(impact) else impact
            if (isinstance(overall, str) and overall in SEVERITY_RANK and isinstance(impact, str)
                    and impact in SEVERITY_RANK and SEVERITY_RANK[overall] > SEVERITY_RANK[impact]):
                errors.push(f'{base}.severity.overall_severity: cannot exceed demonstrated impact {safe_quote(impact)}')
        elif verdict == 'needs_validation':
            if 'severity' in finding:
                errors.push(f'{base}: needs_validation finding must not contain "severity"')
            for forbidden in ('execution', 'remediation', 'reason', 'root_cause'):
                if forbidden in finding:
                    errors.push(f'{base}: needs_validation finding must not contain {safe_quote(forbidden)}')
            plan = _get(finding, 'validation_plan')
            has_local_plan = _truthy(plan) and has_visible_prose(_get(plan, 'local'))
            has_deployment_plan = _truthy(plan) and has_visible_prose(_get(plan, 'deployment'))
            if not has_local_plan and not has_deployment_plan:
                errors.push(f'{base}.validation_plan: requires at least one visible local or deployment plan')
        elif verdict == 'rejected':
            for forbidden in ('severity', 'execution', 'remediation', 'blockers', 'validation_plan', 'root_cause'):
                if forbidden in finding:
                    errors.push(f'{base}: rejected finding must not contain {safe_quote(forbidden)}')

    return errors


def validate_document(findings, schema) -> list:
    schema_errors = collect_schema_errors(schema)
    if schema_errors:
        return schema_errors
    limit_errors = collect_data_limit_errors(findings, '$')
    if limit_errors:
        return limit_errors
    errors = collect_unchecked(findings, schema, '$')
    if len(errors) < LIMITS['validation_errors']:
        errors.push(*collect_finding_semantic_errors(findings))
    return errors


def load_schema(schema_path) -> dict:
    with open(schema_path, 'rb') as handle:
        schema = _json_parse(handle.read().decode('utf-8', 'replace'))
    errors = collect_schema_errors(schema)
    if errors:
        raise ValueError('unsupported or invalid report schema:\n' + '\n'.join(errors))
    return schema


def read_file_within_limit(file) -> str:
    no_follow = getattr(os, 'O_NOFOLLOW', 0)
    non_block = getattr(os, 'O_NONBLOCK', 0)
    if not no_follow or not non_block:
        raise SafeInputError('OS no-follow and nonblocking input protection is unavailable')

    try:
        descriptor = os.open(file, os.O_RDONLY | no_follow | non_block)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.EMLINK):
            raise SafeInputError('input must not be a symlink') from None
        raise
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise SafeInputError('input must be a regular file')
        if status.st_size > LIMITS['input_bytes']:
            raise SafeInputError(f'input exceeds {LIMITS["input_bytes"]} byte limit')

        chunks = []
        bytes_read = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > LIMITS['input_bytes']:
                raise SafeInputError(f'input exceeds {LIMITS["input_bytes"]} byte limit')
            chunks.append(chunk)
        try:
            # A fatal decoder that keeps a byte order mark, which JSON parsing then rejects.
            return b''.join(chunks).decode('utf-8')
        except UnicodeDecodeError:
            raise SafeInputError('input is not valid UTF-8') from None
    finally:
        os.close(descriptor)


def enforce_json_text_limits(contents: str) -> None:
    containers = []  # [is_array, expects_item, items]

    def mark_array_item() -> None:
        if not containers:
            return
        container = containers[-1]
        if not container[0] or not container[1]:
            return
        container[1] = False
        container[2] += 1
        if container[2] > LIMITS['array_items']:
            raise JsonStructureError(f'input exceeds {LIMITS["array_items"]} item array limit')

    # Scan from one structural character to the next. Any other character outside a string marks an
    # array item, and only the first of a run can, so a run between two structural characters is
    # handled once; string bodies are skipped with one match.
    index = 0
    while index < len(contents):
        match = _STRUCTURAL_CHARACTER.search(contents, index)
        end = match.start() if match else len(contents)
        if end > index and _js_trim(contents[index:end]):
            mark_array_item()
        if match is None:
            break
        character = contents[end]
        index = end + 1
        if character == '"':
            mark_array_item()
            index = _STRING_REST.match(contents, index).end()
        elif character in '[{':
            mark_array_item()
            if len(containers) >= LIMITS['nesting_depth']:
                raise JsonStructureError(f'input exceeds {LIMITS["nesting_depth"]} level nesting depth limit')
            containers.append([character == '[', character == '[', 0])
        elif character in ']}':
            if containers:
                containers.pop()
        elif containers and containers[-1][0]:
            containers[-1][1] = True


def parse_json_text(text: str):
    """Apply the text limits, then parse like JSON.parse (JsonStructureError or ValueError)."""
    enforce_json_text_limits(text)
    return _json_parse(text)


def _write(stream, *parts: str) -> None:
    text = _SURROGATE.sub('�', ' '.join(parts) + '\n')
    buffer = getattr(stream, 'buffer', None)
    if buffer is None:
        stream.write(text)
        stream.flush()
    else:
        buffer.write(text.encode('utf-8'))
        buffer.flush()


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    file = args[0] if args else None
    if not file:
        _write(sys.stderr, 'Usage: python3 validate-findings.py <path-to-findings.json>')
        return 1

    try:
        schema = load_schema(Path(__file__).resolve().parent / 'report-schema.json')
    except Exception as error:
        _write(sys.stderr, 'Failed to load report-schema.json:', str(error))
        return 1

    try:
        contents = read_file_within_limit(file)
    except Exception as error:
        reason = str(error) if isinstance(error, SafeInputError) else 'input could not be opened or read safely'
        _write(sys.stderr, f'Failed to read findings JSON: {reason}')
        return 1

    try:
        enforce_json_text_limits(contents)
    except Exception as error:
        reason = str(error) if isinstance(error, JsonStructureError) else 'invalid JSON structure'
        _write(sys.stderr, f'Failed to parse findings JSON: {reason}')
        return 1

    try:
        findings = _json_parse(contents)
    except Exception:
        _write(sys.stderr, 'Failed to parse findings JSON: invalid JSON syntax')
        return 1

    try:
        errors = validate_document(findings, schema)
    except Exception:
        _write(sys.stderr, 'Failed to validate findings JSON: unexpected validation error')
        return 1
    for message in errors:
        _write(sys.stderr, 'ERROR:', escape_unsafe_diagnostic_characters(message))
    if errors:
        cap = f'; output capped at {LIMITS["validation_errors"]}' if len(errors) == LIMITS['validation_errors'] else ''
        _write(sys.stderr, f'FAIL: {len(errors)} validation error(s){cap}')
        return 1
    _write(sys.stdout, f'PASS: {len(findings)} findings valid')
    return 0


if __name__ == '__main__':
    sys.exit(main())
