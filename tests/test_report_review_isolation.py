"""Same requirement IDs in different reports must never share review decisions."""
import base64
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from vibe_job_radar.evidence_ui import EvidenceService, Conflict
from vibe_job_radar.synthesis import load_candidate
from vibe_job_radar.workspace import Workspace
from test_workbench import capture


class ReportReviewIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.w = Workspace(self.temp.name)
        self.w.add_job(capture())
        self.a, self.b = self.w.analyze({})['id'], self.w.analyze({})['id']
        self.e = EvidenceService(self.w)
        self.r = next(row for row in self.e.catalogue({'run_id': self.a})['rows']
                      if row['review_status'] == 'rule_accepted')
        self.rid = self.r['requirement_id']

    def review(self, run_id, decision):
        return self.e.review({'run_id': run_id, 'requirement_id': self.rid,
            'decision': decision, 'reviewer': 'Test only', 'reason': 'Artificial test',
            'expected_revision': self.e.state()['revision']})

    def saved(self, run_id):
        return next(r for r in self.e.catalogue({'run_id': run_id})['rows']
                    if r['requirement_id'] == self.rid)['saved_review']

    def test_catalogue_does_not_leak_review_to_same_id_in_other_report(self):
        self.review(self.a, 'approve')
        self.assertIsNone(self.saved(self.b))
        self.review(self.b, 'reject')
        self.assertEqual(self.saved(self.a)['decision'], 'approve')
        self.assertEqual(self.saved(self.b)['decision'], 'reject')
        self.assertEqual(len(self.e.state()['reviews']), 2)

    def test_personal_generation_uses_the_same_report_review_as_catalogue(self):
        self.review(self.a, 'approve')
        self.review(self.b, 'reject')
        for run_id, expected in ((self.a, 'approved'), (self.b, 'rejected')):
            report = self.e.generate({'run_id': run_id, 'expected_revision': 2})
            row = next(r for r in report['requirements'] if r['requirement_id'] == self.rid)
            self.assertEqual(row['review_status'], expected)
            reviews = json.loads(self.w.report_file(report['id'], 'reviews.effective.json').read_text(encoding='utf-8'))
            self.assertEqual(reviews[self.rid]['source_run_id'], run_id)

    def test_pending_and_restart_preserve_other_report_and_history(self):
        self.review(self.a, 'approve')
        self.review(self.b, 'reject')
        self.review(self.b, 'pending')
        self.e = EvidenceService(self.w)
        self.assertEqual(self.saved(self.a)['decision'], 'approve')
        self.assertEqual(self.saved(self.b)['decision'], 'pending')
        self.assertEqual(len(self.e.state()['history']), 3)

    def test_old_revision_still_conflicts(self):
        self.review(self.a, 'approve')
        with self.assertRaises(Conflict):
            self.e.review({'run_id': self.b, 'requirement_id': self.rid, 'decision': 'reject',
                'reviewer': 'test', 'reason': 'test', 'expected_revision': 0})
        self.assertIsNone(self.saved(self.b))

    def test_legacy_entry_is_read_then_migrated_only_when_edited(self):
        legacy = {'name': 'Test', 'evidence': [], 'reviews': {self.rid: {
            'source_run_id': self.a, 'decision': 'approve', 'reviewer': 'Old reviewer', 'reason': 'fixture'}}}
        original = json.dumps(legacy)
        with closing(self.e.connect()) as c:
            c.execute('INSERT INTO revisions VALUES(1,?,?,?)', (original, 'legacy-test', '2026-09-14T00:00:00Z'))
            c.commit()
        self.review(self.b, 'reject')
        self.assertEqual(self.saved(self.a)['decision'], 'approve')
        self.review(self.a, 'pending')
        self.assertEqual(self.saved(self.b)['decision'], 'reject')
        self.assertNotIn(self.rid, self.e.state()['reviews'])
        with closing(self.e.connect()) as c:
            self.assertEqual(c.execute('SELECT body FROM revisions WHERE revision=1').fetchone()[0], original)

    def test_multireport_export_preserves_distinct_cli_files(self):
        self.review(self.a, 'approve')
        self.review(self.b, 'reject')
        with zipfile.ZipFile(io.BytesIO(self.e.export())) as z:
            self.assertNotIn('reviews.json', z.namelist())
            for run_id, decision in ((self.a, 'approve'), (self.b, 'reject')):
                reviews = json.loads(z.read(f'reports/{run_id}/reviews.json'))
                self.assertEqual(reviews[self.rid]['decision'], decision)
            self.assertEqual(set(json.loads(z.read('reviews.by_report.json'))), {self.a, self.b})

    def test_single_report_export_retains_root_cli_compatibility(self):
        self.review(self.a, 'approve')
        with zipfile.ZipFile(io.BytesIO(self.e.export())) as z:
            self.assertEqual(json.loads(z.read('reviews.json'))[self.rid]['decision'], 'approve')

    def test_scoped_candidate_attachments_are_portable(self):
        artifact = self.e.upload({'name':'proof.txt','content_base64':base64.b64encode(b'fixture only').decode(),
                                  'rights_confirmed':True})['artifact_id']
        for run_id in (self.a, self.b):
            self.e.save({'expected_revision':self.e.state()['revision'], 'name':'Test', 'project':'Fixture',
                'contribution':'Fixture only', 'scope':'offline', 'run_id':run_id, 'artifact_id':artifact,
                'capabilities':[self.r['capability']], 'requirement_ids':[self.rid], 'metrics':[],
                'review_status':'approved','reviewer':'Test', 'attested':True})
        with tempfile.TemporaryDirectory() as target, zipfile.ZipFile(io.BytesIO(self.e.export())) as z:
            z.extractall(target)
            for run_id in (self.a, self.b):
                candidate = load_candidate(Path(target)/'reports'/run_id/'candidate.json', self.w.config)
                self.assertEqual(len(candidate['evidence']), 1)
                self.assertEqual(candidate['evidence'][0]['source_run_id'], run_id)
                self.assertEqual(candidate['evidence'][0]['integrity_status'], 'file_hash_verified_content_not_audited')
