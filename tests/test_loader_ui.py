"""Windows-only checks of real Tk controls and logical DPI layouts."""
import ctypes
import importlib.util
import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

if os.name == 'nt':
    spec = importlib.util.spec_from_file_location('efir_ui_launcher', Path(__file__).resolve().parents[1]/'installer'/'launcher.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    try: ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4 & ((1 << (ctypes.sizeof(ctypes.c_void_p)*8))-1)))
    except (AttributeError, OSError): pass

@unittest.skipUnless(os.name == 'nt', 'Requires Windows and an interactive desktop')
class LoaderUITests(unittest.TestCase):
    def make_window(self, scale):
        with patch.object(m.Window, 'system_scale', return_value=scale):
            win = m.Window(autostart=False, run_loop=False)
        self.addCleanup(lambda: self.destroy(win))
        win.root.update()
        return win

    @staticmethod
    def destroy(win):
        try:
            for callback_id in win.root.tk.call('after', 'info'):
                win.root.after_cancel(callback_id)
            win.root.destroy()
        except Exception: pass

    def test_fixed_size_and_center_at_logical_scales(self):
        for scale in (1.0, 1.25, 1.5):
            with self.subTest(scale=scale):
                win = self.make_window(scale)
                w,h = win.p(500), win.p(310)
                self.assertEqual((win.root.winfo_width(),win.root.winfo_height()),(w,h))
                sw,sh = win.root.winfo_screenwidth(),win.root.winfo_screenheight()
                self.assertLessEqual(abs(win.root.winfo_x() - max(0,(sw-w)//2)),2)
                self.assertLessEqual(abs(win.root.winfo_y() - max(0,(sh-h)//2)),2)
                self.assertEqual(win.root.resizable(),(0,0))
                self.destroy(win)

    def test_progress_states_and_error(self):
        win=self.make_window(1.0)
        win.emit('heading','Загрузка файлов…');win.emit('status','Загружено 6 из 10 МБ');win.emit('progress',.62);win.poll()
        for _ in range(14): win.animate()
        self.assertEqual(win.canvas.itemcget(win.percent,'text'),'62%')
        self.assertGreater(win.display_progress,0)
        self.assertLessEqual(win.display_progress, .62)
        self.assertEqual(win.canvas.itemcget(win.heading,'text'),'Загрузка файлов…')
        self.assertEqual(win.canvas.itemcget(win.status,'text'),'Загружено 6 из 10 МБ')
        # Screenshot is a simulated 62% state, not an actual download.
        try:
            from PIL import ImageGrab
            win.root.update()
            x,y=win.root.winfo_rootx(),win.root.winfo_rooty()
            Path('ui-preview').mkdir(exist_ok=True)
            ImageGrab.grab(bbox=(x,y,x+win.root.winfo_width(),y+win.root.winfo_height()),all_screens=True).save('ui-preview/installer-simulated.png')
        except Exception as exc:
            print('Screenshot unavailable:',exc)
        win.emit('indeterminate',None);win.poll();win.animate()
        self.assertEqual(win.canvas.itemcget(win.percent,'text'),'')
        self.assertTrue(all(win.canvas.itemcget(p,'state')=='hidden' for p in win.bar_parts))
        win.emit('error',('Ошибка подключения','Тестовая ошибка'));win.poll()
        self.assertTrue(win.done)
        self.assertEqual(win.canvas.itemcget(win.heading,'text'),'Ошибка подключения')
        self.assertEqual(win.canvas.itemcget(win.status,'text'),'Тестовая ошибка')

    def test_close_and_minimize_buttons(self):
        win=self.make_window(1.0)
        print('UI test: created', flush=True)
        with patch.object(m.messagebox,'showinfo') as info:
            win.canvas.event_generate('<Motion>',x=478,y=19);win.canvas.event_generate('<Button-1>',x=478,y=19);win.root.update()
            print('UI test: active close', flush=True)
            info.assert_called_once()
            self.assertTrue(win.root.winfo_exists())
        win.canvas.event_generate('<Motion>',x=444,y=19);win.canvas.event_generate('<Button-1>',x=444,y=19);win.root.update()
        print('UI test: minimized', flush=True)
        self.assertEqual(win.root.state(),'iconic')
        win.root.deiconify();win.root.update()
        self.assertEqual(win.root.state(),'normal')
        print('UI test: restored', flush=True)
        win.done=True
        win.canvas.event_generate('<Motion>',x=478,y=19);win.canvas.event_generate('<Button-1>',x=478,y=19)
        win.root.update()
        print('UI test: clicked final close', flush=True)
        self.assertFalse(win.root.tk.call('info','commands','winfo'))

if __name__=='__main__': unittest.main()
