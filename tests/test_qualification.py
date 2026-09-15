"""Local evidence is bound to exact payload bytes, never treated as live/remote CI."""
from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from vibe_job_radar.qualification import fingerprint, source_files, check_source, require_local_evidence


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'src/vibe_job_radar').mkdir(parents=True)
        (self.root/'src/vibe_job_radar/_version.py').write_text('__version__ = "0.2.1"\n')
        (self.root/'pyproject.toml').write_text('version = "0.2.1"\n')
        (self.root/'scripts').mkdir()
        (self.root/'scripts/start.py').write_text('print("fixture")\n')
    def evidence(self):
        return {'schema_version':1,'kind':'local-candidate-verification','success':True,
                'source':fingerprint(self.root),'source_unchanged':True,
                'tests':{'tests_run':3,'success':True,'failures':0,'errors':0},
                'steps':[{'name':n,'returncode':0} for n in ('unit-tests','user-guide','offline-demo','source-doctor')]}
    def test_same_source_accepts(self):
        self.assertEqual(require_local_evidence(self.root,self.evidence()),fingerprint(self.root))
    def test_changed_python_source_rejects_old_report(self):
        evidence=self.evidence()
        (self.root/'scripts/start.py').write_text('print("changed")\n')
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_added_or_removed_source_rejects_old_report(self):
        evidence=self.evidence()
        (self.root/'scripts/extra.py').write_text('pass\n')
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
        (self.root/'scripts/extra.py').unlink()
        (self.root/'scripts/start.py').unlink()
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_changed_docs_also_invalidate_candidate(self):
        evidence=self.evidence();(self.root/'README.md').write_text('Changed docs')
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_runtime_data_and_environment_secrets_are_excluded(self):
        expected=fingerprint(self.root)
        (self.root/'.env').write_text('SECRET=do-not-package')
        (self.root/'jobs.sqlite').write_bytes(b'private')
        (self.root/'runs').mkdir();(self.root/'runs/user.json').write_text('{}')
        self.assertEqual(expected,fingerprint(self.root))
    def test_bytecode_and_logs_do_not_break_stability(self):
        expected=fingerprint(self.root)
        (self.root/'src/__pycache__').mkdir();(self.root/'src/__pycache__/a.pyc').write_bytes(b'x')
        (self.root/'scripts/error.log').write_text('private runtime')
        self.assertEqual(expected,fingerprint(self.root))
    def test_zero_or_boolean_test_count_cannot_qualify(self):
        for value in (0,True,-1):
            evidence=self.evidence();evidence['tests']['tests_run']=value
            with self.subTest(value=value),self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_missing_or_failed_checks_reject(self):
        for name in ('unit-tests','user-guide','offline-demo','source-doctor'):
            evidence=self.evidence()
            evidence['steps']=[s for s in evidence['steps'] if s['name']!=name]
            with self.subTest(name=name),self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_summary_true_does_not_override_test_failure(self):
        evidence=self.evidence();evidence['tests']['failures']=1
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_duplicate_steps_do_not_substitute_for_missing_check(self):
        evidence=self.evidence();evidence['steps'][0]['name']='user-guide'
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_invalid_version_and_python_are_rejected(self):
        (self.root/'pyproject.toml').write_text('version = "0.0.1"\n')
        with self.assertRaises(ValueError):check_source(self.root)
        (self.root/'pyproject.toml').write_text('version = "0.2.1"\n')
        (self.root/'scripts/start.py').write_text('def broken(')
        with self.assertRaises(SyntaxError):check_source(self.root)
    def test_all_source_python_is_parsed(self):
        self.assertEqual(check_source(self.root),{'version':'0.2.1','python_files_parsed':2})
    def test_schema_and_source_stability_are_required(self):
        for override in ({'source_unchanged':False},{'kind':'browser-test'},{'schema_version':2},{'success':False}):
            evidence={**self.evidence(),**override}
            with self.subTest(override=override),self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_source_file_symlink_is_not_packaged(self):
        target=self.root/'outside';target.write_text('secret')
        try:(self.root/'scripts/link.py').symlink_to(target)
        except OSError:self.skipTest('symlink privileges unavailable')
        with self.assertRaises(ValueError):source_files(self.root)
    def test_malformed_evidence_is_actionable_value_error(self):
        for evidence in (None, [], {'schema_version':1,'kind':'local-candidate-verification','success':True,'tests':None}):
            with self.subTest(evidence=evidence),self.assertRaises(ValueError):
                require_local_evidence(self.root,evidence)
    def test_boolean_success_codes_do_not_count_as_zero(self):
        for field in ('failures','errors'):
            evidence=self.evidence();evidence['tests'][field]=False
            with self.subTest(field=field),self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
        evidence=self.evidence();evidence['steps'][0]['returncode']=False
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_malformed_step_does_not_crash_with_attribute_error(self):
        evidence=self.evidence();evidence['steps'][0]='unexpected text'
        with self.assertRaises(ValueError):require_local_evidence(self.root,evidence)
    def test_builder_refuses_output_inside_source(self):
        path=Path(__file__).resolve().parents[1]/'scripts/build_candidate.py'
        spec=importlib.util.spec_from_file_location('candidate_output_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.ROOT=self.root
        report=self.root/'evidence.json';report.write_text(json.dumps(self.evidence()))
        with self.assertRaises(ValueError):module.build(self.root/'src/output',report)
    def test_verify_does_not_reuse_an_old_unit_report(self):
        from unittest.mock import patch
        import subprocess
        path=Path(__file__).resolve().parents[1]/'scripts/verify_candidate.py'
        spec=importlib.util.spec_from_file_location('verify_stale_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);module.ROOT=self.root
        out=self.root/'check';out.mkdir()
        (out/'unit-tests.json').write_text(json.dumps({'success':True,'tests_run':399}))
        with patch.object(module.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'')):
            result=module.verify(out)
        self.assertFalse(result['success'])
        self.assertEqual(result['tests'],{})
    def test_package_contains_evidence_and_exact_source_not_user_state(self):
        import zipfile
        path=Path(__file__).resolve().parents[1]/'scripts/build_candidate.py'
        spec=importlib.util.spec_from_file_location('candidate_builder_test',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.ROOT=self.root
        report=self.root/'evidence.json';report.write_text(json.dumps(self.evidence()))
        (self.root/'.env').write_text('private')
        archive=module.build(self.root/'out',report)
        with zipfile.ZipFile(archive) as bundle:
            names=bundle.namelist()
            self.assertNotIn('vibe-job-radar-0.2.1/.env',names)
            marker=json.loads(bundle.read('vibe-job-radar-0.2.1/CANDIDATE.json'))
            self.assertFalse(marker['live_sites_certified'])
            self.assertEqual(marker['remote_ci'],'separate_verification_required')
            self.assertEqual(marker['source'],fingerprint(self.root))
        (self.root/'scripts/start.py').write_text('changed=1\n')
        with self.assertRaises(ValueError):module.build(self.root/'out',report)


if __name__=='__main__':unittest.main()
