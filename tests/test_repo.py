"""Structural checks for the skills, and behaviour tests for install.py and the skills' scripts.

Run: python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / 'skills'
SHARED = REPO / 'shared'
RUN_AGENT = SHARED / 'scripts' / 'run-agent.sh'
VALIDATE_REVIEW = SHARED / 'scripts' / 'validate-review.py'
LOOP = SHARED / 'loop.md'
LOOP_SKILLS = ('wf-plan-loop', 'wf-impl-loop')
CORE_DOCS = ('workflow.md', 'conventions.md', 'review-rubric.md', 'security.md')
DEV_SERVER = SKILLS / 'wf-init' / 'scripts' / 'dev-server.py'

SKILL_MD_MAX_LINES = 130
SHARED_MAX_LINES = 200

sys.dont_write_bytecode = True  # never run a stale cached install.py
spec = importlib.util.spec_from_file_location('install', REPO / 'install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


def skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if p.is_dir())


class SkillStructure(unittest.TestCase):
    def test_portable_skills_and_resolved_references(self):
        self.assertGreaterEqual(len(skill_dirs()), 20)
        for skill in skill_dirs():
            with self.subTest(skill=skill.name):
                self.assertEqual(install.validate_skill(skill), skill.name)
                frontmatter = install.parse_frontmatter((skill / 'SKILL.md').read_text())
                self.assertEqual(set(frontmatter), {'name', 'description'})
                self.assertLessEqual(len((skill / 'SKILL.md').read_text().splitlines()), 130)
                for link in (skill / 'references').glob('*.md'):
                    self.assertTrue(link.exists(), f'{link} is dangling')

    def test_public_tree_excludes_reference_examples(self):
        self.assertFalse((REPO / 'examples').exists())
        self.assertIn('examples/', (REPO / '.gitignore').read_text())
        self.assertIn('development/', (REPO / '.gitignore').read_text())

    def test_project_instructions_use_portable_skill_paths(self):
        init = (SKILLS / 'wf-init' / 'SKILL.md').read_text()
        block = init.split('## Block format', 1)[1].split('```markdown', 1)[1].split('```', 1)[0]
        self.assertNotIn('<skills-dir>', block)
        self.assertIn('.claude/skills/', block)
        self.assertIn('.agents/skills/', block)

    def test_all_skills_are_documented(self):
        guide = (REPO / 'docs' / 'skills.md').read_text()
        for skill in skill_dirs():
            command = skill.name.replace('-', ' ')
            if skill.name == 'wf-new-worktree':
                command = 'wf new-worktree'
            command = {'wf-plan-review': 'wf plan rev', 'wf-impl-review': 'wf impl rev',
                       'wf-security-review': 'wf sec'}.get(skill.name, command)
            with self.subTest(skill=skill.name):
                self.assertIn(command, guide)

    def test_review_and_test_contexts_are_separate(self):
        self.assertIn('fresh context', (SKILLS / 'wf-test' / 'SKILL.md').read_text())
        for name in ('wf-plan-review', 'wf-impl-review', 'wf-security-review'):
            self.assertIn('fresh context', (SKILLS / name / 'SKILL.md').read_text())
        self.assertIn('finite', (SHARED / 'project.md').read_text())
        self.assertIn('stagnation', (SHARED / 'loop.md').read_text())

    def test_key_stages_and_user_guide_match(self):
        readme = (REPO / 'README.md').read_text()
        for command in ('wf prd', 'wf spec', 'wf roadmap', 'wf new-worktree',
                        'wf test', 'wf wave integrate', 'wf finalize'):
            self.assertIn(command, readme)
        self.assertIn('reviewer=', readme)
        self.assertIn('effort=', readme)
        self.assertIn('rounds=', readme)


class WaveReviewValidator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.script = SHARED / 'scripts' / 'validate-wave-review.py'
        self.sha = 'a' * 40

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def validate(self, body: str):
        path = self.tmp / 'review.md'
        path.write_text(body)
        return subprocess.run([sys.executable, str(self.script), '--reviewed', self.sha,
                               '--round', '2', str(path)], capture_output=True, text=True)

    def test_accepts_review_of_current_wave_commit(self):
        result = self.validate('## Round 2\n- Reviewed: commit ' + self.sha +
                               '\n- Verdict: Changes requested\n- Must-fix open: 1\n'
                               '- Major: incompatible API change\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_stale_or_contradictory_approval(self):
        stale = self.validate('## Round 2\n- Reviewed: commit ' + 'b' * 40 +
                              '\n- Verdict: Approved\n- Must-fix open: 0\n')
        self.assertEqual(stale.returncode, 2)
        contradiction = self.validate('## Round 2\n- Reviewed: commit ' + self.sha +
                                      '\n- Verdict: Approved\n- Must-fix open: 1\n'
                                      '- Major: missing authorization\n')
        self.assertEqual(contradiction.returncode, 2)
        hidden = self.validate('## Round 2\n- Reviewed: commit ' + self.sha +
                               '\n- Verdict: Approved\n- Must-fix open: 0\n'
                               '- Blocker: leaked credential\n')
        self.assertEqual(hidden.returncode, 2)


class Installer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = mock_env(HOME=str(self.tmp / 'home'), CLAUDE_CONFIG_DIR=None, CODEX_HOME=None)
        self.env.__enter__()
        (self.tmp / 'home').mkdir()
        self.project = self.tmp / 'project'
        self.project.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)

    def tearDown(self):
        self.env.__exit__(None, None, None)
        shutil.rmtree(self.tmp)

    def run_install(self, *args: str, project: bool = True) -> int:
        argv = ['--agents', 'claude,codex,gemini']
        if project and '--project' not in args and '--uninstall-user' not in args:
            argv += ['--project', str(self.project)]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return install.main([*argv, *args])

    def dests(self, project: Path | None = None) -> list[Path]:
        project = project or self.project
        return [project / '.claude/skills', project / '.agents/skills', project / '.gemini/skills']

    def user_dests(self) -> list[Path]:
        home = self.tmp / 'home'
        return [home / '.claude/skills', home / '.codex/skills', home / '.gemini/skills']

    def test_dry_run_changes_nothing(self):
        self.assertEqual(self.run_install('--dry-run'), 1)
        for d in self.dests():
            self.assertFalse(d.exists())

    def test_project_is_required_for_normal_install(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                install.parse_args([])
        self.assertEqual(raised.exception.code, 2)

    def test_install_is_idempotent(self):
        self.assertEqual(self.run_install(), 0)
        for d in self.dests():
            for skill in skill_dirs():
                link = d / skill.name
                self.assertTrue(link.is_symlink())
                self.assertEqual(link.resolve(), skill.resolve())
        self.assertEqual(self.run_install('--dry-run'), 0)

    def test_collision_stops_install(self):
        blocker = self.dests()[0] / 'wf-plan'
        blocker.mkdir(parents=True)
        self.assertEqual(self.run_install(), 2)
        self.assertFalse(blocker.is_symlink())
        self.assertFalse((self.dests()[1] / 'wf-plan').exists(), 'collision must stop all destinations')

    def test_dangling_foreign_link_is_a_collision(self):
        d = self.dests()[0]
        d.mkdir(parents=True)
        (d / 'wf-old').symlink_to(self.tmp / 'gone')
        (d / 'wf-plan').symlink_to(self.tmp / 'moved-repo' / 'skills' / 'wf-plan')
        (d / 'other').symlink_to(self.tmp / 'gone-too')
        self.assertEqual(self.run_install(), 2)
        self.assertTrue((d / 'wf-old').is_symlink())
        self.assertTrue((d / 'wf-plan').is_symlink())
        self.assertTrue((d / 'other').is_symlink(), 'links not named wf-* are never touched')

    def test_uninstall_removes_only_own_links(self):
        self.run_install()
        foreign = self.dests()[0] / 'my-skill'
        foreign.mkdir()
        self.assertEqual(self.run_install('--uninstall'), 0)
        for d in self.dests():
            self.assertEqual([p.name for p in d.iterdir() if p.name.startswith('wf-')], [])
        self.assertTrue(foreign.is_dir())

    def test_project_scope_excludes_links_from_git(self):
        project = self.project
        self.assertEqual(self.run_install(), 0)
        for rel in ('.claude/skills', '.agents/skills', '.gemini/skills'):
            self.assertTrue((project / rel / 'wf-plan').is_symlink())
        exclude = (project / '.git/info/exclude').read_text()
        self.assertIn('/.agents/skills/wf-*', exclude)
        status = subprocess.run(['git', '-C', str(project), 'status', '--porcelain'],
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(status, '')
        self.assertEqual(self.run_install('--uninstall'), 0)
        self.assertNotIn('wf-*', (project / '.git/info/exclude').read_text())

    def test_skill_ignore_checks_accept_the_installed_state_link(self):
        self.assertEqual(self.run_install(), 0)
        commands = set()
        for skill in skill_dirs():
            commands.update(re.findall(r'`(git check-ignore -q [^`]+)`', (skill / 'SKILL.md').read_text()))
        self.assertTrue(commands, 'skills check that development/ is ignored')
        for command in sorted(commands):
            with self.subTest(command=command):
                r = subprocess.run(command.split(), cwd=self.project, capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_non_git_project_is_rejected_without_changes(self):
        project = self.tmp / 'not-git'
        project.mkdir()
        self.assertEqual(self.run_install('--project', str(project)), 2)
        self.assertFalse((project / '.agents').exists())

    def test_git_worktree_uses_its_exclude_file(self):
        git = ['git', '-C', str(self.project)]
        (self.project / 'tracked').write_text('x\n')
        subprocess.run([*git, 'add', 'tracked'], check=True)
        subprocess.run([*git, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], check=True)
        worktree = self.tmp / 'worktree'
        subprocess.run([*git, 'worktree', 'add', '-q', '-b', 'test-worktree', str(worktree)], check=True)
        self.assertEqual(self.run_install('--project', str(worktree)), 0)
        status = subprocess.run(['git', '-C', str(worktree), 'status', '--porcelain'],
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(status, '')
        self.assertEqual((worktree / 'development').resolve(), (self.project / '.git/wf-state').resolve())
        self.assertTrue((worktree / 'development').is_symlink())

    def test_state_is_shared_between_worktrees_and_preserved_on_uninstall(self):
        self.assertEqual(self.run_install(), 0)
        (self.project / 'development' / 'marker').write_text('shared')
        git = ['git', '-C', str(self.project)]
        (self.project / 'tracked').write_text('x\n')
        subprocess.run([*git, 'add', 'tracked'], check=True)
        subprocess.run([*git, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], check=True)
        worktree = self.tmp / 'worktree-state'
        subprocess.run([*git, 'worktree', 'add', '-q', '-b', 'state-worktree', str(worktree)], check=True)
        self.assertEqual(self.run_install('--project', str(worktree)), 0)
        self.assertEqual((worktree / 'development' / 'marker').read_text(), 'shared')
        self.assertEqual(self.run_install('--uninstall'), 0)
        self.assertEqual((self.project / 'development' / 'marker').read_text(), 'shared')

    def test_existing_development_directory_blocks_install(self):
        (self.project / 'development').mkdir()
        (self.project / 'development' / 'keep').write_text('private')
        self.assertEqual(self.run_install(), 2)
        self.assertEqual((self.project / 'development' / 'keep').read_text(), 'private')
        self.assertFalse((self.project / '.claude/skills/wf-plan').exists())

    def test_opencode_links_install(self):
        self.assertEqual(self.run_install('--agents', 'opencode'), 0)
        self.assertTrue((self.project / '.opencode/skills/wf-plan').is_symlink())

    def test_legacy_user_uninstall_removes_only_current_repo_links(self):
        own = self.user_dests()[0] / 'wf-plan'
        foreign = self.user_dests()[1] / 'wf-plan'
        own.parent.mkdir(parents=True)
        foreign.parent.mkdir(parents=True)
        own.symlink_to(SKILLS / 'wf-plan')
        foreign.symlink_to(self.tmp / 'gone')
        self.assertEqual(self.run_install('--uninstall-user', project=False), 0)
        self.assertFalse(own.is_symlink())
        self.assertTrue(foreign.is_symlink())


FAKE_AGENT = r'''#!/bin/sh
# Fake agent CLI: records its argv, consumes stdin, acts according to $FAKE_MODE.
name=$(basename "$0")
printf '%s\n' "$*" > "$FAKE_LOG/$name.args"
cat > "$FAKE_LOG/$name.stdin"
out=
prev=
for a in "$@"; do [ "$prev" = "-o" ] && out=$a; prev=$a; done
body='## Round 1 — now
- Verdict: Approved
- Must-fix open: 0'
case ${FAKE_MODE:-ok} in
  fail) echo boom >&2; exit 1 ;;
  empty) exit 0 ;;
  modify) echo changed >> tracked.txt ;;
  record) echo 'Status: Approved' >> development/tasks/TASK-1/TASK-1.md ;;
  fence) body=$(printf '```markdown\n%s\n```' "$body") ;;
esac
if [ -n "$out" ]; then printf '%s\n' "$body" > "$out"; echo progress; else printf '%s\n' "$body"; fi
'''


class RunAgent(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / 'repo'
        self.bin = self.tmp / 'bin'
        self.logs = self.tmp / 'logs'
        for p in (self.repo, self.bin, self.logs):
            p.mkdir()
        git = ['git', '-C', str(self.repo)]
        subprocess.run([*git, 'init', '-q'], check=True)
        (self.repo / 'tracked.txt').write_text('v1\n')
        subprocess.run([*git, 'add', 'tracked.txt'], check=True)
        subprocess.run([*git, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'init'], check=True)
        for name in ('claude', 'codex', 'gemini', 'opencode'):
            f = self.bin / name
            f.write_text(FAKE_AGENT)
            f.chmod(0o755)
        self.prompt = self.tmp / 'prompt.md'
        self.prompt.write_text('Run the stage.\n')
        self.out = self.tmp / 'out.md'

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_agent(self, *args: str, mode: str = 'ok', path: str | None = None, **env: str):
        full_env = {
            **os.environ,
            'PATH': path or f'{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin',
            'FAKE_MODE': mode,
            'FAKE_LOG': str(self.logs),
            **env,
        }
        return subprocess.run(['sh', str(RUN_AGENT), *args], cwd=self.repo, env=full_env,
                              capture_output=True, text=True)

    def args_of(self, agent: str) -> str:
        return (self.logs / f'{agent}.args').read_text()

    def test_syntax(self):
        subprocess.run(['sh', '-n', str(RUN_AGENT)], check=True)
        if shutil.which('shellcheck'):
            subprocess.run(['shellcheck', str(RUN_AGENT)], check=True)

    def test_claude_read_only(self):
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('- Verdict: Approved', self.out.read_text())
        args = self.args_of('claude')
        self.assertIn('--permission-mode dontAsk', args)
        self.assertIn('--disallowedTools Edit,Write,NotebookEdit', args)
        self.assertEqual((self.logs / 'claude.stdin').read_text(), 'Run the stage.\n')

    def test_codex_read_write_writes_last_message(self):
        r = self.run_agent('codex', 'rw', str(self.prompt), str(self.out), WF_AGENT_MODEL='m1')
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.args_of('codex')
        self.assertIn('exec -s workspace-write --ephemeral', args)
        self.assertIn(f'-o {self.out}', args)
        self.assertIn('-m m1', args)
        self.assertIn('- Verdict:', self.out.read_text())
        self.assertIn('progress', Path(f'{self.out}.log').read_text())

    def test_gemini_plan_mode(self):
        r = self.run_agent('gemini', 'ro', str(self.prompt), str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.args_of('gemini')
        self.assertIn('--skip-trust', args)
        self.assertIn('--approval-mode plan', args)

    def test_opencode_model_and_effort_handling(self):
        r = self.run_agent('opencode', 'ro', str(self.prompt), str(self.out),
                           WF_AGENT_MODEL='provider/model', WF_AGENT_EFFORT='high')
        self.assertEqual(r.returncode, 0, r.stderr)
        args = self.args_of('opencode')
        self.assertIn('run --agent plan --model provider/model --variant high', args)
        self.assertIn(f'--file {self.prompt}', args)
        self.assertNotIn('Run the stage.', args)

    def test_effort_is_passed_to_claude_and_codex(self):
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out), WF_AGENT_EFFORT='high')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('--effort high', self.args_of('claude'))
        r = self.run_agent('codex', 'ro', str(self.prompt), str(self.out), WF_AGENT_EFFORT='xhigh')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('-c model_reasoning_effort="xhigh"', self.args_of('codex'))
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out))
        self.assertNotIn('--effort', self.args_of('claude'), 'unset means the CLI default')

    def test_effort_is_rejected_for_gemini_and_when_malformed(self):
        r = self.run_agent('gemini', 'ro', str(self.prompt), str(self.out), WF_AGENT_EFFORT='high')
        self.assertEqual(r.returncode, 2)
        self.assertIn('no effort setting', r.stderr)
        self.assertFalse((self.logs / 'gemini.args').exists(), 'gemini must not run')
        r = self.run_agent('codex', 'ro', str(self.prompt), str(self.out), WF_AGENT_EFFORT='high"\nx=1')
        self.assertEqual(r.returncode, 2)
        self.assertFalse((self.logs / 'codex.args').exists(), 'codex must not run')

    def test_read_only_run_that_modifies_tree_fails(self):
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out), mode='modify')
        self.assertEqual(r.returncode, 3)

    def test_expect_clean(self):
        r = self.run_agent('codex', 'rw', str(self.prompt), str(self.out), mode='modify')
        self.assertEqual(r.returncode, 0, 'rw may modify the tree')
        subprocess.run(['git', '-C', str(self.repo), 'checkout', '-q', '--', 'tracked.txt'], check=True)
        r = self.run_agent('--expect-clean', 'codex', 'rw', str(self.prompt), str(self.out), mode='modify')
        self.assertEqual(r.returncode, 3)

    def test_failures(self):
        self.assertEqual(self.run_agent('claude', 'rw', str(self.prompt), str(self.out), mode='fail').returncode, 5)
        self.assertEqual(self.run_agent('claude', 'rw', str(self.prompt), str(self.out), mode='empty').returncode, 5)
        self.assertEqual(self.run_agent('claude', 'xx', str(self.prompt), str(self.out)).returncode, 2)
        self.assertEqual(self.run_agent('nope', 'ro', str(self.prompt), str(self.out)).returncode, 2)
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out), path='/usr/bin:/bin')
        self.assertEqual(r.returncode, 4)

    def test_outer_code_fence_is_stripped(self):
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out), mode='fence')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self.out.read_text().startswith('## Round 1'))
        self.assertNotIn('```', self.out.read_text())

    def test_exit_code_is_recorded_and_wait_returns_it(self):
        self.run_agent('claude', 'rw', str(self.prompt), str(self.out), mode='fail')
        self.assertEqual(Path(f'{self.out}.exit').read_text().strip(), '5')
        self.assertEqual(self.run_agent('--wait', str(self.out), '10').returncode, 5)
        self.run_agent('claude', 'ro', str(self.prompt), str(self.out))
        self.assertEqual(self.run_agent('--wait', str(self.out)).returncode, 0)

    def test_wait_times_out_while_running(self):
        r = self.run_agent('--wait', str(self.tmp / 'never.md'), '0')
        self.assertEqual(r.returncode, 124)

    def test_dry_run_prints_command(self):
        r = self.run_agent('gemini', 'rw', str(self.prompt), str(self.out),
                           WF_AGENT_DRY_RUN='1', WF_GEMINI_SANDBOX='1')
        self.assertEqual(r.returncode, 0)
        self.assertIn('gemini --skip-trust --approval-mode yolo', r.stdout)
        self.assertIn('--sandbox', r.stdout)

    def test_gemini_headless_write_requires_sandbox(self):
        r = self.run_agent('gemini', 'rw', str(self.prompt), str(self.out))
        self.assertEqual(r.returncode, 2)
        self.assertIn('WF_GEMINI_SANDBOX=1', r.stderr)

    def test_invalid_time_arguments_and_same_paths_are_rejected(self):
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out), WF_AGENT_TIMEOUT='later')
        self.assertEqual(r.returncode, 2)
        r = self.run_agent('--wait', str(self.out), 'later')
        self.assertEqual(r.returncode, 2)
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.prompt))
        self.assertEqual(r.returncode, 2)

    def test_tracked_output_path_is_rejected_without_overwrite(self):
        tracked = self.repo / 'tracked.txt'
        r = self.run_agent('claude', 'ro', str(self.prompt), str(tracked))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(tracked.read_text(), 'v1\n')

    def test_gitignored_output_path_inside_repo_is_allowed(self):
        development = self.repo / 'development'
        development.mkdir()
        (self.repo / '.git/info/exclude').write_text('/development/\n')
        output = development / 'review.md'
        r = self.run_agent('claude', 'ro', str(self.prompt), str(output))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('- Verdict: Approved', output.read_text())

    def test_output_in_shared_state_symlink_is_allowed(self):
        state = self.repo / '.git/wf-state'
        state.mkdir()
        (self.repo / 'development').symlink_to(state, target_is_directory=True)
        (self.repo / '.git/info/exclude').write_text('/development\n')
        output = self.repo / 'development/review.md'
        r = self.run_agent('claude', 'ro', str(self.prompt), str(output))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('- Verdict: Approved', output.read_text())

    def test_review_that_edits_private_records_fails(self):
        state = self.repo / '.git/wf-state'
        (state / 'tasks/TASK-1/runs').mkdir(parents=True)
        (state / 'tasks/TASK-1/TASK-1.md').write_text('Status: Draft\n')
        (self.repo / 'development').symlink_to(state, target_is_directory=True)
        (self.repo / '.git/info/exclude').write_text('/development\n')
        output = self.repo / 'development/tasks/TASK-1/runs/impl-rev-r1.out.md'
        r = self.run_agent('--expect-clean', 'claude', 'rw', str(self.prompt), str(output))
        self.assertEqual(r.returncode, 0, 'writing the run output is allowed: ' + r.stderr)
        r = self.run_agent('--expect-clean', 'claude', 'rw', str(self.prompt), str(output), mode='record')
        self.assertEqual(r.returncode, 3, r.stderr)

    def test_unignored_output_through_a_symlinked_path_is_rejected(self):
        alias = self.tmp / 'repo-alias'
        alias.symlink_to(self.repo)
        r = self.run_agent('claude', 'ro', str(self.prompt), str(alias / 'review.md'))
        self.assertEqual(r.returncode, 2)
        self.assertIn('must be gitignored', r.stderr)
        self.assertFalse((self.repo / 'review.md').exists())

    def test_missing_timeout_executable_fails_before_agent_runs(self):
        isolated = self.tmp / 'isolated-bin'
        isolated.mkdir()
        (isolated / 'claude').symlink_to(self.bin / 'claude')
        (isolated / 'git').symlink_to(shutil.which('git'))
        (isolated / 'sh').symlink_to(shutil.which('sh'))
        (isolated / 'rm').symlink_to(shutil.which('rm'))
        r = self.run_agent('claude', 'ro', str(self.prompt), str(self.out), path=str(isolated))
        self.assertEqual(r.returncode, 4)
        self.assertIn('timeout or gtimeout is required', r.stderr)


class ReviewOutputValidator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.output = self.tmp / 'round.md'

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_validator(self, text: str, *args: str):
        self.output.write_text(text)
        return subprocess.run(
            [sys.executable, str(VALIDATE_REVIEW), *args, str(self.output)],
            capture_output=True, text=True,
        )

    def test_accepts_matching_clean_round(self):
        text = '''## Round 2 — now
- Verdict: Approved
- Must-fix open: 0
- Reviewed: commit abc123 (diff: `old..abc123`)
- Reviewer: codex (fresh context: headless)

### Findings

None.
'''
        r = self.run_validator(text, '--kind', 'impl', '--round', '2', '--reviewed', 'commit abc123')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_accepts_finding_and_carried_must_fix(self):
        text = '''## Round 3 — now
- Verdict: Changes requested
- Must-fix open: 2
- Reviewed: plan revision 3

### Previous findings
| ID | Status | Note |
|---|---|---|
| P1-1 | not resolved | still wrong |

### Findings
### [P3-1] Missing test
- Severity: Major
- Location: plan § Tests
'''
        r = self.run_validator(text, '--kind', 'plan', '--round', '3',
                               '--reviewed', 'plan revision 3')
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_rejects_wrong_target_round_count_and_verdict(self):
        cases = [
            ('## Round 1 — now\n- Verdict: Approved\n- Must-fix open: 0\n'
             '- Reviewed: commit wrong\n', 'Reviewed must be'),
            ('## Round 2 — now\n- Verdict: Approved\n- Must-fix open: 0\n'
             '- Reviewed: commit abc\n', 'output must start'),
            ('## Round 1 — now\n- Verdict: Changes requested\n- Must-fix open: 0\n'
             '- Reviewed: commit abc\n', 'Changes requested'),
            ('## Round 1 — now\n- Verdict: Approved\n- Must-fix open: 1\n'
             '- Reviewed: commit abc\n', 'round describes'),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                r = self.run_validator(text, '--kind', 'impl', '--round', '1',
                                       '--reviewed', 'commit abc')
                self.assertEqual(r.returncode, 2)
                self.assertIn(message, r.stderr)

    def test_code_blocks_in_findings_are_allowed(self):
        text = '''## Round 1 — now
- Verdict: Changes requested
- Must-fix open: 1
- Reviewed: commit abc1234

### Findings
### [I1-1] Shell injection
- Severity: Blocker
- Evidence:
```python
# user input reaches the shell
subprocess.run(cmd, shell=True)
```
'''
        r = self.run_validator(text, '--kind', 'impl', '--round', '1', '--reviewed', 'commit abc1234')
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.run_validator(text + '\n## Round 2 — later\n', '--kind', 'impl', '--round', '1',
                               '--reviewed', 'commit abc1234')
        self.assertEqual(r.returncode, 2)
        self.assertIn('exactly one round section', r.stderr)

    def test_full_and_abbreviated_sha_match(self):
        full = 'ba25a5685ef418951551d36ef2806e002cab853e'
        text = ('## Round 1 — now\n- Verdict: Approved\n- Must-fix open: 0\n'
                f'- Reviewed: {{}} (diff: `main...{full}`)\n')
        for written, expected, code in [(f'commit {full}', 'commit ba25a56', 0),
                                        ('commit ba25a56', f'commit {full}', 0),
                                        (f'commit {full}', 'commit ba25a57', 2)]:
            with self.subTest(written=written, expected=expected):
                r = self.run_validator(text.format(written), '--kind', 'impl', '--round', '1',
                                       '--reviewed', expected)
                self.assertEqual(r.returncode, code, r.stderr)

    def test_not_resolved_status_may_carry_a_note(self):
        text = '''## Round 2 — now
- Verdict: Changes requested
- Must-fix open: 1
- Reviewed: plan revision 2

### Previous findings
| ID | Status | Note |
|---|---|---|
| P1-1 | not resolved — only half done | |
| P1-2 | resolved | |
'''
        r = self.run_validator(text, '--kind', 'plan', '--round', '2', '--reviewed', 'plan revision 2')
        self.assertEqual(r.returncode, 0, r.stderr)


def answers(url: str) -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2):
            return True
    except urllib.error.HTTPError:
        return True
    except OSError:
        return False


def process_gone(pid: int, wait: float = 5.0) -> bool:
    """True once the process no longer exists or is only a zombie waiting for its new parent."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        stat = subprocess.run(['ps', '-o', 'stat=', '-p', str(pid)], capture_output=True, text=True).stdout
        if not stat.strip() or stat.strip().startswith('Z'):
            return True
        time.sleep(0.1)
    return False


class DevServer(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())  # not a git repository: start/stop state is keyed by this dir
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            self.port = s.getsockname()[1]
        self.url = f'http://127.0.0.1:{self.port}/'
        # The shell stays the group leader and the server is its child, as with npm or a reloader.
        self.server = (f'{sys.executable} -m http.server {self.port} --bind 127.0.0.1 & '
                       'echo $! > server.pid; wait')

    def tearDown(self):
        self.helper('stop')
        shutil.rmtree(self.tmp)

    def helper(self, *args: str, timeout: float = 60):
        env = {**os.environ, 'TMPDIR': str(self.tmp)}  # start/stop state and logs stay inside self.tmp
        return subprocess.run([sys.executable, str(DEV_SERVER), *args], cwd=self.tmp, env=env,
                              capture_output=True, text=True, timeout=timeout)

    def fetch(self) -> list[str]:
        return [sys.executable, '-c', 'import sys, urllib.request as u; '
                'u.build_opener(u.ProxyHandler({})).open(sys.argv[1], timeout=5)', self.url]

    def server_pid(self) -> int:
        return int((self.tmp / 'server.pid').read_text())

    def test_run_passes_and_stops_the_server_with_its_children(self):
        r = self.helper('run', '--url', self.url, '--server', self.server, '--', *self.fetch())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(process_gone(self.server_pid()))
        self.assertFalse(answers(self.url))

    def test_run_returns_the_test_exit_code_and_stops_the_server(self):
        r = self.helper('run', '--url', self.url, '--server', self.server, '--',
                        sys.executable, '-c', 'raise SystemExit(7)')
        self.assertEqual(r.returncode, 7)
        self.assertIn('tests failed with code 7', r.stderr)
        self.assertTrue(process_gone(self.server_pid()))

    def test_url_that_already_answers_runs_nothing(self):
        other = subprocess.Popen([sys.executable, '-m', 'http.server', str(self.port), '--bind', '127.0.0.1'],
                                 cwd=self.tmp, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 10
            while not answers(self.url) and time.monotonic() < deadline:
                time.sleep(0.1)
            marker = self.tmp / 'tests-ran'
            r = self.helper('run', '--url', self.url, '--server', 'true', '--', 'touch', str(marker))
            self.assertEqual(r.returncode, 2)
            self.assertIn('already answers', r.stderr)
            self.assertFalse(marker.exists())
            self.assertEqual(self.helper('start', '--url', self.url, '--server', 'true').returncode, 2)
        finally:
            other.terminate()
            other.wait()

    def test_server_that_exits_early_fails_with_its_log(self):
        r = self.helper('run', '--url', self.url, '--server', 'echo boom; exit 3', '--', 'true')
        self.assertEqual(r.returncode, 3)
        self.assertIn('boom', r.stderr)

    def test_server_that_never_answers_times_out_and_is_stopped(self):
        r = self.helper('run', '--url', self.url, '--timeout', '1', '--server',
                        'sleep 30 & echo $! > server.pid; wait', '--', 'true')
        self.assertEqual(r.returncode, 124)
        self.assertTrue(process_gone(self.server_pid()))

    def test_start_and_stop(self):
        r = self.helper('start', '--url', self.url, '--server', self.server)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(answers(self.url))
        again = self.helper('start', '--url', self.url, '--server', self.server)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(self.helper('stop').returncode, 0)
        self.assertTrue(process_gone(self.server_pid()))
        self.assertFalse(answers(self.url))
        self.assertEqual(self.helper('stop').returncode, 0, 'stop is idempotent')

    def test_usage_errors(self):
        self.assertEqual(self.helper('run', '--url', self.url, '--server', 'true').returncode, 2)
        self.assertEqual(self.helper('run', '--url', 'localhost:1', '--server', 'true', '--', 'true').returncode, 2)
        self.assertEqual(self.helper('stop', '--', 'true').returncode, 2)


@contextlib.contextmanager
def _env(**values):
    saved = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def mock_env(**values):
    return _env(**values)


if __name__ == '__main__':
    unittest.main()
