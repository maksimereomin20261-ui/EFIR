import importlib.util
import tempfile
import sys
import types
import unittest
import zipfile
from pathlib import Path
try:
    import tkinter
except ModuleNotFoundError:
    tk = types.ModuleType('tkinter')
    tk.messagebox = types.ModuleType('tkinter.messagebox')
    sys.modules['tkinter'] = tk
    sys.modules['tkinter.messagebox'] = tk.messagebox
spec = importlib.util.spec_from_file_location('efir_installer', Path(__file__).with_name('launcher.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class ArchiveTests(unittest.TestCase):
    def test_rejects_paths_and_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp)/'bad.zip'
            for name in ['../escape', '_internal/../../escape', '_internal\\evil', 'data/binder_data.json']:
                with self.subTest(name=name):
                    with zipfile.ZipFile(archive, 'w') as z:
                        for required in m.REQUIRED_FILES: z.writestr(required, 'ok')
                        item = zipfile.ZipInfo(name)
                        item.filename = name  # Preserve backslashes on Windows as a hostile ZIP entry.
                        z.writestr(item, 'bad')
                    with self.assertRaises(ValueError): m.extract_package(archive, Path(tmp)/'out')
            with zipfile.ZipFile(archive, 'w') as z: z.writestr('Efir.exe','ok')
            with self.assertRaises(ValueError): m.extract_package(archive, Path(tmp)/'missing')

if __name__ == '__main__': unittest.main()
