import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('safe_launcher', Path(__file__).with_name('launcher.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class SafeUpdate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(m, ROOT=self.root, VERSIONS=self.root/'versions', STATE=self.root/'current.json')
        self.paths.start()
        self.addCleanup(self.paths.stop)
        self.root.joinpath('data').mkdir()
        self.root.joinpath('data', 'binds.ini').write_text('personal binds')
        self.a, self.b = 'a'*64, 'b'*64
        self.archive = self.root / 'package.zip'
        with zipfile.ZipFile(self.archive, 'w') as z:
            for name in m.REQUIRED_FILES:
                z.writestr(name, 'original:' + name)
            z.writestr('_internal/testdata.bin', 'test')

    def install(self, digest):
        return m.commit_version(self.archive, digest, 'test')

    def test_corrupted_current_falls_back_to_previous(self):
        old = self.install(self.a)
        new = self.install(self.b)
        self.assertEqual(m.previous(), old)
        (new.parent/'_internal/testdata.bin').write_text('damaged')
        self.assertIsNone(m.current())
        self.assertEqual(m.previous(), old)
        self.assertEqual((self.root/'data'/'binds.ini').read_text(), 'personal binds')

    def test_repair_quarantines_without_deleting_personal_files(self):
        exe = self.install(self.a)
        (exe.parent/'_internal/testdata.bin').write_text('damaged')
        (exe.parent/'notes.txt').write_text('preserve')
        self.assertIsNone(m.current())
        fixed = m.prepare_version(self.archive, self.a)
        self.assertTrue(m.version_valid(self.a))
        self.assertEqual((fixed.parent/'_internal/testdata.bin').read_text(), 'test')
        quarantined = list(m.VERSIONS.glob('.quarantine-*'))
        self.assertEqual(len(quarantined), 1)
        self.assertEqual((quarantined[0]/'notes.txt').read_text(), 'preserve')
        self.assertEqual((self.root/'data'/'binds.ini').read_text(), 'personal binds')

    def test_bad_manifest_is_not_trusted(self):
        self.install(self.a)
        manifest = self.root/'manifests'/(self.a+'.json')
        manifest.write_text(json.dumps({'../binds.ini':'a'*64}))
        self.assertIsNone(m.current())

    def test_legacy_missing_files_cannot_be_used_as_fallback(self):
        self.install(self.a)
        (self.root/'manifests'/(self.a+'.json')).unlink()
        self.assertIsNone(m.current())  # The manifest was recorded and must not silently disappear.
        info = json.loads(m.STATE.read_text())
        info.pop('manifest_required')  # Simulate an installation predating manifests.
        m.STATE.write_text(json.dumps(info))
        self.assertIsNotNone(m.current())
        (m.VERSIONS/self.a/'_internal/python311.dll').unlink()
        self.assertIsNone(m.current())

    def test_failed_extraction_does_not_change_state_or_old_files(self):
        old = self.install(self.a)
        with zipfile.ZipFile(self.archive, 'w') as z:
            z.writestr('Efir.exe', 'incomplete')
        with self.assertRaises(ValueError):
            m.prepare_version(self.archive, self.b)
        self.assertEqual(m.current(), old)
        self.assertEqual((self.root/'data'/'binds.ini').read_text(), 'personal binds')

    def test_repair_does_not_erase_previous_reference(self):
        old = self.install(self.a)
        self.install(self.b)
        (m.VERSIONS/self.b/'_internal/python311.dll').write_text('damaged')
        m.commit_version(self.archive, self.b, 'test')
        self.assertEqual(m.previous(), old)

    def run_worker(self, metadata, process_start):
        class FakeWindow:
            def __init__(self): self.events = []
            def emit(self, kind, value): self.events.append((kind, value))
        window = FakeWindow()
        def download(url, output, size, progress):
            output.write_bytes(self.archive.read_bytes())
            progress(size, size)
            return self.b
        with patch.object(m, 'release_metadata', side_effect=metadata), \
             patch.object(m, 'download', side_effect=download), \
             patch.object(m, 'ensure_launcher_installed'), \
             patch.object(m, 'create_shortcut'), \
             patch.object(m.subprocess, 'Popen', side_effect=process_start):
            m.Window.worker(window)
        return window.events

    def test_worker_open_error_uses_old_and_does_not_commit_new(self):
        old = self.install(self.a)
        calls = []
        class Process:
            def wait(self): return 0
        def launch(args, **kwargs):
            calls.append(Path(args[0]))
            if len(calls) == 1: raise OSError('test launch failure')
            return Process()
        events = self.run_worker(lambda: ('new', 'url', self.b, 10), launch)
        self.assertEqual(calls, [m.VERSIONS/self.b/'Efir.exe', old])
        self.assertEqual(m.current(), old)
        self.assertTrue(any(k == 'warning' for k, v in events))
        self.assertFalse(any(k == 'error' for k, v in events))

    def test_worker_offline_uses_undamaged_previous(self):
        old = self.install(self.a)
        new = self.install(self.b)
        (new.parent/'_internal/python311.dll').write_text('broken')
        calls = []
        class Process:
            def wait(self): return 0
        def launch(args, **kwargs):
            calls.append(Path(args[0]))
            return Process()
        def offline(): raise OSError('offline')
        events = self.run_worker(offline, launch)
        self.assertEqual(calls, [old])
        self.assertFalse(any(k == 'error' for k, v in events))
        self.assertEqual((self.root/'data'/'binds.ini').read_text(), 'personal binds')

if __name__ == '__main__':
    unittest.main()
