"""Guard UTF-8 file readers without forcing the caller's process locale."""
import ast
import unittest
from pathlib import Path


class TextEncodingTests(unittest.TestCase):
    def test_project_path_text_readers_declare_encoding(self):
        root = Path(__file__).resolve().parents[1]
        missing = []
        # These are project-owned paths, including CI fixture/report readers.
        # Byte readers and explicit format-specific decoders remain supported.
        for directory in ('src', 'scripts', 'tests'):
            for path in sorted((root / directory).rglob('*.py')):
                tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
                for node in ast.walk(tree):
                    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == 'read_text'):
                        continue
                    encoding = node.args[0] if node.args else next(
                        (arg.value for arg in node.keywords if arg.arg == 'encoding'), None)
                    if encoding is None or (isinstance(encoding, ast.Constant) and encoding.value is None):
                        missing.append(f'{path.relative_to(root)}:{node.lineno}')
        self.assertEqual(missing, [], 'Text readers must declare an encoding: ' + ', '.join(missing))

    def test_handoff_fixture_writers_use_utf8(self):
        path = Path(__file__).with_name('test_collection_handoff.py')
        tree = ast.parse(path.read_text(encoding='utf-8'))
        writes = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Attribute) and node.func.attr == 'write_text']
        self.assertTrue(writes)
        for node in writes:
            encoding = node.args[1] if len(node.args) > 1 else next(
                (arg.value for arg in node.keywords if arg.arg == 'encoding'), None)
            self.assertIsInstance(encoding, ast.Constant)
            self.assertEqual(encoding.value, 'utf-8')
