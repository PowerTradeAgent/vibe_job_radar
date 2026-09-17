"""Local comparison of two validated complete catalogs, never a vacancy status feed.

No network or persistence here. Observation time is deliberately NOT job content.
A missing ID means only 'not present in this observation', never 'closed/deleted'.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timezone

from .public_contract import ContractError

FIELDS = ('title', 'company', 'location', 'remote', 'text', 'url', 'final_url', 'completeness')
LIMIT = 10000
HASH = re.compile(r'[0-9a-f]{64}')
IDENTITY = re.compile(r'[0-9a-zA-Z_-]{1,100}')
KEYS = {'schema_version', 'source', 'scope', 'status', 'reason', 'before_revision',
        'after_revision', 'before_observed_at', 'observed_at', 'counts',
        'added', 'updated', 'missing'}
COUNT_KEYS = {'previous_total', 'current_total', 'added', 'updated', 'missing', 'unchanged'}


def invalid():
    return ContractError('local_catalog_change_invalid')


def _stamp(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise invalid()
    try:
        datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise invalid() from exc
    return value


def _index(snapshot, source):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('jobs'), list):
        raise invalid()
    if len(snapshot['jobs']) > LIMIT:
        raise invalid()
    result = {}
    for row in snapshot['jobs']:
        if (not isinstance(row, dict) or row.get('source') != source
                or not isinstance(row.get('id'), str) or not IDENTITY.fullmatch(row['id'])
                or row['id'] in result or not set(FIELDS) <= set(row)
                or not isinstance(row.get('adapter_version'), str)):
            raise invalid()
        result[row['id']] = row
    if not isinstance(snapshot.get('revision'), str) or not HASH.fullmatch(snapshot['revision']):
        raise invalid()
    _stamp(snapshot.get('observed_at'))
    return result


def compare_catalogs(previous, current, source):
    """Input snapshots have already passed their provider's full validation.

    Lack of a recent baseline, or a changed adapter, does not fabricate new jobs.
    The result is attached to the SAME atomic cache write as the new snapshot.
    """
    after = _index(current, source)
    before = _index(previous, source) if previous is not None else {}
    reason = 'no_recent_baseline'
    comparable = previous is not None
    if previous is not None:
        if previous.get('api') != current.get('api'):
            raise invalid()
        if previous['observed_at'] > current['observed_at']:
            raise invalid()
        old_versions = {row['adapter_version'] for row in before.values()}
        new_versions = {row['adapter_version'] for row in after.values()}
        if old_versions and new_versions and old_versions != new_versions:
            comparable, reason = False, 'adapter_changed'
    added, updated, missing = [], [], []
    if comparable:
        reason = 'complete_observations'
        added = sorted(after.keys() - before.keys())
        missing = sorted(before.keys() - after.keys())
        for ident in sorted(after.keys() & before.keys()):
            fields = [field for field in FIELDS if before[ident][field] != after[ident][field]]
            if fields:
                updated.append({'id': ident, 'fields': fields})
    value = {'schema_version': 1, 'source': source, 'scope': 'complete_source_catalog',
             'status': 'compared' if comparable else 'baseline', 'reason': reason,
             'before_revision': previous['revision'] if comparable else '',
             'after_revision': current['revision'],
             'before_observed_at': previous['observed_at'] if comparable else None,
             'observed_at': current['observed_at'],
             'counts': {'previous_total': len(before) if comparable else None,
                        'current_total': len(after), 'added': len(added), 'updated': len(updated),
                        'missing': len(missing),
                        'unchanged': len(after) - len(added) - len(updated) if comparable else 0},
             'added': added, 'updated': updated, 'missing': missing}
    return validate_change(value, current, source)


def validate_change(value, snapshot, source):
    """Reject malformed local audit metadata; hashes are not origin attestation."""
    current = _index(snapshot, source)
    if (not isinstance(value, dict) or set(value) != KEYS
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['source'] != source or value['scope'] != 'complete_source_catalog'
            or not isinstance(value['status'], str) or value['status'] not in {'baseline', 'compared'}
            or value['after_revision'] != snapshot['revision']
            or _stamp(value['observed_at']) != snapshot['observed_at']
            or not isinstance(value['counts'], dict) or set(value['counts']) != COUNT_KEYS):
        raise invalid()
    counts = value['counts']
    for key in COUNT_KEYS - {'previous_total'}:
        if type(counts[key]) is not int or not 0 <= counts[key] <= LIMIT:
            raise invalid()
    if counts['current_total'] != len(current):
        raise invalid()
    groups = {}
    for key in ('added', 'missing'):
        rows = value[key]
        if (not isinstance(rows, list) or len(rows) > LIMIT
                or any(not isinstance(x, str) or not IDENTITY.fullmatch(x) for x in rows)
                or rows != sorted(set(rows)) or counts[key] != len(rows)):
            raise invalid()
        groups[key] = set(rows)
    rows = value['updated']
    if not isinstance(rows, list) or len(rows) > LIMIT or counts['updated'] != len(rows):
        raise invalid()
    identities = []
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {'id', 'fields'}
                or not isinstance(row['id'], str) or not IDENTITY.fullmatch(row['id'])
                or not isinstance(row['fields'], list) or not row['fields']
                or any(not isinstance(x, str) or x not in FIELDS for x in row['fields'])
                or row['fields'] != [x for x in FIELDS if x in row['fields']]):
            raise invalid()
        identities.append(row['id'])
    if identities != sorted(set(identities)):
        raise invalid()
    groups['updated'] = set(identities)
    if (not groups['added'] <= current.keys() or not groups['updated'] <= current.keys()
            or groups['missing'] & current.keys() or groups['added'] & groups['updated']):
        raise invalid()
    if value['status'] == 'baseline':
        if (value['reason'] not in ('no_recent_baseline', 'adapter_changed')
                or value['before_revision'] != '' or value['before_observed_at'] is not None
                or counts['previous_total'] is not None
                or any(counts[key] for key in ('added', 'updated', 'missing', 'unchanged'))):
            raise invalid()
    else:
        if (value['reason'] != 'complete_observations'
                or not isinstance(value['before_revision'], str) or not HASH.fullmatch(value['before_revision'])
                or _stamp(value['before_observed_at']) > value['observed_at']
                or type(counts['previous_total']) is not int or not 0 <= counts['previous_total'] <= LIMIT
                or counts['unchanged'] + counts['added'] + counts['updated'] != len(current)
                or counts['unchanged'] + counts['missing'] + counts['updated'] != counts['previous_total']):
            raise invalid()
    return copy.deepcopy(value)


def compact_change(value):
    """Bounded local task/UI view; full ID lists live only in cache/report audit."""
    counts = copy.deepcopy(value['counts'])
    when = datetime.fromtimestamp(value['observed_at'], timezone.utc).isoformat()
    if value['status'] == 'baseline':
        reason = '适配器版本变化，重新建立基线' if value['reason'] == 'adapter_changed' else '尚无可比较的近期完整目录'
        message = f'目录变化：{reason}；本次记录 {counts["current_total"]} 条，不将首次记录算作新增。观测时间：{when}。'
    else:
        before = datetime.fromtimestamp(value['before_observed_at'], timezone.utc).isoformat()
        message = (f'目录变化（所选来源完整目录，不是本页或关键词结果）：新增 {counts["added"]} 条，'
                   f'修改 {counts["updated"]} 条，本次未出现 {counts["missing"]} 条，未变 {counts["unchanged"]} 条。'
                   f'对比 {before} → {when}。本次未出现不等于岗位已关闭；不代表持续监控。')
    return {'schema_version': 1, 'source': value['source'], 'scope': value['scope'],
            'status': value['status'], 'reason': value['reason'], 'counts': counts,
            'before_observed_at': value['before_observed_at'], 'observed_at': value['observed_at'],
            'message': message}
