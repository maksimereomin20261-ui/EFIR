"""Efir bootstrapper. Needs Windows; release payloads are always kept separate from data."""
import ctypes
import hashlib
import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import urllib.error
import urllib.parse
import uuid
from html.parser import HTMLParser
import zipfile
from pathlib import Path, PurePosixPath
import tkinter as tk
from tkinter import messagebox

REPO = 'maksimereomin20261-ui/EFIR'
ASSET = 'Efir-App.zip'
MAX_ARCHIVE = 150 * 1024 * 1024
MAX_EXPANDED = 700 * 1024 * 1024
ROOT = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Efir'
VERSIONS = ROOT / 'versions'
STATE = ROOT / 'current.json'
LAUNCHER = ROOT / 'Efir Launcher.exe'
HEADERS = {'User-Agent':'Efir-Launcher/1', 'Accept':'application/vnd.github+json'}


def release_metadata_api():
    url = f'https://api.github.com/repos/{REPO}/releases/latest'
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=8) as r:
        payload = r.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError('Слишком большой ответ GitHub')
    data = json.loads(payload)
    asset = next((a for a in data.get('assets', []) if a.get('name') == ASSET), None)
    if not asset:
        raise ValueError('В последнем релизе пока нет нового пакета Efir-App.zip')
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('GitHub не предоставил SHA-256 пакета')
    url = asset.get('browser_download_url','')
    if not url.startswith(f'https://github.com/{REPO}/releases/download/'):
        raise ValueError('Недопустимый адрес пакета')
    if not isinstance(asset.get('size'), int) or not 0 < asset['size'] <= MAX_ARCHIVE:
        raise ValueError('Недопустимый размер пакета')
    return data.get('tag_name', ''), url, digest[7:].lower(), asset['size']


class ReleaseAssetParser(HTMLParser):
    """Read only the requested asset URL and its published GitHub SHA-256."""
    def __init__(self, expected_path):
        super().__init__()
        self.expected_path = expected_path
        self.links = []
        self.digests = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a' and attrs.get('href') == self.expected_path:
            self.links.append(attrs['href'])
        if (tag == 'clipboard-copy' and
                attrs.get('aria-label') == f'Copy to clipboard digest for {ASSET}'):
            self.digests.append(attrs.get('value', ''))


def release_metadata_website():
    """Fallback for GitHub's unauthenticated API limits; never weakens SHA checks."""
    base = f'https://github.com/{REPO}'
    with urllib.request.urlopen(urllib.request.Request(base + '/releases/latest', headers=HEADERS, method='HEAD'), timeout=12) as response:
        resolved = urllib.parse.urlsplit(response.geturl())
    prefix = f'/{REPO}/releases/tag/'
    if (resolved.scheme != 'https' or resolved.netloc != 'github.com' or
            not resolved.path.startswith(prefix) or resolved.query or resolved.fragment):
        raise ValueError('Не удалось проверить адрес последнего релиза GitHub')
    tag = urllib.parse.unquote(resolved.path[len(prefix):])
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', tag):
        raise ValueError('Недопустимое имя релиза GitHub')
    encoded_tag = urllib.parse.quote(tag, safe='')
    asset_path = f'/{REPO}/releases/download/{encoded_tag}/{ASSET}'
    url = 'https://github.com' + asset_path
    html_url = base + '/releases/expanded_assets/' + encoded_tag
    with urllib.request.urlopen(urllib.request.Request(html_url, headers=HEADERS), timeout=12) as response:
        content = response.read(2_000_001)
    if len(content) > 2_000_000:
        raise ValueError('Слишком большой ответ GitHub')
    parser = ReleaseAssetParser(asset_path)
    parser.feed(content.decode('utf-8', errors='strict'))
    if len(parser.links) != 1 or len(parser.digests) != 1:
        raise ValueError('GitHub не предоставил единственный проверяемый пакет Efir')
    digest = parser.digests[0]
    if not re.fullmatch(r'sha256:[0-9a-fA-F]{64}', digest):
        raise ValueError('GitHub не предоставил SHA-256 пакета')
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS, method='HEAD'), timeout=20) as response:
        length = response.headers.get('Content-Length', '')
    if not length.isdecimal() or not 0 < int(length) <= MAX_ARCHIVE:
        raise ValueError('GitHub не предоставил корректный размер пакета')
    return tag, url, digest[7:].lower(), int(length)


def release_metadata():
    try:
        return release_metadata_api()
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 429) and exc.code < 500:
            raise
        return release_metadata_website()


def download(url, output, expected_size, progress):
    request = urllib.request.Request(url, headers={'User-Agent': HEADERS['User-Agent']})
    digest = hashlib.sha256(); size = 0
    with urllib.request.urlopen(request, timeout=20) as response, open(output, 'wb') as out:
        while chunk := response.read(128 * 1024):
            size += len(chunk)
            if size > expected_size or size > MAX_ARCHIVE:
                raise ValueError('Размер загрузки превышает ожидаемый')
            digest.update(chunk); out.write(chunk); progress(size, expected_size)
    if size != expected_size:
        raise ValueError('Загрузка завершилась не полностью')
    return digest.hexdigest()


def extract_package(archive, destination):
    allowed = {'Efir.exe', '_internal'}
    destination = Path(destination)
    with zipfile.ZipFile(archive) as z:
        seen = set(); expanded = 0
        for item in z.infolist():
            name = item.filename
            parts = PurePosixPath(name).parts
            if (not parts or item.orig_filename != name or name.startswith('/') or '\\' in item.orig_filename or any(x in ('.','..') or ':' in x for x in parts)
                    or parts[0] not in allowed or item.flag_bits & 1):
                raise ValueError('Недопустимый путь внутри пакета')
            key = name.rstrip('/').casefold()
            if key in seen:
                raise ValueError('Повторяющиеся имена в пакете')
            seen.add(key)
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Ссылки в пакете не допускаются')
            expanded += item.file_size
            if expanded > MAX_EXPANDED or len(seen) > 15000:
                raise ValueError('Пакет слишком большой')
        z.extractall(destination)
    for relative in ('Efir.exe', '_internal/python311.dll', '_internal/PyQt5/Qt5/bin/Qt5Core.dll', '_internal/PyQt5/Qt5/plugins/platforms/qwindows.dll'):
        if not (destination / relative).is_file():
            raise ValueError('В пакете отсутствует необходимый файл: ' + relative)


REQUIRED_FILES = ('Efir.exe', '_internal/python311.dll', '_internal/PyQt5/Qt5/bin/Qt5Core.dll', '_internal/PyQt5/Qt5/plugins/platforms/qwindows.dll')


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def version_valid(digest, require_manifest=False):
    """Check all recorded payload files; older installs without a manifest use a basic check."""
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        return False
    folder = VERSIONS / digest
    try:
        if folder.is_symlink() or getattr(folder.lstat(), 'st_file_attributes', 0) & 0x400:
            return False
        for name in REQUIRED_FILES:
            check = folder
            for part in PurePosixPath(name).parts:
                check = check / part
                if check.is_symlink() or getattr(check.lstat(), 'st_file_attributes', 0) & 0x400:
                    return False
            if not check.is_file():
                return False
        manifest = ROOT / 'manifests' / (digest + '.json')
        if not manifest.exists():
            return not require_manifest  # Legacy installs predate manifests; only basic checks are possible.
        if manifest.stat().st_size > 2_000_000:
            return False
        files = json.loads(manifest.read_text('utf-8'))
        if not isinstance(files, dict) or not files or len(files) > 15000:
            return False
        if not all(name in files for name in REQUIRED_FILES):
            return False
        for name, expected in files.items():
            if not isinstance(name, str) or not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
                return False
            parts = PurePosixPath(name).parts
            if not parts or name.startswith('/') or '\\' in name or any(x in ('.', '..') or ':' in x for x in parts):
                return False
            candidate = folder.joinpath(*parts)
            # Never follow reparse points or links, including intermediate directories.
            check = folder
            for part in parts:
                check = check / part
                if check.is_symlink() or getattr(check.lstat(), 'st_file_attributes', 0) & 0x400:
                    return False
            if not candidate.is_file() or file_hash(candidate) != expected:
                return False
        return True
    except (OSError, ValueError, TypeError, UnicodeError):
        return False


def state_info():
    try:
        value = json.loads(STATE.read_text('utf-8'))
        return value if isinstance(value, dict) else {}
    except (ValueError, OSError, UnicodeError):
        return {}


def current():
    info = state_info()
    digest = info.get('digest')
    return VERSIONS / digest / 'Efir.exe' if version_valid(digest, info.get('manifest_required') is True) else None


def previous():
    info = state_info()
    digest = info.get('previous_digest')
    return VERSIONS / digest / 'Efir.exe' if version_valid(digest, info.get('previous_manifest_required') is True) else None


def write_state(digest, tag):
    old = state_info()
    previous_digest = old.get('digest')
    previous_required = old.get('manifest_required') is True
    if previous_digest == digest or not version_valid(previous_digest, previous_required):
        previous_digest = old.get('previous_digest')
        previous_required = old.get('previous_manifest_required') is True
    if not version_valid(previous_digest, previous_required) or previous_digest == digest:
        previous_digest = None
        previous_required = False
    temp = ROOT / 'current.json.tmp'
    temp.write_text(json.dumps({'digest': digest, 'tag': tag, 'manifest_required': True,
                                'previous_digest': previous_digest, 'previous_manifest_required': previous_required}, ensure_ascii=False), 'utf-8')
    os.replace(temp, STATE)


def prepare_version(archive, digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('Недопустимый идентификатор версии')
    VERSIONS.mkdir(parents=True, exist_ok=True)
    final = VERSIONS / digest
    stage = Path(tempfile.mkdtemp(prefix='.stage-', dir=VERSIONS))
    try:
        extract_package(archive, stage)
        files = {p.relative_to(stage).as_posix(): file_hash(p) for p in stage.rglob('*') if p.is_file()}
        # Never overwrite an existing folder: even an old version may contain user data.
        if final.exists():
            matches = not final.is_symlink() and not (getattr(final.lstat(), 'st_file_attributes', 0) & 0x400)
            for name, expected in files.items():
                if not matches: break
                check = final
                for part in PurePosixPath(name).parts:
                    check = check / part
                    if not check.exists() or check.is_symlink() or getattr(check.lstat(), 'st_file_attributes', 0) & 0x400:
                        matches = False; break
                if matches and (not check.is_file() or file_hash(check) != expected): matches = False
            if not matches:
                quarantine = VERSIONS / ('.quarantine-' + digest + '-' + uuid.uuid4().hex)
                final.rename(quarantine)
                try: stage.rename(final)
                except Exception:
                    quarantine.rename(final)
                    raise
        else:
            stage.rename(final)
        manifests = ROOT / 'manifests'
        manifests.mkdir(parents=True, exist_ok=True)
        temp = manifests / (digest + '.tmp')
        temp.write_text(json.dumps(files, sort_keys=True), 'utf-8')
        os.replace(temp, manifests / (digest + '.json'))
        if not version_valid(digest):
            raise ValueError('Не удалось проверить установленные файлы')
        return final / 'Efir.exe'
    finally:
        if stage.exists(): shutil.rmtree(stage, ignore_errors=True)


def commit_version(archive, digest, tag):
    result = prepare_version(archive, digest)
    write_state(digest, tag)
    return result


def create_shortcut():
    # Windows resolves redirected/OneDrive Desktop via WScript, not USERPROFILE/Desktop.
    script = r'''$w=New-Object -ComObject WScript.Shell
$p=Join-Path $w.SpecialFolders.Item('Desktop') 'Эфир.lnk'
$s=$w.CreateShortcut($p)
$s.TargetPath=$env:EFIR_SHORTCUT_TARGET
$s.WorkingDirectory=$env:EFIR_SHORTCUT_DIR
$s.Description='Эфир'
$s.Save()'''
    env = dict(os.environ, EFIR_SHORTCUT_TARGET=str(LAUNCHER), EFIR_SHORTCUT_DIR=str(ROOT))
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
                   env=env, check=True, timeout=20, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def ensure_launcher_installed():
    if not getattr(sys, 'frozen', False):
        raise RuntimeError('Для установки требуется собранный EXE загрузчика')
    if Path(sys.executable).resolve() != LAUNCHER.resolve():
        # The mutex ensures another Efir launcher is not running during replacement.
        replacement = ROOT / 'Efir Launcher.new.exe'
        try:
            shutil.copy2(sys.executable, replacement)
            os.replace(replacement, LAUNCHER)
        finally:
            if replacement.exists():
                replacement.unlink()


class Window:
    """Compact installer UI; worker and on-disk update logic remain unchanged."""
    VERSION = '2.0'
    WIDTH, HEIGHT = 500, 310

    def __init__(self, autostart=True, run_loop=True):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title('Эфир — установка и обновление')
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        self.root.configure(bg='#010203')
        # The unique colour only appears outside the rounded window silhouette.
        try:
            self.root.wm_attributes('-transparentcolor', '#010203')
        except tk.TclError:
            pass
        self.scale = self.system_scale()
        w, h = self.p(self.WIDTH), self.p(self.HEIGHT)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = max(0, (sw-w)//2), max(0, (sh-h)//2)
        self.root.geometry(f'{w}x{h}+{x}+{y}')
        self.canvas = tk.Canvas(self.root, width=w, height=h, bg='#010203',
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill='both', expand=True)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.bind('<Escape>', lambda _event: self.close())
        self.root.bind('<Map>', self.on_map)
        self.events = queue.Queue()
        self.done = False
        self.determinate = False
        self.actual_progress = 0.0
        self.display_progress = 0.0
        self.spinner_angle = 0
        self.minimized = False
        self.text_fades = {}
        self.draw_ui()
        self.root.deiconify()
        self.root.after(50, self.poll)
        self.root.after(40, self.animate)
        if autostart:
            threading.Thread(target=self.worker, daemon=True).start()
        if run_loop:
            self.root.mainloop()

    @staticmethod
    def system_scale():
        if os.name == 'nt':
            try:
                return min(2.5, max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0))
            except (AttributeError, OSError):
                pass
        return 1.0

    def p(self, value):
        return round(value * self.scale)

    def shape(self, coords):
        return tuple(self.p(v) for v in coords)

    def rounded(self, x1, y1, x2, y2, r, fill, outline=None, width=1, **kw):
        points = (x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
                  x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1)
        return self.canvas.create_polygon(*self.shape(points), smooth=True, splinesteps=24,
                                          fill=fill, outline=outline or fill, width=self.p(width), **kw)

    def text(self, x, y, value, size, color, weight='normal', **kw):
        return self.canvas.create_text(self.p(x), self.p(y), text=value, fill=color,
                                       font=('Segoe UI', -self.p(size), weight), **kw)

    def microphone(self, cx, cy, size):
        """Vector microphone, scaled with the window rather than an icon font."""
        c = self.canvas
        k = size / 48.0
        def box(a, b, d, e):
            return self.shape((cx+a*k, cy+b*k, cx+d*k, cy+e*k))
        def line(coords, color, width):
            points = []
            for x, y in coords:
                points += [cx+x*k, cy+y*k]
            c.create_line(*self.shape(points), fill=color, width=max(1, self.p(width*k)), capstyle='round', joinstyle='round', smooth=True)
        # Outer cradle, stem and stand.
        c.create_arc(*box(-20, -8, 20, 28), start=180, extent=180, style='arc',
                     outline='#a158f5', width=max(1, self.p(4*k)))
        line([(0, 21), (0, 32)], '#a158f5', 4)
        line([(-9, 32), (9, 32)], '#a158f5', 4)
        # The body is a gently shaded, rounded capsule.
        self.rounded(cx-10*k, cy-25*k, cx+10*k, cy+15*k, 10*k, '#8a39ee', '#a657f4', max(1, 1*k))
        c.create_oval(*box(-8, -23, 8, -7), fill='#b071f7', outline='')
        c.create_rectangle(*box(-8, -15, 8, -4), fill='#a35cf5', outline='')
        c.create_oval(*box(-8, -12, 8, 13), fill='#9545ef', outline='')

    def draw_ui(self):
        c = self.canvas
        self.rounded(1, 1, 499, 309, 12, '#090617', '#713ab3', 1)
        # Subtle curved colour fields retain the reference's dark, layered background.
        c.create_polygon(*self.shape((2,39,155,39,135,52,116,73,82,100,42,119,2,122)),
                         fill='#130b29', outline='', smooth=True, splinesteps=32)
        c.create_polygon(*self.shape((2,167,35,145,66,138,94,143,120,154,83,145,46,153,2,181)),
                         fill='#0f0922', outline='', smooth=True, splinesteps=32)
        c.create_polygon(*self.shape((499,50,499,109,477,111,447,121,420,140,390,165,365,180,379,153,399,128,431,103,467,89)),
                         fill='#160c32', outline='', smooth=True, splinesteps=32)
        c.create_line(*self.shape((2,38,498,38)), fill='#2a1b43')
        self.microphone(24, 17, 15)
        self.text(49, 19, 'Эфир', 15, '#f8f5ff', 'bold', anchor='w')
        self.text(91, 19, 'by maxon3r', 10, '#9a8abf', anchor='w')
        for name, x in [('minimize', 444), ('close', 478)]:
            c.create_rectangle(*self.shape((x-16,3,x+16,35)), fill='#090617', outline='', tags=(name,))
        c.create_line(*self.shape((439,19,449,19)), fill='#c3addf', width=self.p(1), tags=('minimize',))
        c.create_line(*self.shape((473,14,483,24)), fill='#c3addf', width=self.p(1), tags=('close',))
        c.create_line(*self.shape((483,14,473,24)), fill='#c3addf', width=self.p(1), tags=('close',))
        c.tag_bind('minimize', '<Button-1>', lambda _e: self.minimize())
        c.tag_bind('close', '<Button-1>', lambda _e: self.close())
        for name in ('minimize', 'close'):
            c.tag_bind(name, '<Enter>', lambda _e: c.configure(cursor='hand2'))
            c.tag_bind(name, '<Leave>', lambda _e: c.configure(cursor=''))
        # Concentric outlines approximate a soft halo without external assets.
        self.rounded(205, 46, 295, 126, 20, '#100824', '#170b31')
        self.rounded(208, 48, 292, 124, 19, '#100821', '#271047')
        self.logo_edge = self.rounded(210, 50, 290, 122, 18, '#0c071b', '#7d31c8', 1)
        self.microphone(250, 80, 41)
        self.text(250, 143, 'Эфир', 30, '#f7f3ff', 'bold')
        self.text(250, 167, 'Умная автозамена текста', 14, '#a994d9')
        self.heading = self.text(42, 198, 'Подготовка к запуску…', 13, '#f1ecfb', anchor='w', width=self.p(390))
        self.rounded(40, 215, 423, 229, 7, '#1a0d31')
        self.rounded(42, 218, 421, 226, 4, '#211335', '#2d1b47')
        self.bar_parts = []
        for i in range(42):
            t = i / 41
            color = '#%02x%02x%02x' % (int(118+49*t), int(45+35*t), 242+int(13*t))
            self.bar_parts.append(c.create_rectangle(self.p(43), self.p(219), self.p(43), self.p(225),
                                                    fill=color, outline='', state='hidden'))
        self.percent = self.text(452, 222, '', 15, '#b563ff', 'bold')
        self.spinner_dots = []
        for i in range(8):
            angle = 2 * math.pi * i / 8
            x, y = 157 + 7*math.cos(angle), 248 + 7*math.sin(angle)
            self.spinner_dots.append(c.create_oval(*self.shape((x-1.5,y-1.5,x+1.5,y+1.5)), fill='#59347f', outline=''))
        self.status = self.text(177, 248, 'Подготавливаем запуск…', 12, '#b8a7d7', anchor='w', width=self.p(300))
        c.create_line(*self.shape((2,269,498,269)), fill='#27183d')
        c.create_oval(*self.shape((140,281,154,295)), outline='#aa87db', width=self.p(1))
        self.text(147, 288, 'i', 9, '#bba3df', 'bold')
        self.footer = self.text(165, 288, 'Пожалуйста, не закрывайте окно', 12, '#ad98d1', anchor='w')

    def on_map(self, _event):
        if self.minimized and self.root.state() == 'normal':
            self.minimized = False
            self.root.overrideredirect(True)

    def minimize(self):
        # Tk requires a window-manager frame while iconifying. Restore our frameless
        # window as soon as it is mapped again.
        self.minimized = True
        self.root.overrideredirect(False)
        self.root.iconify()

    def set_text(self, item, value, target):
        # Fade out the previous label before fading in the newest queued value.
        if self.canvas.itemcget(item, 'text') == value and item not in self.text_fades:
            return
        if item in self.text_fades:
            phase, step, _, _ = self.text_fades[item]
            self.text_fades[item] = (phase, step, value, target) if phase == 'out' else ('out', 0, value, target)
        else:
            self.text_fades[item] = ('out', 0, value, target)

    def animate(self):
        if not self.done:
            self.spinner_angle = (self.spinner_angle + 1) % 80
            palette = ('#c489ff', '#a96ef3', '#8c53d2', '#7140ae', '#563184', '#45286a', '#45286a', '#563184')
            for index, dot in enumerate(self.spinner_dots):
                self.canvas.itemconfigure(dot, fill=palette[(index-self.spinner_angle//3) % 8])
            glow = ('#7330bd','#7932c5','#8034cf','#8737d7','#8034cf','#7932c5')
            self.canvas.itemconfigure(self.logo_edge, outline=glow[(self.spinner_angle//8) % len(glow)])
            self.display_progress += (self.actual_progress-self.display_progress)*0.18
            if abs(self.actual_progress-self.display_progress)<0.001:
                self.display_progress=self.actual_progress
            for i, part in enumerate(self.bar_parts):
                left = 43+377*i/42
                right = min(43+377*(i+1)/42,43+377*self.display_progress)
                x1, x2 = self.p(left), self.p(max(left, right))
                self.canvas.coords(part, x1,self.p(219),x2,self.p(225))
                self.canvas.itemconfigure(part, state='normal' if self.determinate and x2 > x1 else 'hidden')
            for item, (phase, step, value, target) in list(self.text_fades.items()):
                step += 1
                amount = min(1, step/4)
                fraction = 1-amount if phase == 'out' else amount
                rgb0 = (68,55,94); rgb1 = tuple(int(target[i:i+2],16) for i in (1,3,5))
                color = '#'+''.join(f'{round(a+(b-a)*fraction):02x}' for a,b in zip(rgb0,rgb1))
                self.canvas.itemconfigure(item, fill=color)
                if step >= 4 and phase == 'out':
                    self.canvas.itemconfigure(item, text=value)
                    self.text_fades[item] = ('in', 0, value, target)
                elif step >= 4:
                    del self.text_fades[item]
                else:
                    self.text_fades[item] = (phase,step,value,target)
            self.root.after(40, self.animate)

    def destroy_window(self):
        # Cancel timer callbacks before destroying the Tcl interpreter.
        try:
            for callback_id in self.root.tk.call('after', 'info'):
                self.root.after_cancel(callback_id)
        finally:
            self.root.destroy()

    def close(self):
        if self.done:
            # Do not destroy the canvas during its own click callback.
            self.root.after_idle(self.destroy_window)
        else:
            messagebox.showinfo('Эфир', 'Дождитесь завершения установки или проверки обновления.', parent=self.root)

    def emit(self, kind, value):
        self.events.put((kind, value))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'status':
                    self.set_text(self.status, value, '#b9a9d5')
                elif kind == 'heading':
                    self.set_text(self.heading, value, '#f1ecfb')
                elif kind == 'indeterminate':
                    self.determinate = False
                    self.actual_progress = 0
                    self.display_progress = 0
                    self.canvas.itemconfigure(self.percent, text='')
                elif kind == 'progress':
                    self.determinate = True
                    self.actual_progress = max(0.0, min(1.0, value))
                    self.canvas.itemconfigure(self.percent, text=f'{int(self.actual_progress*100)}%')
                elif kind == 'warning':
                    messagebox.showwarning('Эфир', value, parent=self.root)
                elif kind == 'error':
                    self.done = True
                    title, detail = value
                    self.text_fades.clear()
                    self.canvas.itemconfigure(self.heading, text=title, fill='#f5e9ff')
                    self.canvas.itemconfigure(self.status, text=detail[:65] + ('…' if len(detail)>65 else ''), fill='#e5b9df')
                    self.canvas.itemconfigure(self.footer, text='Закройте окно и попробуйте ещё раз', fill='#c4a8d7')
                    self.canvas.itemconfigure(self.percent, text='')
                    for part in self.bar_parts: self.canvas.itemconfigure(part, state='hidden')
                elif kind == 'launched':
                    self.done = True
                    self.root.withdraw()
                elif kind == 'exit':
                    self.destroy_window()
                    return
        except queue.Empty:
            pass
        self.root.after(50, self.poll)
    def worker(self):
        phase = 'installation'
        try:
            self.emit('heading','Запуск программы…'); self.emit('status','Проверяем установленные файлы…')
            ROOT.mkdir(parents=True,exist_ok=True)
            old=current(); chosen=old
            if old is None:
                old = previous(); chosen = old
                if old is not None:
                    self.emit('warning','Текущая версия повреждена. При необходимости будет запущена предыдущая сохранённая версия.')
            if old is not None:
                self.emit('heading','Проверка обновлений…')
            try:
                phase = 'connection'
                self.emit('heading','Поиск обновлений…')
                self.emit('status','Проверяем версию на GitHub…')
                tag,url,digest,size=release_metadata()
                self.emit('connection', True)
                if old is None or old.parent.name != digest:
                    phase = 'download'
                    self.emit('heading','Загрузка файлов…')
                    with tempfile.TemporaryDirectory(prefix='efir-') as tmp:
                        archive=Path(tmp)/ASSET
                        last_mb = -1
                        def progress(done,total):
                            nonlocal last_mb
                            mb = done//1024//1024
                            if mb != last_mb or done == total:
                                last_mb = mb
                                self.emit('status',f'Загружено {mb} из {(total+1024*1024-1)//1024//1024} МБ')
                            self.emit('progress', min(done/total,1))
                        actual=download(url,archive,size,progress)
                        if actual != digest: raise ValueError('Контрольная сумма пакета не совпадает')
                        phase = 'installation'
                        self.emit('heading','Установка обновления…'); self.emit('status','Проверяем и распаковываем файлы…'); self.emit('indeterminate', None)
                        chosen=prepare_version(archive,digest)
            except Exception as exc:
                self.emit('connection', False)
                if old is None: raise RuntimeError(f'Не удалось получить Эфир: {exc}') from exc
                chosen=old
                self.emit('warning','Не удалось проверить или установить обновление. Запускается ранее установленная версия.\n\n'+str(exc))
            phase = 'installation'
            ensure_launcher_installed()
            try: create_shortcut()
            except Exception as e: self.emit('warning','Программа установлена, но ярлык не создан: '+str(e))
            phase = 'launch'
            self.emit('heading','Запуск программы…'); self.emit('status','Открываем Эфир…'); self.emit('indeterminate',None)
            env=dict(os.environ, EFIR_LAUNCHER_PATH=str(LAUNCHER), PYINSTALLER_RESET_ENVIRONMENT='1')
            try:
                proc=subprocess.Popen([str(chosen)],cwd=str(chosen.parent),env=env)
            except OSError as exc:
                if old is None or chosen == old: raise
                self.emit('warning','Не удалось запустить обновление. Открываем предыдущую версию.\n\n'+str(exc))
                chosen=old
                proc=subprocess.Popen([str(chosen)],cwd=str(chosen.parent),env=env)
            if chosen != old:
                write_state(chosen.parent.name, tag)
            # Keep old versions and quarantined folders; they may contain personal data.
            # A running process is not proof that the UI or settings loaded successfully.
            self.emit('heading','Готово'); self.emit('launched',None); proc.wait(); self.emit('exit',None)
        except Exception as exc:
            heading = {'connection':'Ошибка подключения', 'download':'Ошибка загрузки',
                       'installation':'Ошибка установки', 'launch':'Ошибка запуска'}.get(phase, 'Ошибка')
            detail = str(exc).replace('\n', ' ')
            try:
                ROOT.mkdir(parents=True, exist_ok=True)
                (ROOT/'launcher-error.log').write_text(heading + ': ' + detail, encoding='utf-8')
            except OSError:
                pass
            self.emit('error',(heading, detail or 'Неизвестная ошибка'))


def main():
    if os.name != 'nt': raise RuntimeError('Efir предназначен для Windows')
    # Per-monitor DPI awareness is set before creating any Tk windows.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4 & ((1 << (ctypes.sizeof(ctypes.c_void_p)*8)) - 1)))
    except (AttributeError, OSError):
        pass
    kernel=ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]; kernel.CreateMutexW.restype=ctypes.c_void_p
    handle=kernel.CreateMutexW(None,False,'Local\\EfirLauncherSingleton')
    if not handle: raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error()==183:
        messagebox.showinfo('Эфир','Эфир уже запущен. Закройте его перед проверкой обновлений.')
        return
    Window()

if __name__=='__main__': main()
