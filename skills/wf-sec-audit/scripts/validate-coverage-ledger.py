#!/usr/bin/env python3
"""Validate coverage-ledger.json and its canonical coverage IDs.

Usage:
  python3 validate-coverage-ledger.py <path-to-coverage-ledger.json>
  python3 validate-coverage-ledger.py --id <surface> <boundary> <subsystem> <attack_class> [<lifecycle>]

The first form prints `PASS: N coverage units valid` and exits 0, or prints each problem as
`ERROR: ...` on stderr and exits 1. The second prints the canonical coverage_id of the given
canonical references, derived exactly as the validator derives it, and exits 0; it exits 2 when an
argument is missing or a reference is not canonical.

Port of validate-coverage-ledger.cjs from cloudflare/security-audit-skill (MIT License, Copyright (c)
2025-2026 Cloudflare, Inc.) with the same checks, limits, messages and function order. Where Python
and JavaScript differ, the port keeps the JavaScript behaviour: JSON.parse numbers, UTF-16 string
lengths, slices and ordering, Object.keys order, Set identity and Unicode property escapes.
"""

from __future__ import annotations

import bisect
import errno
import json
import math
import os
import re
import stat
import sys
import unicodedata

# Conservative bounds apply before JSON parsing and again to the parsed document.
LIMITS = {
    'input_bytes': 5 * 1024 * 1024,
    'units': 10000,
    'collection_items': 1000,
    'object_fields': 1000,
    'nesting_depth': 64,
    'preflight_values': 500000,
    'validation_errors': 100,
}
MAX_INPUT_BYTES = LIMITS['input_bytes']
MAX_UNITS = LIMITS['units']
MAX_LIST_ITEMS = LIMITS['collection_items']
MAX_TEXT_LENGTH = 4096
REQUIRED_FIELDS = (
    'coverage_id',
    'canonical_refs',
    'surface',
    'boundary',
    'subsystem',
    'attack_class',
    'starting_paths',
    'ordinary_attack_class_block',
    'selected_companion_blocks',
    'excluded_blocks',
    'prior_status',
    'attempts',
    'wave',
    'status',
    'agent_id',
    'reviewed_paths',
    'local_checks',
    'result_fingerprints',
    'unresolved',
)
REF_FIELDS = ('surface', 'boundary', 'subsystem', 'attack_class')
STATUSES = frozenset((
    'planned',
    'not_applicable',
    'out_of_scope',
    'in_progress',
    'covered',
    'candidate',
    'blocked',
    'deferred',
))
ATTEMPT_STATUSES = frozenset(('covered', 'candidate', 'blocked'))
ATTEMPT_FIELDS = (
    'wave',
    'status',
    'agent_id',
    'reviewed_paths',
    'local_checks',
    'result_fingerprints',
    'unresolved',
    'reassignment_reason',
)
PRIOR_STATUSES = frozenset((
    'new',
    'prior_confirmed_same_source',
    'prior_confirmed_changed_source',
    'prior_needs_validation',
    'prior_deferred',
    'prior_blocked',
    'prior_out_of_scope',
    'prior_covered_same_source',
    'prior_covered_changed_source',
    'prior_rejected_claim_changed',
    'none',
))
# Whole-string patterns (JavaScript's ^...$ without the m flag), applied with fullmatch.
FINGERPRINT_PATTERN = re.compile('[A-Za-z0-9][A-Za-z0-9._:/@+-]*')
AGENT_ID_PATTERN = re.compile('[a-z0-9][a-z0-9_-]{0,63}')
WINDOWS_RESERVED_AGENT_ID = re.compile('con|prn|aux|nul|com[1-9]|lpt[1-9]')
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
_ARRAY_INDEX = re.compile('0|[1-9][0-9]{0,9}')
_DRIVE_PREFIX = re.compile('[A-Za-z]:')
_STRING_REST = re.compile(r'[^"\\]*(?:\\[\s\S]?[^"\\]*)*(")?')
_STRUCTURAL_CHARACTER = re.compile(r'["\[\]{}:,]')
_BARE_TOKEN = re.compile('[^ \t\r\n]+')
_UNRESERVED_BYTES = frozenset(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')


class JsonStructureError(Exception):
    """The JSON text exceeds a structural limit or has a malformed structure."""


class SafeInputError(Exception):
    """The input file cannot be read safely."""


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


def _utf16_order(value: str) -> bytes:
    """Sort key for JavaScript string comparison, which compares UTF-16 code units."""
    return value.encode('utf-16-be', 'surrogatepass')


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


def _same_value_zero_key(value):
    """A dict key with the identity a JavaScript Set gives the value (SameValueZero)."""
    if isinstance(value, (dict, list)):
        return 'reference', id(value)
    if isinstance(value, bool):
        return 'boolean', value
    if _is_number(value):
        number = _to_double(value)
        return 'number', 'NaN' if number != number else number
    if isinstance(value, str):
        return 'string', value
    return ('null',) if value is None else ('other', id(value))


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
    elif isinstance(value, list):
        serialized = '"<array>"'
    elif _is_number(value):
        serialized = '"<number>"'
    else:
        serialized = '"<undefined>"' if value is _UNDEFINED else '"<object>"'
    return escape_unsafe_diagnostic_characters(serialized)


class _ErrorList(list):
    """createErrorList: push() keeps at most LIMITS['validation_errors'] escaped messages."""

    def push(self, *messages) -> int:
        remaining = LIMITS['validation_errors'] - len(self)
        if remaining > 0:
            self.extend(escape_unsafe_diagnostic_characters(message) for message in messages[:remaining])
        return len(self)


def create_error_list() -> _ErrorList:
    return _ErrorList()


def is_object(value) -> bool:
    return isinstance(value, dict)


class _Container:
    __slots__ = ('type', 'state', 'items', 'fields', 'top_level')

    def __init__(self, kind: str, state: str, top_level: bool = False) -> None:
        self.type = kind
        self.state = state
        self.items = 0
        self.fields = 0
        self.top_level = top_level


def preflight_json_text(contents: str) -> None:
    containers = []
    root_state = 'value'
    in_string = False
    total_values = 0

    def fail(message: str):
        raise JsonStructureError(f'input {message}')

    def current_container():
        return containers[-1] if containers else None

    def count_value() -> None:
        nonlocal total_values
        total_values += 1
        if total_values > LIMITS['preflight_values']:
            fail(f'exceeds {LIMITS["preflight_values"]} total value limit')

    def begin_value() -> None:
        nonlocal root_state
        container = current_container()
        if container is None:
            if root_state != 'value':
                fail('has malformed JSON structure')
            root_state = 'end'
        elif container.type == 'array':
            if container.state != 'firstValueOrEnd' and container.state != 'value':
                fail('has malformed JSON array structure')
            container.items += 1
            limit = MAX_UNITS if container.top_level else LIMITS['collection_items']
            if container.items > limit:
                fail(f'exceeds {limit} top-level unit limit' if container.top_level
                     else f'exceeds {limit} item array limit')
            container.state = 'commaOrEnd'
        else:
            if container.state != 'value':
                fail('has malformed JSON object structure')
            container.state = 'commaOrEnd'
        count_value()

    def begin_string() -> None:
        container = current_container()
        if (container is not None and container.type == 'object'
                and (container.state == 'firstKeyOrEnd' or container.state == 'key')):
            container.fields += 1
            if container.fields > LIMITS['object_fields']:
                fail(f'exceeds {LIMITS["object_fields"]} field object limit')
            container.state = 'colon'
        else:
            begin_value()

    def begin_container(kind: str) -> None:
        begin_value()
        if len(containers) >= LIMITS['nesting_depth']:
            fail(f'exceeds nesting depth limit {LIMITS["nesting_depth"]}')
        containers.append(_Container(kind, 'firstValueOrEnd', len(containers) == 0) if kind == 'array'
                          else _Container(kind, 'firstKeyOrEnd'))

    def close_container(kind: str) -> None:
        container = current_container()
        if container is None or container.type != kind:
            fail('has mismatched JSON containers')
        can_close = (container.state == 'firstValueOrEnd' or container.state == 'commaOrEnd' if kind == 'array'
                     else container.state == 'firstKeyOrEnd' or container.state == 'commaOrEnd')
        if not can_close:
            fail(f'has malformed JSON {kind} structure')
        containers.pop()

    # Scan from one structural character to the next. Between two of them, each run of characters
    # other than the four JSON whitespace characters is one bare token; string bodies are skipped
    # with one match.
    index = 0
    while index < len(contents):
        match = _STRUCTURAL_CHARACTER.search(contents, index)
        end = match.start() if match else len(contents)
        if end > index:
            for _ in _BARE_TOKEN.finditer(contents, index, end):
                begin_value()
        if match is None:
            break
        character = contents[end]
        index = end + 1
        if character == '"':
            begin_string()
            string = _STRING_REST.match(contents, index)
            index = string.end()
            in_string = string.group(1) is None
        elif character == '[':
            begin_container('array')
        elif character == '{':
            begin_container('object')
        elif character == ']':
            close_container('array')
        elif character == '}':
            close_container('object')
        elif character == ':':
            container = current_container()
            if container is None or container.type != 'object' or container.state != 'colon':
                fail('has malformed JSON object structure')
            container.state = 'value'
        else:
            container = current_container()
            if container is None or container.state != 'commaOrEnd':
                fail('has malformed JSON collection structure')
            container.state = 'value' if container.type == 'array' else 'key'

    if in_string:
        fail('has an unterminated JSON string')
    if containers:
        fail('has truncated JSON structure')
    if root_state != 'end':
        fail('has no JSON value')


def preflight_document(root) -> list:
    errors = create_error_list()
    stack = [(root, 0, '$')]
    visited = 0

    while stack:
        value, depth, location = stack.pop()
        visited += 1
        if visited > LIMITS['preflight_values']:
            errors.push(f'$: exceeds {LIMITS["preflight_values"]} total values')
            return errors
        if not isinstance(value, (dict, list)):
            continue
        if depth >= LIMITS['nesting_depth']:
            errors.push(f'{location}: exceeds nesting depth limit {LIMITS["nesting_depth"]}')
            return errors

        if isinstance(value, list):
            limit = MAX_UNITS if location == '$' else MAX_LIST_ITEMS
            if len(value) > limit:
                errors.push(f'{location}: exceeds {limit} entries')
                return errors
            stack.extend((value[index], depth + 1, f'{location}[{index}]')
                         for index in range(len(value) - 1, -1, -1))
            continue

        keys = _object_keys(value)
        if len(keys) > LIMITS['object_fields']:
            errors.push(f'{location}: exceeds {LIMITS["object_fields"]} object fields')
            return errors
        stack.extend((value[keys[index]], depth + 1, f'{location}{{{index}}}')
                     for index in range(len(keys) - 1, -1, -1))

    return errors


def has_valid_unicode_scalar_values(value: str) -> bool:
    return _SURROGATE.search(value) is None


def has_visible_prose(value) -> bool:
    return isinstance(value, str) and has_valid_unicode_scalar_values(value) and _has_visible_content(value)


def is_visible_text(value, max_length: int = MAX_TEXT_LENGTH) -> bool:
    return (isinstance(value, str) and
            len(value) > 0 and
            _utf16_length(value) <= max_length and
            _js_trim(value) == value and
            has_visible_prose(value))


def is_canonical_ref(value) -> bool:
    return (is_visible_text(value, 1024) and
            not _contains_path_forbidden_character(value) and
            unicodedata.normalize('NFC', value) == value)


def encode_canonical_ref(value) -> str:
    """RFC 3986 percent-encoding of the reference's UTF-8 bytes, leaving only A-Z a-z 0-9 - . _ ~."""
    if not is_canonical_ref(value):
        raise ValueError('invalid canonical reference')
    return ''.join(chr(byte) if byte in _UNRESERVED_BYTES else '%%%02X' % byte for byte in value.encode('utf-8'))


def canonical_coverage_id(refs: dict) -> str:
    """The coverage_id for canonical_refs: encoded references joined with '::' (lifecycle last)."""
    if not is_object(refs):
        raise TypeError('canonical_refs must be an object')
    fields = REF_FIELDS + ('lifecycle',) if 'lifecycle' in refs else REF_FIELDS
    if len(refs) != len(fields) or any(field not in refs for field in fields):
        raise ValueError('canonical_refs has missing or unexpected fields')
    return '::'.join(encode_canonical_ref(refs[field]) for field in fields)


def _is_posix_absolute(value: str) -> bool:
    return value.startswith('/')


def _is_win32_absolute(value: str) -> bool:
    if not value:
        return False
    if value[0] in '/\\':
        return True
    return (len(value) > 2 and value[0].isascii() and value[0].isalpha() and value[1] == ':'
            and value[2] in '/\\')


def is_safe_relative_path(value) -> bool:
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


def is_safe_agent_id(value) -> bool:
    return (isinstance(value, str) and
            AGENT_ID_PATTERN.fullmatch(value) is not None and
            WINDOWS_RESERVED_AGENT_ID.fullmatch(value) is None)


def is_owned_artifact_path(value, agent_id) -> bool:
    if not is_safe_agent_id(agent_id) or not is_safe_relative_path(value):
        return False
    prefix = f'agents/{agent_id}/artifacts/'
    return value.startswith(prefix) and len(value) > len(prefix)


def validate_string_array(value, location: str, errors: _ErrorList, allow_empty: bool = True,
                          fingerprint: bool = False, path_value: bool = False) -> None:
    if not isinstance(value, list):
        errors.push(f'{location}: expected array')
        return
    if not allow_empty and len(value) == 0:
        errors.push(f'{location}: must not be empty')
    if len(value) > MAX_LIST_ITEMS:
        errors.push(f'{location}: exceeds {MAX_LIST_ITEMS} entries')
    seen = set()
    for index, entry in enumerate(value[:MAX_LIST_ITEMS]):
        entry_location = f'{location}[{index}]'
        valid = is_safe_relative_path(entry) if path_value else is_visible_text(entry)
        if not valid:
            errors.push(f'{entry_location}: invalid {"repository-relative path" if path_value else "text"}')
        if fingerprint and isinstance(entry, str) and not FINGERPRINT_PATTERN.fullmatch(entry):
            errors.push(f'{entry_location}: invalid fingerprint')
        if isinstance(entry, str) and entry in seen:
            errors.push(f'{entry_location}: duplicate entry')
        if isinstance(entry, str):
            seen.add(entry)


def validate_checks(value, location: str, errors: _ErrorList) -> None:
    if not isinstance(value, list):
        errors.push(f'{location}: expected array')
        return
    if len(value) > MAX_LIST_ITEMS:
        errors.push(f'{location}: exceeds {MAX_LIST_ITEMS} entries')
    for index, check in enumerate(value[:MAX_LIST_ITEMS]):
        base = f'{location}[{index}]'
        if not is_object(check):
            errors.push(f'{base}: expected object')
            continue
        for field in ('agent_id', 'reviewed_paths', 'invariant', 'method', 'result', 'artifact'):
            if field not in check:
                errors.push(f'{base}: missing required field {safe_quote(field)}')
        agent_id = _get(check, 'agent_id')
        method = _get(check, 'method')
        artifact = _get(check, 'artifact')
        if not is_safe_agent_id(agent_id):
            errors.push(f'{base}.agent_id: expected a canonical lowercase agent ID')
        validate_string_array(_get(check, 'reviewed_paths'), f'{base}.reviewed_paths', errors,
                              allow_empty=False, path_value=True)
        if not is_visible_text(_get(check, 'invariant')):
            errors.push(f'{base}.invariant: invalid text')
        if method != 'source' and method != 'local':
            errors.push(f'{base}.method: expected "source" or "local"')
        if not is_visible_text(_get(check, 'result')):
            errors.push(f'{base}.result: invalid text')
        if method == 'source' and artifact is not None:
            errors.push(f'{base}.artifact: source-only check must use null')
        elif method == 'local':
            if not is_owned_artifact_path(artifact, agent_id):
                owner = agent_id if is_safe_agent_id(agent_id) else '<agent-id>'
                errors.push(f'{base}.artifact: local check requires an artifact owned by agent {safe_quote(owner)}')
        elif method != 'source' and method != 'local' and artifact is not None and not is_safe_relative_path(artifact):
            errors.push(f'{base}.artifact: expected null or a safe output-relative path')


def validate_reviewed_path_ownership(unit: dict, base: str, errors: _ErrorList) -> None:
    reviewed_paths = _get(unit, 'reviewed_paths')
    local_checks = _get(unit, 'local_checks')
    if not isinstance(reviewed_paths, list) or not isinstance(local_checks, list):
        return
    # dicts keep the insertion order that JavaScript Sets iterate in.
    aggregate_paths = dict.fromkeys(value for value in reviewed_paths if isinstance(value, str))
    owned_paths = {}
    for check in local_checks:
        if not is_object(check) or not isinstance(check.get('reviewed_paths'), list):
            continue
        for reviewed_path in check['reviewed_paths']:
            if isinstance(reviewed_path, str):
                owned_paths.setdefault(reviewed_path)
    for reviewed_path in aggregate_paths:
        if reviewed_path not in owned_paths:
            errors.push(f'{base}.reviewed_paths: {safe_quote(reviewed_path)} has no check owner')
    for reviewed_path in owned_paths:
        if reviewed_path not in aggregate_paths:
            errors.push(f'{base}.local_checks: owned path {safe_quote(reviewed_path)} '
                        'is absent from aggregate reviewed_paths')


def validate_excluded_blocks(value, location: str, errors: _ErrorList) -> None:
    if not isinstance(value, list):
        errors.push(f'{location}: expected array')
        return
    if len(value) > MAX_LIST_ITEMS:
        errors.push(f'{location}: exceeds {MAX_LIST_ITEMS} entries')
    seen = set()
    for index, entry in enumerate(value[:MAX_LIST_ITEMS]):
        base = f'{location}[{index}]'
        if not is_object(entry):
            errors.push(f'{base}: expected object')
            continue
        block = _get(entry, 'block')
        if not is_visible_text(block):
            errors.push(f'{base}.block: invalid text')
        if not is_visible_text(_get(entry, 'reason')):
            errors.push(f'{base}.reason: invalid text')
        if isinstance(block, str) and block in seen:
            errors.push(f'{base}.block: duplicate entry')
        if isinstance(block, str):
            seen.add(block)


def semantic_key(unit: dict) -> str:
    return json.dumps([
        unit['surface'],
        unit['boundary'],
        unit['subsystem'],
        unit['attack_class'],
        unit['lifecycle'] if 'lifecycle' in unit else None,
    ], ensure_ascii=False, separators=(',', ':'))


def has_valid_semantic_fields(unit: dict) -> bool:
    return (all(is_visible_text(_get(unit, field)) for field in ('surface', 'boundary', 'subsystem', 'attack_class'))
            and ('lifecycle' not in unit or is_visible_text(unit['lifecycle'])))


def require_empty_array(unit: dict, field: str, base: str, errors: _ErrorList) -> None:
    value = _get(unit, field)
    if isinstance(value, list) and len(value) > 0:
        errors.push(f'{base}.{field}: unit with status {safe_quote(_get(unit, "status"))} must keep this array empty')


def require_nonempty_array(unit: dict, field: str, base: str, errors: _ErrorList) -> None:
    value = _get(unit, field)
    if not isinstance(value, list) or len(value) == 0:
        errors.push(f'{base}.{field}: unit with status {safe_quote(_get(unit, "status"))} requires entries')


def validate_state_invariants(unit: dict, base: str, errors: _ErrorList) -> None:
    status = _get(unit, 'status')

    def empty_evidence() -> None:
        require_empty_array(unit, 'reviewed_paths', base, errors)
        require_empty_array(unit, 'local_checks', base, errors)

    def require_owner() -> None:
        if not is_safe_agent_id(_get(unit, 'agent_id')):
            errors.push(f'{base}.agent_id: unit with status {safe_quote(status)} requires a canonical lowercase agent ID')

    if status != 'candidate':
        require_empty_array(unit, 'result_fingerprints', base, errors)

    if status == 'planned':
        if _get(unit, 'agent_id') is not None:
            errors.push(f'{base}.agent_id: planned unit must be unassigned')
        empty_evidence()
        require_empty_array(unit, 'unresolved', base, errors)
    elif status in ('not_applicable', 'out_of_scope', 'deferred'):
        if _get(unit, 'agent_id') is not None:
            errors.push(f'{base}.agent_id: unit with status {safe_quote(status)} must be unassigned')
        empty_evidence()
        require_nonempty_array(unit, 'unresolved', base, errors)
    elif status == 'in_progress':
        require_owner()
        empty_evidence()
        require_empty_array(unit, 'unresolved', base, errors)
    elif status == 'blocked':
        require_owner()
        require_nonempty_array(unit, 'reviewed_paths', base, errors)
        require_nonempty_array(unit, 'local_checks', base, errors)
        require_nonempty_array(unit, 'unresolved', base, errors)
    elif status == 'covered':
        require_owner()
        require_nonempty_array(unit, 'reviewed_paths', base, errors)
        require_nonempty_array(unit, 'local_checks', base, errors)
        require_empty_array(unit, 'unresolved', base, errors)
    elif status == 'candidate':
        require_owner()
        require_nonempty_array(unit, 'reviewed_paths', base, errors)
        require_nonempty_array(unit, 'local_checks', base, errors)
        require_nonempty_array(unit, 'result_fingerprints', base, errors)


def validate_attempts(value, unit: dict, base: str, errors: _ErrorList) -> None:
    if not isinstance(value, list):
        errors.push(f'{base}.attempts: expected array')
        return
    if len(value) > MAX_LIST_ITEMS:
        errors.push(f'{base}.attempts: exceeds {MAX_LIST_ITEMS} entries')

    prior_owners = set()
    prior_artifacts = set()
    previous_wave = 0
    for index, attempt in enumerate(value[:MAX_LIST_ITEMS]):
        attempt_base = f'{base}.attempts[{index}]'
        if not is_object(attempt):
            errors.push(f'{attempt_base}: expected object')
            continue
        for field in ATTEMPT_FIELDS:
            if field not in attempt:
                errors.push(f'{attempt_base}: missing required field {safe_quote(field)}')
        wave = _get(attempt, 'wave')
        if not _is_integer(wave) or wave < 1:
            errors.push(f'{attempt_base}.wave: expected a positive integer')
        else:
            if wave <= previous_wave:
                errors.push(f'{attempt_base}.wave: archived attempt waves must be strictly increasing')
            if _is_integer(_get(unit, 'wave')) and wave >= unit['wave']:
                errors.push(f'{attempt_base}.wave: archived attempt wave must precede current wave '
                            f'{safe_quote(unit["wave"])}')
            previous_wave = wave
        status = _get(attempt, 'status')
        if not (isinstance(status, str) and status in ATTEMPT_STATUSES):
            errors.push(f'{attempt_base}.status: expected "covered", "candidate", or "blocked"')
        has_fresh_owner = False
        agent_id = _get(attempt, 'agent_id')
        if not is_safe_agent_id(agent_id):
            errors.push(f'{attempt_base}.agent_id: archived attempt requires a canonical lowercase agent ID')
        elif agent_id in prior_owners:
            errors.push(f'{attempt_base}.agent_id: assignment owner must be fresh for each attempt')
        else:
            has_fresh_owner = True
        validate_string_array(_get(attempt, 'reviewed_paths'), f'{attempt_base}.reviewed_paths', errors, path_value=True)
        validate_checks(_get(attempt, 'local_checks'), f'{attempt_base}.local_checks', errors)
        validate_reviewed_path_ownership(attempt, attempt_base, errors)
        validate_string_array(_get(attempt, 'result_fingerprints'), f'{attempt_base}.result_fingerprints', errors,
                              fingerprint=True)
        validate_string_array(_get(attempt, 'unresolved'), f'{attempt_base}.unresolved', errors)
        if not is_visible_text(_get(attempt, 'reassignment_reason')):
            errors.push(f'{attempt_base}.reassignment_reason: invalid text')
        validate_state_invariants(attempt, attempt_base, errors)

        if isinstance(_get(attempt, 'local_checks'), list):
            for check_index, check in enumerate(attempt['local_checks']):
                if not is_object(check):
                    continue
                check_agent_id = _get(check, 'agent_id')
                artifact = _get(check, 'artifact')
                if isinstance(check_agent_id, str) and check_agent_id in prior_owners:
                    errors.push(f'{attempt_base}.local_checks[{check_index}].agent_id: '
                                'prior assignment owner evidence must remain in its earlier attempt')
                if isinstance(artifact, str) and artifact in prior_artifacts:
                    errors.push(f'{attempt_base}.local_checks[{check_index}].artifact: '
                                'artifact from an earlier attempt cannot be reused')
                if _get(check, 'method') == 'local' and isinstance(artifact, str):
                    prior_artifacts.add(artifact)
        if has_fresh_owner:
            prior_owners.add(agent_id)

    unit_agent_id = _get(unit, 'agent_id')
    if is_safe_agent_id(unit_agent_id) and unit_agent_id in prior_owners:
        errors.push(f'{base}.agent_id: current assignment owner must be fresh after reassignment')
    if isinstance(_get(unit, 'local_checks'), list):
        for index, check in enumerate(unit['local_checks']):
            if not is_object(check):
                continue
            check_agent_id = _get(check, 'agent_id')
            artifact = _get(check, 'artifact')
            if isinstance(check_agent_id, str) and check_agent_id in prior_owners:
                errors.push(f'{base}.local_checks[{index}].agent_id: '
                            'prior assignment owner evidence must remain in its archived attempt')
            if isinstance(artifact, str) and artifact in prior_artifacts:
                errors.push(f'{base}.local_checks[{index}].artifact: artifact from an archived attempt cannot be reused')


def collect_unit_errors(unit, index: int) -> list:
    errors = create_error_list()
    base = f'$[{index}]'
    if not is_object(unit):
        return [f'{base}: expected object']

    for field in REQUIRED_FIELDS:
        if field not in unit:
            errors.push(f'{base}: missing required field {safe_quote(field)}')
    for field in ('surface', 'boundary', 'subsystem', 'attack_class'):
        if not is_visible_text(_get(unit, field)):
            errors.push(f'{base}.{field}: invalid text')
    if 'lifecycle' in unit and not is_visible_text(unit['lifecycle']):
        errors.push(f'{base}.lifecycle: invalid text')

    expected_id = None
    refs = _get(unit, 'canonical_refs')
    if not is_object(refs):
        errors.push(f'{base}.canonical_refs: expected object')
    else:
        expected_fields = REF_FIELDS + ('lifecycle',) if 'lifecycle' in refs else REF_FIELDS
        for field in expected_fields:
            if field not in refs:
                errors.push(f'{base}.canonical_refs: missing required field {safe_quote(field)}')
            elif not is_canonical_ref(refs[field]):
                errors.push(f'{base}.canonical_refs.{field}: invalid canonical reference')
        if any(field not in expected_fields for field in _object_keys(refs)):
            errors.push(f'{base}.canonical_refs: contains unexpected fields')
        if ('lifecycle' in unit) != ('lifecycle' in refs):
            errors.push(f'{base}: lifecycle and canonical_refs.lifecycle must appear together')
        try:
            expected_id = canonical_coverage_id(refs)
        except Exception:
            pass  # The specific reference errors above are more useful.
    coverage_id = _get(unit, 'coverage_id')
    if not is_visible_text(coverage_id, 65536):
        errors.push(f'{base}.coverage_id: invalid text')
    elif expected_id is not None and coverage_id != expected_id:
        errors.push(f'{base}.coverage_id: expected canonical ID {safe_quote(expected_id)}')

    validate_string_array(_get(unit, 'starting_paths'), f'{base}.starting_paths', errors,
                          allow_empty=False, path_value=True)
    block = _get(unit, 'ordinary_attack_class_block')
    if block is not None and not is_visible_text(block):
        errors.push(f'{base}.ordinary_attack_class_block: expected null or non-empty text')
    validate_string_array(_get(unit, 'selected_companion_blocks'), f'{base}.selected_companion_blocks', errors)
    validate_excluded_blocks(_get(unit, 'excluded_blocks'), f'{base}.excluded_blocks', errors)
    if isinstance(_get(unit, 'selected_companion_blocks'), list) and isinstance(_get(unit, 'excluded_blocks'), list):
        selected = {_same_value_zero_key(value) for value in unit['selected_companion_blocks']}
        for block_index, entry in enumerate(unit['excluded_blocks']):
            if is_object(entry) and _same_value_zero_key(_get(entry, 'block')) in selected:
                errors.push(f'{base}.excluded_blocks[{block_index}].block: block is also selected')

    prior_status = _get(unit, 'prior_status')
    if not (isinstance(prior_status, str) and prior_status in PRIOR_STATUSES):
        errors.push(f'{base}.prior_status: invalid value {safe_quote(prior_status)}')
    status = _get(unit, 'status')
    if not (isinstance(status, str) and status in STATUSES):
        errors.push(f'{base}.status: invalid value {safe_quote(status)}')
    wave = _get(unit, 'wave')
    if not _is_integer(wave) or wave < 1:
        errors.push(f'{base}.wave: expected a positive integer')
    agent_id = _get(unit, 'agent_id')
    if agent_id is not None and not is_safe_agent_id(agent_id):
        errors.push(f'{base}.agent_id: expected null or a safe agent ID')

    validate_attempts(_get(unit, 'attempts'), unit, base, errors)
    validate_string_array(_get(unit, 'reviewed_paths'), f'{base}.reviewed_paths', errors, path_value=True)
    validate_checks(_get(unit, 'local_checks'), f'{base}.local_checks', errors)
    validate_reviewed_path_ownership(unit, base, errors)
    validate_string_array(_get(unit, 'result_fingerprints'), f'{base}.result_fingerprints', errors, fingerprint=True)
    validate_string_array(_get(unit, 'unresolved'), f'{base}.unresolved', errors)

    validate_state_invariants(unit, base, errors)

    return errors


def read_file_within_limit(file) -> str:
    no_follow = getattr(os, 'O_NOFOLLOW', 0)
    non_block = getattr(os, 'O_NONBLOCK', 0)
    if not no_follow or not non_block:
        # There is no race-safe fallback on these platforms, so reject all inputs.
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
        if status.st_size > MAX_INPUT_BYTES:
            raise SafeInputError(f'input exceeds {MAX_INPUT_BYTES} byte limit')

        chunks = []
        bytes_read = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > MAX_INPUT_BYTES:
                raise SafeInputError(f'input exceeds {MAX_INPUT_BYTES} byte limit')
            chunks.append(chunk)
        try:
            # A fatal decoder that keeps a byte order mark, which JSON parsing then rejects.
            return b''.join(chunks).decode('utf-8')
        except UnicodeDecodeError:
            raise SafeInputError('input is not valid UTF-8') from None
    finally:
        os.close(descriptor)


def validate_document(ledger) -> list:
    errors = create_error_list()
    if not isinstance(ledger, list):
        errors.push('$: expected a top-level array')
        return errors
    if len(ledger) > MAX_UNITS:
        errors.push(f'$: exceeds {MAX_UNITS} coverage units')
        return errors

    errors.push(*preflight_document(ledger))
    if errors:
        return errors

    ids = {}
    semantics = {}
    previous_id = None
    for index, unit in enumerate(ledger):
        if len(errors) >= LIMITS['validation_errors']:
            break
        errors.push(*collect_unit_errors(unit, index))
        if len(errors) >= LIMITS['validation_errors']:
            break
        if not is_object(unit) or not isinstance(unit.get('coverage_id'), str):
            continue

        coverage_id = unit['coverage_id']
        key = semantic_key(unit) if has_valid_semantic_fields(unit) else None
        if coverage_id in ids:
            previous_index, previous_key = ids[coverage_id]
            qualifier = ('canonical identity collision with different semantic fields'
                         if key is not None and previous_key is not None and previous_key != key
                         else 'duplicate coverage ID')
            errors.push(f'$[{index}].coverage_id: {qualifier} at $[{previous_index}]')
        else:
            ids[coverage_id] = (index, key)
        if key is not None and key in semantics and semantics[key][0] != coverage_id:
            previous_coverage_id, previous_index = semantics[key]
            errors.push(f'$[{index}].canonical_refs: semantic tuple already uses coverage ID '
                        f'{safe_quote(previous_coverage_id)} at $[{previous_index}]')
        elif key is not None:
            semantics[key] = (coverage_id, index)
        if previous_id is not None and _utf16_order(previous_id) > _utf16_order(coverage_id):
            errors.push(f'$[{index}].coverage_id: units must be sorted lexicographically')
        previous_id = coverage_id
    return errors


def parse_json_text(text: str):
    """Apply the text preflight, then parse like JSON.parse (JsonStructureError or ValueError)."""
    preflight_json_text(text)
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


def _print_coverage_id(args: list) -> int:
    if len(args) not in (4, 5):
        _write(sys.stderr, 'Usage: python3 validate-coverage-ledger.py --id <surface> <boundary> <subsystem> '
                           '<attack_class> [<lifecycle>]')
        return 2
    refs = dict(zip(REF_FIELDS + ('lifecycle',), args))
    for field, value in refs.items():
        if not is_canonical_ref(value):
            _write(sys.stderr, f'Failed to derive coverage ID: canonical_refs.{field} {safe_quote(value)} '
                               'is not a canonical reference')
            return 2
    _write(sys.stdout, canonical_coverage_id(refs))
    return 0


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == '--id':
        return _print_coverage_id(args[1:])
    file = args[0] if args else None
    if not file:
        _write(sys.stderr, 'Usage: python3 validate-coverage-ledger.py <path-to-coverage-ledger.json>')
        return 1

    try:
        contents = read_file_within_limit(file)
    except Exception as error:
        reason = str(error) if isinstance(error, SafeInputError) else 'input could not be opened or read safely'
        _write(sys.stderr, f'Failed to read coverage ledger: {reason}')
        return 1

    try:
        preflight_json_text(contents)
    except Exception as error:
        reason = str(error) if isinstance(error, JsonStructureError) else 'invalid JSON structure'
        _write(sys.stderr, f'Failed to parse coverage ledger: {reason}')
        return 1
    try:
        ledger = _json_parse(contents)
    except Exception:
        _write(sys.stderr, 'Failed to parse coverage ledger: invalid JSON syntax')
        return 1

    try:
        errors = validate_document(ledger)
    except Exception:
        _write(sys.stderr, 'Failed to validate coverage ledger: unexpected validation error')
        return 1
    for message in errors:
        _write(sys.stderr, 'ERROR:', message)
    if errors:
        cap = f'; output capped at {LIMITS["validation_errors"]}' if len(errors) == LIMITS['validation_errors'] else ''
        _write(sys.stderr, f'FAIL: {len(errors)} validation error(s){cap}')
        return 1
    _write(sys.stdout, f'PASS: {len(ledger)} coverage units valid')
    return 0


if __name__ == '__main__':
    sys.exit(main())
