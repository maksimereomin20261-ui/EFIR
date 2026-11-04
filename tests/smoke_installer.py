"""Exercise the real Windows package with isolated user data and a local release stub."""
import ctypes
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
archive = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('efir_launcher', root/'installer'/'launcher.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
user32 = ctypes.windll.user32
callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows.argtypes = [callback, ctypes.c_void_p]
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]

def window_exists(pid):
    found = []
    def visit(hwnd, _):
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        size = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(size+1)
        user32.GetWindowTextW(hwnd, title, len(title))
        if owner.value == pid and user32.IsWindowVisible(hwnd) and title.value == 'Эфир by maxon3r':
            found.append(True)
            return False
        return True
    user32.EnumWindows(callback(visit), 0)
    return bool(found)

class FakeWindow:
    def __init__(self): self.events=[]
    def emit(self, kind, value): self.events.append((kind, value))

with tempfile.TemporaryDirectory(prefix='efir-installer-smoke-') as tmp:
    local = Path(tmp)/'local'; user = Path(tmp)/'roaming'; work = local/'Efir'
    (user/'Efir').mkdir(parents=True)
    marker = user/'Efir'/'preserve-test.txt'; marker.write_text('keep', encoding='utf-8')
    processes=[]; real_popen=subprocess.Popen
    def start(*args, **kwargs):
        proc=real_popen(*args, **kwargs);processes.append(proc);return proc
    def download(url, output, size, progress):
        shutil.copyfile(archive, output);progress(size,size);return hashlib.sha256(output.read_bytes()).hexdigest()
    win=FakeWindow()
    with patch.multiple(m, ROOT=work, VERSIONS=work/'versions', STATE=work/'current.json', LAUNCHER=work/'Efir Launcher.exe'), \
         patch.dict(os.environ, {'APPDATA':str(user), 'LOCALAPPDATA':str(local)}), \
         patch.object(m, 'release_metadata', return_value=('vtest','local-test',digest,archive.stat().st_size)), \
         patch.object(m, 'download', side_effect=download), \
         patch.object(m, 'ensure_launcher_installed'), patch.object(m, 'create_shortcut'), \
         patch.object(m.subprocess, 'Popen', side_effect=start):
        t=threading.Thread(target=m.Window.worker,args=(win,),daemon=True);t.start()
        try:
            deadline=time.monotonic()+100
            while time.monotonic()<deadline and not processes and t.is_alive(): time.sleep(.2)
            if not processes: raise RuntimeError('Приложение не запущено: '+repr(win.events))
            p=processes[0]
            while time.monotonic()<deadline and p.poll() is None and not window_exists(p.pid): time.sleep(.25)
            if p.poll() is not None or not window_exists(p.pid): raise RuntimeError('Окно из установленного пакета не появилось: '+repr(win.events))
            while time.monotonic()<deadline and not any(k=='launched' for k,v in win.events): time.sleep(.1)
            if not any(k=='launched' for k,v in win.events): raise RuntimeError('Загрузчик не завершил установку: '+repr(win.events))
            assert m.current() == work/'versions'/digest/'Efir.exe'
            assert marker.read_text(encoding='utf-8') == 'keep'
            print('Пакет извлечён и проверен; изолированное окно открылось; пользовательский файл сохранён.')
        finally:
            for p in processes:
                if p.poll() is None: p.terminate(); p.wait(timeout=10)
            t.join(timeout=10)
