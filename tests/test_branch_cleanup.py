"""Offline validation of the one-shot cleanup. These tests never run Git writes."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('cleanup', Path(__file__).resolve().parents[1]/'scripts/cleanup_merged_branches.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
MAIN, HEAD = 'a'*40, 'b'*40


class CleanupTests(unittest.TestCase):
    def heads(self):
        return {'main': MAIN, **MODULE.PRIOR_HEADS, MODULE.ACTIVE: HEAD}

    def test_all_approved_heads_are_included_and_main_never_targeted(self):
        plan = MODULE.validate_plan(self.heads(), MAIN, [MODULE.BASE, HEAD], lambda *_: True)
        self.assertEqual(len(plan), 4)
        self.assertNotIn('main', plan)
        args = MODULE.push_arguments(plan)
        self.assertIn('--atomic', args)
        for branch, sha in plan.items():
            self.assertIn(f'--force-with-lease=refs/heads/{branch}:{sha}', args)
            self.assertIn(f':refs/heads/{branch}', args)

    def test_unmerged_or_moved_branch_blocks_entire_plan(self):
        with self.assertRaises(ValueError):
            MODULE.validate_plan(self.heads(), MAIN, [MODULE.BASE, HEAD], lambda *_: False)
        heads = self.heads(); heads[MODULE.ACTIVE] = 'c'*40
        with self.assertRaises(ValueError):
            MODULE.validate_plan(heads, MAIN, [MODULE.BASE, HEAD], lambda *_: True)

    def test_new_branch_or_changed_main_blocks_cleanup(self):
        for delta in ({'new-work': HEAD}, {'main': 'c'*40}):
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                MODULE.validate_plan({**self.heads(), **delta}, MAIN, [MODULE.BASE, HEAD], lambda *_: True)

    def test_wrong_merge_and_forbidden_target_fail_closed(self):
        with self.assertRaises(ValueError):
            MODULE.validate_plan(self.heads(), MAIN, ['c'*40, HEAD], lambda *_: True)
        for plan in ({'main': MAIN}, {'new-work': HEAD}, {MODULE.ACTIVE: 'invalid'}):
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                MODULE.push_arguments(plan)

    def test_safe_rerun_accepts_already_removed_refs(self):
        self.assertEqual(MODULE.validate_plan({'main': MAIN}, MAIN, [MODULE.BASE, HEAD], lambda *_: True), {})

    def test_workflow_condition_is_a_literal_block_and_pr_validation_is_enabled(self):
        source = (Path(__file__).resolve().parents[1]/'.github/workflows/main-only-cleanup.yml').read_text(encoding='utf-8')
        self.assertIn("if: >-\n      github.event_name == 'push'", source)
        self.assertIn("startsWith(github.event.head_commit.message, '" + MODULE.TITLE + "')", source)
        self.assertIn('  pull_request:', source)
        self.assertIn("if: github.event_name == 'pull_request'", source)
        self.assertIn('python -m unittest discover -s tests -p test_branch_cleanup.py -v', source)
