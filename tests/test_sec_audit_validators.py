"""Behaviour tests for the wf sec audit validators: validate-findings.py and validate-coverage-ledger.py.

The two suites port upstream's validate-findings.test.cjs and validate-coverage-ledger.test.cjs case by
case; each test is named after its upstream title.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / 'skills' / 'wf-sec-audit' / 'scripts'
FINDINGS_VALIDATOR = SCRIPTS / 'validate-findings.py'
LEDGER_VALIDATOR = SCRIPTS / 'validate-coverage-ledger.py'

sys.dont_write_bytecode = True


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


findings = load('validate_findings', FINDINGS_VALIDATOR)
ledger = load('validate_coverage_ledger', LEDGER_VALIDATOR)
SCHEMA = json.loads((SCRIPTS / 'report-schema.json').read_text(encoding='utf-8'))

CLI_TIMEOUT = 10
HOSTILE_CLI_TIMEOUT = 30
# The analogue of upstream's --max-old-space-size=64 for the hostile inputs: a cap on the child's
# address space, where the platform has one.
HOSTILE_ADDRESS_SPACE = 256 * 1024 * 1024
HAS_SAFE_INPUT_OPEN = bool(getattr(os, 'O_NOFOLLOW', 0)) and bool(getattr(os, 'O_NONBLOCK', 0))
TRACE_FAILURE = re.compile(r'Traceback|RecursionError|MemoryError|stack', re.IGNORECASE)


def js_stringify(value) -> bytes:
    """JSON.stringify(value) written as UTF-8, as fs.writeFileSync stores it."""
    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return re.sub('[\ud800-\udfff]', lambda match: '\\u%04x' % ord(match.group()), text).encode('utf-8')


def limited_address_space():
    try:
        import resource
    except ImportError:
        return None

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (HOSTILE_ADDRESS_SPACE, HOSTILE_ADDRESS_SPACE))

    return apply


def run_path(validator: Path, path, timeout: float = CLI_TIMEOUT, preexec_fn=None):
    try:
        return subprocess.run([sys.executable, str(validator), str(path)], capture_output=True,
                              timeout=timeout, preexec_fn=preexec_fn)
    except subprocess.TimeoutExpired:
        raise AssertionError(f'{validator.name} timed out after {timeout} s') from None


def run_cli(validator: Path, name: str, contents, timeout: float = CLI_TIMEOUT, preexec_fn=None):
    with tempfile.TemporaryDirectory(prefix=f'{validator.stem}-') as directory:
        path = Path(directory) / name
        path.write_bytes(contents if isinstance(contents, bytes) else contents.encode('utf-8'))
        return run_path(validator, path, timeout, preexec_fn)


def cli_output(result) -> str:
    return (result.stdout + result.stderr).decode('utf-8', 'replace')


def assign(*path, value):
    """A mutation that sets value at path inside a finding or unit."""
    def mutate(target) -> None:
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    return mutate


class ControlBytes:
    TERMINAL_CONTROL_BYTES: tuple = ()

    def assert_no_injected_control_bytes(self, output) -> None:
        data = output if isinstance(output, bytes) else output.encode('utf-8', 'surrogatepass')
        for marker in self.TERMINAL_CONTROL_BYTES:
            self.assertEqual(data.find(marker), -1, f'found raw control bytes {marker.hex()}')


# --- validate-findings ----------------------------------------------------------------------------

FINDINGS_PAYLOAD = '\u001b\u0007\u0085\u202e\u034f\ufe0f'


def source(kind: str = 'entrypoint', file: str = 'src/handler.c', line: int = 10) -> dict:
    return {'kind': kind, 'file': file, 'line': line, 'scope': 'handle',
            'description': 'Attacker data reaches the operation.'}


def evidence(file: str = 'src/handler.c', line: int = 10) -> dict:
    return {'file': file, 'line': line, 'description': 'The source performs the operation without the required check.'}


def confirmed() -> dict:
    return {
        'verdict': 'confirmed',
        'fingerprint': 'src-handler-missing-check',
        'title': 'Missing ownership check',
        'description': 'An attacker can reach an operation without the intended ownership check.',
        'root_cause': 'handle omits the ownership check before changing the object.',
        'intended_behavior': "Only the object's owner can change it.",
        'trace': [source('entrypoint'), source('propagation', 'src/model.c', 20), source('sink', 'src/store.c', 30)],
        'evidence': [evidence()],
        'conditions': [],
        'execution': {
            'attacker_perspective': 'An unprivileged remote user with their own account.',
            'payloads': ['An object identifier owned by another user.'],
            'instructions': ['Submit the identifier through the public operation.'],
            'observed_result': "The other user's object changes.",
        },
        'remediation': {'strategy': 'Check ownership before the state change.'},
        'severity': {
            'likelihood': {'score': 'medium', 'reason': 'The operation is directly reachable.'},
            'impact': {'score': 'medium', 'reason': 'The attacker changes one protected object.'},
            'overall_severity': 'medium',
        },
        'confidence': {'score': 'high', 'reason': 'The source path and result were reproduced.'},
    }


def needs_validation() -> dict:
    return {
        'verdict': 'needs_validation',
        'fingerprint': 'src-parser-size-hypothesis',
        'title': 'Unchecked parsed size',
        'description': 'A parsed size may reach an allocation without a limit.',
        'claimed_root_cause': 'parse_size may pass an unbounded value to allocate.',
        'trace': [source()],
        'evidence': [evidence()],
        'blockers': ['The generated parser source is absent from this checkout.'],
        'validation_plan': {
            'local': 'Generate the parser and submit the smallest input that exceeds the documented limit.',
            'deployment': 'In an approved test deployment, confirm the request reaches the generated parser '
                          'and record the bounded observable result.',
        },
    }


def rejected() -> dict:
    return {
        'verdict': 'rejected',
        'fingerprint': 'src-router-auth-bypass',
        'title': 'Authorization bypass in router',
        'description': 'The candidate claimed a route bypassed authorization.',
        'claimed_root_cause': 'dispatch was claimed to skip the authorization wrapper.',
        'trace': [source('sink')],
        'evidence': [evidence()],
        'reason': 'All routes pass through the authorization wrapper before dispatch.',
    }


def findings_errors_for(value) -> list:
    return findings.validate_document(value, SCHEMA)


def run_findings_cli(contents, **options):
    return run_cli(FINDINGS_VALIDATOR, 'findings.json', contents, **options)


def producer_shaped_findings() -> list:
    demonstrated = confirmed()
    demonstrated['conditions'] = [{
        'kind': 'authentication_level',
        'description': 'The attacker needs a normal account.',
    }]
    demonstrated['execution']['payloads'] = ['', ' \t\r\n', '\u0000\u001f\u007f', '\u034f', '\ufe0f', '\ud800',
                                             '\udc00', '[{,}]\\"']
    demonstrated['remediation']['code_changes'] = [{'file_name': 'src/handler.c', 'fixed_code': ''}]

    blocked = needs_validation()
    del blocked['validation_plan']['deployment']

    return [demonstrated, blocked, rejected()]


class ValidateFindings(ControlBytes, unittest.TestCase):
    TERMINAL_CONTROL_BYTES = (b'\x1b', b'\x07', '\u0085'.encode(), '\u202e'.encode(), '\u034f'.encode(),
                              '\ufe0f'.encode())

    def reject_mutation(self, factory, mutate) -> None:
        value = factory()
        mutate(value)
        self.assertNotEqual(len(findings_errors_for([value])), 0)

    def test_schema_is_an_actual_top_level_array_with_exactly_three_branches(self):
        self.assertEqual(SCHEMA['type'], 'array')
        self.assertEqual(len(SCHEMA['items']['oneOf']), 3)
        self.assertEqual([branch['properties']['verdict']['const'] for branch in SCHEMA['items']['oneOf']],
                         ['confirmed', 'needs_validation', 'rejected'])
        confirmed_schema = SCHEMA['items']['oneOf'][0]['properties']
        self.assertIs(confirmed_schema['title']['visibleContent'], True)
        self.assertNotIn('minLength', confirmed_schema['execution']['properties']['payloads']['items'])
        self.assertNotIn('visibleContent', confirmed_schema['execution']['properties']['payloads']['items'])
        self.assertNotIn('minLength', confirmed_schema['remediation']['properties']['code_changes']['items']
                         ['properties']['fixed_code'])

    def test_accepts_a_producer_shaped_findings_document_through_the_cli(self):
        result = run_findings_cli(js_stringify(producer_shaped_findings()))
        self.assertEqual(result.returncode, 0, cli_output(result))
        self.assertIn(b'PASS: 3 findings valid', result.stdout)

    def test_accepts_empty_output_and_each_complete_branch(self):
        self.assertEqual(findings_errors_for([]), [])
        self.assertEqual(findings_errors_for([confirmed(), needs_validation(), rejected()]), [])
        local_only = needs_validation()
        del local_only['validation_plan']['deployment']
        self.assertEqual(findings_errors_for([local_only]), [])
        deployment_only = needs_validation()
        del deployment_only['validation_plan']['local']
        self.assertEqual(findings_errors_for([deployment_only]), [])

    def test_allows_a_one_line_finding_trace(self):
        finding = confirmed()
        finding['trace'] = [source('entrypoint')]
        self.assertEqual(findings_errors_for([finding]), [])

    def test_rejects_empty_required_content(self):
        cases = [
            (confirmed, assign('title', value='')),
            (confirmed, assign('title', value='   ')),
            (confirmed, assign('evidence', value=[])),
            (confirmed, assign('execution', 'payloads', value=[])),
            (confirmed, assign('execution', 'instructions', value=[])),
            (confirmed, assign('execution', 'observed_result', value='')),
            (confirmed, assign('remediation', 'strategy', value='')),
            (needs_validation, assign('blockers', value=[])),
            (needs_validation, assign('validation_plan', value={})),
            (needs_validation, assign('validation_plan', value={'local': ' '})),
            (rejected, assign('claimed_root_cause', value='')),
        ]
        for factory, mutate in cases:
            self.reject_mutation(factory, mutate)

    def test_preserves_exact_payload_and_replacement_code_strings(self):
        finding = confirmed()
        payloads = ['', ' \t\r\n', '\u0000\u001f\u007f', '\u034f', '\ufe0f', '\ud800', '\udc00']
        fixed_code = '\u0000 \t\r\n\u001f\u007f\u034f\ufe0f\ud800x\udc00'
        finding['execution']['payloads'] = list(payloads)
        finding['remediation']['code_changes'] = [{'file_name': 'src/handler.c', 'fixed_code': fixed_code}]

        self.assertEqual(findings_errors_for([finding]), [])
        self.assertEqual(finding['execution']['payloads'], payloads)
        self.assertEqual(finding['remediation']['code_changes'][0]['fixed_code'], fixed_code)

    def test_rejects_invalid_scalars_and_whitespace_control_format_or_default_ignorable_prose(self):
        for invisible in ['\u0000\t\r\n\u001f\u007f\u200b', '\u034f', '\ufe0f', '\ud800', '\udc00', 'visible\ud800']:
            cases = [
                (confirmed, assign('title', value=invisible)),
                (confirmed, assign('trace', 0, 'scope', value=invisible)),
                (confirmed, assign('evidence', 0, 'description', value=invisible)),
                (confirmed, assign('execution', 'instructions', value=[invisible])),
                (confirmed, assign('remediation', 'strategy', value=invisible)),
                (confirmed, assign('severity', 'impact', 'reason', value=invisible)),
                (confirmed, assign('confidence', 'reason', value=invisible)),
                (needs_validation, assign('blockers', value=[invisible])),
                (needs_validation, assign('validation_plan', value={'local': invisible})),
                (rejected, assign('reason', value=invisible)),
            ]
            for factory, mutate in cases:
                self.reject_mutation(factory, mutate)

    def test_quotes_input_derived_controls_in_direct_validation_values_and_paths(self):
        finding = confirmed()
        finding['trace'][0]['kind'] = f'invalid-{FINDINGS_PAYLOAD}'
        finding['execution'][f'extra-{FINDINGS_PAYLOAD}'] = 'value'

        cyclic = {}
        cyclic[f'path-{FINDINGS_PAYLOAD}'] = cyclic
        output = '\n'.join([
            *findings_errors_for([finding]),
            *findings.collect(cyclic, {'type': 'object'}, '$input'),
        ])

        for escaped in ['\\u001b', '\\u0007', '\\u0085', '\\u202e', '\\u034f', '\\ufe0f']:
            self.assertIn(escaped, output, f'missing escaped diagnostic {escaped}')
        self.assert_no_injected_control_bytes(output)

    def test_rejects_line_zero(self):
        self.reject_mutation(confirmed, assign('trace', 0, 'line', value=0))
        self.reject_mutation(rejected, assign('evidence', 0, 'line', value=0))

    def test_does_not_treat_inherited_or_object_prototype_properties_as_schema_properties(self):
        # Python objects have no prototype chain: an object whose properties are all inherited has
        # no own properties, so it is ported as an empty object.
        self.reject_mutation(confirmed, assign('constructor', value='not allowed'))
        inherited = {}
        self.assertTrue(any('exactly one' in error for error in findings_errors_for([inherited])))
        self.assertTrue(any('missing required' in error for error in findings.collect({}, {
            'type': 'object',
            'properties': {'constructor': {'type': 'string'}},
            'required': ['constructor'],
            'additionalProperties': False,
        })))

    def test_oneof_requires_exactly_one_passing_branch(self):
        self.assertTrue(any('matched 2' in error for error in findings.collect(
            'value', {'oneOf': [{'type': 'string'}, {'minLength': 1}]}, '$test')))
        self.assertTrue(any('matched 0' in error for error in findings.collect(
            7, {'oneOf': [{'type': 'string'}, {'minimum': 10}]}, '$test')))

    def test_rejects_duplicate_fingerprints_and_unique_array_entries(self):
        first = confirmed()
        second = rejected()
        second['fingerprint'] = first['fingerprint']
        self.assertTrue(any('duplicate of' in error for error in findings_errors_for([first, second])))

        def duplicate_blocker(finding):
            finding['blockers'] = [finding['blockers'][0], finding['blockers'][0]]

        self.reject_mutation(needs_validation, duplicate_blocker)

    def test_uses_canonical_set_uniqueness_for_structured_entries_at_the_array_limit(self):
        entries = [{'id': index, 'label': str(index)} for index in range(findings.LIMITS['array_items'])]
        self.assertEqual(findings.collect(entries, {'type': 'array', 'uniqueItems': True}), [])

        duplicate = entries[:-1]
        duplicate.append({'label': '0', 'id': 0})
        self.assertTrue(any(f'duplicate at index {findings.LIMITS["array_items"] - 1}' in error
                            for error in findings.collect(duplicate, {'type': 'array', 'uniqueItems': True})))

    def test_bounds_canonical_uniqueness_keys_and_set_storage(self):
        oversized_key = 'x' * (findings.LIMITS['canonical_key_bytes'] + 1)
        self.assertTrue(any('canonical key exceeds' in error
                            for error in findings.collect([oversized_key], {'type': 'array', 'uniqueItems': True})))

        item_length = findings.LIMITS['unique_set_bytes'] // 6
        large_unique_items = [f'{index}{"x" * item_length}' for index in range(6)]
        self.assertTrue(any('canonical uniqueness set exceeds' in error
                            for error in findings.collect(large_unique_items, {'type': 'array', 'uniqueItems': True})))

    def test_requires_findings_to_be_sorted_by_fingerprint(self):
        first = confirmed()
        second = rejected()
        self.assertTrue(any('sorted lexicographically' in error for error in findings_errors_for([second, first])))

    def test_rejects_severity_above_demonstrated_impact(self):
        def raise_severity(finding):
            finding['severity']['overall_severity'] = 'high'
            finding['severity']['impact']['score'] = 'medium'

        self.reject_mutation(confirmed, raise_severity)

    def test_rejects_unsafe_source_paths(self):
        bad_paths = [
            '/etc/passwd',
            '../src/file.c',
            'src/../file.c',
            'src//file.c',
            'C:\\src\\file.c',
            'src/file:name.c',
            'src/file\nname.c',
            'src/file\u0001name.c',
            'src/file\u0085name.c',
            'src/file\u2028name.c',
            'src/file\u202ename.c',
            'src/file\u2066name.c',
            'src/file\u200dname.c',
            'src/file\u034fname.c',
            'src/file\ufe0fname.c',
            'src/file\ud800name.c',
            'src/file\udc00name.c',
            'CON',
            'src/con.txt',
            'src/PRN',
            'src/AUX.c',
            'src/NUL',
            'src/COM1.log',
            'src/lpt9',
            'src/CONIN$',
            'src/CONOUT$.txt',
            'src/CLOCK$.txt',
            'src/COM\u00b9.log',
            'src/LPT\u00b2.log',
            'src /file.c',
            'src./file.c',
            'src/file.c ',
            'src/file.c.',
        ]
        for bad_path in bad_paths:
            self.reject_mutation(confirmed, assign('trace', 0, 'file', value=bad_path))
        self.reject_mutation(rejected, assign('evidence', 0, 'file', value='NUL.txt'))
        self.reject_mutation(confirmed, assign('remediation', 'code_changes', value=[
            {'file_name': 'src/file:name.c', 'fixed_code': 'replacement'}]))

    def test_accepts_legitimate_unicode_source_paths_and_prose(self):
        finding = confirmed()
        finding['title'] = 'Finding \U0001F600 cafe\u0301'
        finding['trace'][0]['file'] = 'src/日本語/cafe\u0301-\U0001F600.ts'
        finding['evidence'][0]['file'] = 'src/mañana/файл.ts'
        finding['remediation']['code_changes'] = [{
            'file_name': 'src/修正/éxito.ts',
            'fixed_code': 'replacement',
        }]
        self.assertEqual(findings_errors_for([finding]), [])

    def test_cli_rejects_input_above_the_byte_limit_without_an_exception_trace(self):
        result = run_findings_cli(b' ' * (findings.LIMITS['input_bytes'] + 1))
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn(f'input exceeds {findings.LIMITS["input_bytes"]} byte limit', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_cli_rejects_invalid_utf_8_without_replacement_or_an_exception_trace(self):
        document = producer_shaped_findings()
        document[0]['execution']['payloads'] = ['INVALID_UTF8']
        encoded = js_stringify(document)
        marker = b'INVALID_UTF8'
        marker_offset = encoded.find(marker)
        self.assertNotEqual(marker_offset, -1)
        malformed = encoded[:marker_offset] + b'\x80' + encoded[marker_offset + len(marker):]

        result = run_findings_cli(malformed)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('input is not valid UTF-8', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_quotes_input_derived_controls_in_cli_validation_errors(self):
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        finding = confirmed()
        finding['trace'][0]['kind'] = f'invalid-{FINDINGS_PAYLOAD}'
        finding['execution'][f'extra-{FINDINGS_PAYLOAD}'] = 'value'
        result = run_findings_cli(js_stringify([finding]))

        self.assertEqual(result.returncode, 1, cli_output(result))
        self.assertIn(b'$[0].trace[0].kind', result.stderr)
        self.assertIn(b'\\u001b', result.stderr)
        self.assertIn(b'\\u202e', result.stderr)
        self.assert_no_injected_control_bytes(result.stderr)

    def test_returns_a_generic_syntax_error_without_parser_supplied_controls(self):
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        result = run_findings_cli(b'[' + FINDINGS_PAYLOAD.encode() + b']')

        self.assertEqual(result.returncode, 1, cli_output(result))
        self.assertEqual(result.stderr, b'Failed to parse findings JSON: invalid JSON syntax\n')
        self.assert_no_injected_control_bytes(result.stderr)

    def test_does_not_reflect_controls_from_a_failed_cli_input_path(self):
        with tempfile.TemporaryDirectory(prefix='validate-findings-path-') as directory:
            result = run_path(FINDINGS_VALIDATOR, Path(directory) / f'missing-{FINDINGS_PAYLOAD}.json')
        self.assertEqual(result.returncode, 1, cli_output(result))
        self.assertIn(b'Failed to read findings JSON:', result.stderr)
        self.assert_no_injected_control_bytes(result.stderr)

    def test_cli_rejects_lone_surrogate_prose_without_changing_payload_semantics(self):
        document = producer_shaped_findings()
        document[0]['title'] = '\ud800'
        result = run_findings_cli(js_stringify(document))
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('must contain only valid Unicode scalar values', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_cli_rejects_unicode_format_controls_in_source_paths(self):
        document = producer_shaped_findings()
        document[0]['trace'][0]['file'] = 'src/file\u202ename.c'
        result = run_findings_cli(js_stringify(document))
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('must be a safe repository-relative source path', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_cli_rejects_a_fifo_without_blocking(self):
        if sys.platform == 'win32' or not HAS_SAFE_INPUT_OPEN:
            self.skipTest('needs FIFOs and a no-follow, nonblocking open')
        with tempfile.TemporaryDirectory(prefix='validate-findings-fifo-') as directory:
            fifo_path = Path(directory) / 'findings.json'
            os.mkfifo(fifo_path)
            result = run_path(FINDINGS_VALIDATOR, fifo_path)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('input must be a regular file', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_cli_rejects_a_symlink_without_following_it(self):
        if sys.platform == 'win32' or not HAS_SAFE_INPUT_OPEN:
            self.skipTest('needs symlinks and a no-follow, nonblocking open')
        with tempfile.TemporaryDirectory(prefix='validate-findings-symlink-') as directory:
            target_path = Path(directory) / 'target.json'
            symlink_path = Path(directory) / 'findings.json'
            target_path.write_bytes(js_stringify(producer_shaped_findings()))
            os.symlink(target_path, symlink_path)
            result = run_path(FINDINGS_VALIDATOR, symlink_path)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('input must not be a symlink', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_cli_rejects_input_above_the_nesting_depth_limit_without_an_exception_trace(self):
        levels = findings.LIMITS['nesting_depth'] + 1
        result = run_findings_cli('[' * levels + '0' + ']' * levels)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn(f'{findings.LIMITS["nesting_depth"]} level nesting depth limit', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_cli_rejects_an_oversized_array_without_an_exception_trace(self):
        result = run_findings_cli(js_stringify([None] * (findings.LIMITS['array_items'] + 1)))
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn(f'{findings.LIMITS["array_items"]} item array limit', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_checks_pattern_and_branch_invariants(self):
        self.reject_mutation(rejected, assign('fingerprint', value='not stable'))
        self.reject_mutation(needs_validation, assign('severity', value={
            'impact': {'score': 'low'}, 'overall_severity': 'low'}))

    def test_rejects_unsupported_and_malformed_schema_keywords(self):
        self.assertTrue(any('format' in error for error in findings.collect_schema_errors(
            {'type': 'string', 'format': 'uuid'})))
        self.assertTrue(any('regular expression' in error for error in findings.collect_schema_errors(
            {'type': 'string', 'pattern': '['})))
        self.assertTrue(any('expected boolean' in error for error in findings.collect_schema_errors(
            {'type': 'string', 'visibleContent': 'yes'})))
        self.assertTrue(any('requires type' in error for error in findings.collect_schema_errors(
            {'type': 'array', 'visibleContent': True})))
        self.assertNotEqual(len(findings.validate_document([], {'type': 'array', 'maxItems': 1})), 0)

    def test_caps_malformed_1000_finding_validation_output(self):
        self.assertEqual(len(findings_errors_for([None] * findings.LIMITS['array_items'])),
                         findings.LIMITS['validation_errors'])
        if not HAS_SAFE_INPUT_OPEN:
            return

        result = run_findings_cli(js_stringify([None] * findings.LIMITS['array_items']))
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('output capped at 100', output)
        self.assertLess(len(output), 20000, f'unexpected output length {len(output)}')
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_caps_amplified_in_limit_findings_output_under_a_constrained_node_heap(self):
        """Ported with a capped address space in place of upstream's constrained Node heap."""
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        document = [{
            'verdict': 'confirmed',
            'trace': [0] * findings.LIMITS['array_items'],
            'evidence': [0] * findings.LIMITS['array_items'],
        } for _ in range(750)]
        contents = js_stringify(document)
        self.assertGreater(len(contents), 3 * 1000 * 1000, f'hostile input too small: {len(contents)}')
        self.assertLessEqual(len(contents), findings.LIMITS['input_bytes'], f'hostile input over limit: {len(contents)}')

        result = run_findings_cli(contents, timeout=HOSTILE_CLI_TIMEOUT, preexec_fn=limited_address_space())
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('output capped at 100', output)
        self.assertLess(len(output), 20000, f'unexpected output length {len(output)}')
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_keeps_shared_helpers_aligned_with_the_coverage_ledger_validator(self):
        # Upstream compares the shared regular expressions; here they are tables and predicates.
        for name in ('DEFAULT_IGNORABLE_CODE_POINTS', 'WHITE_SPACE_CODE_POINTS', 'JS_WHITESPACE',
                     'WINDOWS_RESERVED_COMPONENT', 'SIMPLE_CASE_FOLD', 'MAX_DIAGNOSTIC_STRING_LENGTH'):
            self.assertEqual(getattr(findings, name), getattr(ledger, name), name)
        for name in ('_is_default_ignorable', '_is_visible_character', '_is_path_forbidden_character',
                     '_is_unsafe_diagnostic_character', '_is_windows_reserved_component', '_js_trim',
                     'escape_unsafe_diagnostic_characters', 'has_valid_unicode_scalar_values', 'has_visible_prose'):
            self.assertEqual(inspect.getsource(getattr(findings, name)), inspect.getsource(getattr(ledger, name)), name)

        shared_limit_keys = sorted(key for key in findings.LIMITS if key in ledger.LIMITS)
        self.assertEqual(shared_limit_keys, ['input_bytes', 'nesting_depth', 'validation_errors'])
        for key in shared_limit_keys:
            self.assertEqual(findings.LIMITS[key], ledger.LIMITS[key], f'LIMITS.{key}')

        path_corpus = [
            'src/handler.js',
            'src/caf\u00e9/handler.js',
            'src/\u65e5\u672c\u8a9e/\u0444\u0430\u0439\u043b.ts',
            'src/cloc\u212a$.txt',
            'src/CLOCK$.txt',
            'src/con.txt',
            'CON',
            'src/COM\u00b9.log',
            'src/lpt\u00b3',
            '/etc/passwd',
            '../src/file.c',
            'src/../file.c',
            'src//file.c',
            'src\\file.c',
            'src/file:name.c',
            '~home/file.c',
            'C:/file.c',
            'src/file.c ',
            'src/file.c.',
            'src/file\u202ename.c',
            'src/file\u200b.js',
            'src/file\u034f.js',
            'src/file\ufe0f.js',
            'src/file\ud800name.c',
            'src/file\udc00name.c',
        ]
        for value in path_corpus:
            self.assertEqual(findings.is_safe_relative_source_path(value), ledger.is_safe_relative_path(value),
                             f'path verdict diverges for {value!r}')
        self.assertIs(findings.is_safe_relative_source_path('src/cloc\u212a$.txt'), False)
        self.assertIs(ledger.is_safe_relative_path('src/cloc\u212a$.txt'), False)

        prose_corpus = ['Valid prose.', 'caf\u00e9', '', ' \t\r\n', '\u200b', '\u034f', '\ufe0f', '\ud800', '\udc00',
                        'visible\ud800']
        for value in prose_corpus:
            self.assertEqual(findings.has_visible_prose(value), ledger.has_visible_prose(value),
                             f'prose verdict diverges for {value!r}')


# --- validate-coverage-ledger ---------------------------------------------------------------------

LEDGER_PAYLOAD = '\u001b\u0007\u0085\u202e'


def default_refs() -> dict:
    return {
        'surface': 'src/router.ts#POST /users/:id',
        'boundary': 'src/authz.ts#requireOwner',
        'subsystem': 'packages/api',
        'attack_class': 'ATTACK-CLASSES.md#Access control',
    }


def unit(overrides: dict | None = None) -> dict:
    overrides = overrides or {}
    canonical_refs = overrides.get('canonical_refs') or default_refs()
    value = {
        'coverage_id': ledger.canonical_coverage_id(canonical_refs),
        'canonical_refs': canonical_refs,
        'surface': 'Update-user route',
        'boundary': 'Object ownership',
        'subsystem': 'API',
        'attack_class': 'Access control',
        'starting_paths': ['src/router.ts', 'src/authz.ts'],
        'ordinary_attack_class_block': 'ATTACK-CLASSES.md#Access control',
        'selected_companion_blocks': [],
        'excluded_blocks': [{'block': 'WEB-PROTOCOL-AND-AUTH.md#Cache behavior', 'reason': 'The route is not cached.'}],
        'prior_status': 'new',
        'attempts': [],
        'wave': 1,
        'status': 'planned',
        'agent_id': None,
        'reviewed_paths': [],
        'local_checks': [],
        'result_fingerprints': [],
        'unresolved': [],
    }
    value.update(overrides)
    value['canonical_refs'] = canonical_refs
    return value


def ledger_errors_for(value) -> list:
    return ledger.validate_document(value)


def run_ledger_cli(contents, **options):
    return run_cli(LEDGER_VALIDATOR, 'coverage-ledger.json', contents, **options)


def source_check(agent_id='hunter-1', overrides: dict | None = None) -> dict:
    return {
        'agent_id': agent_id,
        'reviewed_paths': ['src/router.ts'],
        'invariant': 'The route checks object ownership.',
        'method': 'source',
        'result': 'The owner check applies before the update.',
        'artifact': None,
        **(overrides or {}),
    }


def local_check(agent_id='hunter-1', overrides: dict | None = None) -> dict:
    return source_check(agent_id, {
        'method': 'local',
        'result': "The bounded fixture accepted the other owner's object.",
        'artifact': f'agents/{"null" if agent_id is None else agent_id}/artifacts/result.txt',
        **(overrides or {}),
    })


def archived_attempt(overrides: dict | None = None) -> dict:
    value = {
        'wave': 1,
        'status': 'blocked',
        'agent_id': 'hunter-1',
        'reviewed_paths': ['src/router.ts'],
        'local_checks': [source_check()],
        'result_fingerprints': [],
        'unresolved': ['The deployed policy is unavailable.'],
        'reassignment_reason': 'The critic found an unchecked parallel path.',
    }
    value.update(overrides or {})
    return value


class ValidateCoverageLedger(ControlBytes, unittest.TestCase):
    TERMINAL_CONTROL_BYTES = (b'\x1b', b'\x07', '\u0085'.encode(), '\u202e'.encode())

    def assert_any(self, errors, text: str, message=None) -> None:
        self.assertTrue(any(text in error for error in errors), message or f'{text!r} not in {errors!r}')

    def test_accepts_an_empty_ledger_and_complete_units(self):
        self.assertEqual(ledger_errors_for([]), [])
        self.assertEqual(ledger_errors_for([unit()]), [])

        missing_attempts = unit()
        del missing_attempts['attempts']
        self.assert_any(ledger_errors_for([missing_attempts]), 'missing required field "attempts"')

        covered = unit({
            'status': 'covered',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check()],
        })
        self.assertEqual(ledger_errors_for([covered]), [])

    def test_accepts_a_complete_ledger_through_the_cli(self):
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        result = run_ledger_cli(js_stringify([unit()]))
        self.assertEqual(result.returncode, 0, cli_output(result))
        self.assertIn(b'PASS: 1 coverage units valid', result.stdout)

    def test_text_preflight_ignores_structural_characters_and_escapes_inside_strings(self):
        value = unit({
            'surface': 'Route \\ slash [list] {object}, colon: quoted "value"',
            'excluded_blocks': [{
                'block': 'COMPANION.md#Literal [brackets] {braces}',
                'reason': 'The text contains a backslash \\ before an escaped "quote".',
            }],
        })
        contents = js_stringify([value]).decode('utf-8')
        ledger.preflight_json_text(contents)
        self.assertEqual(json.loads(contents), [value])
        if HAS_SAFE_INPUT_OPEN:
            result = run_ledger_cli(contents)
            self.assertEqual(result.returncode, 0, cli_output(result))

    def test_text_preflight_enforces_structural_cardinality_limits(self):
        depth_limit = ledger.LIMITS['nesting_depth']
        ledger.preflight_json_text('[' * depth_limit + '0' + ']' * depth_limit)
        with self.assertRaisesRegex(ledger.JsonStructureError, 'exceeds nesting depth limit 64'):
            ledger.preflight_json_text('[' * (depth_limit + 1) + '0' + ']' * (depth_limit + 1))

        too_many_units = '[' + 'null,' * ledger.LIMITS['units'] + 'null]'
        with self.assertRaisesRegex(ledger.JsonStructureError, 'exceeds 10000 top-level unit limit'):
            ledger.preflight_json_text(too_many_units)

        too_many_items = '[[' + 'null,' * ledger.LIMITS['collection_items'] + 'null]]'
        with self.assertRaisesRegex(ledger.JsonStructureError, 'exceeds 1000 item array limit'):
            ledger.preflight_json_text(too_many_items)

        object_fields = ','.join(f'"field{index}":null' for index in range(ledger.LIMITS['object_fields'] + 1))
        with self.assertRaisesRegex(ledger.JsonStructureError, 'exceeds 1000 field object limit'):
            ledger.preflight_json_text(f'[{{{object_fields}}}]')

        full_array = '[' + 'null,' * (ledger.LIMITS['collection_items'] - 1) + 'null]'
        arrays_needed = ledger.LIMITS['preflight_values'] // (ledger.LIMITS['collection_items'] + 1) + 1
        too_many_values = '[' + ','.join([full_array] * arrays_needed) + ']'
        with self.assertRaisesRegex(ledger.JsonStructureError, 'exceeds 500000 total value limit'):
            ledger.preflight_json_text(too_many_values)

    def test_text_preflight_rejects_malformed_structural_truncation_cleanly(self):
        with self.assertRaisesRegex(ledger.JsonStructureError, 'truncated JSON structure'):
            ledger.preflight_json_text('[')
        with self.assertRaisesRegex(ledger.JsonStructureError, 'unterminated JSON string'):
            ledger.preflight_json_text('["unterminated')
        with self.assertRaisesRegex(ledger.JsonStructureError, 'mismatched JSON containers'):
            ledger.preflight_json_text('[{"field":1]')

    def test_derives_collision_free_canonical_ids_from_exact_utf_8_references(self):
        self.assertEqual(ledger.encode_canonical_ref('route:POST /users'), 'route%3APOST%20%2Fusers')
        self.assertNotEqual(ledger.encode_canonical_ref('route name'), ledger.encode_canonical_ref('route-name'))
        self.assertEqual(
            ledger.canonical_coverage_id({'surface': 'a', 'boundary': 'b', 'subsystem': 'c', 'attack_class': 'd',
                                          'lifecycle': 'retry'}),
            'a::b::c::d::retry')
        for value in ('e\u0301', 'bad\u0000ref', 'hidden\u200bref'):
            with self.assertRaisesRegex(ValueError, 'invalid canonical reference'):
                ledger.encode_canonical_ref(value)

    def test_rejects_noncanonical_duplicate_and_colliding_ids(self):
        wrong = unit({'coverage_id': 'display-label-slug'})
        self.assert_any(ledger_errors_for([wrong]), 'expected canonical ID')

        duplicate = unit()
        self.assert_any(ledger_errors_for([duplicate, unit()]), 'duplicate coverage ID')

        collision = unit()
        different_meaning = unit({'surface': 'Delete-user route'})
        self.assert_any(ledger_errors_for([collision, different_meaning]), 'canonical identity collision')

    def test_requires_canonical_references_to_be_own_properties(self):
        # Python objects have no prototype chain: references that are only inherited leave an
        # object without own properties.
        value = unit()
        value['canonical_refs'] = {}
        self.assert_any(ledger_errors_for([value]), 'missing required field')

    def test_rejects_aliases_for_one_semantic_tuple(self):
        first = unit()
        refs = {**first['canonical_refs'], 'surface': 'src/alias.ts#updateUser'}
        alias = unit({'canonical_refs': refs})
        units = sorted([first, alias], key=lambda value: value['coverage_id'])
        self.assert_any(ledger_errors_for(units), 'semantic tuple already uses coverage ID')

    def test_requires_lexicographic_order(self):
        second_refs = {
            'surface': 'zzz',
            'boundary': 'src/authz.ts#requireOwner',
            'subsystem': 'packages/api',
            'attack_class': 'ATTACK-CLASSES.md#Access control',
        }
        self.assert_any(ledger_errors_for([unit({'canonical_refs': second_refs}), unit()]), 'sorted lexicographically')

    def test_validates_assignment_block_maps(self):
        overlap = unit({
            'selected_companion_blocks': ['AI-AND-LLM.md#Tool calls'],
            'excluded_blocks': [{'block': 'AI-AND-LLM.md#Tool calls', 'reason': 'Claimed irrelevant.'}],
        })
        self.assert_any(ledger_errors_for([overlap]), 'also selected')

        no_reason = unit({'excluded_blocks': [{'block': 'AI-AND-LLM.md#Tool calls', 'reason': ''}]})
        self.assert_any(ledger_errors_for([no_reason]), 'reason')

    def test_requires_owned_artifacts_for_local_checks_and_null_artifacts_for_source_checks(self):
        local = unit({
            'status': 'covered',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [local_check()],
        })
        self.assertEqual(ledger_errors_for([local]), [])

        independently_verified = unit({
            'status': 'covered',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts', 'src/authz.ts'],
            'local_checks': [source_check(), local_check('verifier-1', {'reviewed_paths': ['src/authz.ts']})],
        })
        self.assertEqual(ledger_errors_for([independently_verified]), [])

        unowned_path = unit({
            'status': 'covered',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts', 'src/authz.ts'],
            'local_checks': [source_check()],
        })
        self.assert_any(ledger_errors_for([unowned_path]), 'has no check owner')

        for check_agent_id, artifact in [
            (None, 'agents/hunter-1/artifacts/result.txt'),
            ('hunter-1', None),
            ('hunter-1', 'result.txt'),
            ('hunter-1', 'agents/hunter-2/artifacts/result.txt'),
            ('../hunter', 'agents/../hunter/artifacts/result.txt'),
        ]:
            value = unit({
                'status': 'covered',
                'agent_id': 'hunter-1',
                'reviewed_paths': ['src/router.ts'],
                'local_checks': [local_check(check_agent_id, {'artifact': artifact})],
            })
            self.assertNotEqual(len(ledger_errors_for([value])), 0, f'{check_agent_id}: {artifact}')

        unowned_source = unit({
            'status': 'blocked',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check()],
            'unresolved': ['The boundary behavior is not source-visible.'],
        })
        self.assert_any(ledger_errors_for([unowned_source]),
                        'unit with status "blocked" requires a canonical lowercase agent ID')

        source_with_artifact = unit({
            'status': 'covered',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check('hunter-1', {'artifact': 'agents/hunter-1/artifacts/source.txt'})],
        })
        self.assert_any(ledger_errors_for([source_with_artifact]), 'source-only check must use null')

    def test_requires_canonical_lowercase_filesystem_safe_agent_ids(self):
        for value in ('hunter-1', 'verifier_2', 'a0'):
            self.assertIs(ledger.is_safe_agent_id(value), True, value)
        for value in ('Hunter-1', 'hunter.1', 'hunter-1.', 'hunter ', 'con', 'prn', 'aux', 'nul', 'com1', 'lpt9',
                      '../hunter'):
            self.assertIs(ledger.is_safe_agent_id(value), False, value)

        case_alias = unit({'status': 'in_progress', 'agent_id': 'Hunter-1'})
        self.assert_any(ledger_errors_for([case_alias]), 'canonical lowercase agent ID')

    def test_enforces_state_evidence(self):
        self.assert_any(ledger_errors_for([unit({'status': 'in_progress'})]), 'unit with status "in_progress" requires')
        self.assert_any(ledger_errors_for([unit({'status': 'blocked'})]), 'unresolved')
        self.assert_any(ledger_errors_for([unit({'status': 'candidate'})]), 'reviewed_paths')

        self.assertEqual(ledger_errors_for([unit({'status': 'in_progress', 'agent_id': 'hunter-1'})]), [])
        in_progress_evidence = unit({
            'status': 'in_progress',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check()],
        })
        self.assert_any(ledger_errors_for([in_progress_evidence]), 'must keep this array empty')

        assigned_planned = unit({
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check()],
        })
        self.assert_any(ledger_errors_for([assigned_planned]), 'planned unit must be unassigned')

        candidate = unit({
            'status': 'candidate',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check('hunter-1', {'invariant': 'Ownership is required.',
                                                       'result': 'No check exists.'})],
            'result_fingerprints': ['src-router-missing-owner-check'],
            'unresolved': ['validation_budget_exhausted'],
        })
        self.assertEqual(ledger_errors_for([candidate]), [])

        blocked = unit({
            'status': 'blocked',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check()],
            'unresolved': ['The deployed policy is unavailable.'],
        })
        self.assertEqual(ledger_errors_for([blocked]), [])
        blocked_fingerprint = {**blocked, 'result_fingerprints': ['forbidden-fingerprint']}
        self.assert_any(ledger_errors_for([blocked_fingerprint]), 'result_fingerprints')

        for status in ('not_applicable', 'out_of_scope', 'deferred'):
            self.assertEqual(ledger_errors_for([unit({'status': status, 'unresolved': ['Reason recorded.']})]), [])
            invalid = unit({
                'status': status,
                'agent_id': 'hunter-1',
                'reviewed_paths': ['src/router.ts'],
                'local_checks': [source_check()],
                'result_fingerprints': ['forbidden-fingerprint'],
                'unresolved': ['Reason recorded.'],
            })
            errors = ledger_errors_for([invalid])
            self.assert_any(errors, 'must be unassigned', status)
            self.assert_any(errors, 'reviewed_paths', status)
            self.assert_any(errors, 'result_fingerprints', status)

        covered_fingerprint = unit({
            'status': 'covered',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check()],
            'result_fingerprints': ['forbidden-fingerprint'],
        })
        self.assert_any(ledger_errors_for([covered_fingerprint]), 'result_fingerprints')

        covered_unresolved = unit({
            'status': 'covered',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check()],
            'unresolved': ['Unexpected unresolved claim.'],
        })
        self.assert_any(ledger_errors_for([covered_unresolved]), 'unresolved')

    def test_archives_prior_evidence_when_a_critic_assigns_a_fresh_owner(self):
        reassigned = unit({
            'attempts': [archived_attempt()],
            'wave': 2,
            'status': 'in_progress',
            'agent_id': 'hunter-2',
        })
        self.assertEqual(ledger_errors_for([reassigned]), [])

        final_closure = unit({
            'attempts': [archived_attempt()],
            'wave': 2,
            'status': 'covered',
            'agent_id': 'hunter-2',
            'reviewed_paths': ['src/authz.ts'],
            'local_checks': [source_check('hunter-2', {'reviewed_paths': ['src/authz.ts']})],
        })
        self.assertEqual(ledger_errors_for([final_closure]), [])

    def test_preserves_candidate_provenance_when_reassignment_must_be_deferred(self):
        candidate_attempt = archived_attempt({
            'status': 'candidate',
            'result_fingerprints': ['src-router-missing-owner-check'],
            'unresolved': ['validation_budget_exhausted'],
        })
        deferred = unit({
            'attempts': [candidate_attempt],
            'wave': 2,
            'status': 'deferred',
            'unresolved': ['quick_profile_final_critic'],
        })
        self.assertEqual(ledger_errors_for([deferred]), [])

    def test_rejects_reassignment_owner_reuse_and_evidence_mixing(self):
        reused_owner = unit({
            'attempts': [archived_attempt()],
            'wave': 2,
            'status': 'in_progress',
            'agent_id': 'hunter-1',
        })
        self.assert_any(ledger_errors_for([reused_owner]), 'current assignment owner must be fresh')

        mixed_evidence = unit({
            'attempts': [archived_attempt({'local_checks': [local_check()]})],
            'wave': 2,
            'status': 'covered',
            'agent_id': 'hunter-2',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [local_check()],
        })
        errors = ledger_errors_for([mixed_evidence])
        self.assert_any(errors, 'prior assignment owner evidence must remain')
        self.assert_any(errors, 'artifact from an archived attempt cannot be reused')

        mixed_history = unit({
            'attempts': [
                archived_attempt({'local_checks': [local_check()]}),
                archived_attempt({
                    'wave': 2,
                    'agent_id': 'hunter-2',
                    'local_checks': [local_check()],
                }),
            ],
            'wave': 3,
            'status': 'in_progress',
            'agent_id': 'hunter-3',
        })
        history_errors = ledger_errors_for([mixed_history])
        self.assert_any(history_errors, 'prior assignment owner evidence must remain in its earlier attempt')
        self.assert_any(history_errors, 'artifact from an earlier attempt cannot be reused')

        unordered = unit({
            'attempts': [archived_attempt(), archived_attempt({
                'wave': 1,
                'agent_id': 'hunter-2',
                'local_checks': [source_check('hunter-2')],
            })],
            'wave': 3,
            'status': 'in_progress',
            'agent_id': 'hunter-3',
        })
        self.assert_any(ledger_errors_for([unordered]), 'strictly increasing')

    def test_rejects_unsafe_paths_and_malformed_fingerprints(self):
        for value in [
            '/etc/passwd',
            '../src/file.js',
            'src/../file.js',
            'src/con.txt',
            'src/PRN',
            'src/AUX.c',
            'src/NUL',
            'src/CLOCK$.txt',
            'src/conin$.txt',
            'src/conout$',
            'src/COM1.log',
            'src/lpt9',
            'src/COM\u00b9.log',
            'src/COM\u00b2.log',
            'src/COM\u00b3.log',
            'src/lpt\u00b9',
            'src/lpt\u00b2',
            'src/lpt\u00b3',
            'src/file.js.',
            'C:/src/file.js',
            'src/file\n.js',
            'src/file\u0085.js',
            'src/file\u2028.js',
            'src/file\u200b.js',
            'src/file\u034f.js',
            'src/file\ufe0f.js',
        ]:
            self.assertIs(ledger.is_safe_relative_path(value), False, value)
        self.assertIs(ledger.is_safe_relative_path('src/handler.js'), True)
        self.assertIs(ledger.is_safe_relative_path('src/caf\u00e9/handler.js'), True)
        self.assert_any(ledger_errors_for([unit({'starting_paths': ['../src/router.ts']})]), 'repository-relative path')
        self.assert_any(ledger_errors_for([unit({
            'status': 'candidate',
            'agent_id': 'hunter-1',
            'reviewed_paths': ['src/router.ts'],
            'local_checks': [source_check('hunter-1', {'invariant': 'Ownership is required.',
                                                       'result': 'No check exists.'})],
            'result_fingerprints': ['not stable'],
        })]), 'invalid fingerprint')

    def test_rejects_format_default_ignorable_and_invalid_scalar_prose(self):
        for invisible in ('\u200b', '\u034f', '\ufe0f', '\ud800'):
            self.assert_any(ledger_errors_for([unit({'surface': invisible})]), 'surface', repr(invisible))
            self.assert_any(ledger_errors_for([unit({
                'excluded_blocks': [{'block': 'ATTACK-CLASSES.md#Access control', 'reason': invisible}],
            })]), 'reason', repr(invisible))

    def test_quotes_input_derived_controls_in_direct_validation_errors(self):
        value = unit({
            'status': f'invalid-{LEDGER_PAYLOAD}',
            'agent_id': 'hunter-1',
            'reviewed_paths': [f'src/{LEDGER_PAYLOAD}.js'],
            'local_checks': [source_check()],
            'result_fingerprints': ['force-state-error'],
        })
        output = '\n'.join(ledger_errors_for([value]))

        for text in ('$[0].status', '$[0].reviewed_paths', '\\u001b', '\\u0007', '\\u0085', '\\u202e'):
            self.assertIn(text, output)
        self.assert_no_injected_control_bytes(output)

    def test_quotes_input_derived_controls_in_cli_validation_errors(self):
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        value = unit({
            'status': f'invalid-{LEDGER_PAYLOAD}',
            'result_fingerprints': ['force-state-error'],
        })
        result = run_ledger_cli(js_stringify([value]))

        self.assertEqual(result.returncode, 1, cli_output(result))
        self.assertIn(b'$[0].status', result.stderr)
        self.assertIn(b'\\u001b', result.stderr)
        self.assert_no_injected_control_bytes(result.stderr)

    def test_returns_a_generic_syntax_error_without_parser_supplied_controls(self):
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        result = run_ledger_cli(b'[' + LEDGER_PAYLOAD.encode() + b']')

        self.assertEqual(result.returncode, 1, cli_output(result))
        self.assertEqual(result.stderr, b'Failed to parse coverage ledger: invalid JSON syntax\n')
        self.assert_no_injected_control_bytes(result.stderr)

    def test_does_not_reflect_controls_from_a_failed_cli_input_path(self):
        with tempfile.TemporaryDirectory(prefix='validate-coverage-ledger-path-') as directory:
            result = run_path(LEDGER_VALIDATOR, Path(directory) / f'missing-{LEDGER_PAYLOAD}.json')
        self.assertEqual(result.returncode, 1, cli_output(result))
        self.assertIn(b'Failed to read coverage ledger:', result.stderr)
        self.assert_no_injected_control_bytes(result.stderr)

    def test_rejects_invalid_utf_8_through_the_cli(self):
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        encoded = js_stringify([unit()])
        marker = b'Update-user route'
        marker_offset = encoded.find(marker)
        self.assertNotEqual(marker_offset, -1)
        malformed = encoded[:marker_offset] + b'\x80' + encoded[marker_offset + len(marker):]

        result = run_ledger_cli(malformed)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('input is not valid UTF-8', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_rejects_a_fifo_through_the_cli_without_blocking(self):
        if sys.platform == 'win32' or not HAS_SAFE_INPUT_OPEN:
            self.skipTest('needs FIFOs and a no-follow, nonblocking open')
        with tempfile.TemporaryDirectory(prefix='validate-coverage-ledger-fifo-') as directory:
            fifo_path = Path(directory) / 'coverage-ledger.json'
            os.mkfifo(fifo_path)
            result = run_path(LEDGER_VALIDATOR, fifo_path)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('input must be a regular file', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_rejects_a_symlink_through_the_cli(self):
        if sys.platform == 'win32' or not HAS_SAFE_INPUT_OPEN:
            self.skipTest('needs symlinks and a no-follow, nonblocking open')
        with tempfile.TemporaryDirectory(prefix='validate-coverage-ledger-symlink-') as directory:
            target_path = Path(directory) / 'target.json'
            symlink_path = Path(directory) / 'coverage-ledger.json'
            target_path.write_bytes(js_stringify([unit()]))
            os.symlink(target_path, symlink_path)
            result = run_path(LEDGER_VALIDATOR, symlink_path)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('input must not be a symlink', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_rejects_deeply_nested_input_without_recursion_failure(self):
        nested = 0
        for _ in range(20000):
            nested = [nested]
        self.assert_any(ledger_errors_for(nested), 'exceeds nesting depth limit 64')
        if not HAS_SAFE_INPUT_OPEN:
            return

        result = run_ledger_cli('[' * 20000 + '0' + ']' * 20000)
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('exceeds nesting depth limit 64', output)
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_rejects_multi_megabyte_nesting_under_a_constrained_node_heap(self):
        """Ported with a capped address space in place of upstream's constrained Node heap."""
        if not HAS_SAFE_INPUT_OPEN:
            self.skipTest('the OS has no no-follow and nonblocking open')
        open_containers = '[' * 2000000
        for contents in (open_containers, open_containers + '0' + ']' * 2000000):
            result = run_ledger_cli(contents, timeout=HOSTILE_CLI_TIMEOUT, preexec_fn=limited_address_space())
            output = cli_output(result)
            self.assertEqual(result.returncode, 1, output)
            self.assertIn('exceeds nesting depth limit 64', output)
            self.assertNotRegex(output, TRACE_FAILURE)

    def test_caps_malformed_10000_unit_validation_output(self):
        self.assertEqual(len(ledger_errors_for([None] * ledger.LIMITS['units'])), ledger.LIMITS['validation_errors'])
        if not HAS_SAFE_INPUT_OPEN:
            return

        result = run_ledger_cli(js_stringify([None] * ledger.LIMITS['units']))
        output = cli_output(result)
        self.assertEqual(result.returncode, 1, output)
        self.assertIn('output capped at 100', output)
        self.assertLess(len(output), 20000, f'unexpected output length {len(output)}')
        self.assertNotRegex(output, TRACE_FAILURE)

    def test_rejects_malformed_top_level_data_and_excessive_unit_counts(self):
        self.assertEqual(ledger_errors_for({'units': []}), ['$: expected a top-level array'])
        self.assertEqual(ledger_errors_for([None] * 10001), ['$: exceeds 10000 coverage units'])

        oversized_collection = unit({'extra': [None] * (ledger.LIMITS['collection_items'] + 1)})
        self.assert_any(ledger_errors_for([oversized_collection]), 'exceeds 1000 entries')

    def test_accepts_a_canonical_id_derived_from_near_maximum_multibyte_references(self):
        canonical_refs = {
            'surface': '\u6f22' * 1024,
            'boundary': '\u00e9' * 1024,
            'subsystem': 'packages/api',
            'attack_class': '\u6f22' * 1023 + '\u00e9',
        }
        value = unit({'canonical_refs': canonical_refs})
        self.assertGreater(len(value['coverage_id']), 16384, f'coverage_id length {len(value["coverage_id"])}')
        self.assertLessEqual(len(value['coverage_id']), 65536, f'coverage_id length {len(value["coverage_id"])}')
        self.assertEqual(ledger_errors_for([value]), [])

        if HAS_SAFE_INPUT_OPEN:
            result = run_ledger_cli(js_stringify([value]))
            self.assertEqual(result.returncode, 0, cli_output(result))


# --- validate-coverage-ledger --id ----------------------------------------------------------------

def run_coverage_id(*args: str):
    return subprocess.run([sys.executable, str(LEDGER_VALIDATOR), '--id', *args], capture_output=True,
                          timeout=CLI_TIMEOUT)


class CoverageIdCommand(ControlBytes, unittest.TestCase):
    TERMINAL_CONTROL_BYTES = ValidateCoverageLedger.TERMINAL_CONTROL_BYTES

    def test_prints_the_id_the_validator_derives(self):
        refs = default_refs()
        result = run_coverage_id(*refs.values())
        self.assertEqual(result.returncode, 0, cli_output(result))
        self.assertEqual(result.stdout.decode(), ledger.canonical_coverage_id(refs) + '\n')
        self.assertEqual(result.stderr, b'')
        value = unit({'coverage_id': result.stdout.decode().strip()})
        self.assertEqual(ledger_errors_for([value]), [])

    def test_appends_an_encoded_lifecycle(self):
        refs = {**default_refs(), 'subsystem': '\u6f22/api', 'lifecycle': 'retry after failure'}
        result = run_coverage_id(*refs.values())
        self.assertEqual(result.returncode, 0, cli_output(result))
        self.assertEqual(result.stdout.decode(), ledger.canonical_coverage_id(refs) + '\n')
        self.assertTrue(result.stdout.decode().endswith('::retry%20after%20failure\n'))

    def test_rejects_a_noncanonical_reference_without_reflecting_controls(self):
        for bad in ('e\u0301', 'hidden\u200bref', ' padded', '', f'control-{LEDGER_PAYLOAD}'):
            result = run_coverage_id('a', 'b', bad, 'd')
            self.assertEqual(result.returncode, 2, cli_output(result))
            self.assertEqual(result.stdout, b'')
            self.assertIn(b'canonical_refs.subsystem', result.stderr)
            self.assert_no_injected_control_bytes(result.stderr)

    def test_requires_four_or_five_references(self):
        for args in (('a', 'b', 'c'), ('a', 'b', 'c', 'd', 'e', 'f')):
            result = run_coverage_id(*args)
            self.assertEqual(result.returncode, 2, cli_output(result))
            self.assertIn(b'Usage: python3 validate-coverage-ledger.py --id', result.stderr)

    def test_keeps_the_upstream_usage_for_the_normal_mode(self):
        result = subprocess.run([sys.executable, str(LEDGER_VALIDATOR)], capture_output=True, timeout=CLI_TIMEOUT)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b'Usage: python3 validate-coverage-ledger.py <path-to-coverage-ledger.json>\n')


# --- JavaScript semantics the ports keep ----------------------------------------------------------

class JavaScriptSemantics(unittest.TestCase):
    def test_json_constants_that_json_parse_rejects_are_syntax_errors(self):
        for text in ('[NaN]', '[Infinity]', '[-Infinity]'):
            with self.assertRaises(ValueError):
                findings.parse_json_text(text)
            with self.assertRaises(ValueError):
                ledger.parse_json_text(text)
        result = run_findings_cli('[NaN]')
        self.assertEqual(result.stderr, b'Failed to parse findings JSON: invalid JSON syntax\n')

    def test_numbers_are_doubles(self):
        finding = confirmed()
        finding['trace'][0]['line'] = 10.0
        self.assertEqual(findings_errors_for([finding]), [])
        parsed = findings.parse_json_text('[1e400, 12345678901234567890123]')
        finding['trace'][0]['line'] = parsed[0]
        self.assertIn('$[0].trace[0].line: expected integer, got number', findings_errors_for([finding]))
        self.assertEqual(findings.safe_quote(parsed[1]), '1.2345678901234568e+22')
        self.assertEqual(ledger_errors_for([unit({'wave': 1.0})]), [])
        self.assertIn('$[0].wave: expected a positive integer', ledger_errors_for([unit({'wave': True})]))

    def test_object_keys_order_and_duplicate_keys_follow_json_parse(self):
        self.assertEqual(list(findings.parse_json_text('{"b":1,"a":2,"b":3}').items()), [('b', 3), ('a', 2)])
        errors = findings.collect(findings.parse_json_text('{"b":1,"10":2,"2":3}'), {'additionalProperties': False})
        self.assertEqual(errors, ['$data: unexpected field "2"', '$data: unexpected field "10"',
                                  '$data: unexpected field "b"'])

    def test_patterns_anchor_at_the_end_of_input(self):
        self.reject_fingerprint('src-handler\n')
        self.assertEqual(findings.collect('a\n', {'pattern': '^a$'}), ['$data: must match pattern "^a$"'])
        self.assertEqual(findings.collect('a{,5}', {'pattern': '^a{,5}$'}), [])
        self.assertTrue(findings.collect_schema_errors({'pattern': 'a*+'}))

    def reject_fingerprint(self, fingerprint: str) -> None:
        finding = rejected()
        finding['fingerprint'] = fingerprint
        self.assertIn('$[0].fingerprint: must match pattern "^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$"',
                      findings_errors_for([finding]))

    def test_strings_compare_count_and_clip_in_utf16_code_units(self):
        sorted_by_units = [{'fingerprint': '\U00010000'}, {'fingerprint': '\uffff'}]
        self.assertEqual(findings.collect_finding_semantic_errors(sorted_by_units), [])
        self.assertEqual(findings.collect_finding_semantic_errors(sorted_by_units[::-1]),
                         ['$[1].fingerprint: findings must be sorted lexicographically'])
        self.assertEqual(ledger.safe_quote('a' * 255 + '\U0001F600'), '"' + 'a' * 255 + '\\ud83d..."')
        self.assertIs(ledger.is_visible_text('\U0001F600' * 2048), True)
        self.assertIs(ledger.is_visible_text('\U0001F600' * 2049), False)

    def test_trim_uses_ecmascript_whitespace(self):
        self.assertIs(ledger.is_visible_text('\ufeffvisible'), False)
        self.assertIs(ledger.is_visible_text('visible\x1c'), True)


if __name__ == '__main__':
    unittest.main()
