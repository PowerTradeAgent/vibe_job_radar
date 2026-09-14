"""Main-only entrypoints stay useful after development refs are deleted."""
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
GUIDES=["README.md","docs/FIRST_RUN.md","docs/COLLECTION_FORM_CASES.md"]

class MainDocsTests(unittest.TestCase):
    def test_first_run_contains_field_cases_and_known_interpreter(self):
        guide = (ROOT/'docs/FIRST_RUN.md').read_text(encoding='utf-8')
        for text in ('/d/code_environment/anaconda_all_css/py312/python.exe',
                     '我有职位链接：套用 URL 入门参数', '我想先搜索：套用搜索入门参数',
                     '检查填写 / 预览计划（不采集）', '正文预算0', '同一工作区'):
            self.assertIn(text, guide)

    def test_current_guides_do_not_point_to_temporary_branches(self):
        for name in GUIDES:
            text = (ROOT/name).read_text(encoding='utf-8')
            self.assertNotRegex(text, r'https://github\.com/saksim/vibe_job_radar/(?:blob|tree)/(?:feat|docs)/')
            self.assertNotIn('PR 未合并前要下载/切换到对应 PR 分支', text)
