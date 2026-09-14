"""User guide stays synchronized with local files and important UI labels."""
import importlib.util
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
GUIDES = ['README.md', 'docs/FIRST_RUN.md', 'docs/WORKFLOWS.md', 'docs/ACQUISITION_CAPABILITIES.md']


class UserDocsTests(unittest.TestCase):
    def test_first_run_html_is_up_to_date(self):
        spec = importlib.util.spec_from_file_location('build_user_guide', ROOT/'scripts/build_user_guide.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual((ROOT/'START_HERE.html').read_text(encoding='utf-8'), module.render())

    def test_document_local_links_resolve(self):
        for name in GUIDES:
            source = ROOT/name
            text = source.read_text(encoding='utf-8')
            text = re.sub(r'```.*?```', '', text, flags=re.S)
            for target in re.findall(r'\[[^\]]+\]\(([^)]+)\)', text):
                if urlsplit(target).scheme or target.startswith('#'):
                    continue
                path = (source.parent/target.split('#')[0]).resolve()
                with self.subTest(source=name, target=target):
                    self.assertTrue(path.is_relative_to(ROOT))
                    self.assertTrue(path.exists())

    def test_standalone_html_does_not_execute_or_fetch_resources(self):
        tags = []
        class Audit(HTMLParser):
            def handle_starttag(self, tag, attrs):
                tags.append((tag, dict(attrs)))
        Audit().feed((ROOT/'START_HERE.html').read_text(encoding='utf-8'))
        self.assertFalse(any(tag in {'script','iframe','object','embed','link','img','form'} for tag, _ in tags))
        self.assertFalse(any(key.lower().startswith('on') for _, attrs in tags for key in attrs))

    def test_documented_primary_buttons_and_launcher_exist(self):
        guide = (ROOT/'docs/FIRST_RUN.md').read_text(encoding='utf-8')
        ui = ''.join((ROOT/'src/vibe_job_radar'/name).read_text(encoding='utf-8')
                     for name in ['workbench.html', 'advanced.html'])
        for label in ['运行合成演示','保存到真实数据库','分析真实数据并生成报告','加载原文要求','保存个人证据','生成个人报告']:
            with self.subTest(label=label):
                self.assertIn(label, ui)
                self.assertIn(label, guide)
        self.assertTrue((ROOT/'scripts/start_workbench.py').is_file())
        self.assertTrue((ROOT/'start_windows.bat').is_file())
