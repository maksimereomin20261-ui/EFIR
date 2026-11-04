"""Find the real application window on Windows, using isolated temporary data."""
import ctypes
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

exe = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='efir-smoke-') as tmp:
    env = dict(os.environ, APPDATA=tmp, LOCALAPPDATA=tmp)
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env)
    try:
        user32 = ctypes.windll.user32
        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        deadline = time.monotonic() + 35
        found = False
        while time.monotonic() < deadline and proc.poll() is None and not found:
            def visit(hwnd, _):
                global found
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                length = user32.GetWindowTextLengthW(hwnd)
                text = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, text, len(text))
                if pid.value == proc.pid and user32.IsWindowVisible(hwnd) and text.value == 'Эфир by maxon3r':
                    found = True
                    return False
                return True
            user32.EnumWindows(callback(visit), 0)
            if not found: time.sleep(.25)
        if not found: raise RuntimeError('Окно приложения не появилось; код процесса: %s' % proc.poll())
        print('Окно Эфира обнаружено; данные теста изолированы.')
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
