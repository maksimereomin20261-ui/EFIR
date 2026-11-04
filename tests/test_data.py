"""Data tests use a temporary APPDATA, never the user's files."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_temp = tempfile.TemporaryDirectory()
os.environ['APPDATA'] = _temp.name
spec = importlib.util.spec_from_file_location('efir_app', Path(__file__).resolve().parents[1] / 'app' / 'main.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class DataTests(unittest.TestCase):
    def setUp(self):
        import shutil
        shutil.rmtree(app.APP_DIR, ignore_errors=True)

    def test_save_and_reload(self):
        data = app.load_data()
        data['settings']['active_profile'] = 'SF'
        app.save_data(data)
        result = app.load_data()
        self.assertEqual(result['settings']['active_profile'], 'SF')
        self.assertEqual(result['shortcuts'], data['shortcuts'])

    def test_broken_file_is_copied_before_recovery(self):
        data = app.load_data()
        app.save_data(data)
        app.save_data(data, make_backup=True)
        app.DATA_FILE.write_text('{bad json', encoding='utf-8')
        recovered = app.load_data()
        self.assertTrue(app.LOAD_WARNING)
        self.assertEqual(recovered['shortcuts'], data['shortcuts'])
        copies = list(app.APP_DIR.glob('binder_data.corrupted_*.json'))
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0].read_text(encoding='utf-8'), '{bad json')

if __name__ == '__main__':
    unittest.main()
