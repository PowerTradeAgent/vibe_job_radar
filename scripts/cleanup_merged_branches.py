"""One-shot, user-approved issue #22 branch cleanup. Default is validation-only.

Only the recorded redirect-recovery branch qualifies. All heads must be retained by main.
Atomic Git deletion with exact leases rejects concurrently moved branches.
This is repository maintenance, never part of the installed application.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path

REPOSITORY = 'saksim/vibe_job_radar'
BASE = '711872ef8df13568aa5f6cdd3fdde419e7bac616'
ACTIVE = 'fix/redirect-recovery-real-example'
PRIOR_HEADS = {}
TITLE = 'Merge approved redirect recovery and real example (issue 22)'


def git(args, env):
    return subprocess.check_output(['git', *args], env=env, text=True, encoding='utf-8', stderr=subprocess.PIPE).strip()


def remote_heads(env):
    rows = git(['ls-remote', '--heads', 'origin'], env).splitlines()
    return {ref.removeprefix('refs/heads/'): sha for sha, ref in (row.split('\t', 1) for row in rows)}


def validate_plan(heads, main_sha, parents, ancestor):
    if len(parents) != 2 or parents[0] != BASE:
        raise ValueError('Not the approved merge on the recorded main baseline')
    if heads.get('main') != main_sha:
        raise ValueError('main moved; refuse deletion')
    expected = {**PRIOR_HEADS, ACTIVE: parents[1]}
    unknown = set(heads) - set(expected) - {'main'}
    if unknown:
        raise ValueError('Unrecorded branch exists; refuse deletion')
    plan = {}
    for branch, sha in expected.items():
        if branch not in heads:
            continue
        if heads[branch] != sha or not ancestor(sha, main_sha):
            raise ValueError('Branch moved or is not fully included in main: ' + branch)
        plan[branch] = sha
    return plan


def push_arguments(plan):
    if 'main' in plan or set(plan) - (set(PRIOR_HEADS) | {ACTIVE}):
        raise ValueError('Ref outside the approved cleanup scope')
    if any(not re.fullmatch('[a-f0-9]{40}', sha) for sha in plan.values()):
        raise ValueError('Invalid expected SHA')
    return ['push', '--atomic',
            *[f'--force-with-lease=refs/heads/{branch}:{sha}' for branch, sha in plan.items()],
            'origin', *[f':refs/heads/{branch}' for branch in plan]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    report = {'success': False, 'applied': False, 'deleted': {}, 'before': {}, 'after': {}}
    output = Path('branch-cleanup-audit.json')
    try:
        if (os.environ.get('GITHUB_REPOSITORY') != REPOSITORY or os.environ.get('GITHUB_EVENT_NAME') != 'push'
                or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
            raise ValueError('Run only for the approved repository main push')
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        if event.get('head_commit', {}).get('message', '').split('\n')[0] != TITLE:
            raise ValueError('This is not the explicitly approved cleanup merge')
        main_sha = event['after']
        if not re.fullmatch('[a-f0-9]{40}', main_sha):
            raise ValueError('Invalid merge SHA')
        env = dict(os.environ)
        token = env.pop('GH_TOKEN', '')
        if not token:
            raise ValueError('Missing repository maintenance credential')
        # Credential stays in the process environment, not Git config/files/URLs/arguments.
        auth = base64.b64encode(('x-access-token:' + token).encode()).decode()
        env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                   GIT_CONFIG_VALUE_0='AUTHORIZATION: basic ' + auth, GIT_TERMINAL_PROMPT='0')
        if git(['remote', 'get-url', 'origin'], env) != 'https://github.com/' + REPOSITORY:
            if git(['remote', 'get-url', 'origin'], env) != 'https://github.com/' + REPOSITORY + '.git':
                raise ValueError('Unexpected remote URL')
        if git(['rev-parse', 'HEAD'], env) != main_sha:
            raise ValueError('Checkout does not match approved merge')
        parents = git(['show', '-s', '--format=%P', 'HEAD'], env).split()
        heads = remote_heads(env)
        report.update(main_sha=main_sha, before=heads)
        def ancestor(child, parent):
            return subprocess.run(['git', 'merge-base', '--is-ancestor', child, parent], env=env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        plan = validate_plan(heads, main_sha, parents, ancestor)
        report['validated'] = plan
        if args.apply and plan:
            git(push_arguments(plan), env)
            report.update(applied=True, deleted=plan)
        after = remote_heads(env)
        report['after'] = after
        if args.apply and after != {'main': main_sha}:
            raise ValueError('Final remote state is not main-only; inspect audit')
        report['success'] = True
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        report['error_type'] = type(exc).__name__
        if isinstance(exc, ValueError):
            report['error'] = str(exc)
    finally:
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
