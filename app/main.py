# -*- coding: utf-8 -*-
"""Эфир by maxon3r — умная автозамена текста."""
import sys
import os
import json
import time
import datetime
import threading
import tempfile
import struct
import re
import shutil
import copy
import logging
from logging.handlers import RotatingFileHandler
from types import SimpleNamespace
from pathlib import Path

from PyQt5.QtCore import (
    Qt, QObject, pyqtSignal, QTimer, QThread, QPoint,
    QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QEvent, QRect, QSize,
    QVariantAnimation, QPropertyAnimation, QEasingCurve, QRectF, QPointF, QItemSelectionModel,
    QStandardPaths, QUrl
)
from PyQt5.QtGui import (QFont, QIcon, QKeySequence, QPainter, QColor, QPen, QBrush, QPixmap,
                         QLinearGradient, QPainterPath, QRadialGradient, QRegion, QPalette)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel,
    QPushButton, QLineEdit, QTextEdit, QTableView, QStyle, QStyledItemDelegate,
    QHeaderView, QComboBox, QFrame, QSystemTrayIcon, QMenu,
    QAction, QWidgetAction, QSplitter, QMessageBox as _QtMessageBox, QFileDialog as _QtFileDialog, QDialog,
    QAbstractItemView, 
    QSizePolicy, QListWidget, QStackedWidget, QRadioButton,
    QScrollArea, QButtonGroup, QTableWidget,
    QTableWidgetItem, QListWidgetItem, QGraphicsDropShadowEffect, QProgressBar
)

try:
    from pynput import keyboard as pk
    import pyperclip
    PYNPUT_OK = True
except ImportError:
    pk = None
    pyperclip = None
    PYNPUT_OK = False

APP_NAME = "Efir"
APP_VERSION = "2.0"
APP_DISPLAY_NAME = "Эфир by maxon3r"
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Efir"
DATA_FILE = APP_DIR / "binder_data.json"
BACKUP_DIR = APP_DIR / "backups"
LOG_FILE = APP_DIR / "efir.log"

log = logging.getLogger("efir")
log.addHandler(logging.NullHandler())


def setup_logging():
    """Ошибки пишутся в %APPDATA%\\Efir\\efir.log, а не теряются молча."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except OSError:
        pass

RADIO_PROFILES = ["LS", "SF", "LV"]
DEFAULT_PROFILE = RADIO_PROFILES[0]
RADIO_COLORS = {"LS": "#4DA3FF", "SF": "#FF9F43", "LV": "#A55EEA"}
_PROFILE_PALETTE = ["#4DA3FF", "#FF9F43", "#A55EEA", "#2ED573", "#FF6B81", "#1ECBE1", "#F7D358", "#7C8CFF"]


def profile_color(name):
    """Цвет радиоцентра: фиксированный для LS/SF/LV, для остальных — стабильный по имени."""
    if name in RADIO_COLORS:
        return RADIO_COLORS[name]
    return _PROFILE_PALETTE[sum(ord(c) for c in str(name)) % len(_PROFILE_PALETTE)]

GROUP_KEYWORDS = {
    "Транспорт": ["авто", "машин", "транспорт", "nrg", "infernus",
                  "banshee", "sultan", "turismo", "bullet", "hotring", "sandking",
                  "cheetah", "мотоцикл", "лодк", "вертолёт", "самолёт", "велик"],
    "Недвижимость": ["дом", "квартир", "особняк", "вилл", "недвиж", "аренда", "участок", "гараж", "пентхаус"],
    "Бизнес": ["бизнес", "кафе", "магазин", "завод", "компани", "фирм", "ресторан", "бар"],
    "Работа": ["работа", "вакансия", "требуется", "зарплат", "трудоустрой", "собеседован"],
    "Услуги": ["услуг", "ремонт", "помощь", "консульт", "доставк"],
    "Медицина": ["больниц", "врач", "лечен", "медик", "госпитал"],
    "Полиция": ["полици", "lspd", "розыск", "штраф", "арест"],
    "СМИ": ["эфир", "новост", "объявлен", "реклам", "сми"],
}

def guess_group(text):
    t = (text or "").casefold()
    best_group = "Общие"
    best_score = 0

    for group, kws in GROUP_KEYWORDS.items():
        score = 0
        for kw in kws:
            if kw in t:
                # Чем длиннее совпавшее слово, тем больше очков (например, "мотоцикл" важнее, чем "авто")
                score += len(kw)
        if score > best_score:
            best_score = score
            best_group = group

    return best_group

DEFAULT_DATA = {
    "version": 2,
    "settings": {
        "start_on_launch": False,
        "minimize_to_tray": True,
        "hotkey": "ctrl+alt+f10",
        "trigger_mode": "space",
        "case_sensitive": False,
        "cursor_mode": "punto",
        "restore_clipboard": True,
        "history_enabled": True,
        "history_limit": 100,
        "excluded_apps": [],
        "profiles": ["LS", "SF", "LV"],
        "active_profile": "LS",
        "game_overlay_hotkey": "alt+2",
        "game_overlay_enabled": True,
        "overlay_position": "top_right",
        "overlay_opacity": 92,
        "overlay_max_suggestions": 5,
        "overlay_min_query_len": 2,
        "overlay_auto_hide_ms": 5000,
        "buffer_idle_reset_ms": 3000,
        "overlay_screen_margin": 24,
    },
    "shortcuts": {
        "пр": {"text": "Привет! Как дела?", "group": "Общие", "uses": 0, "enabled": True, "favorite": False, "profile": "LS"},
        "спс": {"text": "Спасибо большое!", "group": "Общие", "uses": 0, "enabled": True, "favorite": False, "profile": "LS"},
        "дата": {"text": "Сегодня {date}, время {time}.", "group": "Шаблоны", "uses": 0, "enabled": True, "favorite": False, "profile": "LS"},
    },
    "history": []
}

# --- ЛОГИКА РАБОТЫ С ДАННЫМИ ---

def _punto_xor_bytes(data):
    seed = 0x91DAFA48
    result = bytearray()
    for byte in data:
        seed = (seed * 214013 + 2531011) & 0xFFFFFFFF
        result.append(byte ^ ((seed >> 16) & 0xFF))
    return bytes(result)

def import_punto_dat(path):
    raw = Path(path).read_bytes()
    if not raw.startswith(b"PSRF"):
        raise ValueError("Это не файл автозамен Punto Switcher: не найдена сигнатура PSRF.")
    offset = 4
    result = {}
    record_no = 0
    while offset < len(raw):
        if offset + 4 > len(raw):
            raise ValueError(f"Файл повреждён: не хватает длины записи около байта {offset}.")
        record_size = struct.unpack_from("<I", raw, offset)[0]
        offset += 4
        if record_size < 5 or offset + record_size - 4 > len(raw):
            raise ValueError(f"Файл повреждён: неверный размер записи №{record_no + 1}.")
        offset += 1
        encrypted = raw[offset:offset + record_size - 5]
        offset += record_size - 5
        try:
            decoded = _punto_xor_bytes(encrypted).decode("utf-16-le")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Не удалось прочитать запись №{record_no + 1}: неверная кодировка.") from exc
        fields = decoded.split("\x00")
        while fields and not fields[-1]:
            fields.pop()
        if len(fields) >= 2:
            key, replacement = fields[0].strip(), fields[1]
            if key and replacement:
                # Несколько записей DAT могут иметь полностью одинаковый ключ.
                # Сохраняем каждую до явного выбора пользователя при импорте.
                result[record_no] = {
                    "text": replacement, "group": guess_group(replacement),
                    "uses": 0, "enabled": True, "favorite": False, "profile": DEFAULT_PROFILE,
                    "_import_key": key,
                }
        record_no += 1
    if not result:
        raise ValueError("В файле не найдено ни одной корректной пары «ключ → текст».")
    return result

def export_punto_dat(path, shortcuts):
    output = bytearray(b"PSRF")
    for key, info in shortcuts.items():
        if not isinstance(key, str) or not key or not isinstance(info, dict):
            continue
        text = str(info.get("text", ""))
        if not text:
            continue
        payload = (key + "\x00" + text + "\x00").encode("utf-16-le")
        output.extend(struct.pack("<I", len(payload) + 5))
        output.append(0)
        output.extend(_punto_xor_bytes(payload))
    atomic_write_file(path, bytes(output))

def atomic_write_file(path, payload):
    """Replace only after a complete write; a failed export leaves the old file intact."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="efir_export_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_profile_name(name):
    if not isinstance(name, str) or not name.strip() or "::" in name:
        raise ValueError("Название радиоцентра не может быть пустым или содержать разделитель «::».")
    if any(ord(char) < 32 for char in name):
        raise ValueError("Название радиоцентра не должно содержать управляющие символы.")
    return name.strip()


def validate_stored_data(raw):
    """Existing files must have a real shortcuts object; defaults are for first launch only."""
    if not isinstance(raw, dict) or not isinstance(raw.get("shortcuts"), dict):
        raise ValueError("В JSON отсутствует корректный объект shortcuts с автозаменами.")
    if "settings" in raw and not isinstance(raw["settings"], dict):
        raise ValueError("В JSON повреждён объект settings.")
    settings = raw.get("settings", {})
    if "profiles" in settings:
        if not isinstance(settings["profiles"], list):
            raise ValueError("Список радиоцентров profiles повреждён.")
        for profile in settings["profiles"]:
            validate_profile_name(profile)
    if "active_profile" in settings:
        validate_profile_name(settings["active_profile"])
    for key, info in raw["shortcuts"].items():
        if not isinstance(key, str) or not key or not isinstance(info, dict):
            raise ValueError("В JSON найдена повреждённая запись автозамены.")
        if "profile" in info:
            validate_profile_name(info["profile"])
        if "::" in key and not all(key.split("::", 1)):
            raise ValueError("В JSON найден пустой профиль или ключ автозамены.")
    return raw


def insensitive_key_conflicts(shortcuts):
    seen, conflicts = {}, []
    for ckey, info in shortcuts.items():
        profile, bare = split_ckey(ckey)
        token = (info.get("profile", profile), bare.casefold())
        if token in seen:
            conflicts.append((seen[token], ckey))
        else:
            seen[token] = ckey
    return conflicts


def casefold_groups(keys):
    """Группы неоднозначных ключей в порядке их появления."""
    groups = {}
    for key in keys:
        groups.setdefault(key.casefold(), []).append(key)
    return [group for group in groups.values() if len(group) > 1]


def app_data_dir():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return APP_DIR

def make_arrow_png():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    path = APP_DIR / "arrow_down.png"
    pix = QPixmap(12, 12)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor("#B0BBCB"), 1.6)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPoint(2, 4), QPoint(6, 8), QPoint(10, 4))
    p.end()
    pix.save(str(path), "PNG")
    return path.as_posix()


def plural(n, a, b, c):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return a
    if 2 <= n10 <= 4 and not 12 <= n100 <= 14:
        return b
    return c

def make_ckey(profile, key):
    return f"{profile}::{key}"

def split_ckey(ckey):
    if "::" in ckey:
        profile, key = ckey.split("::", 1)
        return profile, key
    return None, ckey

def _to_int(value, default=0):
    """Число из чего угодно; при мусоре — значение по умолчанию (а не падение всей загрузки)."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return default


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on", "да"):
            return True
        if v in ("0", "false", "no", "off", "нет", ""):
            return False
    return default


def _normalize_settings(raw):
    settings = dict(DEFAULT_DATA["settings"])
    if isinstance(raw, dict):
        settings.update(raw)
    # Приводим известные настройки к нужному типу: одно кривое значение не должно ломать запуск.
    for name, default in DEFAULT_DATA["settings"].items():
        value = settings.get(name)
        if isinstance(default, bool):
            settings[name] = _to_bool(value, default)
        elif isinstance(default, int):
            settings[name] = _to_int(value, default)
        elif isinstance(default, str):
            settings[name] = default if value is None else str(value)
        elif isinstance(default, list):
            settings[name] = list(value) if isinstance(value, list) else list(default)
    settings["excluded_apps"] = [str(x).strip() for x in settings["excluded_apps"] if str(x).strip()]
    return settings


def normalize_data(data):
    if not isinstance(data, dict):
        data = {}
    merged = {
        "version": 2, "settings": _normalize_settings(data.get("settings")),
        "shortcuts": {}, "history": [],
    }
    # A delimiter inside a profile name cannot round-trip through compound keys.
    for profile in merged["settings"].get("profiles", []):
        validate_profile_name(profile)
    validate_profile_name(merged["settings"]["active_profile"])
    raw_shortcuts = data.get("shortcuts")
    if not isinstance(raw_shortcuts, dict):
        # Списка замен нет вообще (первый запуск) — показываем примеры.
        # Пустой список, который пользователь сам очистил, остаётся пустым.
        raw_shortcuts = DEFAULT_DATA["shortcuts"]
    for key, value in raw_shortcuts.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        if "::" in key:
            profile, bare = key.split("::", 1)
        else:
            profile, bare = str(value.get("profile", "По умолчанию")), key
        if not bare:
            continue
        validate_profile_name(profile)
        if "profile" in value:
            validate_profile_name(value["profile"])
        text = value.get("text", "")
        group = value.get("group", "Общие")
        entry = {
            "text": "" if text is None else str(text),
            "group": str(group) if group else "Общие",
            "uses": max(0, _to_int(value.get("uses", 0), 0)),
            "enabled": _to_bool(value.get("enabled", True), True),
            "favorite": _to_bool(value.get("favorite", False), False),
            "profile": profile,
        }
        _hk = str(value.get("hotkey", "") or "").strip()
        if _hk:
            entry["hotkey"] = _hk
        merged["shortcuts"][make_ckey(profile, bare)] = entry
    if isinstance(data.get("history"), list):
        merged["history"] = [h for h in data["history"] if isinstance(h, dict)][-500:]
    raw_profiles = merged["settings"].get("profiles", [])
    if not isinstance(raw_profiles, list):
        raw_profiles = []
    profiles = []
    for p in raw_profiles:
        name = str(p).strip()
        if name and name not in profiles:
            profiles.append(name)
    if not profiles:
        profiles = list(RADIO_PROFILES)
    merged["settings"]["profiles"] = profiles
    if merged["settings"].get("active_profile") not in profiles:
        merged["settings"]["active_profile"] = profiles[0]
    fixed = {}
    for ckey, info in merged["shortcuts"].items():
        if info.get("profile") not in profiles:
            info["profile"] = merged["settings"]["active_profile"]
        _, bare = split_ckey(ckey)
        new_ckey = make_ckey(info["profile"], bare)
        if new_ckey in fixed:
            # Такой ключ в профиле уже есть — не затираем, а сохраняем под новым именем.
            n = 2
            while (make_ckey(info["profile"], f"{bare}_{n}") in fixed
                   or make_ckey(info["profile"], f"{bare}_{n}") in merged["shortcuts"]):
                n += 1
            new_ckey = make_ckey(info["profile"], f"{bare}_{n}")
        fixed[new_ckey] = info
    merged["shortcuts"] = fixed
    return merged


# Сообщение для пользователя, если при загрузке пришлось восстанавливать данные.
LOAD_WARNING = ""


def _read_json(path):
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        raw = json.load(handle)
    return validate_stored_data(raw)


def load_data():
    global LOAD_WARNING
    LOAD_WARNING = ""
    app_data_dir()
    if not DATA_FILE.exists():
        return normalize_data(DEFAULT_DATA)
    try:
        return normalize_data(_read_json(DATA_FILE))
    except Exception as exc:
        log.exception("Не удалось прочитать %s", DATA_FILE)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        damaged = DATA_FILE.with_name(f"binder_data.corrupted_{stamp}.json")
        n = 2
        while damaged.exists():
            damaged = DATA_FILE.with_name(f"binder_data.corrupted_{stamp}_{n}.json")
            n += 1
        try:
            # Копируем, а не переименовываем: исходник остаётся нетронутым до первого сохранения,
            # а копия с уникальным именем не перезапишется при следующем сбое.
            shutil.copy2(DATA_FILE, damaged)
            saved_note = f"Исходный файл сохранён как «{damaged.name}» в папке {APP_DIR}."
        except OSError:
            log.exception("Не удалось сделать копию повреждённого файла")
            saved_note = f"Скопировать исходный файл не удалось — сохраните копию {DATA_FILE} вручную."
        try:
            backups = sorted(BACKUP_DIR.glob("binder_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            backups = []
        for backup in backups:
            try:
                data = normalize_data(_read_json(backup))
                when = datetime.datetime.fromtimestamp(backup.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            except Exception:
                log.warning("Резервная копия %s тоже не читается", backup.name)
                continue
            LOAD_WARNING = (f"Файл с заменами повреждён ({exc}).\n\n"
                            f"Данные восстановлены из резервной копии от {when}.\n{saved_note}")
            return data
        LOAD_WARNING = (f"Файл с заменами повреждён ({exc}), а рабочих резервных копий нет.\n\n"
                        f"Эфир запущен с примерами по умолчанию.\n{saved_note}")
        return normalize_data(DEFAULT_DATA)


def save_data(data, make_backup=False):
    app_data_dir()
    payload = json.dumps(normalize_data(data), ensure_ascii=False, indent=2)
    if make_backup and DATA_FILE.exists():
        try:
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = BACKUP_DIR / f"binder_{stamp}.json"
            serial = 2
            while backup.exists():
                backup = BACKUP_DIR / f"binder_{stamp}_{serial}.json"
                serial += 1
            backup.write_bytes(DATA_FILE.read_bytes())
            backups = sorted(BACKUP_DIR.glob("binder_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in backups[20:]:
                old.unlink(missing_ok=True)
        except OSError:
            log.exception("Не удалось сделать резервную копию")
            raise  # Действие, обещающее резервную копию, не должно продолжаться без неё.
    fd, tmp_name = tempfile.mkstemp(prefix="efir_", suffix=".tmp", dir=str(APP_DIR))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, DATA_FILE)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


# --- ВВОД С КЛАВИАТУРЫ В WINDOWS ---

IS_WINDOWS = os.name == "nt"
INJECT_MARK = 0x45464952  # «EFIR» в dwExtraInfo: так перехватчик узнаёт нажатия, которые отправил сам Эфир
LLKHF_INJECTED = 0x10
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
VK_BACK, VK_RETURN, VK_SHIFT, VK_CONTROL, VK_MENU = 0x08, 0x0D, 0x10, 0x11, 0x12
VK_LEFT, VK_V, VK_LWIN, VK_RWIN = 0x25, 0x56, 0x5B, 0x5C
MODIFIER_VKS = frozenset({0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C})
_EXTENDED_VKS = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E})
_MOD_GROUPS = (("ctrl", (0x11, 0xA2, 0xA3)), ("alt", (0x12, 0xA4, 0xA5)),
               ("shift", (0x10, 0xA0, 0xA1)), ("cmd", (0x5B, 0x5C)))

_WIN_API = None


def _win_structs():
    """Структуры SendInput с явными размерами полей (одинаково верны для 32 и 64 бит)."""
    import ctypes
    ULONG_PTR = ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                    ("time", ctypes.c_uint32), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                    ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_uint32), ("u", _INPUTUNION)]

    return KEYBDINPUT, INPUT


def _win():
    global _WIN_API
    if _WIN_API is None:
        import ctypes
        KEYBDINPUT, INPUT = _win_structs()
        # Свои экземпляры DLL, чтобы не менять argtypes у функций, которыми пользуется pynput.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32")
        H, DW = ctypes.c_void_p, ctypes.c_uint32
        user32.SendInput.argtypes = (ctypes.c_uint, ctypes.c_void_p, ctypes.c_int)
        user32.SendInput.restype = ctypes.c_uint
        user32.MapVirtualKeyW.argtypes = (ctypes.c_uint, ctypes.c_uint)
        user32.MapVirtualKeyW.restype = ctypes.c_uint
        user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        user32.GetAsyncKeyState.restype = ctypes.c_short
        user32.GetForegroundWindow.argtypes = ()
        user32.GetForegroundWindow.restype = H
        user32.GetWindowThreadProcessId.argtypes = (H, ctypes.POINTER(DW))
        user32.GetWindowThreadProcessId.restype = DW
        user32.GetKeyboardLayout.argtypes = (DW,)
        user32.GetKeyboardLayout.restype = H
        user32.MapVirtualKeyExW.argtypes = (ctypes.c_uint, ctypes.c_uint, H)
        user32.MapVirtualKeyExW.restype = ctypes.c_uint
        user32.ToUnicodeEx.argtypes = (ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_ubyte),
                                       ctypes.c_wchar_p, ctypes.c_int, ctypes.c_uint, H)
        user32.ToUnicodeEx.restype = ctypes.c_int
        user32.GetKeyState.argtypes = (ctypes.c_int,)
        user32.GetKeyState.restype = ctypes.c_short
        user32.GetClipboardSequenceNumber.argtypes = ()
        user32.GetClipboardSequenceNumber.restype = DW
        user32.OpenClipboard.argtypes = (H,)
        user32.OpenClipboard.restype = ctypes.c_int
        user32.CloseClipboard.argtypes = ()
        user32.CloseClipboard.restype = ctypes.c_int
        user32.EnumClipboardFormats.argtypes = (ctypes.c_uint,)
        user32.EnumClipboardFormats.restype = ctypes.c_uint
        user32.CountClipboardFormats.argtypes = ()
        user32.CountClipboardFormats.restype = ctypes.c_int
        user32.IsClipboardFormatAvailable.argtypes = (ctypes.c_uint,)
        user32.IsClipboardFormatAvailable.restype = ctypes.c_int
        kernel32.OpenProcess.argtypes = (DW, ctypes.c_int, DW)
        kernel32.OpenProcess.restype = H
        kernel32.QueryFullProcessImageNameW.argtypes = (H, DW, ctypes.c_wchar_p, ctypes.POINTER(DW))
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = (H,)
        kernel32.CloseHandle.restype = ctypes.c_int
        advapi32.OpenProcessToken.argtypes = (H, DW, ctypes.POINTER(H))
        advapi32.OpenProcessToken.restype = ctypes.c_int
        advapi32.GetTokenInformation.argtypes = (H, ctypes.c_int, ctypes.c_void_p, DW, ctypes.POINTER(DW))
        advapi32.GetTokenInformation.restype = ctypes.c_int
        _WIN_API = SimpleNamespace(ctypes=ctypes, KEYBDINPUT=KEYBDINPUT, INPUT=INPUT, user32=user32,
                                   kernel32=kernel32, advapi32=advapi32, shell32=shell32)
    return _WIN_API


def _key_input(vk=0, scan=0, flags=0):
    api = _win()
    if vk and not scan and not flags & KEYEVENTF_UNICODE:
        scan = api.user32.MapVirtualKeyW(vk, 0) & 0xFFFF  # скан-код нужен играм, читающим «сырой» ввод
    if vk in _EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return api.INPUT(type=1, ki=api.KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0,
                                               dwExtraInfo=INJECT_MARK))


def send_inputs(events):
    if not events:
        return 0
    api = _win()
    array = (api.INPUT * len(events))(*events)
    sent = api.user32.SendInput(len(events), array, api.ctypes.sizeof(api.INPUT))
    if sent != len(events):
        # A partial send may leave our modifiers pressed. Release only key-up
        # events from this batch; never retry text or deletion automatically.
        pressed = set()
        for event in events[:sent]:
            token = (event.ki.wVk, event.ki.wScan, event.ki.dwFlags & KEYEVENTF_UNICODE)
            if event.ki.dwFlags & KEYEVENTF_KEYUP:
                pressed.discard(token)
            else:
                pressed.add(token)
        releases = [event for event in events if event.ki.dwFlags & KEYEVENTF_KEYUP
                    and (event.ki.wVk, event.ki.wScan, event.ki.dwFlags & KEYEVENTF_UNICODE) in pressed]
        if releases:
            cleanup = (api.INPUT * len(releases))(*releases)
            api.user32.SendInput(len(releases), cleanup, api.ctypes.sizeof(api.INPUT))
        raise OSError(f"Windows отправила {sent} из {len(events)} событий ввода. "
                      "Вставка не подтверждена; проверьте активное окно и права доступа.")
    return sent


def send_key(vk, times=1):
    events = []
    for _ in range(max(0, int(times))):
        events.append(_key_input(vk))
        events.append(_key_input(vk, flags=KEYEVENTF_KEYUP))
    return send_inputs(events)


def send_text(text):
    events = []
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        unit = raw[i] | (raw[i + 1] << 8)
        events.append(_key_input(0, unit, KEYEVENTF_UNICODE))
        events.append(_key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return send_inputs(events)


def send_ctrl_v():
    return send_inputs([_key_input(VK_CONTROL), _key_input(VK_V),
                        _key_input(VK_V, flags=KEYEVENTF_KEYUP), _key_input(VK_CONTROL, flags=KEYEVENTF_KEYUP)])


def win_modifiers_down():
    user32 = _win().user32
    return frozenset(name for name, vks in _MOD_GROUPS
                     if any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks))


def win_foreground_key_char(key):
    """Переводит клавишу в раскладке активного окна, а не в раскладке потока pynput."""
    fallback = getattr(key, "char", None)
    vk = getattr(key, "vk", None)
    if not vk or vk == 0xE7:  # VK_PACKET уже содержит готовый Unicode-символ
        return fallback
    try:
        api = _win()
        user32, ctypes = api.user32, api.ctypes
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return fallback
        thread_id = user32.GetWindowThreadProcessId(hwnd, None)
        if not thread_id:
            return fallback
        layout = user32.GetKeyboardLayout(thread_id)
        if not layout:
            return fallback
        state = (ctypes.c_ubyte * 256)()
        for code in (0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5):
            if user32.GetAsyncKeyState(code) & 0x8000:
                state[code] = 0x80
        if user32.GetKeyState(0x14) & 1:  # Caps Lock
            state[0x14] = 1
        scan = user32.MapVirtualKeyExW(vk, 0, layout)
        result = ctypes.create_unicode_buffer(8)
        # Windows 10 1607+: bit 2 keeps the keyboard's dead-key state unchanged.
        count = user32.ToUnicodeEx(vk, scan, state, result, len(result), 4, layout)
        if count == 1:
            return result.value
        # Dead keys and composed/multiple characters need the original listener path.
        return fallback
    except Exception:
        log.exception("Не удалось определить символ в раскладке активного окна")
        return fallback


def wait_modifiers_released(timeout=1.5):
    """Ждём, пока отпустят Ctrl/Alt/Shift/Win: иначе вставленный текст превратится в сочетания клавиш."""
    end = time.time() + timeout
    while time.time() < end:
        if not win_modifiers_down():
            return True
        time.sleep(0.02)
    return False


def win_is_admin():
    try:
        return bool(_win().shell32.IsUserAnAdmin())
    except Exception:
        return False


def win_foreground_process():
    """Имя exe активного окна (в нижнем регистре) и запущено ли оно от администратора (True/False/None)."""
    api = _win()
    ctypes = api.ctypes
    hwnd = api.user32.GetForegroundWindow()
    if not hwnd:
        return "", None
    pid = ctypes.c_uint32()
    api.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = api.kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "", None
    try:
        name = ""
        buf = ctypes.create_unicode_buffer(1024)
        size = ctypes.c_uint32(len(buf))
        if api.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            name = os.path.basename(buf.value).lower()
        elevated = None
        token = ctypes.c_void_p()
        if api.advapi32.OpenProcessToken(handle, 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            try:
                value, ret = ctypes.c_uint32(), ctypes.c_uint32()
                if api.advapi32.GetTokenInformation(token, 20, ctypes.byref(value),  # TokenElevation
                                                    ctypes.sizeof(value), ctypes.byref(ret)):
                    elevated = bool(value.value)
            finally:
                api.kernel32.CloseHandle(token)
        return name, elevated
    finally:
        api.kernel32.CloseHandle(handle)


def clipboard_sequence_number():
    if not IS_WINDOWS:
        return None
    try:
        return int(_win().user32.GetClipboardSequenceNumber())
    except Exception:
        return None


def clipboard_has_foreign_data():
    """Only plain-text formats can safely round-trip through pyperclip.

    Windows often synthesizes CF_TEXT/CF_OEMTEXT/CF_LOCALE from Unicode.
    HTML, RTF, files, images and unknown registered formats must be left intact.
    A busy/unreadable clipboard is conservatively treated as foreign data.
    """
    api = _win()
    user32 = api.user32
    if not user32.OpenClipboard(None):
        return True
    try:
        fmt = 0
        while True:
            api.ctypes.set_last_error(0)
            fmt = user32.EnumClipboardFormats(fmt)
            if not fmt:
                return bool(api.ctypes.get_last_error())
            if fmt not in (1, 7, 13, 16):
                return True
    finally:
        user32.CloseClipboard()


# --- ГОРЯЧИЕ КЛАВИШИ ---
# Сочетание хранится строкой «ctrl+alt+f10» и сравнивается по виртуальному коду клавиши,
# поэтому работает в любой раскладке и с любыми клавишами (space, esc, home, num5…).

_HK_MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl", "lctrl": "ctrl", "rctrl": "ctrl",
    "alt": "alt", "alt_l": "alt", "alt_r": "alt", "lalt": "alt", "ralt": "alt", "option": "alt",
    "shift": "shift", "shift_l": "shift", "shift_r": "shift", "lshift": "shift", "rshift": "shift",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd", "win": "cmd", "windows": "cmd", "super": "cmd", "meta": "cmd",
}
_HK_NAMED_VK = {
    "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "backspace": 0x08, "return": 0x0D, "enter": 0x0D,
    "ins": 0x2D, "insert": 0x2D, "del": 0x2E, "delete": 0x2E, "pause": 0x13, "print": 0x2C,
    "print_screen": 0x2C, "printscreen": 0x2C, "home": 0x24, "end": 0x23, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "pgup": 0x21, "pageup": 0x21, "page_up": 0x21, "pgdown": 0x22,
    "pgdn": 0x22, "pagedown": 0x22, "page_down": 0x22, "capslock": 0x14, "caps_lock": 0x14,
    "numlock": 0x90, "num_lock": 0x90, "scrolllock": 0x91, "scroll_lock": 0x91, "menu": 0x5D, "space": 0x20,
}
_HK_OEM_VK = {
    "`": 0xC0, "~": 0xC0, "-": 0xBD, "_": 0xBD, "=": 0xBB, "+": 0xBB, "[": 0xDB, "{": 0xDB, "]": 0xDD,
    "}": 0xDD, "\\": 0xDC, "|": 0xDC, ";": 0xBA, ":": 0xBA, "'": 0xDE, '"': 0xDE, ",": 0xBC, "<": 0xBC,
    ".": 0xBE, ">": 0xBE, "/": 0xBF, "?": 0xBF, "!": 0x31, "@": 0x32, "#": 0x33, "№": 0x33, "$": 0x34,
    "%": 0x35, "^": 0x36, "&": 0x37, "*": 0x38, "(": 0x39, ")": 0x30,
}
_HK_CYR_TO_LAT = dict(zip("йцукенгшщзхъфывапролджэячсмитьбюё", "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"))
_HK_VK_NAMES = {
    0x1B: "esc", 0x09: "tab", 0x08: "backspace", 0x0D: "enter", 0x2D: "insert", 0x2E: "delete",
    0x13: "pause", 0x2C: "print", 0x24: "home", 0x23: "end", 0x25: "left", 0x26: "up", 0x27: "right",
    0x28: "down", 0x21: "pgup", 0x22: "pgdown", 0x14: "capslock", 0x90: "numlock", 0x91: "scrolllock",
    0x5D: "menu", 0x20: "space", 0xC0: "`", 0xBD: "-", 0xBB: "=", 0xDB: "[", 0xDD: "]", 0xDC: "\\",
    0xBA: ";", 0xDE: "'", 0xBC: ",", 0xBE: ".", 0xBF: "/",
}


def hotkey_key_vk(name):
    name = str(name or "").strip().lower().strip("<>")
    if not name:
        return None
    if name in _HK_NAMED_VK:
        return _HK_NAMED_VK[name]
    if name[0] == "f" and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x6F + int(name[1:])
    if name.startswith("num") and len(name) == 4 and name[3].isdigit():
        return 0x60 + int(name[3])
    if len(name) == 1:
        ch = _HK_CYR_TO_LAT.get(name, name)
        if "a" <= ch <= "z" or "0" <= ch <= "9":
            return ord(ch.upper())
        return _HK_OEM_VK.get(ch)
    return None


def hotkey_name_for_vk(vk):
    """Обратное преобразование для поля записи сочетания (не зависит от раскладки)."""
    if not vk:
        return ""
    if 0x41 <= vk <= 0x5A or 0x30 <= vk <= 0x39:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    if 0x60 <= vk <= 0x69:
        return f"num{vk - 0x60}"
    return _HK_VK_NAMES.get(vk, "")


def parse_hotkey(text):
    """«ctrl+alt+f10» → (frozenset({'ctrl', 'alt'}), 0x79). Неверная строка → None."""
    t = str(text or "").strip().lower()
    if not t:
        return None
    main = None
    if t.endswith("++"):
        main, t = "+", t[:-2]
    elif t == "+":
        main, t = "+", ""
    mods = set()
    for part in t.split("+"):
        part = part.strip().strip("<>")
        if not part:
            continue
        if part in _HK_MOD_ALIASES:
            mods.add(_HK_MOD_ALIASES[part])
            continue
        if main is not None:
            return None  # две обычные клавиши в одном сочетании
        main = part
    vk = hotkey_key_vk(main) if main is not None else None
    if vk is None:
        return None
    return frozenset(mods), vk


# --- КОНТРОЛЛЕР БИНДЕРА ---

class BinderController(QObject):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(bool)
    hotkey_signal = pyqtSignal()
    replacement_signal = pyqtSignal(str)
    suggestions_signal = pyqtSignal(str, object)
    overlay_toggle_signal = pyqtSignal(bool)

    _MODIFIER_KEY_NAMES = ("shift", "shift_l", "shift_r", "ctrl", "ctrl_l", "ctrl_r", "alt", "alt_l", "alt_r",
                           "alt_gr", "cmd", "cmd_l", "cmd_r", "caps_lock", "num_lock", "scroll_lock")

    def __init__(self):
        super().__init__()
        self.data = normalize_data({})
        self.listener = None
        self.is_running = False
        self.buffer = ""
        self.lock = threading.RLock()
        self._replacement_lock = threading.Lock()
        self._injecting = threading.Event()
        self.overlay_active = False
        self._last_char_time = 0.0
        self._key_down_at = {}
        self._pressed_vks = set()
        self._suppress_enter_up = False
        self._main_combo = None
        self._overlay_combo = None
        self._shortcut_combos = {}
        self._hotkey_vks = frozenset()
        self._hotkey_errors = []
        self._elevation_warned = set()
        self._modifier_keys = set()
        if PYNPUT_OK:
            self._modifier_keys = {getattr(pk.Key, n) for n in self._MODIFIER_KEY_NAMES if hasattr(pk.Key, n)}

    # ---------- настройка и запуск ----------

    def configure(self, data):
        with self.lock:
            self.data = normalize_data(data)
            self.buffer = ""
        self._rebuild_hotkeys()

    def _rebuild_hotkeys(self):
        with self.lock:
            settings = dict(self.data.get("settings", {}))
            shortcuts = dict(self.data.get("shortcuts", {}))
        errors = []
        main_text = str(settings.get("hotkey", "ctrl+alt+f10") or "")
        main = parse_hotkey(main_text)
        if main is None and main_text.strip():
            errors.append(f"вкл/выкл «{main_text}»")
        overlay_text = str(settings.get("game_overlay_hotkey", "alt+2") or "")
        overlay = parse_hotkey(overlay_text)
        if overlay is None and overlay_text.strip():
            errors.append(f"оверлей «{overlay_text}»")
        elif overlay is not None and overlay == main:
            errors.append("клавиша оверлея совпадает с клавишей вкл/выкл")
            overlay = None
        profile = settings.get("active_profile", DEFAULT_PROFILE)
        combos = {}
        for ckey, info in shortcuts.items():
            hk = str(info.get("hotkey", "") or "").strip()
            if not hk or not info.get("enabled", True) or info.get("profile") != profile:
                continue
            _, bare = split_ckey(ckey)
            combo = parse_hotkey(hk)
            if combo is None:
                errors.append(f"«{hk}» у замены «{bare}»")
            elif combo == main or combo == overlay or combo in combos:
                errors.append(f"«{hk}» у замены «{bare}» уже занята")
            else:
                combos[combo] = ckey
        vks = {c[1] for c in combos}
        vks.update(c[1] for c in (main, overlay) if c)
        self._main_combo, self._overlay_combo, self._shortcut_combos = main, overlay, combos
        self._hotkey_vks = frozenset(vks)
        self._hotkey_errors = errors

    def ensure_listener(self):
        """Перехват клавиатуры работает всё время, пока открыт Эфир, — иначе горячая клавиша
        не сможет снова включить автозамену. Когда Эфир остановлен, набор текста не отслеживается."""
        if not PYNPUT_OK:
            return False
        if self.listener is not None and self.listener.is_alive():
            return True
        try:
            self.listener = pk.Listener(
                on_press=self._on_press_wrapper,
                on_release=self._on_release_wrapper,
                win32_event_filter=self._win32_event_filter,
            )
            self.listener.start()
            return True
        except Exception as exc:
            log.exception("Не удалось запустить перехват клавиатуры")
            self.listener = None
            self.log_signal.emit(f"Не удалось включить перехват клавиатуры: {exc}")
            return False

    def start(self):
        if not PYNPUT_OK:
            self.log_signal.emit("Не найдены pynput/pyperclip. Установите зависимости из requirements.txt.")
            self.status_signal.emit(False)
            return False
        if self.is_running:
            return True
        self._rebuild_hotkeys()
        if not self.ensure_listener():
            self.status_signal.emit(False)
            return False
        with self.lock:
            self.buffer = ""
        self.is_running = True
        self.status_signal.emit(True)
        if self._hotkey_errors:
            message = "Эфир запущен, но не работают горячие клавиши: " + "; ".join(self._hotkey_errors[:3])
            log.warning(message)
            self.log_signal.emit(message)
        else:
            self.log_signal.emit("Эфир запущен. Глобальная замена активна.")
        return True

    def stop(self):
        was_running = self.is_running
        self.is_running = False
        with self.lock:
            self.buffer = ""
        self.overlay_active = False
        self.suggestions_signal.emit("", [])
        self.overlay_toggle_signal.emit(False)
        self.status_signal.emit(False)
        if was_running:
            hk = str(self.data.get("settings", {}).get("hotkey", "") or "")
            if self._main_combo and self.listener is not None:
                self.log_signal.emit(f"Эфир остановлен. Включить снова: {hk}.")
            else:
                self.log_signal.emit("Эфир остановлен.")

    def shutdown(self):
        """Полная остановка при выходе из программы."""
        self.stop()
        self._stop_listeners()

    def _stop_listeners(self):
        listener, self.listener = self.listener, None
        if listener:
            try:
                listener.stop()
            except Exception:
                log.exception("Не удалось остановить перехват клавиатуры")

    # ---------- низкоуровневая обработка клавиш ----------

    @staticmethod
    def _key_vk(key):
        vk = getattr(key, "vk", None)
        if vk is None:
            vk = getattr(getattr(key, "value", None), "vk", None)
        return vk

    def _key_id(self, key):
        vk = self._key_vk(key)
        if vk:
            return ("vk", vk)
        ch = getattr(key, "char", None)
        return ("ch", ch) if ch else ("key", key)

    def _current_mods(self):
        if IS_WINDOWS:
            try:
                return win_modifiers_down()
            except Exception:
                log.exception("GetAsyncKeyState")
        pressed = self._pressed_vks
        return frozenset(name for name, vks in _MOD_GROUPS if pressed.intersection(vks))

    def _win32_event_filter(self, msg, data):
        """Вызывается pynput в Windows до обработчиков. False — событие не передаём в on_press."""
        extra = getattr(data, "dwExtraInfo", None) or 0
        if extra == INJECT_MARK:
            return False  # это наши собственные нажатия при вставке текста
        if (getattr(data, "flags", 0) & LLKHF_INJECTED) and self._injecting.is_set():
            return False
        suppress = False
        try:
            if getattr(data, "vkCode", None) == VK_RETURN:
                if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if self._try_enter_trigger():
                        suppress = True
                        self._suppress_enter_up = True
                elif msg in (WM_KEYUP, WM_SYSKEYUP) and self._suppress_enter_up:
                    self._suppress_enter_up = False
                    self._key_down_at.pop(("vk", VK_RETURN), None)
                    suppress = True
        except Exception:
            log.exception("Ошибка в фильтре клавиатуры")
        if suppress and self.listener is not None:
            # Enter не доходит до программы: иначе чат успел бы отправить сам ключ до замены.
            self.listener.suppress_event()
        return True

    def _try_enter_trigger(self):
        if not self.is_running:
            return False
        if self.data.get("settings", {}).get("trigger_mode", "space") != "enter":
            return False
        if self._current_mods() & {"ctrl", "alt", "cmd"}:
            return False
        self.suggestions_signal.emit("", [])
        return self._match_boundary("\n")

    def _handle_hotkeys(self, vk, is_repeat):
        if not vk or vk in MODIFIER_VKS or vk not in self._hotkey_vks:
            return False
        combo = (self._current_mods(), vk)
        if combo == self._main_combo:
            if not is_repeat:
                self.hotkey_signal.emit()
            return True
        if not self.is_running:
            return False
        if combo == self._overlay_combo and self.data.get("settings", {}).get("game_overlay_enabled", True):
            if not is_repeat:
                self._toggle_game_overlay()
            return True
        ckey = self._shortcut_combos.get(combo)
        if ckey:
            if not is_repeat:
                self._fire_shortcut_hotkey(ckey)
            return True
        return False

    def _on_press_wrapper(self, key):
        try:
            now = time.time()
            kid = self._key_id(key)
            prev = self._key_down_at.get(kid)
            is_repeat = prev is not None and now - prev < 1.0
            self._key_down_at[kid] = now
            vk = self._key_vk(key)
            if vk:
                self._pressed_vks.add(vk)
            # Символ из самой горячей клавиши (например, «2» в alt+2) в буфер не попадает.
            if self._handle_hotkeys(vk, is_repeat):
                return
            if is_repeat and getattr(key, "char", None):
                # Клавишу держат зажатой (бег в игре, автоповтор) — это не набор текста.
                if self.buffer:
                    self.buffer = ""
                    self._emit_suggestions()
                return
        except Exception:
            log.exception("Ошибка обработки нажатия")
        self.on_press(key)

    def _on_release_wrapper(self, key):
        try:
            self._key_down_at.pop(self._key_id(key), None)
            vk = self._key_vk(key)
            if vk:
                self._pressed_vks.discard(vk)
        except Exception:
            log.exception("Ошибка обработки отпускания")
        self.on_release(key)

    # ---------- оверлей ----------

    def _toggle_game_overlay(self):
        if not self.data.get("settings", {}).get("game_overlay_enabled", True):
            return
        self.overlay_active = not self.overlay_active
        # Activate the UI before sending its first snapshot; otherwise the
        # freshly enabled overlay discards suggestions for the current buffer.
        self.overlay_toggle_signal.emit(self.overlay_active)
        if not self.overlay_active:
            self.suggestions_signal.emit("", [])
        else:
            self._emit_suggestions()

    def _emit_suggestions(self):
        if not self.overlay_active:
            return
        buffer = self.buffer
        if not buffer:
            self.suggestions_signal.emit("", [])
            return
        settings = self.data.get("settings", {})
        profile = settings.get("active_profile", DEFAULT_PROFILE)
        case_sensitive = bool(settings.get("case_sensitive", False))
        with self.lock:
            keys = []
            for ckey, info in self.data.get("shortcuts", {}).items():
                if not info.get("enabled", True) or info.get("profile", DEFAULT_PROFILE) != profile:
                    continue
                _, bare = split_ckey(ckey)
                if bare:
                    keys.append((bare if case_sensitive else bare.casefold(), bare, str(info.get("text", ""))))
        # Ищем «текущее слово»: самый длинный хвост буфера после границы слова,
        # с которого начинается хоть один ключ. Буфер при этом не трогаем — от него зависит замена.
        query, found = "", []
        for i in range(len(buffer)):
            if i and buffer[i - 1].isalnum():
                continue
            tail = buffer[i:]
            needle = tail if case_sensitive else tail.casefold()
            matches = [(0 if cand == needle else 1, len(bare), bare, text)
                       for cand, bare, text in keys if cand.startswith(needle)]
            if matches:
                query, found = tail, matches
                break
        min_len = max(1, _to_int(settings.get("overlay_min_query_len", 2), 2))
        if not found or len(query) < min_len:
            self.suggestions_signal.emit("", [])
            return
        found.sort(key=lambda row: (row[0], row[1], row[2].casefold()))
        limit = max(1, min(8, _to_int(settings.get("overlay_max_suggestions", 5), 5)))
        self.suggestions_signal.emit(query, [(row[2], row[3]) for row in found[:limit]])

    # ---------- набор текста ----------

    def on_release(self, key):
        if not self.is_running or not PYNPUT_OK:
            return
        if key in (getattr(pk.Key, "ctrl", None), getattr(pk.Key, "ctrl_l", None), getattr(pk.Key, "ctrl_r", None),
                   getattr(pk.Key, "cmd", None), getattr(pk.Key, "cmd_l", None), getattr(pk.Key, "cmd_r", None)):
            self.buffer = ""

    def on_press(self, key):
        if not self.is_running or not PYNPUT_OK or key is None:
            return
        try:
            settings = self.data.get("settings", {})
            mode = settings.get("trigger_mode", "space")

            # Если долго ничего не печатали (бегали, просто играли) — сбрасываем буфер.
            now = time.time()
            idle_reset = max(500, _to_int(settings.get("buffer_idle_reset_ms", 3000), 3000)) / 1000.0
            if self.buffer and (now - self._last_char_time) > idle_reset:
                self.buffer = ""
                self._emit_suggestions()

            if key in self._modifier_keys:
                return  # Shift, Ctrl, Alt, Caps Lock сами по себе ничего не печатают — буфер не трогаем
            if key == pk.Key.backspace:
                self.buffer = self.buffer[:-1]
                self._emit_suggestions()
                return
            if key in (pk.Key.esc, pk.Key.tab, pk.Key.left, pk.Key.right, pk.Key.up, pk.Key.down, pk.Key.delete):
                self.buffer = ""
                self._emit_suggestions()
                return
            if key == pk.Key.space:
                self.suggestions_signal.emit("", [])
                if mode == "space":
                    self._match_boundary(" ")
                else:
                    self.buffer = ""
                return
            if key == pk.Key.enter:
                self.suggestions_signal.emit("", [])
                if mode == "enter" and not IS_WINDOWS:
                    self._match_boundary("\n")
                else:
                    # В Windows замену по Enter уже проверил _win32_event_filter.
                    self.buffer = ""
                return
            # pynput кэширует раскладку своего потока, которая может отличаться
            # от раскладки поля ввода. Оба потребителя буфера — замены и оверлей —
            # должны видеть символ, соответствующий текущему активному окну.
            ch = win_foreground_key_char(key) if IS_WINDOWS else getattr(key, "char", None)
            if not ch or len(ch) != 1 or ord(ch) < 32:
                self.buffer = ""
                self._emit_suggestions()
                return
            if ch in ".,!?;:" and mode == "punctuation":
                self._match_boundary(ch)
                return
            self.buffer += ch
            self._last_char_time = time.time()
            if len(self.buffer) > 256:
                self.buffer = self.buffer[-256:]
            self._emit_suggestions()
            if mode == "instant":
                self._check_match(boundary="")
        except Exception as exc:
            log.exception("Ошибка обработки клавиши")
            self.log_signal.emit(f"Ошибка обработки клавиши: {exc}")

    def _match_boundary(self, boundary):
        started = False
        try:
            started = self._check_match(boundary=boundary)
            return started
        finally:
            self.buffer = ""
            if not started:
                self.suggestions_signal.emit("", [])

    def _find_candidate(self):
        with self.lock:
            settings = self.data.get("settings", {})
            active_profile = settings.get("active_profile", DEFAULT_PROFILE)
            case_sensitive = bool(settings.get("case_sensitive", False))
            buffer = self.buffer if case_sensitive else self.buffer.casefold()
            if not buffer:
                return None
            best = None
            for ckey, info in self.data.get("shortcuts", {}).items():
                if not ckey or not info.get("enabled", True):
                    continue
                if info.get("profile", DEFAULT_PROFILE) != active_profile:
                    continue
                _, bare = split_ckey(ckey)
                if not bare:
                    continue
                check_key = bare if case_sensitive else bare.casefold()
                if buffer.endswith(check_key):
                    prefix = buffer[:-len(check_key)]
                    if prefix and prefix[-1].isalnum():
                        continue
                    if best is None or len(bare) > len(best[0]):
                        best = (bare, ckey, str(info.get("text", "")))
            return best

    def _check_match(self, boundary=""):
        """Ищет ключ в конце буфера и запускает замену. True — замена запущена."""
        found = self._find_candidate()
        if not found:
            return False
        matched_bare, matched_ckey, matched_text = found
        return self._start_replacement(matched_bare, matched_ckey, matched_text, boundary)

    def _fire_shortcut_hotkey(self, ckey):
        with self.lock:
            info = dict(self.data.get("shortcuts", {}).get(ckey) or {})
        if not info or not info.get("enabled", True):
            return
        _, bare = split_ckey(ckey)
        self._start_replacement(bare, ckey, str(info.get("text", "")), "", erase_count=0, from_hotkey=True)

    def _is_excluded(self, exe_name):
        excluded = self.data.get("settings", {}).get("excluded_apps", []) or []
        if not excluded or not exe_name:
            return False
        name = exe_name.lower()
        stem = name[:-4] if name.endswith(".exe") else name
        for item in excluded:
            entry = re.split(r"[\\/]", str(item).strip().strip('"'))[-1].lower()
            if entry.endswith(".exe"):
                entry = entry[:-4]
            if entry and entry == stem:
                return True
        return False

    def _start_replacement(self, matched_bare, matched_ckey, text, boundary, erase_count=None, from_hotkey=False):
        fg_name, fg_elevated = "", None
        if IS_WINDOWS:
            try:
                fg_name, fg_elevated = win_foreground_process()
            except Exception:
                log.exception("Не удалось определить активное окно")
            if self._is_excluded(fg_name):
                return False
        if not self._replacement_lock.acquire(blocking=False):
            self.log_signal.emit("Пропущено: предыдущая замена ещё выполняется.")
            return False
        self.buffer = ""
        # All trigger modes (including instant, punctuation and shortcut keys)
        # must discard the old query before injected text reaches the target.
        self.suggestions_signal.emit("", [])
        threading.Thread(
            target=self._execute_replacement,
            args=(matched_bare, matched_ckey, text, boundary),
            kwargs={"erase_count": erase_count, "from_hotkey": from_hotkey,
                    "fg_name": fg_name, "fg_elevated": fg_elevated},
            daemon=True,
        ).start()
        return True

    @staticmethod
    def _render_template(template):
        now = datetime.datetime.now()
        text = template.replace("{date}", now.strftime("%d.%m.%Y"))
        text = text.replace("{time}", now.strftime("%H:%M"))
        text = text.replace("{datetime}", now.strftime("%d.%m.%Y %H:%M:%S"))
        if "{clipboard}" in text:
            try:
                text = text.replace("{clipboard}", pyperclip.paste() or "")
            except Exception:
                log.warning("Не удалось прочитать буфер обмена для {clipboard}")
                text = text.replace("{clipboard}", "")
        return text

    @staticmethod
    def _extract_cursor(text, cursor_mode):
        """Убирает метку курсора; возвращает (текст, на сколько символов вернуть курсор влево или None)."""
        if cursor_mode not in ("punto", "both"):
            return text, None
        punto_match = re.search(r"\[(?:курсор|cursor)\]", text, flags=re.IGNORECASE)
        if punto_match:
            return text[:punto_match.start()] + text[punto_match.end():], len(text) - punto_match.end()
        if cursor_mode == "both" and "|" in text:
            marker_index = text.index("|")
            return text[:marker_index] + text[marker_index + 1:], len(text) - marker_index - 1
        return text, None

    def _paste_via_clipboard(self, text, restore):
        # Recheck immediately before touching the clipboard, not only when choosing
        # the insertion method. Rich clipboard contents are never flattened to text.
        if restore:
            try:
                protected = clipboard_has_foreign_data()
            except Exception:
                protected = True
            if protected:
                send_text(text)
                return
        old = None
        if restore:
            try:
                old = pyperclip.paste()
            except Exception:
                send_text(text)
                return
        pyperclip.copy(text)
        sequence = clipboard_sequence_number()
        try:
            time.sleep(0.05)
            send_ctrl_v()
            time.sleep(0.3)
        finally:
            if restore and old is not None:
                try:
                    unchanged = sequence is None or clipboard_sequence_number() == sequence
                    if unchanged and pyperclip.paste() == text and not clipboard_has_foreign_data():
                        pyperclip.copy(old)
                except Exception:
                    log.warning("Не удалось восстановить буфер обмена")

    def _execute_replacement(self, matched_key, matched_ckey, template, boundary, erase_count=None,
                             from_hotkey=False, fg_name="", fg_elevated=None):
        self._injecting.set()
        try:
            if not IS_WINDOWS:
                self.log_signal.emit("Глобальная вставка текста поддерживается в этой сборке только в Windows.")
                return
            settings = self.data.get("settings", {})
            if fg_elevated and not win_is_admin() and fg_name not in self._elevation_warned:
                self._elevation_warned.add(fg_name)
                self.log_signal.emit(f"«{fg_name or 'Окно'}» запущено от имени администратора — Windows блокирует "
                                     f"вставку. Запустите Эфир тоже от администратора.")
            if fg_elevated and not win_is_admin():
                raise RuntimeError("Вставка отменена: целевое окно имеет повышенные права.")
            if from_hotkey and not wait_modifiers_released():
                raise RuntimeError("Вставка отменена: отпустите Ctrl, Alt, Shift и Win и повторите сочетание.")

            # Render before deleting the trigger: preparation errors must not erase input.
            text = self._render_template(template)
            text, cursor_offset = self._extract_cursor(text, settings.get("cursor_mode", "punto"))

            # Пробел/знак препинания уже попали в программу — стираем их и печатаем заново после текста.
            # Enter в режиме «enter» перехвачен и до программы не дошёл — его нажимаем в конце.
            typed_boundary = boundary if boundary not in ("", "\n") else ""
            press_enter = boundary == "\n"
            if erase_count is None:
                erase_count = len(matched_key) + len(typed_boundary)
            for _ in range(erase_count):
                send_key(VK_BACK)
                time.sleep(0.004)

            if typed_boundary:
                text += typed_boundary
                if cursor_offset is not None:
                    cursor_offset += len(typed_boundary)
            if press_enter and cursor_offset is not None:
                press_enter = False  # в тексте есть место для курсора — Enter нажмёт сам пользователь

            if text:
                restore = bool(settings.get("restore_clipboard", True))
                has_emoji = any(ord(char) > 0xFFFF for char in text)
                use_clipboard = has_emoji
                if use_clipboard and restore:
                    try:
                        # Картинку или файлы из буфера обмена не вернуть — тогда печатаем эмодзи напрямую.
                        use_clipboard = not clipboard_has_foreign_data()
                    except Exception:
                        log.exception("Не удалось проверить буфер обмена")
                        use_clipboard = False
                if use_clipboard:
                    self._paste_via_clipboard(text, restore)
                else:
                    send_text(text)
                    time.sleep(0.15)

            if cursor_offset:
                send_key(VK_LEFT, times=min(cursor_offset, 2000))
            if press_enter:
                time.sleep(0.03)
                send_key(VK_RETURN)

            # Engine data is an independent normalized snapshot, NOT MainWindow.data.
            # Keep the engine mirror in sync; record_replacement increments the
            # GUI/persisted counter once. These are two different dictionaries.
            with self.lock:
                info = self.data.get("shortcuts", {}).get(matched_ckey)
                if info:
                    info["uses"] = _to_int(info.get("uses", 0), 0) + 1
            self.replacement_signal.emit(matched_ckey)
            self.log_signal.emit(f"Замена выполнена: {matched_key}")
        except Exception as exc:
            log.exception("Ошибка вставки для %s", matched_key)
            self.log_signal.emit(f"Ошибка вставки для «{matched_key}»: {exc}")
        finally:
            time.sleep(0.05)  # чтобы перехватчик успел пропустить последние наши нажатия
            self._injecting.clear()
            self._replacement_lock.release()



class LoadingOverlay(QWidget):
    """Лёгкий оверлей «Загрузка…» поверх таблицы.

    Не перехватывает клики (WA_TransparentForMouseEvents) и не рисует фон окна
    (WA_NoSystemBackground) — только полупрозрачную плашку со скруглением.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._text = "Загрузка…"
        self.hide()

    def setText(self, text):
        if text != self._text:
            self._text = text
            if self.isVisible():
                self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Полупрозрачное затемнение области таблицы
        painter.fillRect(self.rect(), QColor(17, 21, 34, 190))

        # Плашка по центру
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(self._text)
        chip_w = text_w + 70
        chip_h = 48
        x = (self.width() - chip_w) // 2
        y = (self.height() - chip_h) // 2

        painter.setBrush(QColor(29, 36, 54, 245))
        painter.setPen(QPen(QColor(108, 77, 255, 190), 1.5))
        painter.drawRoundedRect(x, y, chip_w, chip_h, 12, 12)

        # Пульсирующая точка слева
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#A58BFF"))
        cy = y + chip_h // 2
        painter.drawEllipse(x + 22, cy - 4, 8, 8)

        # Текст
        painter.setPen(QColor("#F1F2FF"))
        painter.setFont(QFont("Segoe UI", 12, QFont.DemiBold))
        painter.drawText(x, y, chip_w, chip_h, int(Qt.AlignCenter), self._text)

# --- КАСТОМНЫЕ ВИДЖЕТЫ ---
class _AccentGlow(QObject):
    """Мягкое свечение только у активной навигации и основных действий."""
    def __init__(self, button, role):
        super().__init__(button)
        self.button, self.role, self.level = button, role, 0.0
        self.effect = QGraphicsDropShadowEffect(button)
        self.effect.setOffset(0, 0)
        self.effect.setBlurRadius(18)
        button.setGraphicsEffect(self.effect)
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(170)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._apply)
        button.installEventFilter(self)
        self.refresh(immediate=True)

    def _apply(self, value):
        self.level = float(value)
        color = QColor("#905DFF")
        if self.role == "run" and getattr(self.button, "_running", False):
            color = QColor("#E85988")
        color.setAlpha(round(205 * self.level))
        self.effect.setColor(color)
        self.effect.setBlurRadius(12 + 12 * self.level)

    def refresh(self, immediate=False):
        active = bool(self.button.property("active"))
        over = self.button.underMouse() or self.button.hasFocus()
        if self.role == "nav":
            target = 0.93 if active else (0.30 if over else 0.0)
        elif self.role == "action":
            target = 0.98 if over else 0.80
        else:
            target = 0.95 if over else 0.72
        if not self.button.isEnabled():
            target = 0.0
        self.anim.stop()
        if immediate:
            self._apply(target)
        else:
            self.anim.setStartValue(self.level)
            self.anim.setEndValue(target)
            self.anim.start()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Enter, QEvent.Leave, QEvent.FocusIn, QEvent.FocusOut,
                            QEvent.EnabledChange, QEvent.Show):
            self.refresh()
        elif event.type() == QEvent.DynamicPropertyChange and bytes(event.propertyName()) == b"active":
            self.refresh()
        return False


class RibbonFrame(QFrame):
    def __init__(self, kind, parent=None):
        super().__init__(parent)
        self.kind = kind

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = float(self.width()), float(self.height())
        clip = QPainterPath()
        radius = 12 if self.kind == "header" else 13
        clip.addRoundedRect(QRectF(1,1,w-2,h-2), radius, radius)
        p.setClipPath(clip)
        if self.kind == "header":
            wave = QPainterPath(QPointF(w*.19,h))
            wave.cubicTo(w*.29,h*.45,w*.34,-h*.25,w*.44,0)
            wave.cubicTo(w*.56,h*.9,w*.62,-h*.4,w*.76,0)
            wave.cubicTo(w*.68,h*.16,w*.63,h*.75,w*.51,h*.54)
            wave.cubicTo(w*.38,-h*.1,w*.31,h*.8,w*.19,h)
            grad=QLinearGradient(0,0,w,h)
            grad.setColorAt(0,QColor(88,24,210,15));grad.setColorAt(.55,QColor(106,29,255,100));grad.setColorAt(1,QColor(49,11,126,0))
            p.setBrush(grad);p.setPen(QPen(QColor(133,58,255,70),.7));p.drawPath(wave)
        else:
            wave=QPainterPath(QPointF(0,h*.65))
            wave.cubicTo(w*.65,h*.58,w*.72,h*.28,w,h*.25)
            wave.lineTo(w,h*.46)
            wave.cubicTo(w*.5,h*.53,w*.45,h*.75,0,h*.65)
            grad=QLinearGradient(0,0,w,h)
            grad.setColorAt(0,QColor(40,8,100,0));grad.setColorAt(.7,QColor(75,20,180,55));grad.setColorAt(1,QColor(99,34,255,100))
            p.setBrush(grad);p.setPen(Qt.NoPen);p.drawPath(wave)
            lower=QPainterPath(QPointF(0,h*.67))
            lower.cubicTo(w*.4,h*.60,w*.55,h*.63,w,h*.67)
            lower.lineTo(w,h);lower.lineTo(0,h);lower.closeSubpath()
            shade=QLinearGradient(0,h*.62,0,h)
            shade.setColorAt(0,QColor(98,34,255,100));shade.setColorAt(.3,QColor(37,13,96,20));shade.setColorAt(1,QColor(8,8,23,0))
            p.setBrush(shade);p.setPen(QPen(QColor(137,65,255,85),1));p.drawPath(lower)


class SmoothActionButton(QPushButton):
    """Vector-painted rounded action: no font-symbol icons or raster corner masks."""
    def __init__(self, text="", tone="subtle", parent=None):
        super().__init__(text, parent)
        self.tone=tone
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing|QPainter.SmoothPixmapTransform)
        accent=self.tone=="accent"
        top=QColor("#6531B7" if accent else "#241A48")
        bottom=QColor("#3B1C78" if accent else "#15122E")
        if self.underMouse():
            top=top.lighter(120);bottom=bottom.lighter(115)
        if self.isDown():
            top=top.darker(115);bottom=bottom.darker(115)
        if not self.isEnabled():p.setOpacity(.5)
        fill=QLinearGradient(0,0,0,self.height());fill.setColorAt(0,top);fill.setColorAt(1,bottom)
        p.setBrush(fill)
        p.setPen(QPen(QColor("#C092FF" if self.hasFocus() else "#8654CC" if accent else "#413064"),.8))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.6,.6,-.6,-.6),12,12)
        if self.layout() is not None:
            return  # Label and chevron are laid out explicitly by CollapsibleSection.
        f=QFont("Segoe UI");f.setPixelSize(12);f.setWeight(QFont.DemiBold if accent else QFont.Medium)
        p.setFont(f);p.setPen(QColor("#F5ECFF"))
        fm=p.fontMetrics();text=fm.elidedText(self.text(),Qt.ElideRight,max(0,self.width()-24-(22 if not self.icon().isNull() else 0)))
        tw=fm.horizontalAdvance(text);iw=16 if not self.icon().isNull() else 0;gap=6 if iw else 0
        x=(self.width()-tw-iw-gap)/2
        if iw:self.icon().paint(p,QRect(round(x),round((self.height()-16)/2),16,16))
        p.drawText(QRectF(x+iw+gap,0,tw+1,self.height()),int(Qt.AlignVCenter|Qt.AlignLeft),text)


class SmoothStatusDot(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.running=False
        self.setFixedSize(12,16)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        color=QColor(getattr(self,"color",None) or ("#53EE97" if self.running else "#FF657D"))
        center=QPointF(self.width()/2.,self.height()/2.)
        halo=QRadialGradient(center,6)
        light=QColor(color);light.setAlpha(65)
        clear=QColor(color);clear.setAlpha(0)
        halo.setColorAt(0,light);halo.setColorAt(.55,light);halo.setColorAt(1,clear)
        p.setPen(Qt.NoPen);p.setBrush(halo);p.drawEllipse(center,6,6)
        p.setBrush(color);p.drawEllipse(center,4,4)


class SmoothStatusPill(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.running=False
        self.setAttribute(Qt.WA_TranslucentBackground)

    def set_running(self,running):
        self.running=bool(running)
        self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        fill=QLinearGradient(0,0,0,self.height())
        fill.setColorAt(0,QColor("#0C3029" if self.running else "#20172F"))
        fill.setColorAt(1,QColor("#0D211F" if self.running else "#171326"))
        p.setBrush(fill);p.setPen(QPen(QColor("#194638" if self.running else "#3B2B50"),.8))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(.6,.6,-.6,-.6),15,15)


def paint_selection_box(painter, rect, checked):
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor("#C8ACFF" if checked else "#8174B1"),1.3))
    painter.setBrush(QColor("#D7C0FF" if checked else "#0C0D21"))
    painter.drawRoundedRect(rect,4,4)
    if checked:
        painter.setPen(QPen(QColor("#6521E4"),1.8,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
        tick=QPainterPath(QPointF(rect.x()+4,rect.y()+8))
        tick.lineTo(rect.x()+7,rect.y()+11);tick.lineTo(rect.x()+12,rect.y()+5)
        painter.drawPath(tick)


class ShortcutContextMenu(QMenu):
    """Native keyboard/submenu behavior with an antialiased amethyst surface."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags()|Qt.FramelessWindowHint|Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(292)
        self.setStyleSheet('''
            QMenu {background:transparent;border:none;padding:12px 12px;font-size:13px;}
            QMenu::item {padding:11px 28px 11px 38px;background:transparent;border:none;}
            QMenu::separator {height:9px;margin:0 8px;background:transparent;}
            QMenu::icon {left:12px;}
        ''')
        self.hovered.connect(lambda _action:self.update())
        self.aboutToShow.connect(self.update)

    def add_header(self,title,caption):
        header=QWidget(self)
        header.setFixedHeight(52)
        header.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout=QVBoxLayout(header);layout.setContentsMargins(12,3,12,10);layout.setSpacing(3)
        cap=ElidedLabel(caption);cap.setStyleSheet('color:#A996CB;font-size:10px;font-weight:500;background:transparent;')
        name=ElidedLabel(title);name.setStyleSheet('color:#F4EBFF;font-size:14px;font-weight:600;background:transparent;')
        layout.addWidget(cap);layout.addWidget(name)
        action=QWidgetAction(self);action.setDefaultWidget(header);action.setEnabled(False)
        self.addAction(action)

    def add_command(self,text,glyph,callback=None,danger=False):
        action=QAction(text,self)
        color='#FF759D' if danger else '#C6A4FF'
        action.setIcon(QIcon(glyph_pixmap(glyph,color,24,12)))
        action.setProperty('danger',danger)
        if callback is not None:action.triggered.connect(lambda _checked=False:callback())
        self.addAction(action)
        return action

    def add_branch(self,text,glyph):
        branch=ShortcutContextMenu(self)
        branch.setTitle(text)
        action=self.add_command(text,glyph)
        action.setMenu(branch)
        return branch

    def paintEvent(self,event):
        p=QPainter(self)
        p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing|QPainter.SmoothPixmapTransform)
        panel=QRectF(self.rect()).adjusted(5.5,5.5,-5.5,-5.5)
        for outset,alpha in ((4,12),(3,16),(2,22),(1,30)):
            p.setPen(QPen(QColor(104,49,192,alpha),1.3));p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(panel.adjusted(-outset,-outset,outset,outset),14+outset,14+outset)
        gradient=QLinearGradient(0,0,self.width(),self.height())
        gradient.setColorAt(0,QColor('#24163F'));gradient.setColorAt(.35,QColor('#151126'));gradient.setColorAt(1,QColor('#121024'))
        p.setBrush(gradient);p.setPen(QPen(QColor('#705099'),.9));p.drawRoundedRect(panel,14,14)
        font=QFont('Segoe UI');font.setPixelSize(13);font.setWeight(QFont.Medium);p.setFont(font)
        for action in self.actions():
            if not action.isVisible() or isinstance(action,QWidgetAction):continue
            rect=self.actionGeometry(action)
            if action.isSeparator():
                p.setPen(QPen(QColor('#3C2B55'),.8));p.drawLine(QPointF(rect.left()+10,rect.center().y()),QPointF(rect.right()-10,rect.center().y()))
                continue
            danger=bool(action.property('danger'));active=self.activeAction() is action and action.isEnabled()
            r=QRectF(rect).adjusted(0,.5,0,-.5)
            if active:
                fill=QLinearGradient(r.left(),0,r.right(),0)
                fill.setColorAt(0,QColor('#532C92' if not danger else '#4B1C37'))
                fill.setColorAt(1,QColor('#2E1D55' if not danger else '#291426'))
                p.setBrush(fill);p.setPen(QPen(QColor('#9164C9' if not danger else '#B34A71'),.7));p.drawRoundedRect(r,9,9)
            text_color='#FF86AA' if danger else '#FAF4FF' if active else '#E9DFF8'
            if not action.isEnabled():text_color='#716580'
            p.setPen(QColor(text_color));p.setFont(font)
            icon_rect=QRect(rect.left()+11,rect.center().y()-10,20,20)
            action.icon().paint(p,icon_rect,Qt.AlignCenter,QIcon.Normal if action.isEnabled() else QIcon.Disabled)
            text_rect=rect.adjusted(40,0,-25,0)
            text=p.fontMetrics().elidedText(action.text(),Qt.ElideRight,text_rect.width())
            p.drawText(text_rect,int(Qt.AlignVCenter|Qt.AlignLeft),text)
            if action.menu() is not None:
                p.setPen(QPen(QColor('#C6ACEA'),1.4,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
                x,y=rect.right()-13,rect.center().y()
                path=QPainterPath(QPointF(x-3,y-4));path.lineTo(x+1,y);path.lineTo(x-3,y+4);p.drawPath(path)


class _StatusBox(QFrame):
    """Нейтральная группа статуса и действия — без вложенных капсул и свечения."""
    pass


class _TableOutline(QWidget):
    """Рамка поверх дочернего view: края не перекрываются заголовком или строками."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor("#4A3682"), 1))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)


class TableCard(QFrame):
    """Непрерывная рамка независимо от отрисовки заголовка и viewport таблицы."""
    def __init__(self, table, parent=None):
        super().__init__(parent)
        self.setObjectName("tableCard")
        self.table = table
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(100)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        table.setFrameShape(QFrame.NoFrame)
        table.setObjectName("cardTable")
        table.installEventFilter(self)
        layout.addWidget(table)
        self._outline = _TableOutline(self)
        self._outline.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._outline.setGeometry(self.rect())
        self._outline.raise_()

    def eventFilter(self, obj, event):
        if obj is self.table and event.type() in (QEvent.Resize, QEvent.Show):
            path = QPainterPath()
            path.setFillRule(Qt.WindingFill)
            path.addRoundedRect(QRectF(self.table.rect()), 11, 11)
            if getattr(self,"has_footer",False):
                path.addRect(QRectF(0,11,self.table.width(),max(0,self.table.height()-11)))
            self.table.setMask(QRegion(path.toFillPolygon().toPolygon(), Qt.WindingFill))
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor("#0F1228"))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)


class HoverTable(QTableView):
    actionsRequested = pyqtSignal(QModelIndex)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_row = -1
        self.hover_col = -1
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self._row_levels = {}
        self._row_starts = {}
        self._row_targets = {}
        self._row_anim = QVariantAnimation(self)
        self._row_anim.setDuration(160)
        self._row_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._row_anim.valueChanged.connect(self._advance_rows)

    def row_highlight(self, row, selected=False):
        return self._row_levels.get(row, (float(row == self.hover_row), float(selected),
                                          float(row == self.hover_row and self.hover_col == 4)))

    def _animate_rows(self):
        if self.model() is None or self.selectionModel() is None:
            return
        self._row_anim.stop()
        first = max(0, self.rowAt(0))
        last = self.rowAt(self.viewport().height() - 1)
        if last < 0:
            last = self.model().rowCount() - 1
        self._row_starts, self._row_targets = {}, {}
        for row in range(first, last + 1):
            selected = self.selectionModel().isSelected(self.model().index(row, 0))
            self._row_starts[row] = self._row_levels.get(row, (0.0, 0.0, 0.0))
            self._row_targets[row] = (float(row == self.hover_row), float(selected),
                                      float(row == self.hover_row and self.hover_col == 4))
        self._row_anim.setStartValue(0.0)
        self._row_anim.setEndValue(1.0)
        self._row_anim.start()

    def _advance_rows(self, value):
        t = float(value)
        self._row_levels = {
            row: tuple(a + (b - a) * t for a, b in zip(start, self._row_targets[row]))
            for row, start in self._row_starts.items()
        }
        self.viewport().update()

    def selectionChanged(self, selected, deselected):
        super().selectionChanged(selected, deselected)
        if hasattr(self, "_row_anim"):
            self._animate_rows()

    def clear_row_highlight(self):
        self._row_anim.stop()
        self._row_levels.clear()
        self.hover_row = self.hover_col = -1
        self.viewport().update()

    def reset(self):
        if hasattr(self, "_row_anim"):
            self.clear_row_highlight()
        super().reset()

    def mousePressEvent(self,event):
        index=self.indexAt(event.pos())
        if event.button()==Qt.LeftButton and index.isValid() and index.column()==0 and event.pos().x()<40:
            selected=self.selectionModel().isSelected(index)
            flag=QItemSelectionModel.Deselect if selected else QItemSelectionModel.Select
            self.selectionModel().select(index,flag|QItemSelectionModel.Rows)
            self._checkbox_press=True
            event.accept()
            return
        self._checkbox_press=False
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if getattr(self,"_checkbox_press",False):
            self._checkbox_press=False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.pos()
        row = self.rowAt(pos.y())
        col = self.columnAt(pos.x()) if row >= 0 else -1
        if row != self.hover_row or col != self.hover_col:
            self.hover_row, self.hover_col = row, col
            self._animate_rows()
        if row >= 0 and col in (3, 4):
            self.viewport().setCursor(Qt.PointingHandCursor)
        else:
            self.viewport().unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_row = -1
        self.hover_col = -1
        self.viewport().unsetCursor()
        self._animate_rows()
        super().leaveEvent(event)

MDL2 = "Segoe MDL2 Assets"


def glyph_pixmap(char, color="#B0BBCB", px=20, pt=11):
    """Единые тонкие векторные иконки; чёткая отрисовка на HiDPI.

    Коды сохранены для совместимости с существующими вызовами.
    Редкие пользовательские глифы по-прежнему поддерживаются через MDL2.
    """
    pix = QPixmap(px * 2, px * 2)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    artwork = min(float(px), max(16.0, pt * 1.9))
    p.translate((px - artwork) / 2.0, (px - artwork) / 2.0)
    p.scale(artwork / 24.0, artwork / 24.0)
    p.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(Qt.NoBrush)
    def lines(*points):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))
        p.drawPath(path)
    def box(x, y, w, h, radius=2):
        p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)
    code = ord(char) if len(char) == 1 else 0
    if code in (0xE710, 0xE921, 0xE738):
        lines((5,12),(19,12))
        if code == 0xE710: lines((12,5),(12,19))
    elif code == 0xE73E:
        lines((5,12),(10,17),(19,7))
    elif code == 0xE711:
        lines((6,6),(18,18)); lines((18,6),(6,18))
    elif code in (0xE70D, 0xE70E):
        lines(*(((6,9),(12,15),(18,9)) if code == 0xE70D else ((6,15),(12,9),(18,15))))
    elif code == 0xE72A:
        lines((4,12),(20,12)); lines((14,6),(20,12),(14,18))
    elif code in (0xE896, 0xE898):
        if code == 0xE896:
            lines((12,3),(12,15)); lines((7,10),(12,15),(17,10))
        else:
            lines((12,15),(12,3)); lines((7,8),(12,3),(17,8))
        lines((5,17),(5,20),(19,20),(19,17))
    elif code == 0xE721:
        p.drawEllipse(QRectF(4,4,12,12)); lines((15,15),(20,20))
    elif code == 0xE8C8:
        box(8,8,12,13); lines((15,8),(15,3),(3,3),(3,16),(8,16))
    elif code == 0xE74E:
        lines((4,3),(17,3),(21,7),(21,21),(3,21),(3,3),(4,3))
        box(7,3,9,6,0); box(7,14,10,7,1)
    elif code == 0xE74D:
        lines((4,6),(20,6)); lines((9,6),(9,3),(15,3),(15,6))
        lines((6,6),(7,21),(17,21),(18,6)); lines((10,10),(10,17)); lines((14,10),(14,17))
    elif code == 0xE00B:
        heart=QPainterPath(QPointF(12,21))
        heart.cubicTo(9,18,2,13,2,8);heart.cubicTo(2,2,9,1,12,7)
        heart.cubicTo(15,1,22,2,22,8);heart.cubicTo(22,13,15,18,12,21)
        p.drawPath(heart)
    elif code == 0xE734:
        import math
        points=[]
        for i in range(10):
            angle=-math.pi/2+i*math.pi/5; r=9 if i%2==0 else 4.3
            points.append((12+math.cos(angle)*r,12+math.sin(angle)*r))
        lines(*(points+[points[0]]))
    elif code == 0xE8FD:
        for y in (6,12,18):
            lines((9,y),(21,y)); box(3,y-1,2,2,0.5)
    elif code in (0xE8B7, 0xE7A7):
        lines((3,7),(3,20),(21,20),(21,7),(13,7),(10,4),(3,4),(3,7))
    elif code == 0xE74C:
        for x,y in ((3,3),(13,3),(3,13),(13,13)):
            tile=QLinearGradient(x,y,x+7,y+7)
            tint=QColor(color)
            tile.setColorAt(0,tint.lighter(135));tile.setColorAt(1,tint.darker(140))
            p.setBrush(tile);box(x,y,7,7,1.3)
    elif code == 0xE81C:
        arc = QPainterPath(QPointF(3, 11))
        arc.cubicTo(3.5, 6.5, 7.3, 3, 12, 3)
        arc.cubicTo(17, 3, 21, 7, 21, 12)
        arc.cubicTo(21, 17, 17, 21, 12, 21)
        arc.cubicTo(8.5, 21, 5.5, 19, 4, 16)
        p.drawPath(arc)
        lines((3,5),(3,11),(9,11))
        lines((12,7),(12,12),(16,14))
    elif code == 0xE713:
        import math
        points=[]
        for i in range(32):
            angle=i*math.pi/16; radius=10 if i%4 in (1,2) else 8
            points.append((12+math.cos(angle)*radius,12+math.sin(angle)*radius))
        lines(*(points+[points[0]])); p.drawEllipse(QRectF(8.5,8.5,7,7))
    elif code in (0xE70F, 0xE765):
        if code == 0xE70F:
            lines((4,15),(16,3),(21,8),(9,20),(3,21),(4,15)); lines((13,6),(18,11))
        else:
            box(2,6,20,13,2)
            for y in (10,13):
                for x in (6,10,14,18): lines((x,y),(x+0.1,y))
            lines((7,16),(17,16))
    elif code == 0xE7FC:
        path=QPainterPath();path.moveTo(8,6);path.lineTo(16,6)
        path.cubicTo(21,6,23,19,19,19);path.cubicTo(17,19,17,15,15,15)
        path.lineTo(9,15);path.cubicTo(7,15,7,19,5,19);path.cubicTo(1,19,3,6,8,6)
        p.drawPath(path);lines((6,10),(10,10));lines((8,8),(8,12))
        p.drawEllipse(QRectF(16,9,1,1));p.drawEllipse(QRectF(18,12,1,1))
    elif code == 0xE71B:
        lines((4,4),(4,9),(12,15),(12,21));lines((20,4),(20,9),(12,15))
    elif code == 0xE768:
        lines((8,4),(20,12),(8,20),(8,4))
    elif code == 0xE7C1:
        bolt=QPainterPath(QPointF(13,2));bolt.lineTo(5,13);bolt.lineTo(11,13)
        bolt.lineTo(9,22);bolt.lineTo(20,9);bolt.lineTo(14,9);bolt.closeSubpath()
        p.setBrush(QColor(color));p.drawPath(bolt)
    elif code in (0xE945, 0xE7F4):
        p.drawEllipse(QRectF(3,3,18,18));lines((12,7),(12,13));lines((12,17),(12,17.1))
    elif code == 0xE706:
        p.drawArc(QRectF(4,7,16,14), 180*16, -180*16)
        lines((12,3),(12,16));lines((8,12),(12,16),(16,12))
    elif code in (0xE916, 0xE8B0):
        box(3,4,18,13,2);lines((9,21),(15,21));lines((12,17),(12,21))
    elif code == 0xE72C:
        lines((5,5),(5,19),(19,19));lines((8,13),(12,9),(16,13),(21,5))
    elif code == 0xE7E8:
        box(5,3,14,18,2);lines((9,8),(15,8));lines((9,12),(15,12));lines((9,16),(13,16))
    else:
        p.resetTransform()
        p.setFont(QFont(MDL2, pt))
        p.drawText(QRect(0,0,px,px), Qt.AlignCenter, char)
    p.end()
    return pix


class SegmentedControl(QWidget):
    """Строгий сегментированный выбор: заменяет выпадающие списки и переключатели.

    options: [(подпись, значение, глиф_MDL2_или_None), ...]
    """
    changed = pyqtSignal(object)

    STYLE = """
        QFrame#segFrame { background: #110D25; border: 1px solid #483064; border-radius: 9px; }
        QPushButton#seg { background: transparent; border: 1px solid transparent; border-radius: 7px;
                          padding: 7px 14px; color: #B0BBCB; font-weight: 600; }
        QPushButton#seg:hover { color: #F1F2FF; background: #281941; }
        QPushButton#seg:pressed { background: #281941; }
        QPushButton#seg:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #6331A8,stop:1 #382057); color: #F1E1FF; border: 1px solid #A06BDF; }
        QPushButton#seg[tone="on"]:checked { background: #14261D; color: #2ED573; border: 1px solid #245C3B; }
        QPushButton#seg[tone="off"]:checked { background: #2A2039; color: #CDD5E2; border: 1px solid #5B4679; }
    """

    def __init__(self, options, value=None, parent=None):
        super().__init__(parent)
        self._options = list(options)
        self._value = None
        self._buttons = []
        self._block = False

        frame = QFrame()
        frame.setObjectName("segFrame")
        frame.setStyleSheet(self.STYLE)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

        row = QHBoxLayout(frame)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(3)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        for idx, opt in enumerate(self._options):
            label = opt[0]
            glyph = opt[2] if len(opt) > 2 else None
            tone = opt[3] if len(opt) > 3 else None
            btn = QPushButton(f"  {label}" if glyph else label)
            btn.setObjectName("seg")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            if tone:
                btn.setProperty("tone", tone)
            if glyph:
                on_color = {"on": "#2ED573", "off": "#CDD5E2"}.get(tone, "#F1E1FF")
                icon = QIcon()
                icon.addPixmap(glyph_pixmap(glyph, "#919DBF", 16, 9), QIcon.Normal, QIcon.Off)
                icon.addPixmap(glyph_pixmap(glyph, on_color, 16, 9), QIcon.Normal, QIcon.On)
                btn.setIcon(icon)
                btn.setIconSize(QSize(14, 14))
            self._group.addButton(btn, idx)
            row.addWidget(btn)
            self._buttons.append(btn)

        (getattr(self._group, "idClicked", None) or self._group.buttonClicked[int]).connect(self._on_clicked)
        if value is None and self._options:
            value = self._options[0][1]
        self.setValue(value)

    def _on_clicked(self, idx):
        new_val = self._options[idx][1]
        if new_val != self._value:
            self._value = new_val
            self.changed.emit(new_val)

    def value(self):
        return self._value

    def setValue(self, value):
        """Программная установка значения (сигнал не посылается)."""
        for idx, opt in enumerate(self._options):
            if opt[1] == value:
                self._value = value
                self._buttons[idx].setChecked(True)
                return
        if self._options:
            self._value = self._options[0][1]
            self._buttons[0].setChecked(True)


class BoolChoice(SegmentedControl):
    """Вместо переключателя-«тумблера»: две кнопки ✓ Вкл / ✕ Выкл."""
    toggled = pyqtSignal(bool)

    def __init__(self, on_text="Вкл", off_text="Выкл", checked=True, parent=None):
        super().__init__(
            [(on_text, True, "\uE73E", "on"), (off_text, False, "\uE711", "off")],
            value=bool(checked), parent=parent,
        )
        self.changed.connect(lambda v: self.toggled.emit(bool(v)))

    def isChecked(self):
        return bool(self.value())

    def setChecked(self, state):
        self.setValue(bool(state))


class ModernSpinBox(QWidget):
    """Степпер:  [−]  значение  [+]  (удержание кнопки ускоряет изменение)."""
    valueChanged = pyqtSignal(int)

    def __init__(self, min_val=0, max_val=100, val=0, step=1, suffix=""):
        super().__init__()
        self._min, self._max, self._step = min_val, max_val, step
        self._value = max(min_val, min(max_val, val))
        self._suffix = suffix

        self.setObjectName("modernSpin")
        self.setFixedHeight(40)
        self.setMinimumWidth(176)
        self.setStyleSheet("""
            QWidget#modernSpin { background: #110D25; border: 1px solid #483064; border-radius: 9px; }
            QPushButton { background: #281941; border: 1px solid #483064; border-radius: 7px;
                          color: #CDD5E2; font-size: 13px; padding: 0; }
            QPushButton:hover { background: #52278A; border-color: #8F81B1; color: #F1E1FF; }
            QPushButton:pressed { background: #252044; }
            QPushButton:disabled { background: #151A35; color: #3D4660; border-color: #303655; }
            QLabel { color: #F1F2FF; font-weight: 600; font-size: 13px; background: transparent; }
        """)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)

        self.btn_minus = QPushButton()
        self.btn_plus = QPushButton()
        self.btn_minus.setIcon(QIcon(glyph_pixmap("\uE738", "#CDD5E2", 18, 10)))
        self.btn_plus.setIcon(QIcon(glyph_pixmap("\uE710", "#CDD5E2", 18, 10)))
        self.btn_minus.setAccessibleName("Уменьшить значение")
        self.btn_plus.setAccessibleName("Увеличить значение")
        self.btn_minus.setToolTip("Уменьшить")
        self.btn_plus.setToolTip("Увеличить")
        for b, fn in ((self.btn_minus, self._decrease), (self.btn_plus, self._increase)):
            b.setFixedSize(32, 32)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(350)
            b.setAutoRepeatInterval(70)
            b.clicked.connect(fn)

        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)

        lay.addWidget(self.btn_minus)
        lay.addWidget(self.label, 1)
        lay.addWidget(self.btn_plus)
        self._refresh()

    def _refresh(self):
        self.label.setText(f"{self._value}{self._suffix}")
        self.btn_minus.setEnabled(self._value > self._min)
        self.btn_plus.setEnabled(self._value < self._max)

    def _set(self, v):
        v = max(self._min, min(self._max, v))
        if v != self._value:
            self._value = v
            self._refresh()
            self.valueChanged.emit(self._value)

    def _increase(self):
        self._set(self._value + self._step)

    def _decrease(self):
        self._set(self._value - self._step)

    def value(self):
        return self._value

    def setValue(self, val):
        self._value = max(self._min, min(self._max, int(val)))
        self._refresh()


class AmethystPanel(QFrame):
    """Shared antialiased glass surface for pages and the non-activating overlay."""
    def __init__(self,parent=None,danger=False):
        super().__init__(parent)
        self.danger=danger

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        rect=QRectF(self.rect()).adjusted(.6,.6,-.6,-.6)
        path=QPainterPath();path.addRoundedRect(rect,14,14)
        p.setClipPath(path)
        fill=QLinearGradient(0,0,self.width(),self.height())
        fill.setColorAt(0,QColor('#2C1428' if self.danger else '#23163F'))
        fill.setColorAt(.35,QColor('#1B101D' if self.danger else '#151027'))
        fill.setColorAt(1,QColor('#180E1B' if self.danger else '#100D23'))
        p.fillPath(path,fill)
        glow=QRadialGradient(QPointF(self.width()*.15,0),max(120,self.width()*.7))
        glow.setColorAt(0,QColor(203,57,108,18) if self.danger else QColor(151,64,255,22))
        glow.setColorAt(1,QColor(80,30,140,0));p.fillPath(path,glow)
        p.setClipping(False);p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor('#75364F' if self.danger else '#4D327A'),.9));p.drawPath(path)


class SidebarDivider(QWidget):
    """A quiet amethyst rule separating the profile picker from settings."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(12)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        y = self.height() / 2
        gradient = QLinearGradient(12, y, self.width()-12, y)
        gradient.setColorAt(0, QColor(117, 77, 175, 0))
        gradient.setColorAt(.5, QColor(153, 106, 217, 155))
        gradient.setColorAt(1, QColor(117, 77, 175, 0))
        p.setPen(QPen(QBrush(gradient), 1))
        p.drawLine(QPointF(12,y), QPointF(self.width()-12,y))
        p.setPen(Qt.NoPen); p.setBrush(QColor("#AE80E4"))
        p.drawEllipse(QPointF(self.width()/2,y), 1.6, 1.6)


class SearchClearButton(QPushButton):
    """Small vector cross with short hover/press feedback; presence changes immediately."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24,24)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Очистить поиск")
        self.setToolTip("Очистить поиск")
        self._hover = self._press = self._opacity = 0.0
        self._present = False
        self._animations = {}
        for name in ("hover", "press"):
            animation = QVariantAnimation(self)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.valueChanged.connect(lambda value,n=name:self._set_value(n,value))
            self._animations[name] = animation
        self.hide()

    def _set_value(self, name, value):
        setattr(self, "_"+name, float(value)); self.update()

    def _animate(self, name, target, duration):
        animation = self._animations[name]
        animation.stop(); animation.setDuration(duration)
        animation.setStartValue(getattr(self,"_"+name)); animation.setEndValue(target)
        animation.start()

    def set_present(self, present):
        present = bool(present)
        if present == self._present:
            return
        self._present = present
        self.setEnabled(self._present)
        # No fade or delayed callback: typing/clicking changes visibility now.
        self._opacity = 1.0 if present else 0.0
        if present:
            self.show()
        else:
            self.hide()
            self._animations["hover"].stop()
            self._animations["press"].stop()
            self._hover = self._press = 0.0
        self.update()

    def enterEvent(self, event):
        self._animate("hover",1.0,70); super().enterEvent(event)

    def leaveEvent(self, event):
        self._animate("hover",0.0,80); super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._animate("press",1.0,40)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._animate("press",0.0,70)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.setOpacity(self._opacity)
        p.translate(12,12)
        scale=1-.12*self._press;p.scale(scale,scale)
        glow=QRadialGradient(QPointF(0,0),12)
        glow.setColorAt(0,QColor(164,93,255,round(65*self._hover+30*self._press)))
        glow.setColorAt(1,QColor(103,47,180,0))
        p.setPen(Qt.NoPen);p.setBrush(glow);p.drawEllipse(QPointF(0,0),12,12)
        p.setBrush(QColor(125,72,192,round(15+38*self._hover)))
        p.setPen(QPen(QColor(168,116,231,round(25+110*self._hover)),.8))
        p.drawRoundedRect(QRectF(-9,-9,18,18),6,6)
        p.setPen(QPen(QColor("#F0DFFF" if self._hover>.2 else "#AE9BC9"),1.5,Qt.SolidLine,Qt.RoundCap))
        p.drawLine(QPointF(-3,-3),QPointF(3,3));p.drawLine(QPointF(-3,3),QPointF(3,-3))


class SearchLineEdit(QLineEdit):
    cleared = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextMargins(0,0,30,0)
        self.clear_button = SearchClearButton(self)
        self.clear_button.clicked.connect(self._clear_from_button)
        self.textChanged.connect(lambda text:self.clear_button.set_present(bool(text)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.clear_button.move(self.width()-32,(self.height()-24)//2)

    def _clear_from_button(self):
        self.clear()
        self.setFocus(Qt.MouseFocusReason)
        # Paint the now-empty field before filtering a large table. No nested
        # processEvents() and no animation/timer gating the actual clear.
        self.repaint()
        self.cleared.emit()


class CountNavButton(QPushButton):
    """Reserve real space for the full count instead of overlaying it on the label."""
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing|QPainter.SmoothPixmapTransform)
        active=bool(self.property('active'))
        if active or self.underMouse() or self.hasFocus():
            fill=QLinearGradient(0,0,self.width(),0)
            fill.setColorAt(0,QColor('#7D3DE0' if active else '#2C1C4E'))
            fill.setColorAt(1,QColor('#372064' if active else '#211833'))
            p.setBrush(fill);p.setPen(QPen(QColor('#BD92FF' if active else '#65418F'),1))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),11,11)
            if active:
                p.setBrush(QColor('#D3ACFF'));p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(1,9,3,self.height()-18),1.5,1.5)
        self.icon().paint(p,QRect(12,(self.height()-22)//2,22,22))
        f=QFont('Segoe UI');f.setPixelSize(12);f.setWeight(QFont.DemiBold if active else QFont.Medium)
        p.setFont(f);p.setPen(QColor('#FFFFFF' if active else '#BFB1E4'))
        right=self.badge.geometry().left()-6 if getattr(self,'badge',None) is not None else self.width()-10
        rect=QRect(43,0,max(0,right-43),self.height())
        p.drawText(rect,int(Qt.AlignLeft|Qt.AlignVCenter),p.fontMetrics().elidedText(self.text().strip(),Qt.ElideRight,rect.width()))


class SettingGroup(QFrame):
    """Строгая карточка-список: заголовок группы + строки настроек с разделителями."""

    def __init__(self, title=None, danger=False, parent=None):
        super().__init__(parent)
        self._danger = danger
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        if title:
            cap = QLabel(title.upper())
            cap.setStyleSheet(
                f"color: {'#D497A1' if danger else '#B2A0CC'}; font-size: 11px; "
                "font-weight: 600; letter-spacing: 1px; padding-left: 4px;"
            )
            outer.addWidget(cap)

        self._card = AmethystPanel(danger=danger)
        self._card.setObjectName("sgCard")
        self._lay = QVBoxLayout(self._card)
        self._lay.setContentsMargins(1, 1, 1, 1)
        self._lay.setSpacing(0)
        outer.addWidget(self._card)
        self._rows = 0

    def add_row(self, icon, title, desc, control=None, stacked=False):
        if self._rows:
            sep = QFrame()
            sep.setFixedHeight(1)
            sep.setStyleSheet(
                f"background: {'#3a242c' if self._danger else '#342346'}; border: none;"
            )
            self._lay.addWidget(sep)
        self._rows += 1

        row = QWidget()
        outer = QVBoxLayout(row)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(14)

        tile = QLabel()
        tile.setFixedSize(38, 38)
        tile.setAlignment(Qt.AlignCenter)
        is_glyph = len(icon) == 1 and ord(icon) >= 0xE000
        if is_glyph:
            tile.setPixmap(glyph_pixmap(icon, "#FF8089" if self._danger else "#A58BFF", 38, 13))
        else:
            tile.setText(icon)
        tile.setStyleSheet(
            "background: %s; border: 1px solid %s; border-radius: 10px; "
            "color: #A58BFF; font-weight: 600; font-size: 13px;"
            % (("#2B2027", "#72545E") if self._danger else ("#2B194A", "#5C3984"))
        )
        top.addWidget(tile, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size: 13px; font-weight: 600; color: #F1F2FF; background: transparent;")
        col.addWidget(t)
        if desc:
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet("font-size: 12px; color: #A0AABB; background: transparent;")
            col.addWidget(d)
        top.addLayout(col, 1)

        if control is not None and not stacked:
            top.addSpacing(12)
            top.addWidget(control, 0, Qt.AlignVCenter | Qt.AlignRight)
        outer.addLayout(top)

        if control is not None and stacked:
            wrap = QHBoxLayout()
            wrap.setContentsMargins(52, 0, 0, 0)
            wrap.addWidget(control)
            wrap.addStretch()
            outer.addLayout(wrap)

        self._lay.addWidget(row)
        return row


def launch_command():
    """Как запустить Эфир заново: (программа, аргументы)."""
    if getattr(sys, "frozen", False):  # собранный exe (PyInstaller и т. п.)
        return sys.executable, []
    exe = sys.executable
    if os.name == "nt":
        pythonw = Path(exe).with_name("pythonw.exe")
        if pythonw.exists():
            exe = str(pythonw)  # без чёрного окна консоли
    return exe, [os.path.abspath(sys.argv[0])]


def read_autostart():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, "Efir")
            return True
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def write_autostart(enabled):
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
        try:
            if enabled:
                program, args = launch_command()
                # Installed copies should update at Windows startup too. Do not trust
                # an arbitrary environment path for a persistent registry entry.
                expected = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Efir" / "Efir Launcher.exe"
                configured = os.environ.get("EFIR_LAUNCHER_PATH", "")
                if getattr(sys, "frozen", False) and configured and expected.is_file():
                    if Path(configured).resolve() == expected.resolve():
                        program, args = str(expected), []
                exe_path = " ".join(f'"{part}"' for part in [program, *args])
                winreg.SetValueEx(key, "Efir", 0, winreg.REG_SZ, exe_path)
            else:
                try:
                    winreg.DeleteValue(key, "Efir")
                except FileNotFoundError:
                    pass
            for old in ("TextFlow", "SMIHelp"):   # записи старых версий, если остались
                try:
                    winreg.DeleteValue(key, old)
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)
    except Exception as exc:
        log.error("Ошибка настройки автозапуска: %s", exc)


class PageHeader(QWidget):
    """Единый заголовок страницы: название, описание, справа — произвольные виджеты."""

    def __init__(self, title, desc="", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        page_glyph={"Настройки":"\uE713","Группы":"\uE8B7","История":"\uE81C"}.get(title)
        if page_glyph:
            emblem=QLabel();emblem.setFixedSize(36,36)
            emblem.setPixmap(glyph_pixmap(page_glyph,"#B780FF",36,18))
            lay.addWidget(emblem,0,Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.title = QLabel(title)
        self.title.setStyleSheet("font-size: 24px; font-weight: 600; background: transparent;")
        self.desc = QLabel(desc)
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet("font-size: 13px; color: #B0BBCB; background: transparent;")
        col.addWidget(self.title)
        col.addWidget(self.desc)
        lay.addLayout(col,1)
        self.right = QHBoxLayout()
        self.right.setSpacing(10)
        lay.addLayout(self.right)

    def set_desc(self, text):
        self.desc.setText(text)


def _custom_logo_path():
    """Если рядом с программой (или в папке данных) лежит logo.png/jpg/webp/ico — берём его."""
    try:
        base = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
    except Exception:
        base = Path(".")
    for folder in (base, APP_DIR):
        for ext in ("png", "jpg", "jpeg", "webp", "ico"):
            candidate = folder / f"logo.{ext}"
            if candidate.exists():
                return candidate
    return None


def make_emblem(size, radius_ratio=0.30):
    """Эмблема приложения: премиальная плитка с радио-волнами.
    Если рядом лежит logo.png (или .jpg/.webp/.ico) — вместо неё используется ваше изображение."""
    dpr = 2
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    S = float(size)
    rect = QRectF(0, 0, S, S)
    r = S * radius_ratio
    clip = QPainterPath()
    clip.addRoundedRect(rect, r, r)
    p.setClipPath(clip)

    custom = _custom_logo_path()
    img = QPixmap(str(custom)) if custom else QPixmap()
    if not img.isNull():
        side = int(S * dpr)
        img = img.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        ox, oy = (img.width() - side) / 2.0, (img.height() - side) / 2.0
        p.drawPixmap(rect, img, QRectF(ox, oy, side, side))
    else:
        base = QLinearGradient(0, 0, S, S)
        base.setColorAt(0.0, QColor("#A58BFF"))
        base.setColorAt(0.45, QColor("#6C4DFF"))
        base.setColorAt(1.0, QColor("#2F1F9E"))
        p.fillRect(rect, base)

        glow = QRadialGradient(S * 0.26, S * 0.12, S * 0.85)
        glow.setColorAt(0.0, QColor(255, 255, 255, 105))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(rect, glow)

        shade = QLinearGradient(0, S * 0.55, 0, S)
        shade.setColorAt(0.0, QColor(10, 6, 50, 0))
        shade.setColorAt(1.0, QColor(10, 6, 50, 110))
        p.fillRect(rect, shade)

        # радио-волны: точка + дуги слева и справа
        cx, cy = S * 0.5, S * 0.5
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(cx, cy), S * 0.088, S * 0.088)
        for rad, alpha in ((S * 0.225, 255), (S * 0.345, 150)):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, alpha), S * 0.072, Qt.SolidLine, Qt.RoundCap))
            arc = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            p.drawArc(arc, -44 * 16, 88 * 16)
            p.drawArc(arc, 136 * 16, 88 * 16)

    # тонкая светлая кромка по контуру
    p.setClipping(False)
    p.setBrush(Qt.NoBrush)
    p.setPen(QPen(QColor(255, 255, 255, 55), 1))
    p.drawRoundedRect(QRectF(0.5, 0.5, S - 1, S - 1), r, r)
    p.end()
    return pix


def _mix(c1, c2, t):
    """Линейное смешение двух QColor (t от 0 до 1)."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(c1.red() + (c2.red() - c1.red()) * t),
        int(c1.green() + (c2.green() - c1.green()) * t),
        int(c1.blue() + (c2.blue() - c1.blue()) * t),
    )


class ElidedLabel(QLabel):
    """Длинные имена профилей сокращаются многоточием, полный текст — в подсказке."""
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = str(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, max(0, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight, max(0, self.width())))


class ProfileButton(QPushButton):
    """Кнопка профиля в сайдбаре: аватар-плитка, подпись, название и шеврон.
    Фон и рамка плавно подсвечиваются цветом радиоцентра при наведении и пока открыто меню."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("profileBtn")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(56)
        self.setFocusPolicy(Qt.NoFocus)
        self._color = QColor("#6C4DFF")
        self._t = 0.0
        self._open = False

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(12)

        self.avatar = QLabel("")
        self.avatar.setFixedSize(38, 38)
        self.avatar.setAlignment(Qt.AlignCenter)

        col = QVBoxLayout()
        col.setSpacing(0)
        col.setContentsMargins(0, 0, 0, 0)
        self.caption = QLabel("РАДИОЦЕНТР")
        self.caption.setStyleSheet(
            "color: #919DBF; font-size: 10px; font-weight: 600; letter-spacing: 1px; background: transparent;")
        self.name_lbl = ElidedLabel("")
        self.name_lbl.setStyleSheet("color: #F1F2FF; font-size: 15px; font-weight: 600; background: transparent;")
        col.addStretch()
        col.addWidget(self.caption)
        col.addWidget(self.name_lbl)
        col.addStretch()

        self.chev = QLabel()
        self.chev.setFixedSize(16, 16)
        self.chev.setPixmap(glyph_pixmap("\uE70E", "#919DBF", 16, 8))

        lay.addWidget(self.avatar)
        lay.addLayout(col, 1)
        lay.addWidget(self.chev)
        for w in (self.avatar, self.caption, self.name_lbl, self.chev):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_profile(self, name, color):
        self._color = QColor(color)
        c = self._color
        self.name_lbl.setText(name)
        self.avatar.setText(name[:2].upper())
        self.avatar.setStyleSheet(
            f"color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},38); "
            f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},120); border-radius: 19px; "
            "font-size: 13px; font-weight: 600;"
        )
        self.update()

    # --- плавная подсветка ---
    def _animate_to(self, end):
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(float(end))
        self._anim.start()

    def _on_anim(self, v):
        self._t = float(v)
        self.update()

    def set_open(self, is_open):
        self._open = bool(is_open)
        self.chev.setPixmap(glyph_pixmap("\uE70E" if not is_open else "\uE70D",
                                         "#A58BFF" if is_open else "#919DBF", 16, 8))
        self._animate_to(1.0 if (self._open or self.underMouse()) else 0.0)

    def enterEvent(self, e):
        self._animate_to(1.0)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._animate_to(1.0 if self._open else 0.0)
        super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = _mix(QColor("#171C37"), QColor("#282D51"), self._t)
        border = _mix(QColor("#454E78"), self._color, 0.6 * self._t)
        if self.isDown():
            bg = _mix(bg, QColor("black"), 0.25)
            border = self._color
        p.setBrush(bg)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)


class _ProfileRow(QFrame):
    clicked = pyqtSignal(str)
    deleteClicked = pyqtSignal(str)

    def __init__(self, name, color, count, active, can_delete, parent=None):
        super().__init__(parent)
        self.name = name
        self._color = QColor(color)
        self._active = active
        self._t = 0.0
        self.setFixedHeight(52)
        self.setCursor(Qt.PointingHandCursor)

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(130)
        self._anim.valueChanged.connect(self._on_anim)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 8, 0)
        lay.setSpacing(12)

        c = self._color
        avatar = QLabel(name[:2].upper())
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"color: {color}; background: rgba({c.red()},{c.green()},{c.blue()},38); "
            f"border: 1px solid rgba({c.red()},{c.green()},{c.blue()},120); border-radius: 9px; "
            "font-size: 11px; font-weight: 600;")

        col = QVBoxLayout()
        col.setSpacing(1)
        col.setContentsMargins(0, 0, 0, 0)
        col.addStretch()
        title = ElidedLabel(name)
        title.setStyleSheet("color: #F1F2FF; font-size: 13px; font-weight: 600; background: transparent;")
        sub_text = "нет замен" if count == 0 else f"{count:,} {plural(count, 'замена', 'замены', 'замен')}".replace(",", " ")
        sub = ElidedLabel(sub_text)
        sub.setStyleSheet("color: #A0AABB; font-size: 11px; background: transparent;")
        col.addWidget(title)
        col.addWidget(sub)
        col.addStretch()

        lay.addWidget(avatar)
        lay.addLayout(col, 1)

        check = QLabel()
        check.setFixedSize(16, 16)
        if active:
            check.setPixmap(glyph_pixmap("\uE73E", color, 16, 9))
        lay.addWidget(check)

        self.trash = QPushButton()
        self.trash.setFixedSize(30, 30)
        self.trash.setCursor(Qt.PointingHandCursor)
        self.trash.setFocusPolicy(Qt.NoFocus)
        icon = QIcon()
        icon.addPixmap(glyph_pixmap("\uE74D", "#59627F", 18, 10), QIcon.Normal)
        icon.addPixmap(glyph_pixmap("\uE74D", "#D497A1", 18, 10), QIcon.Active)
        self.trash.setIcon(icon)
        self.trash.setIconSize(QSize(16, 16))
        self.trash.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 8px; padding: 0px; }"
            "QPushButton:hover { background: rgba(255,128,137,38); }"
            "QPushButton:disabled { background: transparent; }")
        self.trash.setToolTip("Удалить радиоцентр" if can_delete else "Нельзя удалить единственный радиоцентр")
        self.trash.setEnabled(can_delete)
        self.trash.clicked.connect(lambda: self.deleteClicked.emit(self.name))
        lay.addWidget(self.trash)

        for w in (avatar, title, sub, check):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _on_anim(self, v):
        self._t = float(v)
        self.update()

    def enterEvent(self, e):
        self._anim.stop(); self._anim.setStartValue(self._t); self._anim.setEndValue(1.0); self._anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._anim.stop(); self._anim.setStartValue(self._t); self._anim.setEndValue(0.0); self._anim.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.name)
        super().mousePressEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        if self._active:
            fill = QColor(self._color); fill.setAlpha(30)
            edge = QColor(self._color); edge.setAlpha(95)
            p.setBrush(fill); p.setPen(QPen(edge, 1))
            p.drawRoundedRect(r, 11, 11)
        if self._t > 0:
            p.setBrush(QColor(255, 255, 255, int(16 * self._t)))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(r, 11, 11)


class ProfilePopup(QFrame):
    """Современное всплывающее окно выбора радиоцентра: плавное появление, удаление прямо в списке."""
    chosen = pyqtSignal(str)
    deleteRequested = pyqtSignal(str)
    createRequested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, profiles, active, counts, width, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._anims = []
        self._closing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)

        panel = QFrame()
        panel.setObjectName("ppPanel")
        panel.setStyleSheet("QFrame#ppPanel { background: #171C37; border: 1px solid #454E78; border-radius: 16px; }")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 170))
        panel.setGraphicsEffect(shadow)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 10, 8, 8)
        lay.setSpacing(2)

        cap = QLabel("РАДИОЦЕНТРЫ")
        cap.setStyleSheet("color: #919DBF; font-size: 10px; font-weight: 600; letter-spacing: 1px; "
                          "padding: 0px 8px 6px 8px; background: transparent;")
        lay.addWidget(cap)

        can_delete = len(profiles) > 1
        for name in profiles:
            row = _ProfileRow(name, profile_color(name), counts.get(name, 0), name == active, can_delete)
            row.clicked.connect(lambda n: self._finish(self.chosen, n))
            row.deleteClicked.connect(lambda n: self._finish(self.deleteRequested, n))
            lay.addWidget(row)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #303655; border: none;")
        lay.addSpacing(4)
        lay.addWidget(sep)
        lay.addSpacing(4)

        create = QPushButton("Новый радиоцентр")
        create.setCursor(Qt.PointingHandCursor)
        create.setFocusPolicy(Qt.NoFocus)
        create.setFixedHeight(40)
        create.setIcon(QIcon(glyph_pixmap("\uE710", "#A58BFF", 18, 10)))
        create.setIconSize(QSize(16, 16))
        create.setStyleSheet(
            "QPushButton { background: transparent; border: none; border-radius: 10px; text-align: left; "
            "padding: 0px 12px; color: #CFC5FF; font-weight: 600; font-size: 13px; }"
            "QPushButton:hover { background: rgba(108,77,255,36); color: #E3DCFF; }")
        create.clicked.connect(lambda: self._finish(self.createRequested))
        lay.addWidget(create)

        outer.addWidget(panel)
        self.setFixedWidth(max(width + 28, 300))

    def hideEvent(self, event):
        super().hideEvent(event)
        self.closed.emit()

    # --- плавное закрытие: сигнал уходит уже после исчезновения окна ---
    def _finish(self, signal, *args):
        if self._closing:
            return
        self._closing = True
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(110)
        fade.setStartValue(self.windowOpacity())
        fade.setEndValue(0.0)

        def done():
            signal.emit(*args)
            self.close()
        fade.finished.connect(done)
        self._anims.append(fade)
        fade.start()

    def popup_above(self, widget):
        self.adjustSize()
        g = widget.mapToGlobal(QPoint(0, 0))
        x = g.x() - 14
        y = g.y() - self.height() + 6
        self.setWindowOpacity(0.0)
        self.move(x, y + 12)
        self.show()

        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(170); fade.setStartValue(0.0); fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        slide = QPropertyAnimation(self, b"pos", self)
        slide.setDuration(190); slide.setStartValue(QPoint(x, y + 12)); slide.setEndValue(QPoint(x, y))
        slide.setEasingCurve(QEasingCurve.OutCubic)
        self._anims += [fade, slide]
        fade.start(); slide.start()


class RunButton(QPushButton):
    """Контурная кнопка: play для запуска, розовый квадрат для остановки."""
    def __init__(self, parent=None):
        super().__init__("Запустить", parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(148, 36)
        self.setAccessibleName("Запустить автозамену")
        self._running = False
        self._hover = 0.0
        self._h_anim = QVariantAnimation(self)
        self._h_anim.setDuration(140)
        self._h_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._h_anim.valueChanged.connect(self._on_hover)

    def set_running(self, running):
        self._running = bool(running)
        self.setText("Остановить" if self._running else "Запустить")
        self.setAccessibleName(self.text() + " автозамену")
        if hasattr(self, "_ui_glow"):
            self._ui_glow.refresh()
        self.update()

    def _on_hover(self, value):
        self._hover = float(value)
        self.update()

    def _hover_to(self, value):
        self._h_anim.stop()
        self._h_anim.setStartValue(self._hover)
        self._h_anim.setEndValue(float(value))
        self._h_anim.start()

    def enterEvent(self, event):
        self._hover_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover_to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        running = self._running
        edge = QColor("#D47198" if running else "#B487FF")
        foreground = QColor("#FFEAF2" if running else "#FFFFFF")
        top = _mix(QColor("#67324A" if running else "#823DFF"),
                   QColor("#89405C" if running else "#A064FF"), self._hover)
        bottom = QColor("#36162D" if running else "#5013D6")
        if self.isDown():
            top, bottom = bottom, bottom.darker(112)
        if self.hasFocus():
            edge = QColor("#FFB3CF" if running else "#D9B6FF")
        if not self.isEnabled():
            p.setOpacity(0.55)
        fill = QLinearGradient(0, 0, 0, self.height())
        fill.setColorAt(0, top)
        fill.setColorAt(1, bottom)
        p.setBrush(fill)
        p.setPen(QPen(edge, 1))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 9, 9)
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        font.setWeight(QFont.DemiBold)
        p.setFont(font)
        text_width = p.fontMetrics().horizontalAdvance(self.text())
        icon_width, gap = 8, 10
        x = (self.width() - text_width - icon_width - gap) / 2.0
        cy = self.height() / 2.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FF94BA" if running else "#FFFFFF"))
        if running:
            p.drawRoundedRect(QRectF(x, cy - 4, 8, 8), 1.6, 1.6)
        else:
            play = QPainterPath(QPointF(x + 0.5, cy - 4))
            play.lineTo(x + 7.5, cy)
            play.lineTo(x + 0.5, cy + 4)
            play.closeSubpath()
            p.drawPath(play)
        p.setPen(foreground)
        p.drawText(QRectF(x + icon_width + gap, 0, text_width + 1, self.height()),
                   int(Qt.AlignVCenter | Qt.AlignLeft), self.text())


class StarButton(QPushButton):
    """Иконка-звезда «в избранное» в шапке редактора."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("starIcon")
        self.setCheckable(True)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.toggled.connect(self._refresh)
        self._refresh()

    def setChecked(self, state):
        self.blockSignals(True)
        super().setChecked(bool(state))
        self.blockSignals(False)
        self._refresh()

    def _refresh(self, *_):
        on = self.isChecked()
        self.setText("\u2605" if on else "\u2606")
        self.setToolTip("Убрать из избранного" if on else "Добавить в избранное")


class ToggleSwitch(QWidget):
    """Переключатель-«пилюля» (Включено / Выключено). Интерфейс как у BoolChoice."""
    toggled = pyqtSignal(bool)

    def __init__(self, on_text="Включено", off_text="Выключено", checked=True, parent=None):
        super().__init__(parent)
        self._on_text = on_text
        self._off_text = off_text
        self._checked = bool(checked)
        self._pos = 1.0 if self._checked else 0.0
        self.setFixedSize(132, 30)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.valueChanged.connect(self._on_anim)

    def _on_anim(self, value):
        self._pos = float(value)
        self.update()

    def isChecked(self):
        return self._checked

    def setChecked(self, state):
        """Программная установка (сигнал не посылается)."""
        self._checked = bool(state)
        self._anim.stop()
        self._pos = 1.0 if self._checked else 0.0
        self.update()

    def _user_toggle(self):
        self._checked = not self._checked
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if self._checked else 0.0)
        self._anim.start()
        self.update()
        self.toggled.emit(self._checked)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self._user_toggle()
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._user_toggle()
        else:
            super().keyPressEvent(event)

    def paintEvent(self,_event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing)
        x,y=2,(self.height()-16)/2
        edge=QColor("#B88CFF" if self._checked else "#6F608A")
        if self.hasFocus():edge=QColor("#E5CDFF")
        p.setPen(QPen(edge,1));p.setBrush(_mix(QColor("#3C3155"),QColor("#7928FA"),self._pos))
        p.drawRoundedRect(QRectF(x,y,30,16),8,8)
        p.setPen(Qt.NoPen);p.setBrush(QColor("#FCF8FF"));p.drawEllipse(QRectF(x+2+self._pos*12,y+1,14,14))
        font=QFont("Segoe UI");font.setPixelSize(12);p.setFont(font);p.setPen(QColor("#EDE4FF"))
        p.drawText(QRectF(42,0,self.width()-42,self.height()),int(Qt.AlignLeft|Qt.AlignVCenter),self._on_text if self._checked else self._off_text)


class HotkeyEdit(QLineEdit):
    """Поле записи сочетания клавиш: сфокусируйтесь и нажмите комбинацию."""
    changedByUser = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Нет")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Нажмите и введите сочетание. Esc или Backspace — очистить.")
        self.addAction(QIcon(glyph_pixmap("\uE765", "#919DBF", 18, 11)), QLineEdit.TrailingPosition)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Escape, Qt.Key_Backspace, Qt.Key_Delete):
            if self.text():
                self.setText("")
                self.changedByUser.emit()
            return
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr):
            return
        mods = event.modifiers()
        parts = []
        if mods & Qt.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.AltModifier:
            parts.append("alt")
        if mods & Qt.ShiftModifier:
            parts.append("shift")
        if mods & Qt.MetaModifier:
            parts.append("cmd")
        name = ""
        if os.name == "nt":
            name = hotkey_name_for_vk(event.nativeVirtualKey())
        if not name:
            name = QKeySequence(key).toString().lower()
        if not name:
            return
        is_fkey = name.startswith("f") and name[1:].isdigit()
        if not parts and not is_fkey:
            return  # одиночные буквы не принимаем
        parts.append(name)
        self.setText("+".join(parts))
        self.changedByUser.emit()


class CollapsibleSection(QWidget):
    """Сворачиваемый блок: заголовок с подписью и шевроном, внутри — body_lay."""

    def __init__(self, title, subtitle="", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.header = SmoothActionButton()
        self.header.setObjectName("collapse")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.PointingHandCursor)
        self.header.setFocusPolicy(Qt.NoFocus)
        self.header.setMinimumHeight(52)
        self.header.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(14, 5, 12, 5)
        hl.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size: 12px; font-weight: 500; color: #E5DBFA; background: transparent;")
        col.addWidget(t)
        widgets = [t]
        if subtitle:
            s = QLabel(subtitle)
            s.setWordWrap(True)
            s.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            s.setStyleSheet("font-size: 11px; color: #919DBF; background: transparent;")
            col.addWidget(s)
            widgets.append(s)
        hl.addLayout(col, 1)
        self._chev = QLabel()
        self._chev.setFixedSize(18, 18)
        hl.addWidget(self._chev, 0, Qt.AlignVCenter)
        widgets.append(self._chev)
        for w in widgets:
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(0, 4, 0, 4)
        self.body_lay.setSpacing(10)

        lay.addWidget(self.header)
        lay.addWidget(self.body)
        self.header.toggled.connect(self._on_toggle)
        self._on_toggle(False)

    def _on_toggle(self, opened):
        self.body.setVisible(bool(opened))
        self._chev.setPixmap(glyph_pixmap("\uE70E" if opened else "\uE70D", "#A5AFD0", 18, 9))


class TableHeader(QHeaderView):
    def __init__(self,parent=None):
        super().__init__(Qt.Horizontal,parent)
        self.setDefaultAlignment(Qt.AlignCenter)
        self.setHighlightSections(False)

    def mousePressEvent(self,event):
        view=self.parent()
        if isinstance(view,HoverTable) and event.button()==Qt.LeftButton and event.pos().x()<40:
            total=view.model().rowCount()
            if len(view.selectionModel().selectedRows())==total:
                view.clearSelection()
            else:
                view.selectAll()
            self.viewport().update();event.accept();return
        if isinstance(view, HoverTable) and self.logicalIndexAt(event.pos()) >= 3:
            event.accept()
            return
        super().mousePressEvent(event)

    def paintSection(self,painter,rect,logicalIndex):
        painter.save()
        gradient=QLinearGradient(0,rect.top(),0,rect.bottom())
        gradient.setColorAt(0,QColor("#181432"));gradient.setColorAt(1,QColor("#111127"))
        painter.fillRect(rect,gradient)
        painter.setPen(QColor("#2C254A"));painter.drawLine(rect.bottomLeft(),rect.bottomRight())
        font=QFont("Segoe UI");font.setPixelSize(11);font.setWeight(QFont.DemiBold);painter.setFont(font)
        painter.setPen(QColor("#C5B5ED"))
        text=str(self.model().headerData(logicalIndex,Qt.Horizontal,Qt.DisplayRole) or "")
        view=self.parent()
        if isinstance(view,HoverTable) and logicalIndex==0:
            total=view.model().rowCount();selected=len(view.selectionModel().selectedRows())
            paint_selection_box(painter,QRectF(rect.x()+15,rect.center().y()-8,16,16),bool(total and selected==total))
            painter.setPen(QColor("#C5B5ED"));painter.drawText(rect.adjusted(58,0,-23,0),int(Qt.AlignLeft|Qt.AlignVCenter),"Ключ")
        elif isinstance(view,HoverTable) and logicalIndex==1:
            painter.drawText(rect.adjusted(10,0,-23,0),int(Qt.AlignLeft|Qt.AlignVCenter),text)
        else:
            painter.drawText(rect.adjusted(4,0,-22 if logicalIndex < 3 else -4,0),int(Qt.AlignCenter),text)
        if logicalIndex < 3 and logicalIndex == self.sortIndicatorSection():
            painter.setPen(QPen(QColor("#D9B1FF"), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            x, y = rect.right() - 12, rect.center().y()
            path = QPainterPath()
            if self.sortIndicatorOrder() == Qt.AscendingOrder:
                path.moveTo(x - 3, y + 2); path.lineTo(x, y - 2); path.lineTo(x + 3, y + 2)
            else:
                path.moveTo(x - 3, y - 2); path.lineTo(x, y + 2); path.lineTo(x + 3, y - 2)
            painter.drawPath(path)
        painter.restore()


class ToggleDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        view, rect, col = option.widget, option.rect, index.column()
        selected = bool(option.state & QStyle.State_Selected)
        hovered = view is not None and getattr(view, "hover_row", -1) == index.row()
        hover, selection, action_hover = (view.row_highlight(index.row(), selected)
            if hasattr(view, "row_highlight") else (float(hovered), float(selected), float(hovered)))
        base = QColor("#101024" if index.row() % 2 else "#0C0D20")
        base = _mix(base, QColor("#1B2140"), hover)
        painter.save()
        painter.setClipRect(rect)
        row_fill = QLinearGradient(0, rect.top(), 0, rect.bottom())
        row_fill.setColorAt(0, _mix(base, QColor("#45208E"), selection))
        row_fill.setColorAt(1, _mix(base, QColor("#291958"), selection))
        painter.fillRect(rect, QBrush(row_fill))
        if selection > 0.001:
            edge_glow = QLinearGradient(0, rect.top(), 0, rect.bottom())
            edge_glow.setColorAt(0, QColor(174, 145, 255, round(35 * selection)))
            edge_glow.setColorAt(0.25, QColor(174, 145, 255, 0))
            edge_glow.setColorAt(1, QColor(139, 111, 255, round(15 * selection)))
            painter.fillRect(rect, QBrush(edge_glow))
        if selection > 0.001:
            # Draw one continuous rounded selection across all model columns;
            # each delegate clips its own segment, avoiding seams at cell edges.
            full_width = view.viewport().width() if view is not None else rect.right()+1
            glow_fill = QLinearGradient(0,rect.top(),full_width,rect.bottom())
            for position,hex_color in ((0,"#642BCB"),(.45,"#3B1D85"),(1,"#241348")):
                tint=QColor(hex_color);tint.setAlpha(round(255*selection));glow_fill.setColorAt(position,tint)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(glow_fill)
            rim=QColor("#AC77FF");rim.setAlpha(round(210*selection))
            painter.setPen(QPen(rim,1))
            painter.drawRoundedRect(QRectF(.5,rect.top()+.5,full_width-1,rect.height()-1),9,9)
        painter.setPen(QColor("#24203D"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        painter.setFont(font)
        if col == 0 and selection > 0.001:
            painter.fillRect(QRect(rect.x(), rect.y() + 1, 3, rect.height() - 2),
                             QColor(180, 147, 255, round(255 * selection)))
        if col == 3:
            checked=index.data(Qt.CheckStateRole)==Qt.Checked
            chip=QRectF(rect.center().x()-30,rect.center().y()-13,60,26)
            painter.setPen(QPen(QColor("#19482E" if checked else "#49405F"),1))
            painter.setBrush(QColor("#0D2B20" if checked else "#242036"));painter.drawRoundedRect(chip,13,13)
            fg=QColor("#61EA8B" if checked else "#A99BBE")
            painter.setPen(Qt.NoPen);painter.setBrush(fg);painter.drawEllipse(QRectF(chip.x()+10,chip.center().y()-4,8,8))
            font.setPixelSize(11);font.setWeight(QFont.DemiBold);painter.setFont(font);painter.setPen(fg)
            painter.drawText(QRectF(chip.x()+26,chip.y(),32,26),int(Qt.AlignLeft|Qt.AlignVCenter),"Вкл" if checked else "Выкл")
        elif col == 2:
            text = str(index.data(Qt.DisplayRole) or "")
            fm = painter.fontMetrics()
            max_w = max(24, rect.width() - 24)
            text = fm.elidedText(text, Qt.ElideRight, max_w - 20)
            width = min(max_w, fm.horizontalAdvance(text) + 20)
            chip = QRectF(rect.center().x() - width / 2.0, rect.center().y() - 12, width, 24)
            template = text == "Шаблоны"
            painter.setBrush(QColor("#44217E" if template else "#142F6B"))
            painter.setPen(QPen(QColor("#8748D8" if template else "#3B63C7"),1))
            painter.drawRoundedRect(chip,12,12)
            painter.setPen(QColor("#E7D4FF" if template else "#B7D9FF"))
            painter.drawText(chip,int(Qt.AlignCenter),text)
        elif col == 4:
            cx, cy = rect.center().x(), rect.center().y()
            painter.setBrush(_mix(QColor("#211B43"),QColor("#45218D"),max(action_hover,selection)))
            painter.setPen(QPen(_mix(QColor("#463275"),QColor("#9F66F5"),max(action_hover,selection)),1))
            painter.drawRoundedRect(QRectF(cx-15,cy-15,30,30),8,8)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#C2CAD8"))
            for dx in (-5, 0, 5):
                painter.drawEllipse(QRectF(cx + dx - 1, cy - 1, 2, 2))
        else:
            font.setWeight(QFont.DemiBold if col == 0 else QFont.Normal)
            painter.setFont(font)
            painter.setPen(QColor("#FFFFFF" if selected else ("#F1F3FF" if col == 0 else "#D8DDF5")))
            inset=58 if col==0 else 10
            text_rect=rect.adjusted(inset,0,-8,0)
            text=painter.fontMetrics().elidedText(str(index.data(Qt.DisplayRole) or ""),Qt.ElideRight,max(0,text_rect.width()))
            painter.drawText(text_rect,int(Qt.AlignLeft|Qt.AlignVCenter),text)
            if col==0:
                paint_selection_box(painter,QRectF(rect.x()+15,rect.center().y()-8,16,16),selected)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            if index.column() == 3:
                current = index.data(Qt.CheckStateRole)
                model.setData(index, Qt.Unchecked if current == Qt.Checked else Qt.Checked, Qt.CheckStateRole)
                return True
            if index.column() == 4:
                view = option.widget
                if view is not None and hasattr(view, "actionsRequested"):
                    view.actionsRequested.emit(index)
                return True
        return super().editorEvent(event, model, option, index)

# --- МОДЕЛИ ДАННЫХ ---

class ShortcutsModel(QAbstractTableModel):
    HEADERS = ["Ключ", "Текст замены", "Группа", "Статус", "Действия"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows = []          # list of dict-row (см. set_rows)
        self._index_by_ckey = {}

    def set_rows(self, rows):
        """rows: список (ckey, info). Внутри строки храним предвычисленные поля,
        чтобы data(UserRole) был O(1) и не делал casefold на каждый вызов."""
        self.beginResetModel()
        self._rows = []
        self._index_by_ckey = {}
        for ckey, info in rows:
            _, bare = split_ckey(ckey)
            bare_lower = bare.casefold()
            text = str(info.get("text", ""))
            group = str(info.get("group", "Общие"))
            row = {
                "ckey": ckey,
                "info": info,
                "bare": bare,
                "bare_lower": bare_lower,
                "text": text,
                "text_lower": text.casefold(),
                "group": group,
                "group_lower": group.casefold(),
                "haystack": f"{bare_lower} {text.casefold()} {group.casefold()}",
            }
            self._index_by_ckey[ckey] = len(self._rows)
            self._rows.append(row)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.HEADERS[section]
            if role == Qt.TextAlignmentRole:
                return int(Qt.AlignCenter)
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        info = row["info"]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return row["bare"]
            if col == 1:
                preview = row["text"].replace("\n", " ↵ ")
                return preview if len(preview) <= 100 else preview[:97] + "…"
            if col == 2:
                return row["group"]
            if col == 3:
                return ""
        elif role == Qt.UserRole:
            if col == 0:
                return row["bare_lower"]
            if col == 1:
                return int(info.get("uses", 0))
            if col == 2:
                return row["group_lower"]
            return ""
        elif role == Qt.UserRole + 1:
            return bool(info.get("favorite", False)) if col == 0 else False
        elif role == Qt.CheckStateRole and col == 3:
            return Qt.Checked if info.get("enabled", True) else Qt.Unchecked
        elif role == Qt.ForegroundRole:
            if col == 0:
                return QColor("#F1F2FF")
            if col == 1:
                return QColor("#CDD5E2")
            if col == 2:
                return QColor("#B0BBCB")
        elif role == Qt.FontRole and col == 0:
            f = QFont("Segoe UI", 10)
            f.setBold(True)
            return f
        elif role == Qt.ToolTipRole:
            if col == 0:
                return row["bare"] + " — открыть в редакторе"
            if col == 1:
                return row["text"]
            if col == 2:
                return row["group"]
        elif role == Qt.TextAlignmentRole:
            return int(Qt.AlignCenter)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 3:
            f |= Qt.ItemIsUserCheckable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.column() == 3:
            self._rows[index.row()]["info"]["enabled"] = (value == Qt.Checked)
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def row_for_key(self, key):
        return self._index_by_ckey.get(key, -1)

    def row_data(self, row):
        r = self._rows[row]
        return r["ckey"], r["info"]

class ShortcutFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(Qt.UserRole)
        self.setSortCaseSensitivity(Qt.CaseInsensitive)
        self.setDynamicSortFilter(True)
        self._search = ""
        self._group = ""
        self._favorites_only = False
        self._profile = None

    def set_group(self, group):
        group = group or ""
        if group != self._group:
            self._group = group
            self.invalidateFilter()

    def set_all(self, profile, search, group, favorites_only):
        search = (search or "").casefold().strip()
        group = group or ""
        favorites_only = bool(favorites_only)
        if (profile == self._profile and search == self._search
                and group == self._group and favorites_only == self._favorites_only):
            return False
        search_changed = search != self._search
        self._profile, self._search = profile, search
        self._group, self._favorites_only = group, favorites_only
        # Search changes affect both membership and relevance order. A filter-
        # only invalidation can leave rows in their previous alphabetical order.
        if search_changed:
            self.invalidate()
        else:
            self.invalidateFilter()
        return True

    def _search_rank(self, row):
        query = self._search
        if not query:
            return 0
        if row.get("_rank_query") == query:
            return row["_search_rank"]
        key = row["bare_lower"]
        if key == query:
            rank = 0
        elif key.startswith(query):
            rank = 1
        elif query in key:
            rank = 2
        elif query in row["text_lower"]:
            rank = 3
        elif query in row["group_lower"]:
            rank = 4
        else:
            rank = 5
        # Cache belongs to the row snapshot, so a model reset cannot preserve
        # outdated ranks after an edit/import or a profile change.
        row["_rank_query"], row["_search_rank"] = query, rank
        return rank

    def lessThan(self, left, right):
        # Compare cached Python fields directly. Calling the base comparator
        # re-enters data(UserRole) through Qt for BOTH indexes on every one of
        # O(n log n) comparisons and noticeably stalls large searches/clears.
        rows = self.sourceModel()._rows
        a, b = rows[left.row()], rows[right.row()]
        if self._search:
            ar, br = self._search_rank(a), self._search_rank(b)
            if ar != br:
                return ar < br if self.sortOrder() == Qt.AscendingOrder else ar > br
        column = left.column()
        if column == 0:
            return a["bare_lower"] < b["bare_lower"]
        if column == 1:
            return int(a["info"].get("uses", 0)) < int(b["info"].get("uses", 0))
        if column == 2:
            return a["group_lower"] < b["group_lower"]
        return super().lessThan(left, right)

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if model is None:
            return True
        # Модель уже содержит только активный профиль — проверка по профилю не нужна.
        row = model._rows[source_row]
        info = row["info"]

        # Самые дешёвые проверки — первыми
        if self._favorites_only and not info.get("favorite", False):
            return False
        if self._group and row["group"] != self._group:
            return False
        if self._search and self._search_rank(row) == 5:
            return False
        return True


# --- ДИАЛОГИ ---


class GameHintOverlay(QWidget):
    """Click-through, non-activating hints with deterministic logical-pixel geometry.

    No child labels/layouts are rebuilt on keystrokes. One painted snapshot keeps
    the query and rows in sync and avoids QLabel size caches and deferred deletes.
    The overlay observes input; it never captures keys or activates itself.
    """
    EXPANDED_W = 400
    SHADOW = 10
    ROW_H = 30
    ROW_GAP = 4
    MAX_ROWS = 8

    def __init__(self, parent=None):
        super().__init__(parent)
        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        if hasattr(Qt, "WindowTransparentForInput"):
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_X11DoNotAcceptFocus, True)
        self.setWindowTitle("Efir_GameHintOverlay")
        self.position = "top_right"
        self._active = False
        self._is_compact = True
        self._compact_kind = None
        self._last_sig = None
        self._query = ""
        self._suggestions = ()
        self._visible_rows = 0
        self._hotkey_text = "Alt+2"
        self._auto_hide_ms = 5000
        self._screen_margin = 24
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._go_compact)
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.timeout.connect(self._go_compact_if_idle)
        self.setWindowOpacity(.95)
        self.hide()

    @staticmethod
    def _font(px, bold=False):
        font = QFont("Segoe UI")
        font.setPixelSize(px)
        font.setWeight(QFont.DemiBold if bold else QFont.Normal)
        return font

    @staticmethod
    def _single_line(text):
        # Replacements are previewed literally, never interpreted as rich text.
        return " ".join(str(text).replace("\r\n", " ↵ ").replace("\n", " ↵ ").replace("\r", " ↵ ").split())

    def showEvent(self, event):
        super().showEvent(event)
        if IS_WINDOWS:
            try:
                import ctypes
                # Use typed HWNDs: untyped ctypes calls may truncate a 64-bit
                # window handle and silently fail to apply WS_EX_NOACTIVATE.
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int)
                user32.GetWindowLongW.restype = ctypes.c_long
                user32.SetWindowLongW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_long)
                user32.SetWindowLongW.restype = ctypes.c_long
                user32.SetWindowPos.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint)
                user32.SetWindowPos.restype = ctypes.c_int
                hwnd = int(self.winId())
                style = user32.GetWindowLongW(hwnd, -20)
                user32.SetWindowLongW(hwnd, -20, style | 0x20 | 0x80 | 0x08000000)
                # TOPMOST, NOSIZE | NOMOVE | NOACTIVATE | FRAMECHANGED.
                user32.SetWindowPos(hwnd, ctypes.c_void_p(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10 | 0x20)
            except Exception:
                log.exception("Не удалось установить неактивирующий режим оверлея")

    def set_position(self, position):
        self.position = position if position in ("top_left", "top_right", "bottom_left", "bottom_right") else "top_right"
        self.move_to_corner()

    def set_screen_margin(self, margin):
        self._screen_margin = max(0, _to_int(margin, 24))
        if self._active:
            self._resize()

    def set_hotkey_text(self, hotkey):
        text = "+".join(p.strip().capitalize() for p in str(hotkey or "alt+2").split("+") if p.strip())
        self._hotkey_text = text or "Alt+2"
        if self._active:
            self._resize()

    def _available_geometry(self):
        screen = QApplication.primaryScreen()
        return screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    def move_to_corner(self):
        g = self._available_geometry()
        margin = max(0, self._screen_margin - self.SHADOW)
        x = g.left() + margin if self.position.endswith("left") else g.right() + 1 - self.width() - margin
        y = g.top() + margin if self.position.startswith("top") else g.bottom() + 1 - self.height() - margin
        x = max(g.left(), min(x, g.right() + 1 - self.width()))
        y = max(g.top(), min(y, g.bottom() + 1 - self.height()))
        self.move(x, y)

    def _resize(self):
        g = self._available_geometry()
        margin = max(0, self._screen_margin - self.SHADOW)
        max_w = max(1, g.width() - 2 * margin)
        if self._is_compact:
            # Measure painted text, not the last expanded layout's minimum.
            from PyQt5.QtGui import QFontMetrics
            fm = QFontMetrics(self._font(11, True))
            title = "Подсказки включены" if self._compact_kind == "on" else "Подсказки активны"
            width = min(max_w, 20 + 24 + 8 + fm.horizontalAdvance(title) + 16 +
                        min(120, fm.horizontalAdvance(self._hotkey_text) + 14))
            height = 54
            self._visible_rows = 0
        else:
            width = min(self.EXPANDED_W, max_w)
            # Header/query/footer/margins + rows. Clamp to the available screen
            # rather than clipping the last row under a changing window height.
            max_h = max(1, g.height() - 2 * margin)
            capacity = max(1, (max_h - 110 + self.ROW_GAP) // (self.ROW_H + self.ROW_GAP))
            self._visible_rows = min(len(self._suggestions), self.MAX_ROWS, capacity)
            height = 110 + self._visible_rows * self.ROW_H + max(0, self._visible_rows - 1) * self.ROW_GAP
        self.setFixedSize(width, height)
        self.move_to_corner()
        if self._active and not self.isVisible():
            self.show()
        # No raise_()/activateWindow() here: typing must stay in the game/chat.
        self.update()

    def _show_compact(self, kind):
        self._auto_hide_timer.stop()
        self._is_compact = True
        self._compact_kind = kind
        self._last_sig = None
        self._query = ""
        self._suggestions = ()
        self._resize()

    def set_active(self, active):
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        self._auto_hide_timer.stop()
        self._activation_timer.stop()
        self._last_sig = None
        self._query = ""
        self._suggestions = ()
        if active:
            self._show_compact("on")
            self._activation_timer.start(1800)
        else:
            self._is_compact = True
            self._compact_kind = None
            self._visible_rows = 0
            self.hide()

    def _go_compact_if_idle(self):
        if self._active and self._is_compact and self._compact_kind == "on":
            self._show_compact("idle")

    def _go_compact(self):
        if self._active:
            self._show_compact("idle")

    def set_suggestions(self, query, suggestions):
        if not self._active:
            return
        query = str(query or "")
        rows = tuple((str(key), str(text)) for key, text in (suggestions or ()))[:self.MAX_ROWS]
        if not query or not rows:
            self._auto_hide_timer.stop()
            if not self._is_compact or self._compact_kind != "on":
                self._show_compact("idle")
            return
        # Include the entire query: па -> пам must repaint even if matching rows
        # are identical. Store one immutable snapshot for a coherent paintEvent.
        signature = (query, rows)
        self._auto_hide_timer.start(max(1000, _to_int(self._auto_hide_ms, 5000)))
        if signature == self._last_sig and not self._is_compact:
            return
        self._activation_timer.stop()
        self._last_sig = signature
        self._query, self._suggestions = query, rows
        self._is_compact = False
        self._compact_kind = None
        self._resize()

    def _draw_text(self, painter, rect, text, px=11, color="#EDE5FF", bold=False, elide=Qt.ElideRight):
        painter.setFont(self._font(px, bold))
        painter.setPen(QColor(color))
        clipped = painter.fontMetrics().elidedText(self._single_line(text), elide, max(0, int(rect.width())))
        painter.drawText(rect, int(Qt.AlignLeft | Qt.AlignVCenter | Qt.TextSingleLine), clipped)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        panel = QRectF(self.rect()).adjusted(self.SHADOW, self.SHADOW, -self.SHADOW, -self.SHADOW)
        # Soft edge without a graphics effect (which can cache obsolete child
        # pixmaps during very fast updates and resizes on Windows).
        for spread in range(6, 0, -1):
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(133, 61, 228, 4 + (6-spread)*2), 1.5))
            p.drawRoundedRect(panel.adjusted(-spread, -spread, spread, spread), 13+spread, 13+spread)
        fill = QLinearGradient(panel.topLeft(), panel.bottomRight())
        fill.setColorAt(0, QColor("#211337")); fill.setColorAt(1, QColor("#100E21"))
        p.setBrush(fill); p.setPen(QPen(QColor("#65418B"), .9))
        p.drawRoundedRect(panel.adjusted(.5,.5,-.5,-.5), 13, 13)
        path = QPainterPath(); path.addRoundedRect(panel.adjusted(1,1,-1,-1), 12, 12)
        p.setClipPath(path)
        x, right = panel.left()+12, panel.right()-12
        top = panel.top()+8
        on = self._is_compact and self._compact_kind == "on"
        dot_color = QColor("#42E593" if on else "#A877FF")
        p.setPen(Qt.NoPen);p.setBrush(QColor(dot_color.red(),dot_color.green(),dot_color.blue(),30))
        p.drawEllipse(QPointF(x+4, top+9), 6,6)
        p.setBrush(dot_color);p.drawEllipse(QPointF(x+4, top+9),3.2,3.2)
        p.setFont(self._font(10,True))
        hk_width = min(120, p.fontMetrics().horizontalAdvance(self._hotkey_text)+14)
        hk = QRectF(right-hk_width, top, hk_width,18)
        p.setPen(QPen(QColor("#644183"),.7));p.setBrush(QColor("#2A1B40"));p.drawRoundedRect(hk,5,5)
        self._draw_text(p,hk.adjusted(7,0,-7,0),self._hotkey_text,10,"#D8BFF5",True)
        title = ("Подсказки включены" if on else "Подсказки активны") if self._is_compact else "Подсказки"
        self._draw_text(p,QRectF(x+16,top,max(0,hk.left()-x-24),18),title,11,"#42E593" if on else "#D7C4EF",True)
        if self._is_compact:
            return
        query_rect = QRectF(x,top+26,max(0,right-x-48),22)
        p.setPen(QPen(QColor("#7650A5"),.8));p.setBrush(QColor("#32204D"));p.drawRoundedRect(query_rect,7,7)
        self._draw_text(p,query_rect.adjusted(9,0,-9,0),self._query,12,"#F0DEFF",True,Qt.ElideLeft)
        self._draw_text(p,QRectF(right-40,top+26,40,22),str(self._visible_rows)+" шт.",10,"#9F8BB9")
        row_y = top+56
        for i, (key,text) in enumerate(self._suggestions[:self._visible_rows]):
            row = QRectF(x,row_y+i*(self.ROW_H+self.ROW_GAP),right-x,self.ROW_H)
            exact = key.casefold() == self._query.casefold()
            gradient = QLinearGradient(row.topLeft(),row.topRight())
            gradient.setColorAt(0,QColor("#422366" if i==0 else "#1C152E"))
            gradient.setColorAt(1,QColor("#251A39" if i==0 else "#181326"))
            p.setBrush(gradient);p.setPen(QPen(QColor("#8056AC" if i==0 else "#352544"),.8));p.drawRoundedRect(row,7,7)
            self._draw_text(p,QRectF(x+9,row.top(),16,self.ROW_H),str(i+1),10,"#AE8BCB")
            key_width = min(105,max(66,row.width()*.28))
            self._draw_text(p,QRectF(x+30,row.top(),key_width,self.ROW_H),key,11,"#DDC2FF",True)
            preview_x = x+30+key_width+10
            self._draw_text(p,QRectF(preview_x,row.top(),max(0,right-preview_x-16),self.ROW_H),text,11,"#E0D6ED")
            if exact:
                p.setPen(Qt.NoPen);p.setBrush(QColor("#42E593"));p.drawEllipse(QPointF(right-8,row.center().y()),2.5,2.5)
        foot_y = row_y+self._visible_rows*(self.ROW_H+self.ROW_GAP)+2
        self._draw_text(p,QRectF(x,foot_y,right-x,14),"Совпадения по началу ключа",10,"#9A86AE")


class ImportWorker(QThread):
    finished_data = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            if self.path.lower().endswith(".dat"):
                incoming = import_punto_dat(self.path)
            else:
                with open(self.path, "r", encoding="utf-8-sig") as handle:
                    imported_json = json.load(handle)
                validate_stored_data(imported_json)
                normalized = normalize_data(imported_json).get("shortcuts", {})
                incoming = {}
                for ckey, original in normalized.items():
                    source, bare = split_ckey(ckey)
                    info = dict(original)
                    info["_import_key"] = bare
                    info["_import_source_profile"] = source
                    incoming[ckey] = info
                if not imported_json.get("shortcuts"):
                    incoming = {}
            self.finished_data.emit(incoming)
        except Exception as exc:
            self.failed.emit(str(exc))


# Единая тема всех модальных окон. Qt-коды ответов и стандартная
# клавиатурная навигация сохранены; оформление не меняет смысл действий.
_DIALOG_STYLE = """
QDialog#efirDialog, QMessageBox#efirDialog, QInputDialog#efirDialog,
QFileDialog#efirDialog, QProgressDialog#efirDialog { background: transparent; color:#F4EEFF; }
QLabel { color:#DDD2EF; background:transparent; font-size:13px; }
QLabel#dialogTitle { color:#F8F3FF; font-size:17px; font-weight:600; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox { color:#F3ECFF; background:#100D24;
 border:1px solid #574078; border-radius:10px; padding:9px 12px; min-height:18px; selection-background-color:#6836B7; }
QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color:#BD8AFF; background:#17102F; }
QComboBox::drop-down { border:0; width:27px; }
QComboBox QAbstractItemView { color:#F3ECFF; background:#17102C; border:1px solid #65448E;
 selection-background-color:#543080; padding:5px; outline:0; }
QPushButton, QToolButton { color:#E8DDFB; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #281B48,stop:1 #17112B);
 border:1px solid #4E336F; border-radius:10px; padding:9px 15px; font-size:12px; font-weight:600; min-height:18px; }
QToolButton { padding:4px; min-height:18px; min-width:18px; }
QPushButton:hover, QToolButton:hover { background:#3E2464; border-color:#A26DE2; }
QPushButton:focus, QToolButton:focus { border-color:#C79BFF; }
QPushButton:pressed, QToolButton:pressed { background:#291447; }
QPushButton:default, QPushButton[accent="true"] { color:white; background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #8547F5,stop:1 #5212CA); border-color:#BB8AFF; }
QPushButton[danger="true"] { color:#FF9AB9; background:#2C1023; border-color:#9E355D; }
QPushButton[danger="true"]:hover { background:#4C1632; border-color:#EE628D; }
QPushButton:disabled, QToolButton:disabled { color:#7F7099; background:#171126; border-color:#302141; }
QPushButton#dialogClose { padding:0; min-height:26px; min-width:26px; border-radius:9px; background:transparent; }
QRadioButton, QCheckBox { color:#E4DAF3; spacing:10px; padding:6px 0; background:transparent; font-size:13px; }
QRadioButton::indicator { width:16px; height:16px; border:1px solid #8764B5; border-radius:9px; background:#110D24; }
QRadioButton::indicator:checked { background:#BD91FF; border:5px solid #553287; width:8px; height:8px; }
QCheckBox::indicator { width:16px; height:16px; border:1px solid #8764B5; border-radius:5px; background:#110D24; }
QCheckBox::indicator:checked { background:#9154F0; border-color:#C69DFF; }
QListView, QTreeView, QListWidget, QTextBrowser { color:#E9DFF9; background:#100C22; alternate-background-color:#19112E;
 border:1px solid #46315F; border-radius:9px; selection-background-color:#57308A; selection-color:white; outline:0; }
QListView::item, QTreeView::item { min-height:26px; padding:2px 5px; }
QHeaderView::section { background:#24183C; color:#CBB5EA; border:0; border-bottom:1px solid #493063; padding:7px; }
QScrollBar:vertical { background:#130E23; width:9px; margin:2px; }
QScrollBar::handle:vertical { background:#634581; min-height:24px; border-radius:3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QScrollBar:horizontal { background:#130E23; height:9px; margin:2px; }
QScrollBar::handle:horizontal { background:#634581; min-width:24px; border-radius:3px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
QProgressBar { background:#160E29; border:1px solid #543775; border-radius:7px; min-height:12px; text-align:center; color:#EBDAFF; }
QProgressBar::chunk { background:#8B40EF; border-radius:6px; }
QFrame { background:transparent; }
"""


class _DialogTitle(QWidget):
    """Перетаскивание только за заголовок, без перехвата полей и кнопок."""
    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self._drag_offset = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(11)
        icon = QLabel()
        icon.setPixmap(make_emblem(28))
        icon.setFixedSize(28, 28)
        title = QLabel(dialog.windowTitle())
        title.setObjectName("dialogTitle")
        title.setWordWrap(True)
        title.setAttribute(Qt.WA_TransparentForMouseEvents)
        icon.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.title = title
        layout.addWidget(icon)
        layout.addWidget(title, 1)
        close = QPushButton()
        close.setObjectName("dialogClose")
        close.setIcon(QIcon(glyph_pixmap("\uE711", "#BDA7D8", 16, 10)))
        close.setFixedSize(28, 28)
        close.setToolTip("Закрыть · Esc")
        close.setAutoDefault(False)
        close.clicked.connect(dialog.close)
        layout.addWidget(close, 0, Qt.AlignTop)
        self.close_button = close

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.dialog.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.dialog.move(event.globalPos() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class _AmethystDialog:
    def _init_theme(self):
        self.setObjectName("efirDialog")
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumWidth(420)
        self._dialog_header = None

    def _prepare_theme(self):
        if self._dialog_header is None:
            layout = self.layout()
            if layout is not None:
                header = _DialogTitle(self)
                if isinstance(layout, QGridLayout):
                    items = []
                    while layout.count():
                        item = layout.itemAt(0)
                        position = layout.getItemPosition(0)
                        layout.takeAt(0)
                        items.append((item, position))
                    columns = max([c + cs for _, (_, c, _, cs) in items] + [1])
                    layout.addWidget(header, 0, 0, 1, columns)
                    for item, (row, col, rs, cs) in items:
                        layout.addItem(item, row + 1, col, rs, cs)
                elif isinstance(layout, QVBoxLayout):
                    layout.insertWidget(0, header)
                else:
                    header.deleteLater()
                    header = None
                self._dialog_header = header
                layout.setContentsMargins(22, 18, 22, 22)
                layout.setSpacing(13)
        if self._dialog_header:
            self._dialog_header.title.setText(self.windowTitle())
        translations = {_QtMessageBox.Yes: "Да", _QtMessageBox.No: "Нет",
                        _QtMessageBox.Cancel: "Отмена", _QtMessageBox.Ok: "Понятно",
                        _QtMessageBox.Save: "Сохранить", _QtMessageBox.Discard: "Не сохранять"}
        if isinstance(self, QMessageBox):
            self.setIconPixmap(QPixmap())
            for button in self.buttons():
                standard = self.standardButton(button)
                if standard in translations:
                    button.setText(translations[standard])
                if self.buttonRole(button) == _QtMessageBox.DestructiveRole:
                    button.setProperty("danger", True)
                elif (standard == _QtMessageBox.Yes and self.button(self.Cancel) is None and
                      any(word in self.windowTitle().casefold() for word in ("удал", "очист"))):
                    button.setProperty("danger", True)
                if standard == self.Yes and self.windowTitle() == "Автораскладка":
                    button.setText("Переразложить")
                if standard == self.No and self.windowTitle() == "Автораскладка":
                    button.setText("Отмена")
                if self.button(self.Cancel) is None and self.windowTitle().startswith("Удалить"):
                    if standard == self.Yes:
                        button.setText("Удалить")
                    elif standard == self.No:
                        button.setText("Отмена")
            for name in ("qt_msgbox_label", "qt_msgbox_informativelabel"):
                label = self.findChild(QLabel, name)
                if label:
                    label.setWordWrap(True)
                    label.setFixedWidth(460)
                    label.setMinimumHeight(max(20, label.heightForWidth(460)))
                    label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        elif isinstance(self, QInputDialog):
            self.setOkButtonText("Применить")
            self.setCancelButtonText("Отмена")
            for label in self.findChildren(QLabel):
                if label is not getattr(self._dialog_header, "title", None):
                    label.setWordWrap(True)
                    label.setMaximumWidth(560)
        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.PointingHandCursor)
            button.style().unpolish(button)
            button.style().polish(button)
        screen = self.screen() or QApplication.primaryScreen()
        if screen:
            self.setMaximumWidth(max(300, screen.availableGeometry().width() - 40))

    def showEvent(self, event):
        self._prepare_theme()
        super().showEvent(event)
        self.layout().activate()
        self.adjustSize()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor("#26153E"))
        gradient.setColorAt(.42, QColor("#171027"))
        gradient.setColorAt(1, QColor("#100C22"))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor("#8B57BD"), 1.15))
        painter.drawRoundedRect(rect, 18, 18)
        painter.end()
        super().paintEvent(event)


class QMessageBox(_AmethystDialog, QDialog):
    # Qt standard answer/role codes retained for existing handlers and tests.
    Yes, No, Cancel, Ok, Save, Discard, NoButton = (_QtMessageBox.Yes, _QtMessageBox.No,
        _QtMessageBox.Cancel, _QtMessageBox.Ok, _QtMessageBox.Save, _QtMessageBox.Discard, _QtMessageBox.NoButton)
    AcceptRole, RejectRole, DestructiveRole, YesRole, NoRole = (_QtMessageBox.AcceptRole,
        _QtMessageBox.RejectRole, _QtMessageBox.DestructiveRole, _QtMessageBox.YesRole, _QtMessageBox.NoRole)
    Question, Information, Warning, Critical = (_QtMessageBox.Question, _QtMessageBox.Information,
        _QtMessageBox.Warning, _QtMessageBox.Critical)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_theme()
        self._buttons = []
        self._clicked = None
        self._standards = {}
        self._roles = {}
        layout = QVBoxLayout(self)
        self.message_label = QLabel()
        self.message_label.setObjectName("qt_msgbox_label")
        self.message_label.setWordWrap(True)
        self.message_label.setTextFormat(Qt.PlainText)
        self.info_label = QLabel()
        self.info_label.setObjectName("qt_msgbox_informativelabel")
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.PlainText)
        self.info_label.hide()
        # Очень длинные ошибки/пути прокручиваются, а не уходят за нижний край экрана.
        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(12)
        body_layout.addWidget(self.message_label)
        body_layout.addWidget(self.info_label)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; border:0; }")
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.body)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll)
        self.footer = QHBoxLayout()
        self.footer.setContentsMargins(0, 7, 0, 0)
        self.footer.addStretch()
        layout.addLayout(self.footer)

    def setTextFormat(self, value):
        self.message_label.setTextFormat(value)

    def setText(self, text):
        self.message_label.setText(str(text))

    def text(self):
        return self.message_label.text()

    def setInformativeText(self, text):
        self.info_label.setText(str(text))
        self.info_label.setVisible(bool(text))

    def setIcon(self, icon):
        self._message_icon = icon

    def setIconPixmap(self, pixmap):
        pass  # Единая векторная эмблема уже находится в заголовке.

    def addButton(self, text, role):
        button = QPushButton(text, self)
        button.setAutoDefault(False)
        button.clicked.connect(lambda checked=False, b=button: self._choose(b))
        self.footer.addWidget(button)
        self._buttons.append(button)
        self._roles[button] = role
        return button

    def setStandardButtons(self, buttons):
        for button in list(self._buttons):
            self.footer.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()
        self._roles.clear()
        self._standards.clear()
        for code, text, role in [(self.Yes, "Да", self.YesRole), (self.No, "Нет", self.NoRole),
                                 (self.Ok, "Понятно", self.AcceptRole), (self.Save, "Сохранить", self.AcceptRole),
                                 (self.Discard, "Не сохранять", self.DestructiveRole),
                                 (self.Cancel, "Отмена", self.RejectRole)]:
            if buttons & code:
                button = self.addButton(text, role)
                self._standards[button] = code

    def buttons(self):
        return list(self._buttons)

    def standardButton(self, button):
        return self._standards.get(button, self.NoButton)

    def button(self, code):
        return next((b for b, value in self._standards.items() if value == code), None)

    def buttonRole(self, button):
        return self._roles[button]

    def clickedButton(self):
        return self._clicked

    def setDefaultButton(self, button):
        target = self.button(button) if not isinstance(button, QPushButton) else button
        for candidate in self._buttons:
            candidate.setDefault(candidate is target)
        if target:
            target.setFocus()

    def _choose(self, button):
        self._clicked = button
        self.done(int(self._standards.get(button, QDialog.Accepted)))

    def reject(self):
        escape = self.button(self.Cancel) or self.button(self.No)
        if escape is None:
            escape = next((b for b in self._buttons if self._roles[b] == self.RejectRole), None)
        if escape is not None:
            self._choose(escape)
        elif len(self._buttons) == 1:
            self._choose(self._buttons[0])
        else:
            super().reject()

    def _prepare_theme(self):
        super()._prepare_theme()
        content_height = self.message_label.heightForWidth(460)
        if not self.info_label.isHidden():
            content_height += self.info_label.heightForWidth(460) + 12
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry().height() if screen else 800
        height = min(max(32, content_height + 6), max(100, available - 210))
        self.scroll.setFixedHeight(height)
        self.setMinimumWidth(520)

    @classmethod
    def _message(cls, icon, parent, title, text, buttons, defaultButton):
        box = cls(parent)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(icon)
        box.setStandardButtons(buttons)
        if defaultButton != cls.NoButton:
            box.setDefaultButton(defaultButton)
        elif box.button(cls.Ok):
            box.setDefaultButton(cls.Ok)
        elif box.button(cls.Cancel) or box.button(cls.No):
            box.setDefaultButton(cls.Cancel if box.button(cls.Cancel) else cls.No)
        result = box.exec_()
        box.deleteLater()
        return result

    @classmethod
    def question(cls, parent, title, text, buttons=_QtMessageBox.Yes | _QtMessageBox.No,
                 defaultButton=_QtMessageBox.NoButton):
        return cls._message(cls.Question, parent, title, text, buttons, defaultButton)

    @classmethod
    def information(cls, parent, title, text, buttons=_QtMessageBox.Ok, defaultButton=_QtMessageBox.NoButton):
        return cls._message(cls.Information, parent, title, text, buttons, defaultButton)

    @classmethod
    def warning(cls, parent, title, text, buttons=_QtMessageBox.Ok, defaultButton=_QtMessageBox.NoButton):
        return cls._message(cls.Warning, parent, title, text, buttons, defaultButton)

    @classmethod
    def critical(cls, parent, title, text, buttons=_QtMessageBox.Ok, defaultButton=_QtMessageBox.NoButton):
        return cls._message(cls.Critical, parent, title, text, buttons, defaultButton)


class QInputDialog(_AmethystDialog, QDialog):
    def __init__(self, parent=None, flags=Qt.WindowFlags()):
        super().__init__(parent, flags)
        self._init_theme()
        layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.label)
        self.input = QLineEdit()
        self.combo = QComboBox()
        self.combo.hide()
        self._use_combo = False
        layout.addWidget(self.input)
        layout.addWidget(self.combo)
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = QPushButton("Отмена")
        self.ok_button = QPushButton("Применить")
        self.ok_button.setDefault(True)
        self.ok_button.setProperty("accent", True)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.ok_button.clicked.connect(self.accept)
        self.input.returnPressed.connect(self.accept)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.ok_button)
        layout.addLayout(footer)

    def setLabelText(self, text):
        self.label.setText(text)

    def setTextEchoMode(self, echo):
        self.input.setEchoMode(echo)

    def setTextValue(self, text):
        if self._use_combo:
            self.combo.setCurrentText(text)
        else:
            self.input.setText(text)
            self.input.selectAll()

    def textValue(self):
        return self.combo.currentText() if self._use_combo else self.input.text()

    def setComboBoxItems(self, items):
        self._use_combo = True
        self.input.hide()
        self.combo.show()
        self.combo.clear()
        self.combo.addItems(items)

    def setComboBoxEditable(self, editable):
        self.combo.setEditable(editable)

    def setOkButtonText(self, text):
        self.ok_button.setText(text)

    def setCancelButtonText(self, text):
        self.cancel_button.setText(text)

    def showEvent(self, event):
        super().showEvent(event)
        (self.combo if self._use_combo else self.input).setFocus()

    @classmethod
    def getText(cls, parent, title, label, echo=QLineEdit.Normal, text="", flags=Qt.WindowFlags(),
                inputMethodHints=Qt.ImhNone):
        dialog = cls(parent, flags)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setTextEchoMode(echo)
        dialog.setTextValue(text)
        dialog.input.setInputMethodHints(inputMethodHints)
        ok = dialog.exec_() == QDialog.Accepted
        result = dialog.textValue()
        dialog.deleteLater()
        return result, ok

    @classmethod
    def getItem(cls, parent, title, label, items, current=0, editable=True, flags=Qt.WindowFlags(),
                inputMethodHints=Qt.ImhNone):
        dialog = cls(parent, flags)
        dialog.setWindowTitle(title)
        dialog.setLabelText(label)
        dialog.setComboBoxItems([str(item) for item in items])
        dialog.setComboBoxEditable(editable)
        if items:
            dialog.setTextValue(str(items[max(0, min(current, len(items)-1))]))
        dialog.combo.setInputMethodHints(inputMethodHints)
        ok = dialog.exec_() == QDialog.Accepted
        result = dialog.textValue()
        dialog.deleteLater()
        return result, ok


class QFileDialog(_AmethystDialog, _QtFileDialog):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setOption(_QtFileDialog.DontUseNativeDialog, True)
        self._init_theme()
        self.setMinimumSize(700, 480)
        self.setLabelText(self.Accept, "Выбрать")
        self.setLabelText(self.Reject, "Отмена")
        self.setLabelText(self.LookIn, "Папка:")
        self.setLabelText(self.FileName, "Имя файла:")
        self.setLabelText(self.FileType, "Тип файла:")
        # Используем системные пути Windows (в том числе перенаправленные папки),
        # а не предполагаем, что Downloads лежит в домашнем каталоге.
        urls = list(self.sidebarUrls())
        for location in (QStandardPaths.DownloadLocation, QStandardPaths.DesktopLocation,
                         QStandardPaths.DocumentsLocation):
            path = QStandardPaths.writableLocation(location)
            if path and Path(path).is_dir():
                url = QUrl.fromLocalFile(path)
                if url not in urls:
                    urls.append(url)
        self.setSidebarUrls(urls)

    @classmethod
    def _choose_file(cls, parent, caption, directory, filters, selectedFilter, options, save):
        dialog = cls(parent, caption, directory, filters)
        dialog.setOptions(options | cls.DontUseNativeDialog)
        dialog.setAcceptMode(cls.AcceptSave if save else cls.AcceptOpen)
        dialog.setFileMode(cls.AnyFile if save else cls.ExistingFile)
        dialog.setLabelText(cls.Accept, "Сохранить" if save else "Открыть")
        dialog.setLabelText(cls.Reject, "Отмена")
        # Все подтверждения сохранения тоже оформлены в теме приложения.
        dialog.setOption(cls.DontConfirmOverwrite, True)
        if selectedFilter:
            dialog.selectNameFilter(selectedFilter)
        while dialog.exec_() == QDialog.Accepted:
            paths = dialog.selectedFiles()
            if not paths or not str(paths[0]).strip():
                break
            if paths:
                path = paths[0]
                selected = dialog.selectedNameFilter()
                if save:
                    ext = Path(path).suffix.lower()
                    if ext not in (".json", ".dat"):
                        ext = ".dat" if "*.dat" in selected and "*.json" not in selected else ".json"
                        path += ext
                    matching = next((f for f in dialog.nameFilters() if "*" + ext in f), None)
                    if matching:
                        selected = matching
                    if Path(path).exists() and QMessageBox.question(
                            dialog, "Заменить файл?", f"Файл «{Path(path).name}» уже существует.\nЗаменить его?",
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                        continue
                dialog.deleteLater()
                return path, selected
        selected = dialog.selectedNameFilter()
        dialog.deleteLater()
        return "", selected

    @classmethod
    def getOpenFileName(cls, parent=None, caption="", directory="", filter="", selectedFilter="", options=_QtFileDialog.Options()):
        return cls._choose_file(parent, caption, directory, filter, selectedFilter, options, False)

    @classmethod
    def getSaveFileName(cls, parent=None, caption="", directory="", filter="", selectedFilter="", options=_QtFileDialog.Options()):
        return cls._choose_file(parent, caption, directory, filter, selectedFilter, options, True)


class QProgressDialog(_AmethystDialog, QDialog):
    """Неблокирующий индикатор чтения: закрытие окна не отменяет поток."""
    def __init__(self, label, cancel, minimum, maximum, parent=None):
        super().__init__(parent)
        self._init_theme()
        layout = QVBoxLayout(self)
        self.label = QLabel(label)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.progress = QProgressBar()
        self.progress.setRange(minimum, maximum)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)

    def setCancelButton(self, button):
        # В приложении используется только неотменяемое чтение небольшого файла.
        if button is not None:
            raise ValueError("Индикатор импорта не поддерживает отмену потока")

    def _prepare_theme(self):
        super()._prepare_theme()
        if self._dialog_header:
            self._dialog_header.close_button.hide()

    def reject(self):
        return  # Esc не оставляет работающий импорт без индикатора.


class EmojiDialog(_AmethystDialog, QDialog):
    def __init__(self, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Работа с эмодзи")
        self.setFixedWidth(460)
        
        self._init_theme()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(15)

        layout.addWidget(QLabel(f"Применить к заменам (выбрано/в базе: {count})"))

        self.input_emoji = QLineEdit()
        self.input_emoji.setPlaceholderText("Вставьте сюда эмодзи (например: 🚗)")
        layout.addWidget(self.input_emoji)

        layout.addWidget(QLabel("Действие:"))
        
        self.radio_add_start = QRadioButton("Добавить в начало текста")
        self.radio_add_end = QRadioButton("Добавить в конец текста")
        self.radio_remove = QRadioButton("Удалить ВСЕ эмодзи из текста")
        
        self.radio_add_start.setChecked(True)
        
        layout.addWidget(self.radio_add_start)
        layout.addWidget(self.radio_add_end)
        layout.addWidget(self.radio_remove)
        self.radio_remove.toggled.connect(lambda remove: self.input_emoji.setEnabled(not remove))

        layout.addStretch()

        btn_layout = QHBoxLayout()
        cancel = QPushButton("Отмена")
        apply_btn = QPushButton("Применить")
        apply_btn.setProperty("accent", True)
        apply_btn.setDefault(True)
        
        cancel.clicked.connect(self.reject)
        apply_btn.clicked.connect(self.accept)
        
        btn_layout.addStretch()
        btn_layout.addWidget(cancel)
        btn_layout.addWidget(apply_btn)
        layout.addLayout(btn_layout)

    def get_result(self):
        emoji = self.input_emoji.text().strip()
        if self.radio_remove.isChecked():
            action = "remove"
        elif self.radio_add_end.isChecked():
            action = "add_end"
        else:
            action = "add_start"
        return emoji, action


def _ghost_button(text, danger=False, glyph=None):
    btn = QPushButton(f"  {text}" if glyph else text)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setMinimumHeight(38)
    if danger:
        btn.setStyleSheet(
            "QPushButton { background: transparent; border: 1px solid #72545E; border-radius: 8px; "
            "padding: 8px 16px; color: #D497A1; font-weight: 600; }"
            "QPushButton:hover { background: #2B2027; border-color: #A87F8B; }"
            "QPushButton:pressed { background: #382731; }"
            "QPushButton:disabled { background: transparent; border-color: #2a1d22; color: #6b4a50; }"
        )
    if glyph:
        btn.setIcon(QIcon(glyph_pixmap(glyph, "#D497A1" if danger else "#CDD5E2", 18, 10)))
        btn.setIconSize(QSize(16, 16))
    return btn


class StatsStrip(QWidget):
    """Keep all four counters visible; move actions below them on narrow windows."""
    def __init__(self, cards, actions, parent=None):
        super().__init__(parent)
        self.cards, self.actions = cards, actions
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(8)
        self.grid.addWidget(cards, 0, 0)
        self.grid.addWidget(actions, 0, 1, Qt.AlignRight)
        self.grid.setColumnStretch(0, 1)
        self._compact = None
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(46)

    def minimumSizeHint(self):
        return QSize(0, self.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        needed = self.cards.sizeHint().width() + self.actions.sizeHint().width() + 12
        # Collapse as soon as content no longer fits, but only expand once
        # there is spare room. This dead band avoids 46/92 px resize jitter.
        threshold = needed + (24 if self._compact else 0)
        compact = self.width() < threshold
        if compact != self._compact:
            self._compact = compact
            self.grid.removeWidget(self.actions)
            if compact:
                self.grid.addWidget(self.actions, 1, 0, 1, 2, Qt.AlignRight)
                self.setFixedHeight(92)
            else:
                self.grid.addWidget(self.actions, 0, 1, Qt.AlignRight)
                self.setFixedHeight(46)


class SettingsPage(QWidget):
    """Страница «Настройки» внутри главного окна (вкладки сверху, сохранение внизу)."""
    saved = pyqtSignal()

    TABS = [
        ("Основные", "\uE713", "Общие параметры работы программы"),
        ("Игровой режим", "\uE7FC", "Подсказки поверх игры и их внешний вид"),
        ("Поведение", "\uE70F", "Как работает автозамена и позиционирование курсора"),
        ("Система", "\uE74E", "Автозапуск, история и управление данными"),
    ]

    STYLE = """
        QPushButton#tab {background:transparent;border:1px solid transparent;border-radius:10px;
            padding:10px 16px;color:#AF9FC8;font-weight:600;font-size:13px;}
        QPushButton#tab:hover {background:#27163E;border-color:#4E306E;color:#F7EEFF;}
        QPushButton#tab:checked {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #542893,stop:1 #2B1C50);
            color:#FBF3FF;border:1px solid #9764D6;}
        QFrame#tabLine {background:#39234F;border:none;}
        QFrame#footer {background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1E1436,stop:1 #110D23);
            border:none;border-top:1px solid #493063;}
        QScrollArea {background:transparent;border:none;}
    """

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setObjectName("centerWidget")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._loading = False
        self._dirty = False
        self.setStyleSheet(self.STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- шапка ---
        head = QWidget()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(28, 24, 28, 0)
        hl.setSpacing(16)
        self.header = PageHeader("Настройки", self.TABS[0][2])
        self.dirty_lbl = QLabel("●  Есть несохранённые изменения")
        self.dirty_lbl.setStyleSheet(
            "color: #E6B86A; background: #241F19; border: 1px solid #51402A; "
            "border-radius: 12px; padding: 4px 12px; font-size: 11px; font-weight: 600;"
        )
        self.dirty_lbl.hide()
        self.header.right.addWidget(self.dirty_lbl, 0, Qt.AlignTop)
        hl.addWidget(self.header)

        # --- вкладки ---
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(0, 0, 0, 0)
        tabs_row.setSpacing(8)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        for i, (text, glyph, _desc) in enumerate(self.TABS):
            b = QPushButton(f"  {text}")
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFocusPolicy(Qt.NoFocus)
            icon = QIcon()
            icon.addPixmap(glyph_pixmap(glyph, "#919DBF", 18, 10), QIcon.Normal, QIcon.Off)
            icon.addPixmap(glyph_pixmap(glyph, "#A58BFF", 18, 10), QIcon.Normal, QIcon.On)
            b.setIcon(icon)
            b.setIconSize(QSize(16, 16))
            self.tab_group.addButton(b, i)
            tabs_row.addWidget(b)
        tabs_row.addStretch()
        hl.addLayout(tabs_row)
        hl.setSpacing(10)
        root.addWidget(head)

        line = QFrame()
        line.setObjectName("tabLine")
        line.setFixedHeight(1)
        line_wrap = QHBoxLayout()
        line_wrap.setContentsMargins(28, 0, 28, 0)
        line_wrap.addWidget(line)
        root.addLayout(line_wrap)

        # --- содержимое ---
        self.stack = QStackedWidget()
        self.pages = []
        for _ in self.TABS:
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(28, 22, 28, 28)
            lay.setSpacing(22)
            self.pages.append(page)
            self.stack.addWidget(page)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.stack)
        self.scroll.viewport().installEventFilter(self)
        root.addWidget(self.scroll, 1)

        self._build_general(self.pages[0].layout())
        self._build_game(self.pages[1].layout())
        self._build_behavior(self.pages[2].layout())
        self._build_system(self.pages[3].layout())
        for page in self.pages:
            page.layout().addStretch()

        # --- подвал ---
        footer = QFrame()
        footer.setObjectName("footer")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(28, 14, 28, 14)
        fl.setSpacing(10)

        self.btn_defaults = _ghost_button("Сбросить к стандартным", glyph="\uE7A7")
        self.btn_defaults.clicked.connect(self.reset_to_defaults)
        self.btn_revert = QPushButton("Отменить изменения")
        self.btn_revert.setMinimumHeight(38)
        self.btn_revert.setCursor(Qt.PointingHandCursor)
        self.btn_revert.clicked.connect(self.revert)
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setObjectName("accent")
        self.btn_save.setMinimumHeight(38)
        self.btn_save.setMinimumWidth(150)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.setIcon(QIcon(glyph_pixmap("\uE74E", "white", 18, 10)))
        self.btn_save.setIconSize(QSize(16, 16))
        self.btn_save.clicked.connect(self.save)

        fl.addWidget(self.btn_defaults)
        fl.addStretch()
        fl.addWidget(self.btn_revert)
        fl.addWidget(self.btn_save)
        root.addWidget(footer)

        (getattr(self.tab_group, "idClicked", None) or self.tab_group.buttonClicked[int]).connect(self.switch_tab)
        self.load_from(self.main.data["settings"])
        self.tab_group.button(0).setChecked(True)
        self.switch_tab(0)

    # ---------- построение вкладок ----------
    def _hotkey_edit(self):
        e = QLineEdit()
        e.setFixedWidth(230)
        e.setAlignment(Qt.AlignCenter)
        e.setMinimumHeight(40)
        e.setStyleSheet("font-family: Consolas; font-size: 13px; font-weight: 600; letter-spacing: 0.5px;")
        e.textEdited.connect(self._touch)
        return e

    def _seg(self, options):
        s = SegmentedControl(options)
        s.changed.connect(self._touch)
        return s

    def _bool(self, on_text, off_text):
        b = BoolChoice(on_text, off_text)
        b.changed.connect(self._touch)
        return b

    def _step(self, lo, hi, step=1, suffix=""):
        s = ModernSpinBox(lo, hi, lo, step=step, suffix=suffix)
        s.valueChanged.connect(self._touch)
        return s

    def _build_general(self, lay):
        g = SettingGroup("Управление")
        self.start_mode = self._seg([("Сразу при запуске", True), ("По кнопке", False)])
        g.add_row("\uE768", "Запуск автозамены",
                  "Включать автозамену автоматически при старте программы или вручную кнопкой «Запустить»",
                  self.start_mode)
        self.hotkey = self._hotkey_edit()
        g.add_row("\uE765", "Горячая клавиша", "Включение и выключение автозамены", self.hotkey)
        self.mode = self._seg([
            ("После пробела", "space"), ("После Enter", "enter"),
            ("Мгновенно", "instant"), ("После пунктуации", "punctuation"),
        ])
        g.add_row("\uE945", "Режим срабатывания", "Когда именно программа заменит введённый ключ", self.mode, stacked=True)
        lay.addWidget(g)

    def _build_game(self, lay):
        g = SettingGroup("Оверлей")
        self.game_enabled = self._bool("Включён", "Выключен")
        g.add_row("\uE7FC", "Игровой оверлей", "Показывать подсказки поверх игры", self.game_enabled)
        self.game_hotkey = self._hotkey_edit()
        g.add_row("\uE765", "Горячая клавиша", "Комбинация для вызова оверлея", self.game_hotkey)
        self.overlay_position = self._seg([
            ("↖  Слева сверху", "top_left"), ("↗  Справа сверху", "top_right"),
            ("↙  Слева снизу", "bottom_left"), ("↘  Справа снизу", "bottom_right"),
        ])
        g.add_row("\uE7F4", "Положение подсказки", "Угол экрана, в котором появляются подсказки",
                  self.overlay_position, stacked=True)
        lay.addWidget(g)

        g2 = SettingGroup("Параметры показа")
        self.overlay_opacity = self._step(55, 100, 1, " %")
        g2.add_row("\uE706", "Прозрачность", "Насколько непрозрачно окно подсказок", self.overlay_opacity)
        self.overlay_limit = self._step(1, 8)
        g2.add_row("\uE8FD", "Количество вариантов", "Сколько вариантов показывать одновременно", self.overlay_limit)
        self.overlay_min_query_len = self._step(1, 10)
        g2.add_row("Ab", "Минимум символов", "С какой длины запроса появляются подсказки", self.overlay_min_query_len)
        self.overlay_auto_hide = self._step(1000, 30000, 500, " мс")
        g2.add_row("\uE916", "Сворачивать через", "Через сколько оверлей уходит в компактный вид", self.overlay_auto_hide)
        self.buffer_idle_reset = self._step(500, 20000, 250, " мс")
        g2.add_row("\uE72C", "Сброс буфера", "Через сколько простоя сбрасывается набранный текст", self.buffer_idle_reset)
        self.overlay_screen_margin = self._step(0, 200, 2, " px")
        g2.add_row("↔", "Отступ от края", "Расстояние от края экрана до оверлея", self.overlay_screen_margin)
        lay.addWidget(g2)

        warning = QLabel(
            "Для совместимости используйте режим игры «Безрамочный / Borderless Fullscreen». "
            "В эксклюзивном полноэкранном режиме некоторые игры могут скрывать сторонние окна."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            "color:#E6B86A; background:#241F19; border:1px solid #51402A; border-radius:10px; "
            "padding:12px 16px; font-size:12px;"
        )
        lay.addWidget(warning)

    def _build_behavior(self, lay):
        g = SettingGroup("Автозамена")
        self.case_sensitive = self._bool("Учитывать", "Не учитывать")
        g.add_row("Aa", "Регистр", "Влияет ли регистр на срабатывание («Пр» и «пр»)", self.case_sensitive)
        self.cursor_mode = self._seg([
            ("Только [курсор]", "punto"), ("[курсор] и символ |", "both"), ("Отключено", "off"),
        ])
        g.add_row("\uE8B0", "Позиция курсора", "Куда ставить курсор после вставки текста",
                  self.cursor_mode, stacked=True)
        lay.addWidget(g)

    def _build_system(self, lay):
        g = SettingGroup("Приложение")
        self.history_enabled = self._bool("Вести", "Не вести")
        g.add_row("\uE81C", "История замен", "Сохранять журнал срабатываний", self.history_enabled)
        self.minimize = self._bool("В трей", "Закрывать")
        g.add_row("\uE921", "При закрытии окна", "Сворачивать в системный трей или полностью закрывать", self.minimize)
        self.run_at_startup = self._bool("Включён", "Выключен")
        g.add_row("\uE7E8", "Автозапуск", "Запускать Эфир вместе с Windows", self.run_at_startup)
        lay.addWidget(g)

        d = SettingGroup("Опасная зона", danger=True)
        btn_clear = _ghost_button("Очистить…", danger=True, glyph="\uE74D")
        btn_clear.clicked.connect(self._confirm_clear_all)
        d.add_row("\uE74D", "Очистить всю базу", "Удалить все автозамены во всех радиоцентрах", btn_clear)

        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        self.delete_profile_combo = QComboBox()
        self.delete_profile_combo.setMinimumWidth(110)
        self.delete_profile_combo.setMinimumHeight(38)
        btn_del = _ghost_button("Очистить замены…", danger=True)
        btn_del.clicked.connect(self._confirm_delete_profile)
        btn_rm = _ghost_button("Удалить радиоцентр…", danger=True, glyph="\uE74D")
        btn_rm.clicked.connect(lambda: self.main.delete_profile(self.delete_profile_combo.currentText()))
        bl.addWidget(self.delete_profile_combo)
        bl.addWidget(btn_del)
        bl.addWidget(btn_rm)
        d.add_row("\uE74D", "Радиоцентр", "Очистить замены выбранного радиоцентра или удалить его целиком", box)
        lay.addWidget(d)

    # ---------- состояние ----------
    @property
    def is_dirty(self):
        return self._dirty

    def _set_dirty(self, value):
        self._dirty = bool(value)
        self.dirty_lbl.setVisible(self._dirty)
        self.btn_save.setEnabled(self._dirty)
        self.btn_revert.setEnabled(self._dirty)

    def _touch(self, *_):
        if not self._loading:
            self._set_dirty(True)

    def switch_tab(self, index):
        self.stack.setCurrentIndex(index)
        self.header.set_desc(self.TABS[index][2])
        self.scroll.verticalScrollBar().setValue(0)
        QTimer.singleShot(0,self._fit_page_height)

    def _fit_page_height(self):
        # QStackedWidget normally inherits the tallest hidden page's minimum.
        # Size only the visible page, so General does not get a useless scrollbar.
        page=self.stack.currentWidget()
        if page is not None:
            page.layout().activate()
            needed=page.layout().minimumSize().height()
            self.stack.setFixedHeight(max(self.scroll.viewport().height(),needed))

    def eventFilter(self,obj,event):
        if hasattr(self,'scroll') and obj is self.scroll.viewport() and event.type()==QEvent.Resize:
            QTimer.singleShot(0,self._fit_page_height)
        return super().eventFilter(obj,event)

    def refresh_dynamic(self):
        """Обновить то, что зависит от внешних данных (список радиоцентров)."""
        current = self.delete_profile_combo.currentText()
        self.delete_profile_combo.clear()
        self.delete_profile_combo.addItems(self.main.data["settings"].get("profiles", RADIO_PROFILES))
        idx = self.delete_profile_combo.findText(current)
        if idx >= 0:
            self.delete_profile_combo.setCurrentIndex(idx)

    def load_from(self, s, autostart=None):
        self._loading = True
        try:
            d = DEFAULT_DATA["settings"]
            g = lambda k: s.get(k, d[k])
            self.start_mode.setValue(bool(g("start_on_launch")))
            self.hotkey.setText(str(g("hotkey")))
            self.mode.setValue(g("trigger_mode"))
            self.game_enabled.setChecked(bool(g("game_overlay_enabled")))
            self.game_hotkey.setText(str(g("game_overlay_hotkey")))
            self.overlay_position.setValue(g("overlay_position"))
            self.overlay_opacity.setValue(int(g("overlay_opacity")))
            self.overlay_limit.setValue(int(g("overlay_max_suggestions")))
            self.overlay_min_query_len.setValue(int(g("overlay_min_query_len")))
            self.overlay_auto_hide.setValue(int(g("overlay_auto_hide_ms")))
            self.buffer_idle_reset.setValue(int(g("buffer_idle_reset_ms")))
            self.overlay_screen_margin.setValue(int(g("overlay_screen_margin")))
            self.case_sensitive.setChecked(bool(g("case_sensitive")))
            self.cursor_mode.setValue(g("cursor_mode"))
            self.history_enabled.setChecked(bool(g("history_enabled")))
            self.minimize.setChecked(bool(g("minimize_to_tray")))
            self.run_at_startup.setChecked(read_autostart() if autostart is None else autostart)
            self.refresh_dynamic()
        finally:
            self._loading = False
        self._set_dirty(False)

    def apply(self, settings=None):
        s = self.main.data["settings"] if settings is None else settings
        s["start_on_launch"] = bool(self.start_mode.value())
        s["hotkey"] = self.hotkey.text().strip().lower() or "ctrl+alt+f10"
        s["game_overlay_hotkey"] = self.game_hotkey.text().strip().lower() or "alt+2"
        s["game_overlay_enabled"] = self.game_enabled.isChecked()
        s["overlay_position"] = self.overlay_position.value()
        s["overlay_opacity"] = self.overlay_opacity.value()
        s["overlay_max_suggestions"] = self.overlay_limit.value()
        s["overlay_min_query_len"] = self.overlay_min_query_len.value()
        s["overlay_auto_hide_ms"] = self.overlay_auto_hide.value()
        s["buffer_idle_reset_ms"] = self.buffer_idle_reset.value()
        s["overlay_screen_margin"] = self.overlay_screen_margin.value()
        s["trigger_mode"] = self.mode.value()
        s["case_sensitive"] = self.case_sensitive.isChecked()
        s["cursor_mode"] = self.cursor_mode.value()
        s["history_enabled"] = self.history_enabled.isChecked()
        s["minimize_to_tray"] = self.minimize.isChecked()

    def save(self):
        candidate = dict(self.main.data)
        candidate["settings"] = copy.deepcopy(self.main.data["settings"])
        self.apply(candidate["settings"])
        if (self.main.data["settings"].get("case_sensitive", False)
                and not candidate["settings"].get("case_sensitive", False)):
            conflicts = insensitive_key_conflicts(candidate["shortcuts"])
            if conflicts:
                preview = "\n".join(
                    f"• {split_ckey(a)[0]}: «{split_ckey(a)[1]}» / «{split_ckey(b)[1]}»"
                    for a, b in conflicts[:5])
                differing = [(a, b) for a, b in conflicts
                             if candidate["shortcuts"][a].get("text", "") != candidate["shortcuts"][b].get("text", "")]
                if differing:
                    diff_preview = "\n".join(
                        f"• {split_ckey(a)[0]}: «{split_ckey(a)[1]}» / «{split_ckey(b)[1]}»"
                        for a, b in differing[:5])
                    QMessageBox.warning(
                        self, "Совпадающие ключи",
                        f"Найдено совпадений: {len(conflicts)}. У {len(differing)} из них разный текст:\n"
                        f"{diff_preview}\n\nПри выключенном учёте регистра они неразличимы. "
                        "Переименуйте или удалите лишние записи, затем повторите сохранение.")
                    return
                answer = QMessageBox.question(
                    self, "Совпадающие ключи",
                    f"Найдено повторов с одинаковым текстом: {len(conflicts)}.\n{preview}\n\n"
                    "Чтобы отключить учёт регистра, можно оставить первый ключ в каждой группе, "
                    "а остальные удалить. Их настройки, горячие клавиши и счётчики также будут удалены. "
                    "Перед сохранением создаётся резервная копия.\n\nУдалить повторы и сохранить?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
                candidate["shortcuts"] = {key: dict(info) for key, info in candidate["shortcuts"].items()}
                for _, duplicate in conflicts:
                    candidate["shortcuts"].pop(duplicate, None)
        if not self.main._commit_data(candidate):
            self._set_dirty(True)
            return
        self.main.refresh_table()
        write_autostart(self.run_at_startup.isChecked())
        self._set_dirty(False)
        self.saved.emit()

    def revert(self):
        self.load_from(self.main.data["settings"])
        self.main.show_message("Изменения отменены.")

    def reset_to_defaults(self):
        if QMessageBox.question(
            self, "Сброс настроек",
            "Вернуть все параметры к стандартным значениям?\n\nИзменения вступят в силу после нажатия «Сохранить».",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return
        self.load_from(DEFAULT_DATA["settings"], autostart=False)
        self._set_dirty(True)
        self.main.show_message("Стандартные значения подставлены — нажмите «Сохранить».")

    # ---------- опасная зона ----------
    def _confirm_clear_all(self):
        self.main.delete_all_shortcuts()

    def _confirm_delete_profile(self):
        if not self.main._confirm_editor_changes():
            return
        profile = self.delete_profile_combo.currentText()
        if not profile:
            return
        data = self.main.data
        count = sum(1 for v in data.get("shortcuts", {}).values() if v.get("profile") == profile)
        if count == 0:
            QMessageBox.information(self, "Удаление", f"В радиоцентре {profile} нет замен.")
            return
        reply = QMessageBox.question(
            self, "Удалить замены",
            f"Удалить ВСЕ {count} замен из радиоцентра {profile}?\n\nБудет создана резервная копия.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        shortcuts = {k: dict(v) for k, v in data["shortcuts"].items() if v.get("profile") != profile}
        if not self.main._commit_shortcuts(shortcuts):
            return
        self.main.clear_editor()
        self.main.refresh_table()
        self.main.show_message(f"Удалено {count} замен из {profile}")
        self.refresh_dynamic()


class GroupItemDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):return QSize(0,64)

    def paint(self,painter,option,index):
        painter.save();painter.setRenderHints(QPainter.Antialiasing|QPainter.TextAntialiasing|QPainter.SmoothPixmapTransform)
        rect=QRectF(option.rect).adjusted(2,4,-2,-4)
        selected=bool(option.state & QStyle.State_Selected);hovered=bool(option.state & QStyle.State_MouseOver)
        fill=QLinearGradient(rect.left(),rect.top(),rect.right(),rect.bottom())
        fill.setColorAt(0,QColor('#51258D' if selected else '#26173D' if hovered else '#19112D'))
        fill.setColorAt(1,QColor('#261742' if selected else '#171126'))
        painter.setBrush(fill);painter.setPen(QPen(QColor('#9C69DA' if selected else '#4C2D6A' if hovered else '#302040'),.8))
        painter.drawRoundedRect(rect,11,11)
        tile=QRectF(rect.left()+12,rect.center().y()-17,34,34)
        painter.setBrush(QColor('#65369B' if selected else '#291A44'));painter.setPen(QPen(QColor('#9663C9' if selected else '#543372'),.8));painter.drawRoundedRect(tile,9,9)
        icon=glyph_pixmap('\uE8B7','#D6B0FF',24,12)
        painter.drawPixmap(QRectF(tile.x()+5,tile.y()+5,24,24),icon,QRectF(icon.rect()))
        name=str(index.data(Qt.UserRole) or '');count=int(index.data(Qt.UserRole+1) or 0)
        f=QFont('Segoe UI');f.setPixelSize(12);painter.setFont(f)
        label=f"{count:,}".replace(',', ' ')+' '+plural(count,'замена','замены','замен')
        bw=painter.fontMetrics().horizontalAdvance(label)+22
        badge=QRectF(rect.right()-bw-12,rect.center().y()-13,bw,26)
        painter.setBrush(QColor('#32214F' if selected else '#20162F'));painter.setPen(QPen(QColor('#785099' if selected else '#402C56'),.8));painter.drawRoundedRect(badge,8,8)
        painter.setPen(QColor('#DEC9F7'));painter.drawText(badge,int(Qt.AlignCenter),label)
        f.setPixelSize(14);f.setWeight(QFont.DemiBold);painter.setFont(f);painter.setPen(QColor('#FAF3FF'))
        text_rect=QRectF(rect.left()+58,rect.top(),max(0,badge.left()-rect.left()-70),rect.height())
        shown=painter.fontMetrics().elidedText(name,Qt.ElideRight,int(text_rect.width()))
        painter.drawText(text_rect,int(Qt.AlignLeft|Qt.AlignVCenter),shown)
        painter.restore()


class GroupsPage(QWidget):
    """Страница «Группы»: список групп текущего радиоцентра и действия над ними."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setObjectName("centerWidget")
        self.setAttribute(Qt.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)
        self.header = PageHeader("Группы", "")
        root.addWidget(self.header)

        body = QHBoxLayout()
        body.setSpacing(18)

        self.list_widget = QListWidget()
        self.list_widget.setMouseTracking(True)
        self.list_widget.setItemDelegate(GroupItemDelegate(self.list_widget))
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_widget.setStyleSheet("QListWidget {background:transparent;border:none;padding:0;outline:none;} QListWidget::item {border:none;}")
        self.list_widget.setFrameShape(QFrame.NoFrame)
        list_panel=AmethystPanel()
        list_layout=QVBoxLayout(list_panel);list_layout.setContentsMargins(10,10,10,10);list_layout.addWidget(self.list_widget)
        self.list_widget.currentItemChanged.connect(self._update_side)
        body.addWidget(list_panel, 1)

        self.empty_label = QLabel("В этом радиоцентре пока нет групп")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #919DBF; font-size: 14px; background: transparent;")
        self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        el = QVBoxLayout(self.list_widget.viewport())
        el.addWidget(self.empty_label, alignment=Qt.AlignCenter)
        self.empty_label.hide()

        side = AmethystPanel()
        side.setObjectName("sidePanel")
        side.setFixedWidth(300)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(20, 20, 20, 20)
        sl.setSpacing(10)

        cap = QLabel("ВЫБРАННАЯ ГРУППА")
        cap.setStyleSheet("color: #919DBF; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        self.sel_name = ElidedLabel("—")
        self.sel_name.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.sel_count = QLabel("Выберите группу в списке")
        self.sel_count.setStyleSheet("color: #B0BBCB; font-size: 12px;")
        sl.addWidget(cap)
        sl.addWidget(self.sel_name)
        sl.addWidget(self.sel_count)
        sl.addSpacing(14)

        self.btn_rename = SmoothActionButton("Переименовать", "accent")
        self.btn_rename.setIcon(QIcon(glyph_pixmap("\uE70F", "#CDD5E2", 18, 10)))
        self.btn_merge = SmoothActionButton("Объединить с…")
        self.btn_merge.setIcon(QIcon(glyph_pixmap("\uE71B", "#CDD5E2", 18, 10)))
        for b in (self.btn_rename, self.btn_merge):
            b.setMinimumHeight(40)
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton { text-align: left; padding-left: 14px; }")
            sl.addWidget(b)
        self.btn_delete = _ghost_button("Удалить группу…", danger=True, glyph="\uE74D")
        self.btn_delete.setMinimumHeight(40)
        self.btn_delete.setStyleSheet(self.btn_delete.styleSheet() + "QPushButton { text-align: left; padding-left: 14px; }")
        sl.addWidget(self.btn_delete)
        sl.addStretch()
        hint = QLabel("Изменения применяются сразу. Перед удалением замен создаётся резервная копия.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #919DBF; font-size: 11px;")
        sl.addWidget(hint)
        body.addWidget(side)
        root.addLayout(body, 1)

        self.btn_rename.clicked.connect(self.rename_group)
        self.btn_merge.clicked.connect(self.merge_group)
        self.btn_delete.clicked.connect(self.delete_group)
        self.reload()

    @property
    def profile(self):
        return self.main.data["settings"].get("active_profile", DEFAULT_PROFILE)

    def reload(self):
        keep = self.selected_group()
        counts = {}
        for info in self.main.data.get("shortcuts", {}).values():
            if info.get("profile") == self.profile:
                g = info.get("group", "Общие")
                counts[g] = counts.get(g, 0) + 1

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for group in sorted(counts, key=str.casefold):
            item = QListWidgetItem()
            item.setData(Qt.UserRole, group)
            item.setToolTip(group)
            item.setData(Qt.UserRole + 1, counts[group])
            self.list_widget.addItem(item)
            if group == keep:
                self.list_widget.setCurrentItem(item)
        if self.list_widget.currentRow()<0 and self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self.list_widget.blockSignals(False)

        total = sum(counts.values())
        n = len(counts)
        self.header.set_desc(
            f"Радиоцентр {self.profile}  •  {n} {plural(n, 'группа', 'группы', 'групп')}  •  "
            f"{total:,} {plural(total, 'замена', 'замены', 'замен')}".replace(",", " ")
        )
        self.empty_label.setVisible(n == 0)
        self._update_side()

    def selected_group(self):
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _update_side(self, *_):
        item = self.list_widget.currentItem()
        has = item is not None
        for b in (self.btn_rename, self.btn_merge, self.btn_delete):
            b.setEnabled(has)
        if has:
            c = int(item.data(Qt.UserRole + 1))
            self.sel_name.setText(item.data(Qt.UserRole))
            self.sel_name.setToolTip(item.data(Qt.UserRole))
            self.sel_count.setText((f"{c:,} {plural(c, 'замена', 'замены', 'замен')} в группе").replace(","," "))
        else:
            self.sel_name.setText("—")
            self.sel_count.setText("Выберите группу в списке")

    def _apply_changes(self, shortcuts, message="Группы обновлены"):
        if not self.main._commit_shortcuts(shortcuts):
            return False
        self.main.refresh_table()
        self.main.show_message(message)
        return True

    def rename_group(self):
        old = self.selected_group()
        if not old:
            return
        new_name, ok = QInputDialog.getText(self, "Переименовать группу", f"Новое имя для «{old}»:", text=old)
        new_name = new_name.strip()
        if ok and new_name and new_name != old:
            shortcuts = self.main._copy_shortcuts()
            for info in shortcuts.values():
                if info.get("profile") == self.profile and info.get("group") == old:
                    info["group"] = new_name
            self._apply_changes(shortcuts, message=f"Группа переименована: {new_name}")

    def merge_group(self):
        old = self.selected_group()
        if not old:
            return
        others = [self.list_widget.item(i).data(Qt.UserRole) for i in range(self.list_widget.count())
                  if self.list_widget.item(i).data(Qt.UserRole) != old]
        if not others:
            QMessageBox.information(self, "Объединение", "Нет других групп для объединения.")
            return
        target, ok = QInputDialog.getItem(
            self, "Объединить группы", f"Перенести все замены из «{old}» в:", others, 0, False
        )
        if ok and target:
            shortcuts = self.main._copy_shortcuts()
            for info in shortcuts.values():
                if info.get("profile") == self.profile and info.get("group") == old:
                    info["group"] = target
            self._apply_changes(shortcuts, message=f"Группы объединены: {old} → {target}")

    def delete_group(self):
        if not self.main._confirm_editor_changes():
            return
        group = self.selected_group()
        if not group:
            return
        ans = QMessageBox.question(
            self, "Удалить группу",
            f"Что сделать с заменами из группы «{group}»?\n\n"
            "Да — переместить их в «Общие».\n"
            "Нет — удалить замены вместе с группой.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes
        )
        if ans == QMessageBox.Cancel:
            return
        data = self.main._copy_shortcuts()
        to_delete = []
        for key, info in data.items():
            if info.get("profile") == self.profile and info.get("group") == group:
                if ans == QMessageBox.Yes:
                    info["group"] = "Общие"
                else:
                    to_delete.append(key)
        for k in to_delete:
            del data[k]
        if self._apply_changes(data, message=f"Группа удалена: {group}"):
            self.main.clear_editor()


class HistoryTable(QTableWidget):
    """Подсветка всей строки журнала при наведении."""
    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.hover_row = -1
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def mouseMoveEvent(self, event):
        row = self.rowAt(event.pos().y())
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(event)


class HistoryDelegate(QStyledItemDelegate):
    """Полная отрисовка: никаких белых системных фонов, даже при потере фокуса."""
    def paint(self, painter, option, index):
        painter.save()
        rect, col = option.rect, index.column()
        painter.setClipRect(rect)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = getattr(option.widget, "hover_row", -1) == index.row()
        base = QColor("#15102C" if index.row() % 2 else "#0F0C21")
        if hovered:
            base = QColor("#271741")
        fill = QLinearGradient(0, rect.top(), 0, rect.bottom())
        fill.setColorAt(0, QColor("#532694") if selected else base)
        fill.setColorAt(1, QColor("#281744") if selected else base)
        painter.fillRect(rect, QBrush(fill))
        painter.setPen(QColor("#352247"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if selected:
            painter.setRenderHint(QPainter.Antialiasing)
            width=option.widget.viewport().width()
            tint=QLinearGradient(0,rect.top(),width,rect.bottom())
            tint.setColorAt(0,QColor('#5E2CA2'));tint.setColorAt(1,QColor('#27163F'))
            painter.setBrush(tint);painter.setPen(QPen(QColor('#9B65D5'),.8))
            painter.drawRoundedRect(QRectF(.5,rect.top()+.5,width-1,rect.height()-1),9,9)
        if selected and col == 0:
            painter.fillRect(QRect(rect.x(), rect.y() + 1, 3, rect.height() - 2), QColor("#A58BFF"))
        painter.setRenderHint(QPainter.Antialiasing)
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        painter.setFont(font)
        text = str(index.data(Qt.DisplayRole) or "")
        fm = painter.fontMetrics()
        if col == 0:
            stamp = index.data(Qt.UserRole) or {}
            clock, day = stamp.get("clock", text), stamp.get("day", "")
            painter.setPen(QColor("#F0EDFF"))
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)
            if day:
                painter.drawText(rect.adjusted(16, 7, -16, -24), int(Qt.AlignCenter), clock)
                font.setPixelSize(11)
                font.setWeight(QFont.Normal)
                painter.setFont(font)
                painter.setPen(QColor("#A6B0D1"))
                painter.drawText(rect.adjusted(16, 30, -16, -5), int(Qt.AlignCenter), day)
            else:
                shown = fm.elidedText(clock, Qt.ElideRight, max(0, rect.width() - 32))
                painter.drawText(rect.adjusted(16, 0, -16, 0), int(Qt.AlignCenter), shown)
        elif col == 1:
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)
            fm = painter.fontMetrics()
            shown = fm.elidedText(text, Qt.ElideRight, max(0, rect.width() - 56))
            width = min(rect.width() - 24, max(52, fm.horizontalAdvance(shown) + 28))
            badge = QRectF(rect.center().x() - width / 2, rect.center().y() - 14, width, 28)
            painter.setBrush(QColor("#32204E" if not selected else "#61359C"))
            painter.setPen(QPen(QColor("#9961CE"), 1))
            painter.drawRoundedRect(badge, 7, 7)
            painter.setPen(QColor("#E4DAFF"))
            painter.drawText(badge, int(Qt.AlignCenter), shown)
        else:
            color = QColor(profile_color(text)) if text != "—" else QColor("#A6B0D1")
            shown = fm.elidedText(text, Qt.ElideRight, max(0, rect.width() - 48))
            width = min(rect.width() - 24, max(48, fm.horizontalAdvance(shown) + 24))
            badge = QRectF(rect.center().x() - width / 2, rect.center().y() - 13, width, 26)
            tint, edge = QColor(color), QColor(color)
            tint.setAlpha(28)
            edge.setAlpha(140)
            painter.setBrush(tint)
            painter.setPen(QPen(edge, 1))
            painter.drawRoundedRect(badge, 7, 7)
            font.setWeight(QFont.DemiBold)
            painter.setFont(font)
            painter.setPen(color.lighter(115))
            painter.drawText(badge, int(Qt.AlignCenter), shown)
        painter.restore()


class HistoryPage(QWidget):
    """Страница «История»: журнал последних срабатываний."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setObjectName("centerWidget")
        self.setAttribute(Qt.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        self.header = PageHeader("История", "")
        self.btn_clear = _ghost_button("Очистить историю", danger=True, glyph="\uE74D")
        self.btn_clear.clicked.connect(self.clear_history)
        self.header.right.addWidget(self.btn_clear, 0, Qt.AlignTop)
        root.addWidget(self.header)

        self.table = HistoryTable()
        self.table.setHorizontalHeader(TableHeader(self.table))
        self.table.setItemDelegate(HistoryDelegate(self.table))
        self.table.setHorizontalHeaderLabels(["Время", "Ключ замены", "Радиоцентр"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(56)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        hv = self.table.horizontalHeader()
        hv.setDefaultAlignment(Qt.AlignCenter)
        hv.setFixedHeight(44)
        hv.setHighlightSections(False)
        hv.setStretchLastSection(False)
        hv.setSectionResizeMode(0, QHeaderView.Fixed)
        hv.setSectionResizeMode(1, QHeaderView.Stretch)
        hv.setSectionResizeMode(2, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(2, 180)
        self.table_card = TableCard(self.table)
        root.addWidget(self.table_card, 1)

        self.empty_label = QLabel("История пока пуста")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #919DBF; font-size: 14px; background: transparent;")
        self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        el = QVBoxLayout(self.table.viewport())
        el.addWidget(self.empty_label, alignment=Qt.AlignCenter)
        self.empty_label.hide()
        self.reload()

    def reload(self):
        history = self.main.data.get("history", [])
        n = len(history)
        sig = (tuple((str(item.get("time", "")), str(item.get("key", ""))) for item in history),
               self.main.data["settings"].get("history_enabled", True))
        if sig == getattr(self, "_sig", None):
            return
        self._sig = sig
        desc = f"Последние срабатывания  •  {n} {plural(n, 'запись', 'записи', 'записей')}"
        enabled = self.main.data["settings"].get("history_enabled", True)
        if not enabled:
            desc += "  •  журнал отключён в настройках"
        self.header.set_desc(desc)
        self.btn_clear.setEnabled(n > 0)

        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(n)
        mono = QFont("Consolas", 10)
        mono.setBold(True)
        for row, item in enumerate(reversed(history)):
            profile, bare = split_ckey(item.get("key", ""))
            cells = [
                QTableWidgetItem(str(item.get("time", ""))),
                QTableWidgetItem(bare or item.get("key", "")),
                QTableWidgetItem(profile or "—"),
            ]
            raw_time = str(item.get("time", ""))
            try:
                dt = datetime.datetime.fromisoformat(raw_time)
                cells[0].setData(Qt.UserRole, {"clock": dt.strftime("%H:%M:%S"), "day": dt.strftime("%d.%m.%Y")})
            except (TypeError, ValueError, OverflowError):
                cells[0].setData(Qt.UserRole, {"clock": raw_time, "day": ""})
            cells[0].setForeground(QColor("#B0BBCB"))
            cells[1].setFont(mono)
            cells[2].setForeground(QColor(profile_color(profile) if profile else "#B0BBCB"))
            for col, c in enumerate(cells):
                c.setTextAlignment(Qt.AlignCenter)
                c.setToolTip(c.text())
                self.table.setItem(row, col, c)
        self.table.setUpdatesEnabled(True)
        self.empty_label.setVisible(n == 0)

    def clear_history(self):
        if QMessageBox.question(
            self, "Очистить историю", "Удалить всю историю срабатываний?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) == QMessageBox.Yes:
            candidate = dict(self.main.data)
            candidate["history"] = []
            if not self.main._commit_data(candidate):
                return
            self.reload()
            self.main.show_message("История очищена")


def dark_startup_palette():
    """Dark fallback before any native window or styled child can be created."""
    palette = QPalette(QApplication.palette())
    colors = {
        QPalette.Window: "#09091B", QPalette.WindowText: "#F1F2FF",
        QPalette.Base: "#100E28", QPalette.AlternateBase: "#15122E",
        QPalette.Text: "#F1F2FF", QPalette.Button: "#1A2040",
        QPalette.ButtonText: "#E1E5FA", QPalette.Highlight: "#6B2CDB",
        QPalette.HighlightedText: "#FFFFFF", QPalette.ToolTipBase: "#231A39",
        QPalette.ToolTipText: "#F1F2FF",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.Text, QPalette.WindowText, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#80748F"))
    return palette


# --- ГЛАВНОЕ ОКНО ---

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setPalette(dark_startup_palette())
        self.setAutoFillBackground(True)
        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setWindowIcon(QIcon(make_emblem(256)))
        self.setMinimumSize(1160, 620)
        # Просторнее при запуске, но без выхода за рабочую область обычного экрана.
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else QRect(0, 0, 1400, 860)
        self.resize(min(1360, max(1160, available.width() - 32)),
                    min(800, max(620, available.height() - 48)))
        
        self.data = load_data()
        self.editing_key = None
        self._allow_close = False
        self._binder_transition = False
        self.binder = BinderController()
        self.binder.configure(self.data)
        self.binder.log_signal.connect(self.show_message)
        self.binder.status_signal.connect(self.update_status)
        self.binder.hotkey_signal.connect(self.toggle_binder)
        self.binder.replacement_signal.connect(self.record_replacement)
        self.binder.ensure_listener()  # горячая клавиша вкл/выкл работает, даже когда Эфир остановлен

        self.game_overlay = GameHintOverlay()
        self.game_overlay.set_position(self.data["settings"].get("overlay_position", "top_right"))
        self.game_overlay.set_screen_margin(int(self.data["settings"].get("overlay_screen_margin", 24)))
        self.game_overlay.setWindowOpacity(int(self.data["settings"].get("overlay_opacity", 92)) / 100.0)
        self.game_overlay.set_hotkey_text(self.data["settings"].get("game_overlay_hotkey", "alt+2"))
        self.binder.suggestions_signal.connect(self.game_overlay.set_suggestions)
        self.binder.overlay_toggle_signal.connect(self._set_game_overlay_active)

        self.current_view = "all"
        self.setStyleSheet(self.styles())
        self._build_ui()
        for field in self.findChildren(QLineEdit) + self.findChildren(QTextEdit):
            palette = field.palette()
            palette.setColor(QPalette.PlaceholderText, QColor("#919DBF"))
            field.setPalette(palette)
        for icon_label in self.findChildren(QLabel):
            pix=icon_label.pixmap()
            if pix is not None and not pix.isNull() and 22<=icon_label.width()<=38:
                glow=QGraphicsDropShadowEffect(icon_label)
                glow.setOffset(0,0);glow.setBlurRadius(12);glow.setColor(QColor(151,63,255,170))
                icon_label.setGraphicsEffect(glow)
        self._build_tray()
        self.refresh_table()
        self.refresh_profiles()
        self.update_status(False)
        for button in self.findChildren(QPushButton):
            name = button.objectName()
            role = ("nav" if name == "nav" else
                    "action" if name in ("accent", "save_btn") else
                    "run" if button is self.btn_toggle else None)
            if role:
                button._ui_glow = _AccentGlow(button, role)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._flush_save)
        if self.data["settings"].get("start_on_launch"):
            QTimer.singleShot(500, self.toggle_binder)
        if LOAD_WARNING:
            QTimer.singleShot(700, lambda: QMessageBox.warning(self, "Восстановление данных", LOAD_WARNING))
        # Only create the HWND after the palette, stylesheet and children exist.
        self._apply_native_dark_frame()

    def _apply_native_dark_frame(self):
        if not IS_WINDOWS:
            return False
        try:
            import ctypes
            dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
            apply = dwm.DwmSetWindowAttribute
            apply.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint)
            apply.restype = ctypes.c_long
            enabled = ctypes.c_int(1)
            hwnd = int(self.winId())
            for attribute in (20, 19):  # older Windows 10 builds use attribute 19
                if apply(hwnd, attribute, ctypes.byref(enabled), ctypes.sizeof(enabled)) == 0:
                    return True
        except Exception:
            log.debug("Не удалось применить тёмную системную рамку", exc_info=True)
        return False


    def _set_game_overlay_active(self, active):
        if active and self.data.get("settings", {}).get("game_overlay_enabled", True):
            s = self.data["settings"]
            self.game_overlay.set_position(s.get("overlay_position", "top_right"))
            self.game_overlay.set_screen_margin(int(s.get("overlay_screen_margin", 24)))
            self.game_overlay.setWindowOpacity(int(s.get("overlay_opacity", 92)) / 100.0)
            self.game_overlay._auto_hide_ms = int(s.get("overlay_auto_hide_ms", 5000))
            self.game_overlay.set_hotkey_text(s.get("game_overlay_hotkey", "alt+2"))
            self.game_overlay.set_active(True)
        else:
            self.game_overlay.set_active(False)

    def styles(self):
        return """
        * { font-family: "Segoe UI", "Arial", sans-serif; font-size: 13px; color: #F1F2FF; }
        QWidget { background: transparent; }
        QMainWindow, QDialog { background: #0B0D18; }
        QWidget#centerWidget { background: qradialgradient(cx:0.15,cy:0.95,radius:1.1,fx:0.15,fy:0.95,stop:0 #1A1437,stop:0.65 #101127,stop:1 #0D1021); }
        QToolTip { background: #232833; color: #F1F2FF; border: 1px solid #505968; padding: 7px 10px; font-size: 12px; }
        QFrame#header { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #19163E,stop:0.55 #15112F,stop:1 #1B1540); border-bottom: 1px solid #3B3D6A; }
        QLabel#brand { font-size: 18px; font-weight: 600; }
        QLabel#subtitle { color: #A5AFD0; font-size: 11px; }
        QFrame#statusBox { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #151A3A,stop:1 #101431); border: 1px solid #45416E; border-radius: 14px; }
        QLabel#status { color: #A5AFD0; font-size: 12px; font-weight: 400; }

        QPushButton { background: #1A2040; border: 1px solid #515A89; border-radius: 8px; padding: 8px 13px; font-weight: 600; color: #E1E5FA; }
        QPushButton:hover { background: #282D51; border-color: #727A8B; color: #F5F6FA; }
        QPushButton:pressed { background: #11152B; border-color: #A498BF; }
        QPushButton:focus { border-color: #A58BFF; }
        QPushButton:disabled { background: #14182E; border-color: #303744; color: #707A8B; }
        QPushButton#accent, QPushButton#save_btn { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #6040D0,stop:1 #3D2791); border: 1px solid #9A82E8; color: #F2ECFF; }
        QPushButton#accent:hover, QPushButton#save_btn:hover { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #7350E3,stop:1 #4A30A4); border-color: #C1A7FF; color: #F4EDFF; }
        QPushButton#accent:pressed, QPushButton#save_btn:pressed { background: #1B1927; border-color: #A58BFF; }
        QPushButton#accent:disabled, QPushButton#save_btn:disabled { background: #141830; border-color: #353B4A; color: #747C8D; }
        QPushButton#danger { background: #2B2027; color: #ECB7BD; border-color: #8B606A; }
        QPushButton#danger:hover { background: #382731; border-color: #BE8391; }
        QPushButton#ghostDanger, QPushButton#ghost { background: transparent; border: 1px solid #515A89; color: #C5CDDA; }
        QPushButton#ghost:hover { background: #22274A; border-color: #73788C; color: #F1F3F8; }
        QPushButton#ghostDanger:hover { background: #2B2027; border-color: #9D717B; color: #ECB7BD; }
        QPushButton#ghostDanger:disabled, QPushButton#ghost:disabled { color: #707A8B; border-color: #303744; }
        QPushButton#profileBtn { padding: 0; text-align: left; }

        QFrame#sidebar { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #14152F,stop:0.65 #181334,stop:1 #0E1022); border-right: 1px solid #303655; }
        QPushButton#nav { text-align: left; padding: 11px 12px; background: transparent; border: 1px solid transparent; border-left: 3px solid transparent; border-radius: 8px; color: #A5AFD0; font-weight: 500; font-size: 13px; }
        QPushButton#nav:hover { background: #242448; color: #F1F3F8; }
        QPushButton#nav:pressed { background: #292C3A; }
        QPushButton#nav[active="true"] { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #422B9A,stop:0.6 #2E226E,stop:1 #171A3F); border: 1px solid #8460DF; border-left: 3px solid #BA98FF; color: #F8F4FF; font-weight: 600; }
        QLabel#navBadge { color: #C8BCDF; font-size: 12px; font-weight: 600; background: transparent; }
        QFrame#editor { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #191536,stop:1 #101226); border-left: 1px solid #353B64; }
        QFrame#statCard { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1D2146,stop:1 #171B39); border: 1px solid #4A4D7E; border-radius: 9px; }
        QFrame#testPanel { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #1B2044,stop:1 #141932); border: 1px solid #515382; border-radius: 10px; }
        QFrame#metaCard { background: #151A35; border: 1px solid #454E78; border-radius: 8px; }
        QSplitter::handle { background: #303655; }
        QScrollArea { background: transparent; border: none; }

        QLineEdit, QTextEdit, QComboBox { background: #0F1228; border: 1px solid #515A89; border-radius: 8px; padding: 8px 12px; color: #F1F2FF; selection-background-color: #5F47AA; }
        QLineEdit:hover, QTextEdit:hover, QComboBox:hover { border-color: #6B7586; }
        QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border-color: #A58BFF; background: #151A39; }
        QLineEdit:read-only { color: #BBD5C9; }
        QComboBox QLineEdit, QComboBox QLineEdit:focus { background: transparent; border: none; padding: 0; margin: 0; }
        QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }
        QComboBox::down-arrow { image: url(ARROW_URL); width: 12px; height: 12px; }
        QComboBox QAbstractItemView { background: #1B2140; selection-background-color: #332C65; border: 1px solid #525D70; outline: none; padding: 4px; }
        QComboBox QAbstractItemView::item { min-height: 28px; padding: 6px 10px; border: none; }
        QComboBox QAbstractItemView::item:selected { background: #332C65; color: #F4EDFF; }

        QTableView { alternate-background-color: #151932; background: #0F1228; border: 1px solid #5E6798; border-radius: 10px; gridline-color: transparent; outline: 0; font-size: 13px; selection-background-color: #2B293E; }
        QTableView#cardTable { border: none; border-radius: 0; }
        QTableView::item { border: none; }
        QTableCornerButton::section { background: #151A35; border: none; }
        QHeaderView::section { background: #151A35; color: #B1BCE0; border: none; border-bottom: 1px solid #343B60; padding: 12px; font-weight: 600; }
        QScrollBar:vertical { background: #0F1228; width: 10px; margin: 4px 1px; border: none; }
        QScrollBar::handle:vertical { background: #454E5D; min-height: 30px; border-radius: 4px; }
        QScrollBar::handle:vertical:hover { background: #837697; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
        QScrollBar:horizontal { background: #0F1228; height: 10px; margin: 1px 4px; border: none; }
        QScrollBar::handle:horizontal { background: #454E5D; min-width: 30px; border-radius: 4px; }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }

        QMenu { background: #171027; border: 1px solid #69438F; padding: 6px; border-radius: 8px; }
        QMenu::item { padding: 9px 24px 9px 12px; border-radius: 5px; color: #F1F2FF; }
        QMenu::item:selected { background: #533083; }
        QMenu::separator { height: 1px; background: #39264F; margin: 5px 8px; }
        QStatusBar { background: #0B0D18; color: #A5AFD0; border-top: 1px solid #303655; }
        QStatusBar::item { border: none; }
        QPushButton#starIcon { background: transparent; border: 1px solid transparent; border-radius: 7px; padding: 0; color: #A5AFD0; font-family: "Segoe UI Symbol"; font-size: 18px; }
        QPushButton#starIcon:hover { background: #252A4A; border-color: #515A89; }
        QPushButton#starIcon:checked { color: #D6BB80; }
        QPushButton#chip { background: #22232F; border: 1px solid #645189; border-radius: 6px; padding: 5px 8px; color: #CFC5FF; font-family: Consolas, monospace; font-size: 11px; font-weight: 500; }
        QPushButton#chip:hover { background: #312D42; border-color: #AE8CF3; }
        QPushButton#collapse { background: transparent; border: none; border-top: 1px solid #3B4453; border-radius: 0; padding: 0; text-align: left; }
        QPushButton#collapse:hover { background: transparent; }
        QLineEdit[error="true"], QLineEdit[error="true"]:focus { border-color: #D497A1; }

        /* Reference edition: luminous amethyst on layered midnight glass. */
        QMainWindow, QDialog { background:#09091B; }
        QWidget#centerWidget { background:qradialgradient(cx:0.8,cy:0.05,radius:1.2,fx:0.8,fy:0.05,stop:0 #11102B,stop:0.6 #0C0B20,stop:1 #080919); }
        QFrame#header { background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #16102E,stop:0.45 #0C0D23,stop:1 #151032); border:1px solid #30205E; border-radius:12px; }
        QLabel#brand {font-size:21px;font-weight:600;}
        QFrame#statusBox {background:transparent;border:none;}
        QFrame#runIndicator {background:#0C201F;border:1px solid #183C34;border-radius:12px;}
        QFrame#runIndicator[running="false"] {background:#181329;border-color:#3E2C54;}
        QLabel#status {font-size:11px;font-weight:600;}
        QFrame#sidebar {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #100E27,stop:0.55 #10102B,stop:1 #0A0B1F);border:1px solid #352457;border-radius:13px;}
        QPushButton {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #201A47,stop:0.5 #15132F,stop:1 #111127);border:1px solid #34275F;border-radius:10px;padding:7px 12px;font-size:13px;font-weight:500;color:#EBE2FF;}
        QPushButton:hover {background:#2D1E59;border-color:#8B54EF;color:white;}
        QPushButton:pressed {background:#371D70;border-color:#B98BFF;}
        QPushButton:disabled {background:#141127;border-color:#28203F;color:#756C90;}
        QPushButton#accent, QPushButton#save_btn {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #9754FF,stop:0.2 #792FFF,stop:1 #5314E6);border:1px solid #BC8DFF;color:white;font-weight:600;}
        QPushButton#accent:hover, QPushButton#save_btn:hover {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #B57FFF,stop:0.3 #9146FF,stop:1 #6424EA);border-color:#DCBCFF;}
        QPushButton#accent:disabled, QPushButton#save_btn:disabled {background:#241640;border-color:#51347F;color:#9984B8;}
        QPushButton#danger {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #301024,stop:1 #1D0B1B);color:#FF658F;border:1px solid #9A244F;}
        QPushButton#danger:hover {background:#49132F;border-color:#F24B85;}
        QPushButton#danger:disabled {background:#1D1025;border-color:#4B2444;color:#846075;}
        QPushButton#ghost, QPushButton#ghostDanger {background:#111027;border-color:#34275F;}
        QPushButton#nav {text-align:left;padding:8px 10px;background:transparent;border:1px solid transparent;border-left:3px solid transparent;border-radius:11px;color:#BFB1E4;font-size:13px;}
        QPushButton#nav:hover {background:#221641;color:#F2E7FF;}
        QPushButton#nav[active="true"] {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8246EB,stop:0.22 #5129B0,stop:0.72 #32216C,stop:1 #4F347F);border:1px solid #BD92FF;border-left:3px solid #DEBFFF;color:white;font-weight:600;}
        QLabel#navBadge {background:#201349;border-radius:8px;color:#F3E9FF;font-size:11px;padding:3px;}
        QFrame#editor {background:qlineargradient(x1:0,y1:0,x2:0.7,y2:1,stop:0 #1C153E,stop:0.25 #100E28,stop:0.8 #0E0D23,stop:1 #15102D);border:1px solid #453078;border-radius:13px;}
        QFrame#statCard {background:qlineargradient(x1:0,y1:0,x2:0.7,y2:1,stop:0 #211345,stop:0.6 #121129,stop:1 #0E1026);border:1px solid #3B2867;border-radius:11px;}
        QLineEdit,QTextEdit,QComboBox {background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #0E0C25,stop:0.7 #11102B,stop:1 #1A1738);border:1px solid #493676;border-radius:10px;padding:7px 11px;color:#F3EBFF;font-size:13px;selection-background-color:#6B2CDB;}
        QLineEdit:hover,QTextEdit:hover,QComboBox:hover {border-color:#7955B9;}
        QLineEdit:focus,QTextEdit:focus,QComboBox:focus {border-color:#B98AFF;background:#17102E;}
        QComboBox QLineEdit,QComboBox QLineEdit:focus {background:transparent;border:none;padding:0;margin:0;}
        QFrame#selectionBar {background:qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #111026,stop:1 #17112F);border:none;border-top:1px solid #352253;border-bottom-left-radius:12px;border-bottom-right-radius:12px;}
        QPushButton#footerAction {padding:4px 11px;font-size:12px;border-radius:8px;}
        QPushButton#footerMore {padding:0;font-size:15px;border-radius:8px;}
        QPushButton#favoriteAction {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #171232,stop:1 #30216C);border:1px solid #392266;font-size:11px;}
        QPushButton#collapse {background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #15112F,stop:1 #241A51);border:1px solid #36255B;border-radius:9px;padding:0 10px;font-size:11px;}
        QPushButton#collapse:hover {background:#2B1D58;border-color:#8253C9;}
        QTableView {background:#0C0C22;alternate-background-color:#111027;}
        QHeaderView::section {background:#17122F;border-color:#392A59;}
        QScrollBar:vertical {background:#100D26;width:7px;}
        QScrollBar::handle:vertical {background:#51416E;border-radius:3px;}
        QScrollBar::handle:vertical:hover {background:#9C66DB;}
        """.replace("ARROW_URL", make_arrow_png())

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = RibbonFrame("header")
        header.setObjectName("header")
        header.setFixedHeight(66)
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 0, 16, 0)
        h.setSpacing(16)
        logo = QLabel()
        logo.setFixedSize(48, 48)
        logo.setPixmap(make_emblem(48, 0.23))
        h.addWidget(logo)
        brand_col = QVBoxLayout()
        brand_col.setSpacing(1)
        brand_col.addStretch()
        brand = QLabel('Эфир <span style="font-size:12px;color:#B89AFF;">by maxon3r</span>')
        brand.setObjectName("brand")
        brand_col.addWidget(brand)
        subtitle = QLabel("Умная автозамена текста")
        subtitle.setObjectName("subtitle")
        brand_col.addWidget(subtitle)
        brand_col.addStretch()
        h.addLayout(brand_col)
        h.addStretch()
        self.status_box = _StatusBox()
        self.status_box.setObjectName("statusBox")
        sb = QHBoxLayout(self.status_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.setSpacing(16)
        self.run_indicator = SmoothStatusPill()
        self.run_indicator.setObjectName("smoothRunIndicator")
        self.run_indicator.setFixedSize(132, 40)
        ri = QHBoxLayout(self.run_indicator)
        ri.setContentsMargins(12, 0, 10, 0)
        ri.setSpacing(8)
        self.status_dot = SmoothStatusDot()
        self.status_label = QLabel("Остановлено")
        self.status_label.setObjectName("status")
        ri.addWidget(self.status_dot)
        ri.addWidget(self.status_label)
        divider = QFrame()
        divider.setFixedSize(1, 26)
        divider.setStyleSheet("background:#7254AA;border:none;")
        self.btn_toggle = RunButton()
        self.btn_toggle.setFixedSize(140, 40)
        self.btn_toggle.clicked.connect(self.toggle_binder)
        sb.addWidget(self.run_indicator)
        sb.addWidget(divider)
        sb.addWidget(self.btn_toggle)
        h.addWidget(self.status_box)
        header_wrap = QHBoxLayout()
        header_wrap.setContentsMargins(12, 0, 12, 0)
        header_wrap.addWidget(header)
        layout.addLayout(header_wrap)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        sidebar = RibbonFrame("sidebar")
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(8, 18, 12, 16)
        side.setSpacing(8)
        nav_title = QLabel("РАБОЧЕЕ ПРОСТРАНСТВО")
        nav_title.setStyleSheet("color:#9B90C4;font-size:10px;font-weight:600;padding-left:12px;margin-bottom:8px;")
        side.addWidget(nav_title)
        self.nav_all = self._nav_button("Все замены", "\uE74C", True, badge=True)
        self.nav_fav = self._nav_button("Избранное", "\uE734", badge=True)
        self.nav_groups = self._nav_button("Группы", "\uE8B7")
        self.nav_history = self._nav_button("История", "\uE81C")
        self.nav_settings = self._nav_button("Настройки", "\uE713")
        for button in (self.nav_all, self.nav_fav, self.nav_groups, self.nav_history):
            side.addWidget(button)
        side.addStretch()
        self.profile_btn = ProfileButton()
        self.profile_btn.clicked.connect(self._open_profile_menu)
        side.addWidget(self.profile_btn)
        self.sidebar_divider = SidebarDivider()
        side.addWidget(self.sidebar_divider)
        side.addWidget(self.nav_settings)

        center = QWidget()
        center.setObjectName("centerWidget")
        c = QVBoxLayout(center)
        c.setContentsMargins(20, 20, 20, 12)
        c.setSpacing(14)
        top = QHBoxLayout()
        top.setSpacing(12)
        title_icon = QLabel()
        title_icon.setPixmap(glyph_pixmap("\uE74C", "#BC8AFF", 36, 20))
        title_icon.setFixedSize(36, 36)
        self.page_title = QLabel("Все замены")
        self.page_title.setStyleSheet("font-size:24px;font-weight:600;background:transparent;")
        top.addWidget(title_icon)
        top.addWidget(self.page_title)
        top.addStretch()
        self.btn_import = QPushButton("Импорт")
        self.btn_export = QPushButton("Экспорт")
        self.btn_add = QPushButton("Новая замена")
        self.btn_add.setObjectName("accent")
        for button, glyph, handler in ((self.btn_import,"\uE896",self.import_data),
                                       (self.btn_export,"\uE898",self.export_data),
                                       (self.btn_add,"\uE710",self.new_shortcut)):
            button.setIcon(QIcon(glyph_pixmap(glyph,"#F0E5FF",20,11)))
            button.setIconSize(QSize(18,18))
            button.setFixedHeight(38)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(handler)
            top.addWidget(button)
        c.addLayout(top)

        stats_cards = QWidget()
        stats = QHBoxLayout(stats_cards)
        stats.setContentsMargins(0, 0, 0, 0)
        stats.setSpacing(8)
        self.card_total = self._stat_card("\uE74C", "замены")
        self.card_active = self._stat_card("\uE73E", "в работе", "#66DEAA")
        self.card_favorite = self._stat_card("\uE00B", "в избранном")
        self.card_groups = self._stat_card("\uE8B7", "группы")
        for card in (self.card_total, self.card_active, self.card_favorite, self.card_groups):
            stats.addWidget(card)
        stats_actions = QWidget()
        actions_layout = QHBoxLayout(stats_actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(10)
        self.btn_auto_group = SmoothActionButton("Автораскладка", "accent")
        self.btn_auto_group.setFixedSize(148,38)
        self.btn_auto_group.setIcon(QIcon(glyph_pixmap("\uE7C1", "#DBBEFF",20,11)))
        self.btn_auto_group.setToolTip("Разложить замены текущего радиоцентра по группам. Перед изменениями появится подтверждение.")
        self.btn_auto_group.clicked.connect(self.auto_layout_groups)
        actions_layout.addWidget(self.btn_auto_group)
        self.btn_emoji = SmoothActionButton("Эмодзи…")
        self.btn_emoji.setFixedHeight(38)
        self.btn_emoji.clicked.connect(self.open_emoji_dialog)
        actions_layout.addWidget(self.btn_emoji)
        self.stats_strip = StatsStrip(stats_cards, stats_actions)
        c.addWidget(self.stats_strip)

        search = QHBoxLayout()
        search.setContentsMargins(0,0,0,0)
        search.setSpacing(10)
        self.search_input = SearchLineEdit()
        self.search_input.setToolTip("Сначала точный ключ и начало ключа, затем совпадения внутри ключа, текста и группы. Внутри каждого уровня действует выбранная сортировка.")
        self.search_input.setPlaceholderText("Поиск по ключу, тексту или группе…")
        self.search_input.setFixedHeight(38)
        self.search_input.addAction(QIcon(glyph_pixmap("\uE721","#A89ACF",20,11)),QLineEdit.LeadingPosition)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(100)
        self._search_timer.timeout.connect(self._apply_filters)
        self.search_input.textChanged.connect(lambda _text:self._search_timer.start())
        self.search_input.cleared.connect(self._on_search_cleared)
        self.group_filter = QComboBox()
        self.group_filter.addItem("Все группы","")
        self.group_filter.currentIndexChanged.connect(self._on_group_changed)
        self.sort_filter = QComboBox()
        for text,value in (("Ключ: А → Я","key_asc"),("Ключ: Я → А","key_desc"),("Группа: А → Я","group"),("Группа: Я → А","group_desc"),("Частые сверху","uses"),("Редкие сверху","uses_asc")):
            self.sort_filter.addItem(text,value)
        self.sort_filter.currentIndexChanged.connect(self._apply_sort)
        for combo in (self.group_filter,self.sort_filter):combo.setFixedSize(140,38)
        search.addWidget(self.search_input,1)
        search.addWidget(self.group_filter)
        search.addWidget(self.sort_filter)
        c.addLayout(search)

        self.model = ShortcutsModel(self)
        self.proxy = ShortcutFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.table = HoverTable()
        self.table.setHorizontalHeader(TableHeader(self.table))
        self.table.setModel(self.proxy)
        self.proxy.layoutChanged.connect(self.table.clear_row_highlight)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(0,Qt.AscendingOrder)
        self.table.setWordWrap(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(44)
        hv = self.table.horizontalHeader()
        hv.setSortIndicatorShown(True)
        hv.sortIndicatorChanged.connect(self._on_table_sort_changed)
        hv.setFixedHeight(40)
        hv.setStretchLastSection(False)
        for col,width in ((0,164),(2,108),(3,94),(4,66)):
            hv.setSectionResizeMode(col,QHeaderView.Fixed)
            self.table.setColumnWidth(col,width)
        hv.setSectionResizeMode(1,QHeaderView.Stretch)
        self.empty_label = QLabel("")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color:#A99CCA;font-size:13px;background:transparent;")
        self.empty_label.setAttribute(Qt.WA_TransparentForMouseEvents,True)
        empty = QVBoxLayout(self.table.viewport())
        empty.addWidget(self.empty_label,alignment=Qt.AlignCenter)
        self.empty_label.hide()
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        self.table.clicked.connect(self.load_selected_row)
        self.table.actionsRequested.connect(self._on_row_actions)
        self.model.dataChanged.connect(self._on_model_data_changed)
        self.toggle_delegate = ToggleDelegate(self.table)
        self.table.setItemDelegate(self.toggle_delegate)
        self.table_card = TableCard(self.table)
        self.table_card.has_footer = True
        footer = QFrame()
        footer.setObjectName("selectionBar")
        footer.setFixedHeight(50)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14,6,12,6)
        fl.setSpacing(8)
        self.selection_label = QLabel("Выбрано: 0 из 0")
        self.selection_label.setStyleSheet("font-size:11px;color:#C0B3E5;background:transparent;")
        fl.addWidget(self.selection_label)
        fl.addStretch()
        self.btn_bulk_fav = QPushButton("В избранное")
        self.btn_bulk_edit = QPushButton("Редактировать")
        self.btn_bulk_delete = QPushButton("Удалить")
        for button,glyph,handler in ((self.btn_bulk_fav,"\uE734",self._selection_favorite),
                                     (self.btn_bulk_edit,"\uE70F",self._selection_edit),
                                     (self.btn_bulk_delete,"\uE74D",self._selection_delete)):
            button.setObjectName("footerAction")
            button.setIcon(QIcon(glyph_pixmap(glyph,"#DDD0FF",19,11)))
            button.setFixedHeight(34)
            button.clicked.connect(handler)
            fl.addWidget(button)
        self.btn_bulk_more = QPushButton("•••")
        self.btn_bulk_more.setObjectName("footerMore")
        self.btn_bulk_more.setAccessibleName("Дополнительные действия с выбранными заменами")
        self.btn_bulk_more.setToolTip("Дополнительные действия")
        self.btn_bulk_more.setFixedSize(32,34)
        self.btn_bulk_more.clicked.connect(self._selection_more)
        fl.addWidget(self.btn_bulk_more)
        self.table_card.layout().addWidget(footer)
        self.table.selectionModel().selectionChanged.connect(self._update_selection_bar)
        self.proxy.modelReset.connect(self._update_selection_bar)
        self.proxy.rowsInserted.connect(self._update_selection_bar)
        self.proxy.rowsRemoved.connect(self._update_selection_bar)
        c.addWidget(self.table_card,1)
        self.loading_overlay = LoadingOverlay(self.table)
        self.loading_overlay.hide()
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._deferred_filter_with_overlay)

        editor = QFrame()
        editor.setObjectName("editor")
        e = QVBoxLayout(editor)
        e.setContentsMargins(16,14,16,14)
        e.setSpacing(10)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        pencil = QLabel()
        pencil.setPixmap(glyph_pixmap("\uE70F","#AF78FF",26,15))
        pencil.setFixedSize(26,26)
        title_row.addWidget(pencil)
        self.e_title = QLabel("Редактор замены")
        self.e_title.setStyleSheet("font-size:14px;font-weight:600;background:transparent;")
        self.favorite_toggle = StarButton()
        self.favorite_toggle.toggled.connect(self._on_editor_toggle_changed)
        self.favorite_toggle.toggled.connect(self._sync_favorite_action)
        title_row.addWidget(self.e_title)
        title_row.addStretch()
        title_row.addWidget(self.favorite_toggle)
        e.addLayout(title_row)
        self.editor_stack = QStackedWidget()
        self.hint_page = QWidget()
        hint = QVBoxLayout(self.hint_page)
        hint.setContentsMargins(0,0,0,0)
        self.editor_hint = QLabel("Выберите замену в таблице\nили нажмите «Новая замена».")
        self.editor_hint.setWordWrap(True)
        self.editor_hint.setStyleSheet("color:#B5A6D6;font-size:13px;padding:20px 0;background:transparent;")
        hint.addWidget(self.editor_hint)
        hint.addStretch()
        self.editor_stack.addWidget(self.hint_page)
        self.form_page = QWidget()
        fp = QVBoxLayout(self.form_page)
        fp.setContentsMargins(0,0,0,0)
        fp.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_body = QWidget()
        form = QVBoxLayout(scroll_body)
        form.setContentsMargins(0,0,0,0)
        form.setSpacing(6)
        scroll.setWidget(scroll_body)
        form.addWidget(self._field_label("Ключ замены"))
        self.input_key = QLineEdit()
        self.input_key.setFixedHeight(36)
        self.input_key.setPlaceholderText("например: пр")
        self.input_key.addAction(QIcon(glyph_pixmap("\uE765","#B6A5DE",18,11)),QLineEdit.TrailingPosition)
        self.input_key.textChanged.connect(self.update_editor_state)
        self.input_key.textChanged.connect(self._update_key_count)
        form.addWidget(self.input_key)
        self.key_count = QLabel("0 / 50")
        self.key_count.setAlignment(Qt.AlignRight)
        self.key_count.setStyleSheet("color:#AD9DCB;font-size:10px;background:transparent;")
        self.key_count.setToolTip("Рекомендуемая длина ключа — до 50 символов; существующие ключи не обрезаются.")
        form.addWidget(self.key_count)
        form.addWidget(self._field_label("Текст замены"))
        self.input_text = QTextEdit()
        self.input_text.setFixedHeight(112)
        self.input_text.setPlaceholderText("Введите текст замены…")
        self.input_text.setTabChangesFocus(True)
        self.input_text.setViewportMargins(0,0,0,18)
        self.char_count = QLabel("0 / 1000")
        self.char_count.setStyleSheet("color:#AD9DCB;font-size:10px;background:transparent;")
        self.char_count.setAttribute(Qt.WA_TransparentForMouseEvents,True)
        count = QVBoxLayout(self.input_text.viewport())
        count.setContentsMargins(0,0,3,0)
        count.addStretch()
        count.addWidget(self.char_count,0,Qt.AlignRight)
        self.input_text.textChanged.connect(self.update_char_count)
        self.input_text.textChanged.connect(self.update_editor_state)
        form.addWidget(self.input_text)
        form.addWidget(self._field_label("Группа"))
        self.combo_group = QComboBox()
        self.combo_group.setEditable(True)
        self.combo_group.setFixedHeight(36)
        self.combo_group.addItems(["Общие","Шаблоны","Транспорт","Бизнес"])
        self.combo_group.currentTextChanged.connect(self.update_editor_state)
        self.combo_group.lineEdit().addAction(QIcon(glyph_pixmap("\uE8B7","#D2BEFF",22,12)),QLineEdit.LeadingPosition)
        form.addWidget(self.combo_group)
        status = QHBoxLayout()
        status.setContentsMargins(0,6,0,2)
        status.addWidget(self._field_label("Статус"))
        status.addStretch()
        self.enabled_toggle = ToggleSwitch("Активна","Неактивна")
        self.enabled_toggle.toggled.connect(self._on_editor_toggle_changed)
        status.addWidget(self.enabled_toggle)
        form.addLayout(status)
        self.favorite_action = QPushButton("Добавить в избранное")
        self.favorite_action.setObjectName("favoriteAction")
        self.favorite_action.setIcon(QIcon(glyph_pixmap("\uE734","#DDD0FF",21,12)))
        self.favorite_action.setFixedHeight(36)
        self.favorite_action.clicked.connect(lambda:self.favorite_toggle.toggle())
        form.addWidget(self.favorite_action)
        self.advanced = CollapsibleSection("Дополнительные параметры")
        self.advanced.header.setMinimumHeight(38)
        adv = self.advanced.body_lay
        adv.addWidget(self._field_label("Горячие клавиши"))
        self.input_hotkey = HotkeyEdit()
        self.input_hotkey.setMinimumHeight(36)
        self.input_hotkey.changedByUser.connect(self.update_editor_state)
        adv.addWidget(self.input_hotkey)
        self.meta_profile,self.meta_uses = QLabel("—"),QLabel("—")
        meta = QGridLayout()
        for row,(caption,value) in enumerate((("Радиоцентр",self.meta_profile),("Использована раз",self.meta_uses))):
            meta.addWidget(QLabel(caption),row,0)
            value.setAlignment(Qt.AlignRight)
            meta.addWidget(value,row,1)
        adv.addLayout(meta)
        chips = QGridLayout()
        for i,token in enumerate(("{date}","{time}","{datetime}","{clipboard}","[курсор]")):
            chip = QPushButton(token)
            chip.setObjectName("chip")
            chip.clicked.connect(lambda _=False,t=token:self._insert_variable(t))
            chips.addWidget(chip,i//3,i%3)
        adv.addLayout(chips)
        self.test_input = QLineEdit()
        self.test_input.setPlaceholderText("Быстрый тест: введите ключ…")
        self.test_input.textChanged.connect(self._update_test_result)
        self.test_output = QLineEdit()
        self.test_output.setReadOnly(True)
        self.test_output.setPlaceholderText("Результат автозамены")
        self._test_plain = ""
        self.btn_copy = QPushButton("Копировать результат")
        self.btn_copy.clicked.connect(self._copy_test_result)
        self.btn_clear = QPushButton("Очистить редактор")
        self.btn_clear.clicked.connect(self._discard_editor)
        for widget in (self.test_input,self.test_output,self.btn_copy,self.btn_clear):adv.addWidget(widget)
        form.addWidget(self.advanced)
        form.addStretch()
        fp.addWidget(scroll,1)
        save_row = QHBoxLayout()
        save_row.setSpacing(12)
        self.btn_save = QPushButton("Сохранить")
        self.btn_save.setObjectName("save_btn")
        self.btn_delete = QPushButton("Удалить")
        self.btn_delete.setObjectName("danger")
        for button,glyph,handler in ((self.btn_save,"\uE74E",self.save_shortcut),(self.btn_delete,"\uE74D",self.delete_shortcut)):
            button.setFixedHeight(40)
            button.setIcon(QIcon(glyph_pixmap(glyph,"#F5E9FF" if button is self.btn_save else "#F05A91",21,12)))
            button.setEnabled(False)
            button.clicked.connect(handler)
            save_row.addWidget(button,1)
        fp.addLayout(save_row)
        self.editor_stack.addWidget(self.form_page)
        e.addWidget(self.editor_stack,1)
        editor_shell = QWidget()
        editor_shell.setMinimumWidth(324)
        editor_shell.setMaximumWidth(360)
        shell = QVBoxLayout(editor_shell)
        shell.setContentsMargins(2,8,12,8)
        shell.addWidget(editor)
        workspace = QSplitter(Qt.Horizontal)
        workspace.setChildrenCollapsible(False)
        workspace.setHandleWidth(0)
        workspace.addWidget(center)
        workspace.addWidget(editor_shell)
        workspace.setSizes([668,324])
        workspace.setStretchFactor(0,1)
        workspace.setStretchFactor(1,0)
        self.main_stack = QStackedWidget()
        self.main_stack.setObjectName("centerWidget")
        self.main_stack.setAttribute(Qt.WA_StyledBackground,True)
        self.groups_page,self.history_page,self.settings_page = GroupsPage(self),HistoryPage(self),SettingsPage(self)
        self.settings_page.saved.connect(self._on_settings_saved)
        self.PAGE_INDEX = {"all":0,"favorites":0,"groups":1,"history":2,"settings":3}
        for page in (workspace,self.groups_page,self.history_page,self.settings_page):self.main_stack.addWidget(page)
        # Слева та же высота отступа и скругления, что у оболочки редактора справа.
        sidebar_shell = QWidget()
        sidebar_shell.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar_shell)
        sidebar_layout.setContentsMargins(12, 8, 0, 8)
        sidebar_layout.setSpacing(0)
        sidebar_layout.addWidget(sidebar)
        body.addWidget(sidebar_shell)
        body.addWidget(self.main_stack,1)
        layout.addLayout(body,1)
        for button,name in ((self.nav_all,"all"),(self.nav_fav,"favorites"),(self.nav_groups,"groups"),(self.nav_history,"history"),(self.nav_settings,"settings")):
            button.clicked.connect(lambda _=False,v=name:self.set_view(v))
        self.current_page = "all"
        self.update_editor_state()
        self._update_selection_bar()


    def _update_key_count(self,*_):
        if hasattr(self,"key_count"):
            self.key_count.setText(f"{len(self.input_key.text())} / 50")

    def _sync_favorite_action(self,*_):
        if hasattr(self,"favorite_action"):
            on=self.favorite_toggle.isChecked()
            self.favorite_action.setText("Убрать из избранного" if on else "Добавить в избранное")
            self.favorite_action.setProperty("active",on)
            self.favorite_action.style().unpolish(self.favorite_action)
            self.favorite_action.style().polish(self.favorite_action)

    def _update_selection_bar(self,*_):
        if not hasattr(self,"selection_label") or self.table.selectionModel() is None:
            return
        count=len(self.table.selectionModel().selectedRows())
        keys = [self.model.row_data(self.proxy.mapToSource(index).row())[0]
                for index in self.table.selectionModel().selectedRows()
                if self.proxy.mapToSource(index).isValid()]
        all_favorite = bool(keys) and all(self.data["shortcuts"][key].get("favorite", False) for key in keys)
        self.btn_bulk_fav.setText("Убрать из избранного" if all_favorite else "В избранное")
        self.btn_bulk_fav.setToolTip("Убрать выбранные из избранного" if all_favorite else "Добавить выбранные в избранное")
        self.selection_label.setText(f"Выбрано: {count} из {self.proxy.rowCount()}")
        for button in (self.btn_bulk_fav,self.btn_bulk_edit,self.btn_bulk_delete,self.btn_bulk_more):
            button.setEnabled(count>0)
        self.table.horizontalHeader().viewport().update()

    def _selection_favorite(self):
        keys = [self.model.row_data(self.proxy.mapToSource(index).row())[0]
                for index in self.table.selectionModel().selectedRows()
                if self.proxy.mapToSource(index).isValid()]
        if not keys:
            return
        target = not all(self.data["shortcuts"][key].get("favorite", False) for key in keys)
        self._context_set_flag(keys, "favorite", target)

    def _selection_edit(self):
        rows=self.table.selectionModel().selectedRows()
        if rows:
            self.load_selected_row(rows[0]);self.input_text.setFocus()

    def _selection_delete(self):
        rows=self.table.selectionModel().selectedRows()
        if rows:self.mass_delete(rows)

    def _selection_more(self):
        rows = sorted(self.table.selectionModel().selectedRows(), key=lambda i: i.row())
        keys = [self.model.row_data(self.proxy.mapToSource(index).row())[0]
                for index in rows if self.proxy.mapToSource(index).isValid()]
        if keys:
            # Footer actions belong to the current selection, even if its rows
            # are scrolled out of view. Anchor the popup to the button itself.
            anchor = self.btn_bulk_more.mapToGlobal(QPoint(0, self.btn_bulk_more.height()))
            self._exec_shortcut_context_menu(keys, anchor)

    def _svg_icon(self, char, color="#B0BBCB"):
        return glyph_pixmap(char, color, 24, 12)

    def _nav_button(self, text, icon_char, active=False, badge=False):
        btn = CountNavButton(f"  {text}") if badge else QPushButton(f"  {text}")
        btn.setObjectName("nav")
        btn.setProperty("active", active)
        btn.setMinimumHeight(46)
        btn.setIcon(QIcon(self._svg_icon(icon_char, "#A58BFF" if active else "#B0BBCB")))
        btn.setIconSize(QSize(22, 22))
        btn._glyph = icon_char
        btn.setCursor(Qt.PointingHandCursor)
        btn.badge = None
        if badge:
            lay = QHBoxLayout(btn)
            lay.setContentsMargins(0, 0, 14, 0)
            lay.addStretch()
            lbl = QLabel("")
            lbl.setObjectName("navBadge")
            lbl.setFixedHeight(24)
            lbl.setMinimumWidth(24)
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            lay.addWidget(lbl)
            btn.badge = lbl
        return btn

    def _field_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("color: #CDD5E2; font-size: 13px; font-weight: 600; margin-top: 0px; background: transparent;")
        return label

    def _stat_card(self,icon_char,caption,tone="#B783FF"):
        frame=QFrame();frame.setObjectName("statCard");frame.setFixedHeight(46)
        frame.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        row=QHBoxLayout(frame);row.setContentsMargins(12,6,12,6);row.setSpacing(10)
        icon=QLabel();icon.setFixedSize(26,26);icon.setPixmap(glyph_pixmap(icon_char,tone,26,15));row.addWidget(icon)
        col=QVBoxLayout();col.setSpacing(0)
        value=QLabel("0");value.setStyleSheet("font-size:13px;font-weight:600;background:transparent;")
        cap=QLabel(caption);cap.setStyleSheet("font-size:10px;color:#BEB0DA;background:transparent;")
        col.addWidget(value);col.addWidget(cap);row.addLayout(col)
        frame.value_label,frame.caption_label=value,cap
        return frame

    def _build_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(make_emblem(64)))
        menu = QMenu(self)
        
        open_action = QAction("Открыть Эфир", self)
        open_action.triggered.connect(self.restore_window)
        
        self.tray_toggle_action = QAction("Запустить", self)
        self.tray_toggle_action.triggered.connect(self.toggle_binder)
        
        restart_action = QAction("Перезапустить", self)
        restart_action.triggered.connect(self.restart_app)
        
        quit_action = QAction("Полностью выйти", self)
        quit_action.triggered.connect(self.quit_app)
        
        menu.addAction(open_action)
        menu.addAction(self.tray_toggle_action)
        menu.addSeparator()
        menu.addAction(restart_action)
        menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(lambda reason: self.restore_window() if reason == QSystemTrayIcon.DoubleClick else None)
        self.tray_icon.show()

    def restore_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _import_is_busy(self):
        worker = getattr(self, "_import_worker", None)
        if worker is not None and worker.isRunning():
            QMessageBox.information(self, "Импорт ещё выполняется",
                                    "Дождитесь окончания чтения файла перед выходом или перезапуском.")
            return True
        return False

    def _confirm_editor_changes(self):
        if not getattr(self, "_is_editor_dirty", False):
            return True
        answer = QMessageBox.question(
            self, "Несохранённая замена", "Сохранить изменения в редакторе замены?",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.save_shortcut()
            return not self._is_editor_dirty
        return answer == QMessageBox.No

    def _confirm_settings_changes(self):
        if not self.settings_page.is_dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Несохранённые настройки")
        box.setText("В настройках есть несохранённые изменения.")
        box.setInformativeText("Сохранить их перед продолжением?")
        save = box.addButton("Сохранить", QMessageBox.AcceptRole)
        discard = box.addButton("Не сохранять", QMessageBox.DestructiveRole)
        box.addButton("Остаться", QMessageBox.RejectRole)
        box.setDefaultButton(save)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is save:
            self.settings_page.save()
            return not self.settings_page.is_dirty
        if clicked is discard:
            self.settings_page.revert()
            return True
        return False

    def _can_exit(self):
        if getattr(self, "_exit_checking", False) or self._import_is_busy():
            return False
        self._exit_checking = True
        try:
            if not self._confirm_editor_changes() or not self._confirm_settings_changes():
                return False
            # On failure leave the engine, window and Retry action alive.
            return self._flush_save()
        finally:
            self._exit_checking = False

    def _finish_shutdown(self):
        self._shutdown_complete = True
        self._allow_close = True
        self._save_timer.stop()
        self.binder.shutdown()
        self.game_overlay.hide()
        self.tray_icon.hide()

    def closeEvent(self, event):
        if getattr(self, "_shutdown_complete", False):
            event.accept()
            return
        if self._import_is_busy():
            event.ignore()
            return
        if not self._allow_close and self.data["settings"].get("minimize_to_tray", True) and QSystemTrayIcon.isSystemTrayAvailable():
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(APP_DISPLAY_NAME, "Окно свёрнуто в системный трей.", QSystemTrayIcon.Information, 1800)
            return
        if not self._can_exit():
            event.ignore()
            return
        self._finish_shutdown()
        event.accept()

    def restart_app(self):
        if not self._can_exit():
            return
        from PyQt5.QtCore import QProcess
        server = getattr(self, "instance_server", None)
        if server is not None:
            server.close()
        try:
            program, args = launch_command()
            result = QProcess.startDetached(program, args + sys.argv[1:])
            started = result[0] if isinstance(result, tuple) else result
        except Exception:
            log.exception("Не удалось запустить новую копию")
            started = False
        if not started:
            if server is not None and not server.listen(INSTANCE_NAME):
                log.error("Не удалось восстановить сервер единственного экземпляра: %s", server.errorString())
            QMessageBox.critical(self, "Ошибка перезапуска", "Не удалось запустить новую копию Эфира. Текущее окно остаётся открытым.")
            return
        self._finish_shutdown()
        QApplication.quit()

    def quit_app(self):
        if not self._can_exit():
            return
        self._finish_shutdown()
        QApplication.quit()

    def set_view(self, view):
        if (self.current_page == "settings" and view != "settings"
                and not self._confirm_settings_changes()):
            return

        if view in ("all", "favorites"):
            if view != self.current_view:
                self.current_view = view
                self.page_title.setText("Избранное" if view == "favorites" else "Все замены")
                self.loading_overlay.setText("Загрузка замен…")
                self.loading_overlay.setGeometry(self.table.rect())
                self.loading_overlay.show()
                self.loading_overlay.raise_()
                self._filter_timer.start(16)
        elif view == "groups":
            self.groups_page.reload()
        elif view == "history":
            self.history_page.reload()
        elif view == "settings":
            self.settings_page.refresh_dynamic()

        self.current_page = view
        self.main_stack.setCurrentIndex(self.PAGE_INDEX[view])

        buttons = {"all": self.nav_all, "favorites": self.nav_fav, "groups": self.nav_groups,
                   "history": self.nav_history, "settings": self.nav_settings}
        for name, btn in buttons.items():
            active = (name == view)
            btn.setProperty("active", active)
            btn.setIcon(QIcon(self._svg_icon(btn._glyph, "#A58BFF" if active else "#B0BBCB")))
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _deferred_filter_with_overlay(self):
        try:
            self._apply_filters(force=False)
        finally:
            self.loading_overlay.hide()

    def refresh_profiles(self):
        self._update_radio_badge(self.data["settings"].get("active_profile", DEFAULT_PROFILE))

    def _update_radio_badge(self, name):
        self.profile_btn.set_profile(name, profile_color(name))
        self.profile_btn.setToolTip(f"Активный радиоцентр: {name}\nНажмите, чтобы сменить или удалить")

    def _on_profile_popup_closed(self):
        self._profile_popup_closed_at = time.monotonic()
        self.profile_btn.set_open(False)

    def _open_profile_menu(self):
        # Клик по кнопке, пока окно открыто, закрывает его (Qt.Popup гасится по нажатию снаружи),
        # а тот же клик доходит до кнопки — поэтому сразу после закрытия заново не открываем.
        if time.monotonic() - getattr(self, "_profile_popup_closed_at", 0.0) < 0.3:
            return
        s = self.data["settings"]
        profiles = list(s.get("profiles", RADIO_PROFILES))
        active = s.get("active_profile", DEFAULT_PROFILE)
        counts = {p: 0 for p in profiles}
        for v in self.data.get("shortcuts", {}).values():
            p = v.get("profile")
            if p in counts:
                counts[p] += 1

        popup = ProfilePopup(profiles, active, counts, self.profile_btn.width(), self)
        popup.chosen.connect(self._on_profile_changed)
        popup.deleteRequested.connect(self.delete_profile)
        popup.createRequested.connect(self._create_profile)
        popup.closed.connect(self._on_profile_popup_closed)
        self.profile_btn.set_open(True)
        popup.popup_above(self.profile_btn)

    def delete_profile(self, name):
        if not self._confirm_editor_changes():
            return
        s = self.data["settings"]
        profiles = s.setdefault("profiles", list(RADIO_PROFILES))
        if name not in profiles:
            return
        if len(profiles) <= 1:
            QMessageBox.information(self, "Удаление", "Нельзя удалить единственный радиоцентр.")
            return
        keys = [k for k, v in self.data.get("shortcuts", {}).items() if v.get("profile") == name]
        text = f"Удалить радиоцентр «{name}»?"
        if keys:
            text += (f"\n\nВместе с ним будут удалены все его замены ({len(keys)}). "
                     "Перед удалением будет создана резервная копия.")
        if QMessageBox.question(self, "Удалить радиоцентр", text,
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return

        candidate = dict(self.data)
        s = copy.deepcopy(self.data["settings"])
        candidate["settings"] = s
        profiles = s["profiles"]
        shortcuts = self._copy_shortcuts()
        for k in keys:
            del shortcuts[k]
        profiles.remove(name)
        was_active = s.get("active_profile") == name
        if was_active:
            s["active_profile"] = profiles[0]
        candidate["shortcuts"] = shortcuts
        if not self._commit_data(candidate):
            return
        self.clear_editor()
        if was_active and hasattr(self, "test_input"):
            self.test_input.clear()
        self.refresh_table()
        self.refresh_profiles()
        self.settings_page.refresh_dynamic()
        self.show_message(f"Радиоцентр {name} удалён")

    def _create_profile(self):
        name, ok = QInputDialog.getText(self, "Новый радиоцентр", "Введите название (например, LS2):")
        if not (ok and name.strip()):
            return
        name = name.strip().upper()
        try:
            validate_profile_name(name)
        except ValueError as exc:
            QMessageBox.warning(self, "Название радиоцентра", str(exc))
            return
        if name in self.data["settings"]["profiles"]:
            QMessageBox.warning(self, "Ошибка", "Радиоцентр с таким именем уже существует.")
            return
        if not self._confirm_editor_changes():
            return
        self.data["settings"]["profiles"].append(name)
        self._on_profile_changed(name, confirm=False)
        self.refresh_profiles()

    def _on_profile_changed(self, profile_name, confirm=True):
        if profile_name and profile_name != self.data["settings"].get("active_profile"):
            if confirm and not self._confirm_editor_changes():
                return
            self.data["settings"]["active_profile"] = profile_name
            self.persist()
            self.clear_editor()
            if hasattr(self, "test_input"):
                self.test_input.clear()
            self.refresh_table()
            self._update_radio_badge(profile_name)
            self.show_message(f"Активный радиоцентр: {profile_name}")

    def refresh_table(self, *_):
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        shortcuts = [(k, v) for k, v in self.data.get("shortcuts", {}).items()
                     if v.get("profile", DEFAULT_PROFILE) == active]

        groups = sorted({info.get("group", "Общие") for _, info in shortcuts}, key=str.casefold)
        selected_group = self.group_filter.currentData()
        existing = [self.group_filter.itemData(i) for i in range(self.group_filter.count())]
        wanted = [""] + groups
        if existing != wanted:
            self.group_filter.blockSignals(True)
            self.group_filter.clear()
            self.group_filter.addItem("Все группы", "")
            for g in groups:
                self.group_filter.addItem(g, g)
            idx = self.group_filter.findData(selected_group)
            self.group_filter.setCurrentIndex(max(0, idx))
            self.group_filter.blockSignals(False)

        default_groups = ["Общие", "Транспорт", "Недвижимость", "Бизнес", "Работа",
                          "Услуги", "Медицина", "Полиция", "СМИ", "Шаблоны"]
        all_groups = sorted(set(default_groups) | set(groups), key=str.casefold)
        current_editor_group = self.combo_group.currentText()

        self.combo_group.blockSignals(True)
        self.combo_group.clear()
        self.combo_group.addItems(all_groups)
        if current_editor_group:
            self.combo_group.setCurrentText(current_editor_group)
        self.combo_group.blockSignals(False)

        self.model.set_rows(shortcuts)
        self._apply_filters(force=False)
        if hasattr(self, "groups_page"):
            self.groups_page.reload()

    def _on_search_cleared(self):
        # clear() already emitted textChanged; cancel its debounce and show the
        # restored list now, instead of leaving stale results for another 250 ms.
        self._search_timer.stop()
        self._apply_filters()

    def _apply_filters(self, force=True):
        previous_query = self.proxy._search
        view = getattr(self, "current_view", "all")
        changed = self.proxy.set_all(
            self.data["settings"].get("active_profile", DEFAULT_PROFILE),
            self.search_input.text(), self.group_filter.currentData(), view == "favorites")
        if force and not changed:
            self.proxy.invalidateFilter()
        if previous_query != self.proxy._search:
            self.table.scrollToTop()
        self._update_counts()

    def _update_empty_state(self):
        if not hasattr(self, "empty_label"):
            return
        if self.proxy.rowCount() > 0:
            self.empty_label.hide()
            return
        if getattr(self, "current_view", "all") == "favorites" and not self.search_input.text().strip():
            text = "Нет избранных замен\nНажмите ☆ в заголовке редактора, чтобы добавить"
        elif self.search_input.text().strip() or self.group_filter.currentData():
            text = "Ничего не найдено"
        else:
            text = "Список пуст"
        self.empty_label.setText(text)
        self.empty_label.show()

    def _update_counts(self):
        self._update_empty_state()
        rows = self.model._rows
        total = len(rows)
        enabled = sum(1 for r in rows if r["info"].get("enabled", True))
        fav = sum(1 for r in rows if r["info"].get("favorite", False))
        groups_count = len({r["group"] for r in rows})

        def fmt(n):
            return f"{n:,}".replace(",", " ")

        self.nav_fav.setText("  Избранное")
        for button, count, label in ((self.nav_all, total, "Все замены"),
                                     (self.nav_fav, fav, "Избранное")):
            if getattr(button, "badge", None) is not None:
                count_text = fmt(count)
                button.badge.setText(count_text)
                button.badge.setFixedWidth(max(24, button.badge.fontMetrics().horizontalAdvance(count_text) + 16))
                button.badge.setToolTip(f"{label}: {count_text}")
                button.setToolTip(f"{label} — {count_text}")
                button.layout().activate()
                button.update()

        self.card_total.value_label.setText(fmt(total))
        self.card_total.caption_label.setText(plural(total, "замена", "замены", "замен"))
        self.card_active.value_label.setText(fmt(enabled))
        self.card_active.setToolTip(f"Включено: {fmt(enabled)} · выключено: {fmt(total - enabled)}")
        self.card_favorite.value_label.setText(fmt(fav))
        self.card_favorite.caption_label.setText("в избранном")
        self.card_groups.value_label.setText(fmt(groups_count))
        self.card_groups.caption_label.setText(plural(groups_count, "группа", "группы", "групп"))
        self.stats_strip.updateGeometry()
        self.stats_strip.reflow()
        self._update_selection_bar()

    def _on_group_changed(self, *_):
        self.proxy.set_group(self.group_filter.currentData())
        self._update_counts()

    def _apply_sort(self, *_):
        modes = {"key_asc": (0, Qt.AscendingOrder), "key_desc": (0, Qt.DescendingOrder),
                 "group": (2, Qt.AscendingOrder), "group_desc": (2, Qt.DescendingOrder),
                 "uses": (1, Qt.DescendingOrder), "uses_asc": (1, Qt.AscendingOrder)}
        column, order = modes.get(self.sort_filter.currentData(), (0, Qt.AscendingOrder))
        header = self.table.horizontalHeader()
        unchanged = (header.sortIndicatorSection() == column and
                     header.sortIndicatorOrder() == order)
        self.table.sortByColumn(column, order)
        # A changed indicator emits the connected signal synchronously. Only
        # refresh explicitly when Qt has no change (and emits no signal).
        if unchanged:
            self._on_table_sort_changed(column, order)

    def _on_table_sort_changed(self, column, order):
        modes = {(0, Qt.AscendingOrder): "key_asc", (0, Qt.DescendingOrder): "key_desc",
                 (2, Qt.AscendingOrder): "group", (2, Qt.DescendingOrder): "group_desc",
                 (1, Qt.DescendingOrder): "uses", (1, Qt.AscendingOrder): "uses_asc"}
        index = self.sort_filter.findData(modes.get((column, order), "key_asc"))
        if index >= 0:
            blocked = self.sort_filter.blockSignals(True)
            self.sort_filter.setCurrentIndex(index)
            self.sort_filter.blockSignals(blocked)
        label = {0: "ключу", 1: "частоте использования", 2: "группе"}.get(column, "ключу")
        direction = "по возрастанию" if order == Qt.AscendingOrder else "по убыванию"
        self.table.horizontalHeader().setToolTip(f"Сортировка по {label}, {direction}. Нажмите заголовок, чтобы сменить направление.")
        self.table.horizontalHeader().viewport().update()

    def _on_model_data_changed(self, index):
        if not index.isValid() or index.column() != 3:
            return
        self.persist()
        self._update_counts()

    def _on_editor_toggle_changed(self, checked):
        if not self.editing_key or self.editing_key not in self.data["shortcuts"]:
            self.update_editor_state()
            return
        info = self.data["shortcuts"][self.editing_key]
        info["enabled"] = self.enabled_toggle.isChecked()
        info["favorite"] = self.favorite_toggle.isChecked()
        self.persist()
        if self.sender() is self.enabled_toggle:
            self.binder.configure(self.data)
        row = self.model.row_for_key(self.editing_key)
        if row >= 0:
            self.model.dataChanged.emit(self.model.index(row, 0), self.model.index(row, 3))
        self._apply_filters()
        if self.sender() is self.favorite_toggle:
            self.show_message("Добавлено в избранное" if info["favorite"] else "Убрано из избранного")

    def load_selected_row(self, proxy_index):
        if not proxy_index.isValid():
            return
        self.table.selectRow(proxy_index.row()) 
        source_index = self.proxy.mapToSource(proxy_index)
        if not source_index.isValid():
            return
        key, info = self.model.row_data(source_index.row())
        if key != self.editing_key and not self._confirm_editor_changes():
            self.table.clearSelection()
            return

        self.editing_key = key
        _, bare_key = split_ckey(key)
        
        self.input_key.blockSignals(True)
        self.input_key.setText(bare_key)
        self.input_key.blockSignals(False)
        
        self.input_text.blockSignals(True)
        self.input_text.setPlainText(info.get("text", ""))
        self.input_text.blockSignals(False)
        self.update_char_count()
        
        self.combo_group.blockSignals(True)
        self.combo_group.setCurrentText(info.get("group", "Общие"))
        self.combo_group.blockSignals(False)
        
        self.enabled_toggle.setChecked(info.get("enabled", True))
        self.favorite_toggle.setChecked(info.get("favorite", False))
        
        self.input_hotkey.setText(str(info.get("hotkey", "") or ""))
        self.btn_delete.setEnabled(True)
        self._is_editor_dirty = False
        self.update_editor_state()

    def _update_test_result(self, *args):
        key = self.test_input.text().strip()
        if not key:
            self.test_output.clear()
            self.test_output.setStyleSheet(
                "QLineEdit { background: #0F1228; color: #2ED573; border: 1px solid #515A89; "
                "padding: 8px 12px; border-radius: 8px; }"
            )
            self._test_plain = ""
            return
            
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        ckey = make_ckey(active, key)
        
        if ckey in self.data["shortcuts"]:
            info = self.data["shortcuts"][ckey]
            text = str(info.get("text", ""))
            
            now = datetime.datetime.now()
            text = text.replace("{date}", now.strftime("%d.%m.%Y"))
            text = text.replace("{time}", now.strftime("%H:%M"))
            text = text.replace("{datetime}", now.strftime("%d.%m.%Y %H:%M:%S"))
            text = text.replace("{clipboard}", "[Буфер обмена]")
            
            import re
            self._test_plain = re.sub(r"\[(?:курсор|cursor)\]", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\[(?:курсор|cursor)\]", "|", text, flags=re.IGNORECASE)
            text_preview = text.replace("\n", " ↵ ")
            self.test_output.setText(text_preview)
            
            if not info.get("enabled", True):
                self.test_output.setText(f"[ОТКЛЮЧЕНА] {text_preview}")
                self.test_output.setStyleSheet("QLineEdit { background: #0F1228; color: #D497A1; border: 1px solid #72545E; padding: 8px 12px; border-radius: 6px; }")
            else:
                self.test_output.setStyleSheet("QLineEdit { background: #0F1228; color: #2ED573; border: 1px solid #515A89; padding: 8px 12px; border-radius: 6px; }")
        else:
            self._test_plain = ""
            self.test_output.setText("Ключ не найден в текущем радиоцентре")
            self.test_output.setStyleSheet("QLineEdit { background: #0F1228; color: #D497A1; border: 1px solid #72545E; padding: 8px 12px; border-radius: 6px; }")

    def new_shortcut(self):
        if not self._confirm_editor_changes():
            return
        self.clear_editor()
        if hasattr(self, "editor_stack"):
            self.editor_stack.setCurrentIndex(1)
        self.favorite_toggle.show()
        self.input_key.setFocus()

    def _discard_editor(self):
        if self._confirm_editor_changes():
            self.clear_editor()

    def clear_editor(self):
        self.editing_key = None
        
        self.input_key.blockSignals(True)
        self.input_key.clear()
        self.input_key.blockSignals(False)
        
        self.input_text.blockSignals(True)
        self.input_text.clear()
        self.input_text.blockSignals(False)
        
        self.combo_group.blockSignals(True)
        self.combo_group.setCurrentText("Общие")
        self.combo_group.blockSignals(False)
        
        self.enabled_toggle.setChecked(True)
        self.favorite_toggle.setChecked(False)
        self.input_hotkey.setText("")
        self.btn_delete.setEnabled(False)
        self.table.clearSelection()
        self.update_char_count()
        self._is_editor_dirty = False
        self.update_editor_state()

    def _storage_key_index(self, profile):
        sensitive = self.data["settings"].get("case_sensitive", False)
        index = {}
        for ckey, info in self.data["shortcuts"].items():
            if info.get("profile") == profile:
                bare = split_ckey(ckey)[1]
                token = bare if sensitive else bare.casefold()
                index.setdefault(token, []).append(ckey)
        return index

    def _matching_storage_keys(self, bare, profile=None, shortcuts=None, exclude=None):
        profile = profile or self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        shortcuts = self.data["shortcuts"] if shortcuts is None else shortcuts
        sensitive = self.data["settings"].get("case_sensitive", False)
        needle = bare if sensitive else bare.casefold()
        return [ckey for ckey, info in shortcuts.items()
                if ckey != exclude and info.get("profile") == profile
                and (split_ckey(ckey)[1] if sensitive else split_ckey(ckey)[1].casefold()) == needle]

    def update_editor_state(self, *args):
        if self.sender() in (self.input_key, self.input_text, self.combo_group, self.input_hotkey):
            self._is_editor_dirty = True

        key = self.input_key.text().strip()
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        duplicate = bool(self._matching_storage_keys(key, profile=active, exclude=self.editing_key))
        self.input_key.setProperty("error", duplicate)
        self.input_key.style().unpolish(self.input_key)
        self.input_key.style().polish(self.input_key)

        has_changes = getattr(self, "_is_editor_dirty", False)
        self.btn_save.setEnabled(bool(key) and not duplicate and has_changes)

        if hasattr(self, "e_title"):
            self.e_title.setText("Редактор замены  •" if has_changes else "Редактор замены")

        empty_editor = (not self.editing_key and not key and not self.input_text.toPlainText().strip())

        if hasattr(self, "editor_stack"):
            self.editor_stack.setCurrentIndex(0 if empty_editor else 1)
        if hasattr(self, "favorite_toggle"):
            self.favorite_toggle.setVisible(not empty_editor)
        self._refresh_editor_meta()
        self._update_key_count()
        self._sync_favorite_action()

    def _refresh_editor_meta(self):
        if not hasattr(self, "meta_uses"):
            return
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        info = self.data.get("shortcuts", {}).get(self.editing_key) if self.editing_key else None
        self.meta_profile.setText(str(info.get("profile", active)) if info else str(active))
        if info:
            self.meta_uses.setText(f"{int(info.get('uses', 0)):,}".replace(",", " "))
        else:
            self.meta_uses.setText("—")

    def _insert_variable(self, token):
        self.input_text.insertPlainText(token)
        self.input_text.setFocus()

    def _on_row_actions(self, index):
        pos = self.table.visualRect(index).center()
        QTimer.singleShot(0, lambda p=pos: self.show_context_menu(p))

    def _copy_test_result(self):
        text = getattr(self, "_test_plain", "") or ""
        if not text:
            self.show_message("Нечего копировать: введите существующий ключ")
            return
        QApplication.clipboard().setText(text)
        self.show_message("Результат скопирован в буфер обмена")

    def update_char_count(self):
        self.char_count.setText(f"{len(self.input_text.toPlainText())} / 1000")

    def save_shortcut(self):
        key = self.input_key.text().strip()
        text = self.input_text.toPlainText()
        if not key:
            QMessageBox.warning(self, "Нужен ключ", "Поле ключа не может быть пустым.")
            self.input_key.setFocus()
            return
        if not text:
            reply = QMessageBox.question(self, "Пустой текст", "Текст замены пустой. Сохранить такую замену?")
            if reply != QMessageBox.Yes:
                return
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        ckey = make_ckey(active, key)
        if self._matching_storage_keys(key, profile=active, exclude=self.editing_key):
            QMessageBox.warning(self, "Дубликат ключа", f"Ключ «{key}» уже существует в этом радиоцентре с учётом настройки регистра.")
            return
        shortcuts = self._copy_shortcuts()
        old_ckey = self.editing_key
        old_info = shortcuts.get(old_ckey, {}) if old_ckey else {}
        if old_ckey and old_ckey != ckey:
            shortcuts.pop(old_ckey, None)
        shortcuts[ckey] = {
            "text": text,
            "group": self.combo_group.currentText().strip() or "Общие",
            "uses": int(old_info.get("uses", shortcuts.get(ckey, {}).get("uses", 0))),
            "enabled": self.enabled_toggle.isChecked(),
            "favorite": self.favorite_toggle.isChecked(),
            "profile": active,
        }
        _hk = self.input_hotkey.text().strip()
        if _hk:
            shortcuts[ckey]["hotkey"] = _hk
        if not self._commit_shortcuts(shortcuts):
            return
        self.refresh_table()
        self.editing_key = ckey
        self._is_editor_dirty = False
        self.update_editor_state()
        self.show_message(f"Сохранено: {key}")

    def delete_shortcut(self):
        if self.editing_key:
            ckey = self.editing_key
        else:
            active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
            ckey = make_ckey(active, self.input_key.text().strip())
        if ckey not in self.data["shortcuts"]:
            return
        _, bare = split_ckey(ckey)
        reply = QMessageBox.question(self, "Удалить замену", f"Удалить «{bare}» без возможности отмены?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        shortcuts = self._copy_shortcuts()
        del shortcuts[ckey]
        if not self._commit_shortcuts(shortcuts):
            return
        self.refresh_table()
        self.clear_editor()
        self.show_message(f"Удалена замена: {bare}")

    def delete_all_shortcuts(self):
        if not self._confirm_editor_changes():
            return False
        total = len(self.data.get("shortcuts", {}))
        if total == 0:
            QMessageBox.information(self, "Удаление всех замен", "Список уже пуст.")
            return False
            
        message = (
            f"Будут удалены ВСЕ {total} автозамен.\n"
            f"Это действие нельзя отменить обычной кнопкой.\n\n"
            f"Перед удалением будет создана резервная копия."
        )
        reply = QMessageBox.warning(
            self, "Удалить ВСЕ автозамены?", message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return False
            
        if not self._commit_shortcuts({}):
            return False
        self.clear_editor()
        self.refresh_table()
        self.show_message(f"Удалено автозамен: {total}")
        return True

    def show_context_menu(self, pos):
        if pos.x()<0 and self.table.selectionModel().selectedRows():
            pos=self.table.visualRect(self.table.selectionModel().selectedRows()[0]).center()
        index=self.table.indexAt(pos)
        if not index.isValid():return
        if not self.table.selectionModel().isSelected(index):
            self.table.clearSelection();self.table.selectRow(index.row())
        rows=sorted(self.table.selectionModel().selectedRows(),key=lambda i:i.row())
        keys=[self.model.row_data(self.proxy.mapToSource(i).row())[0] for i in rows]
        if not keys:return
        self._exec_shortcut_context_menu(keys, self.table.viewport().mapToGlobal(pos))

    def _exec_shortcut_context_menu(self, keys, global_pos):
        menu = self._create_shortcut_context_menu(keys)
        try:
            menu.exec_(global_pos)
        finally:
            menu.deleteLater()

    def _context_indexes(self,keys):
        result=[]
        for key in keys:
            row=self.model.row_for_key(key)
            if row>=0:
                index=self.proxy.mapFromSource(self.model.index(row,0))
                if index.isValid():result.append(index)
        return result

    def _create_shortcut_context_menu(self,keys):
        # Snapshot stable keys, not row numbers: background sorting cannot retarget an action.
        keys=tuple(key for key in keys if key in self.data['shortcuts'])
        menu=ShortcutContextMenu(self)
        count=len(keys)
        if not count:return menu
        profile=self.data['settings'].get('active_profile',DEFAULT_PROFILE)
        title=split_ckey(keys[0])[1] if count==1 else f'{count} '+plural(count,'замена','замены','замен')
        menu.add_header(title,f'{"ЗАМЕНА" if count==1 else "ВЫБРАННЫЕ ЗАПИСИ"}  ·  {profile}')
        if count==1:
            menu.add_command('Редактировать', '\uE70F',lambda:self._context_edit(keys[0]))
            menu.add_command('Создать дубликат', '\uE8C8',lambda:self._context_duplicate(keys[0]))
        copy_menu=menu.add_branch('Копировать', '\uE8C8')
        copy_menu.add_command('Ключ' if count==1 else 'Ключи выбранных', '\uE765',lambda:self._context_copy(keys,False))
        copy_menu.add_command('Текст замены' if count==1 else 'Тексты выбранных', '\uE7E8',lambda:self._context_copy(keys,True))
        menu.addSeparator()
        all_favorite=all(self.data['shortcuts'][k].get('favorite',False) for k in keys)
        menu.add_command('Убрать из избранного' if all_favorite else 'Добавить в избранное', '\uE734',
                         lambda:self._context_set_flag(keys,'favorite',not all_favorite))
        all_enabled=all(self.data['shortcuts'][k].get('enabled',True) for k in keys)
        state_text=('Выключить замену' if all_enabled else 'Включить замену') if count==1 else ('Выключить выбранные' if all_enabled else 'Включить выбранные')
        menu.add_command(state_text,'\uE711' if all_enabled else '\uE73E',lambda:self._context_set_flag(keys,'enabled',not all_enabled))
        menu.addSeparator()
        menu.add_command('Сменить группу…','\uE8B7',lambda:self._context_bulk(keys,self.mass_change_group))
        radio_menu=menu.add_branch('Другой радиоцентр','\uE916')
        radio_menu.add_command('Перенести…','\uE72A',lambda:self._context_bulk(keys,self.mass_move_radio))
        radio_menu.add_command('Создать копии…','\uE8C8',lambda:self._context_bulk(keys,self.mass_clone_radio))
        menu.addSeparator()
        menu.add_command('Удалить замену…' if count==1 else 'Удалить выбранные…','\uE74D',
                         lambda:self._context_bulk(keys,self.mass_delete),danger=True)
        return menu

    def _context_bulk(self,keys,handler):
        indexes=self._context_indexes(keys)
        if indexes:handler(indexes)

    def _context_edit(self,key):
        indexes=self._context_indexes([key])
        if indexes:self.load_selected_row(indexes[0]);self.input_text.setFocus()

    def _context_duplicate(self,key):
        if not self._confirm_editor_changes():
            return
        info=self.data['shortcuts'].get(key)
        if info is None:return
        info=dict(info)
        self.clear_editor()
        self.input_key.setText(self._unique_key(split_ckey(key)[1]))
        self.input_text.setPlainText(info.get('text',''))
        self.combo_group.setCurrentText(info.get('group','Общие'))
        self.enabled_toggle.setChecked(info.get('enabled',True))
        self.favorite_toggle.setChecked(info.get('favorite',False))
        self._sync_favorite_action()
        self.input_key.setFocus();self.input_key.selectAll()

    def _context_copy(self,keys,text):
        entries=[(key,self.data['shortcuts'][key]) for key in keys if key in self.data['shortcuts']]
        if not entries:return
        content=('\n\n' if text else '\n').join(str(info.get('text','')) if text else split_ckey(key)[1] for key,info in entries)
        QApplication.clipboard().setText(content)
        self.show_message('Текст скопирован' if text else 'Ключи скопированы' if len(entries)>1 else 'Ключ скопирован')

    def _context_set_flag(self,keys,flag,value):
        for key in keys:
            if key in self.data['shortcuts']:self.data['shortcuts'][key][flag]=bool(value)
        if self.editing_key in self.data['shortcuts']:
            info=self.data['shortcuts'][self.editing_key]
            self.enabled_toggle.setChecked(info.get('enabled',True))
            self.favorite_toggle.setChecked(info.get('favorite',False))
            self._sync_favorite_action()
        self.persist();self.refresh_table()
        for index in self._context_indexes(keys):
            self.table.selectionModel().select(index,QItemSelectionModel.Select|QItemSelectionModel.Rows)
        self._update_selection_bar()


    def mass_change_group(self, proxy_indexes):
        keys = []
        for index in proxy_indexes:
            source = self.proxy.mapToSource(index)
            if source.isValid():
                keys.append(self.model.row_data(source.row())[0])
        keys = list(dict.fromkeys(keys))
        if not keys:
            return
        groups = sorted({v.get("group", "Общие") for v in self.data.get("shortcuts", {}).values()})
        if not groups:
            groups = ["Общие"]
            
        new_group, ok = QInputDialog.getItem(
            self, "Сменить группу", 
            f"Выбрано замен: {len(proxy_indexes)}\nВ какую группу их перенести?", 
            groups, 0, True
        )
        
        if ok and new_group.strip():
            new_group = new_group.strip()
            shortcuts = self._copy_shortcuts()
            for k in keys:
                if k in shortcuts:
                    shortcuts[k]["group"] = new_group
                    
            if not self._commit_shortcuts(shortcuts):
                return
            self.refresh_table()
            self.show_message(f"Группа изменена на «{new_group}» для {len(keys)} замен.")

    def _replace_radio_prefix(self, text, old_radio, new_radio, profiles):
        import re
        search_radios = [old_radio] + profiles
        escaped = [re.escape(p) for p in set(search_radios)]
        pattern = re.compile(rf"^({'|'.join(escaped)})\s*\|\s*", re.IGNORECASE)
        if pattern.search(text):
            return pattern.sub(lambda _match: f"{new_radio} | ", text, count=1)
        return text

    def mass_move_radio(self, proxy_indexes):
        keys = []
        for index in proxy_indexes:
            source = self.proxy.mapToSource(index)
            if source.isValid():
                keys.append(self.model.row_data(source.row())[0])
        keys = list(dict.fromkeys(keys))
        old_editing_key = self.editing_key
        if not self._confirm_editor_changes():
            return
        if old_editing_key and old_editing_key not in self.data["shortcuts"] and self.editing_key:
            keys = [self.editing_key if key == old_editing_key else key for key in keys]
        keys = [key for key in keys if key in self.data["shortcuts"]]
        if not keys:
            return
        profiles = self.data["settings"].get("profiles", RADIO_PROFILES)
        radio, ok = QInputDialog.getItem(
            self, "Перенос в радиоцентр", 
            f"Выбрано замен: {len(proxy_indexes)}\nВ какой радиоцентр их перенести?", 
            profiles, 0, False
        )
        if not ok or not radio:
            return
            
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        if radio == active:
            QMessageBox.information(self, "Перенос", "Замены уже находятся в этом радиоцентре.")
            return
            
        target_index = self._storage_key_index(radio)
        sensitive = self.data["settings"].get("case_sensitive", False)
        destinations = {k: target_index.get(split_ckey(k)[1] if sensitive else split_ckey(k)[1].casefold(), [])
                        for k in keys}
        if any(len(matches) > 1 for matches in destinations.values()):
            QMessageBox.warning(self, "Неоднозначные ключи", "В целевом радиоцентре есть ключи, различающиеся только регистром. Сначала устраните совпадения или включите учёт регистра.")
            return
        conflicts = [k for k in keys if destinations[k]]
        if not self.data["settings"].get("case_sensitive", False):
            names = [split_ckey(k)[1].casefold() for k in keys]
            if len(names) != len(set(names)):
                QMessageBox.warning(self, "Неоднозначные ключи", "Среди переносимых записей есть ключи, различающиеся только регистром.")
                return
        if conflicts:
            answer = QMessageBox.question(
                self, "Совпадающие ключи при переносе",
                f"В радиоцентре {radio} уже есть ключи: {len(conflicts)}.\n\n"
                "Да — заменить существующие записи.\n"
                "Нет — перенести только записи без совпадений.\n"
                "Отмена — ничего не переносить.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.No:
                keys = [key for key in keys if key not in conflicts]
            if not keys:
                return
        shortcuts = self._copy_shortcuts()
        changed_count = 0

        for k in keys:
            if k in shortcuts:
                info = shortcuts[k]
                _, bare = split_ckey(k)
                info["profile"] = radio
                info["text"] = self._replace_radio_prefix(info["text"], active, radio, profiles)
                del shortcuts[k]
                destination = destinations[k][0] if destinations[k] else make_ckey(radio, bare)
                shortcuts[destination] = info
                changed_count += 1
                
        if not self._commit_shortcuts(shortcuts):
            return
        self.clear_editor()
        self.refresh_table()
        self.show_message(f"Перенесено в {radio}: {changed_count} замен.")

    def mass_clone_radio(self, proxy_indexes):
        keys = []
        for index in proxy_indexes:
            source = self.proxy.mapToSource(index)
            if source.isValid():
                keys.append(self.model.row_data(source.row())[0])
        keys = list(dict.fromkeys(keys))
        old_editing_key = self.editing_key
        if not self._confirm_editor_changes():
            return
        if old_editing_key and old_editing_key not in self.data["shortcuts"] and self.editing_key:
            keys = [self.editing_key if key == old_editing_key else key for key in keys]
        keys = [key for key in keys if key in self.data["shortcuts"]]
        if not keys:
            return
        profiles = self.data["settings"].get("profiles", RADIO_PROFILES)
        radio, ok = QInputDialog.getItem(
            self, "Клонировать в радиоцентр", 
            f"Выбрано замен: {len(proxy_indexes)}\nВ какой радиоцентр создать копии?", 
            profiles, 0, False
        )
        if not ok or not radio:
            return
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        if radio == active:
            QMessageBox.information(self, "Клонирование", "Нельзя клонировать в текущий радиоцентр.")
            return
        shortcuts = self._copy_shortcuts()
        cloned = 0
        target_index = self._storage_key_index(radio)
        sensitive = self.data["settings"].get("case_sensitive", False)
        for old_ckey in keys:
            if old_ckey not in shortcuts:
                continue
            old_info = shortcuts[old_ckey]
            _, bare = split_ckey(old_ckey)
            new_ckey = make_ckey(radio, bare)
            new_bare = bare
            token = new_bare if sensitive else new_bare.casefold()
            if token in target_index:
                n = 2
                while (f"{bare}_{n}" if sensitive else f"{bare}_{n}".casefold()) in target_index:
                    n += 1
                new_bare = f"{bare}_{n}"
                token = new_bare if sensitive else new_bare.casefold()
                new_ckey = make_ckey(radio, new_bare)
            target_index[token] = [new_ckey]
            new_info = dict(old_info)
            new_info["profile"] = radio
            new_info["text"] = self._replace_radio_prefix(old_info["text"], active, radio, profiles)
            shortcuts[new_ckey] = new_info
            cloned += 1
        if not self._commit_shortcuts(shortcuts):
            return
        self.clear_editor()
        self.refresh_table()
        self.show_message(f"Склонировано в {radio}: {cloned} замен.")

    def mass_delete(self, proxy_indexes):
        keys = []
        for index in proxy_indexes:
            source = self.proxy.mapToSource(index)
            if source.isValid():
                keys.append(self.model.row_data(source.row())[0])
        keys = list(dict.fromkeys(keys))
        old_editing_key = self.editing_key
        if not self._confirm_editor_changes():
            return
        if old_editing_key and old_editing_key not in self.data["shortcuts"] and self.editing_key:
            keys = [self.editing_key if key == old_editing_key else key for key in keys]
        keys = [key for key in keys if key in self.data["shortcuts"]]
        if not keys:
            return
        reply = QMessageBox.question(
            self, "Удалить замены", 
            f"Вы уверены, что хотите удалить {len(proxy_indexes)} замен?", 
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
            
        shortcuts = self._copy_shortcuts()
        for k in keys:
            if k in shortcuts:
                del shortcuts[k]
                
        if not self._commit_shortcuts(shortcuts):
            return
        self.clear_editor()
        self.refresh_table()
        self.show_message(f"Удалено замен: {len(keys)}")

    def _unique_key(self, base):
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        candidate = f"{base}_копия"
        n = 2
        while self._matching_storage_keys(candidate, profile=active):
            candidate = f"{base}_копия{n}"
            n += 1
        return candidate

    def _copy_shortcuts(self):
        # Entry fields are scalar values; a per-entry copy isolates every edit.
        return {key: dict(info) for key, info in self.data.get("shortcuts", {}).items()}

    def _commit_shortcuts(self, shortcuts, backup=True):
        candidate = dict(self.data)
        candidate["shortcuts"] = shortcuts
        return self._commit_data(candidate, backup=backup)

    def _commit_data(self, candidate, backup=True):
        """Disk-first commit for destructive/bulk actions and explicit Save.

        Callers stage a separate candidate: a failed write must not publish it
        to the GUI/engine. Frequent reversible toggles and profile selection
        deliberately use persist() instead; see its deferred-write contract.
        """
        if self._save_timer.isActive():
            self._save_timer.stop()
        try:
            save_data(candidate, make_backup=backup)
        except Exception as exc:
            self._report_save_error(exc, staged=True)
            return False
        self.data = candidate
        self.binder.configure(self.data)
        self._save_succeeded()
        return True

    def _report_save_error(self, exc, staged=False):
        log.error("Ошибка записи данных: %s", exc, exc_info=True)
        already_failed = getattr(self, "_save_failed", False)
        self._save_failed = True
        # Permanent status + explicit retry: this warning cannot disappear in 3.5 seconds.
        self.statusBar().showMessage("Не удалось сохранить данные. Изменения не записаны на диск.", 0)
        if not hasattr(self, "btn_retry_save"):
            self.btn_retry_save = QPushButton("Повторить сохранение")
            self.btn_retry_save.setToolTip("Записать текущее состояние. Неудавшееся действие нужно повторить отдельно; черновик — сохранить кнопкой «Сохранить».")
            self.btn_retry_save.clicked.connect(self._flush_save)
        if not getattr(self, "_retry_save_attached", False):
            self.statusBar().addPermanentWidget(self.btn_retry_save)
            self._retry_save_attached = True
        self.btn_retry_save.show()
        if not already_failed:
            detail = ("Изменения этого действия не применены. После устранения ошибки повторите действие."
                      if staged else "Изменения остаются только в памяти. Нажмите «Повторить сохранение» после устранения ошибки.")
            QMessageBox.critical(self, "Ошибка сохранения", f"Не удалось записать данные:\n{exc}\n\n{detail}")

    def _save_succeeded(self):
        was_failed = getattr(self, "_save_failed", False)
        self._save_failed = False
        if hasattr(self, "btn_retry_save"):
            self.statusBar().removeWidget(self.btn_retry_save)
            self.btn_retry_save.hide()
            self._retry_save_attached = False
        if was_failed:
            self.statusBar().showMessage("Данные сохранены.", 3500)

    def persist(self, backup=False):
        """Debounce frequent, reversible in-memory changes (400 ms).

        Favorite/enabled toggles, profile creation/selection and usage counters
        update the GUI/engine immediately rather than fsync on every click.
        These edits are pending, not confirmed disk writes: _flush_save reports
        failures persistently and offers Retry. Destructive/bulk operations and
        explicit Save must stage candidates via _commit_data/_commit_shortcuts.
        backup=True is a legacy immediate-write path, not a rollback mechanism
        for a dictionary that the caller has already mutated.
        """
        if backup:
            return self._commit_data(self.data, backup=True)
        self.binder.configure(self.data)
        self._save_timer.start()
        return True

    def _flush_save(self):
        self._save_timer.stop()
        try:
            save_data(self.data)
        except Exception as exc:
            self._report_save_error(exc)
            return False
        self._save_succeeded()
        return True

    def toggle_binder(self):
        if self._binder_transition:
            return
        self._binder_transition = True
        self.btn_toggle.setEnabled(False)
        try:
            if self.binder.is_running:
                self.binder.stop()
            else:
                self.binder.configure(self.data)
                self.binder.start()
        except Exception as exc:
            log.exception("Ошибка переключения автозамены")
            try:
                self.binder.stop()
            except Exception:
                self.binder.is_running = False
                log.exception("Не удалось остановить автозамену после ошибки")
            self.show_message(f"Не удалось переключить автозамену: {exc}")
        finally:
            self._binder_transition = False
            self.btn_toggle.setEnabled(True)
            self.update_status(self.binder.is_running)

    def update_status(self, running):
        # Очередь сигналов от горячей клавиши не должна возвращать устаревший статус.
        if self.sender() is self.binder:
            running = self.binder.is_running
        running = bool(running)
        self.status_box.setProperty("running", running)
        self.run_indicator.set_running(running)
        self.status_dot.running=running
        self.status_dot.update()
        if not running and hasattr(self, "game_overlay"):
            self.game_overlay.hide()
        self.status_label.setText("Работает" if running else "Остановлено")
        self.status_label.setStyleSheet(
            "background: transparent; border:none; font-size: 12px; font-weight: 500; color: "
            + ("#2ED573;" if running else "#9AA7C2;")
        )
        self.btn_toggle.set_running(running)
        self.tray_toggle_action.setText("Остановить" if running else "Запустить")

    def show_message(self, message):
        if getattr(self, "_save_failed", False):
            self.statusBar().showMessage("Не сохранено на диск · " + str(message), 0)
        else:
            self.statusBar().showMessage(message, 3500)

    def record_replacement(self, key):
        if self.data["settings"].get("history_enabled", True):
            self.data.setdefault("history", []).append({
                "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "key": key,
            })
            limit = max(10, min(500, int(self.data["settings"].get("history_limit", 100))))
            self.data["history"] = self.data["history"][-limit:]
        
        if key in self.data.get("shortcuts", {}):
            info = self.data["shortcuts"][key]
            info["uses"] = int(info.get("uses", 0)) + 1
            row = self.model.row_for_key(key)
            if row >= 0:
                index = self.model.index(row, 1)
                self.model.dataChanged.emit(index, index, [Qt.UserRole])
            try:
                self._apply_sort()
            except Exception:
                log.exception("Не удалось пересортировать таблицу")

        # Сохраняем с задержкой и пачкой, а не синхронно на каждую замену (без микролагов в игре).
        self._save_timer.start()
        if getattr(self, "current_page", "") == "history":
            self.history_page.reload()

    def _on_settings_saved(self):
        # SettingsPage emits this only after its candidate was written successfully.
        s = self.data["settings"]
        self.game_overlay.set_position(s.get("overlay_position", "top_right"))
        self.game_overlay.set_screen_margin(int(s.get("overlay_screen_margin", 24)))
        self.game_overlay.setWindowOpacity(int(s.get("overlay_opacity", 92)) / 100.0)
        self.game_overlay.set_hotkey_text(s.get("game_overlay_hotkey", "alt+2"))
        self.game_overlay._auto_hide_ms = int(s.get("overlay_auto_hide_ms", 5000))
        if not s.get("game_overlay_enabled", True):
            self.binder.overlay_active = False
            self.game_overlay.set_active(False)
        self.show_message("Настройки сохранены.")

    def auto_layout_groups(self):
        active_profile = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        to_update = []
        for key, info in self.data.get("shortcuts", {}).items():
            if info.get("profile") == active_profile:
                new_group = guess_group(info.get("text", ""))
                if new_group != info.get("group"):
                    to_update.append((key, new_group))
        
        if not to_update:
            QMessageBox.information(self, "Автораскладка", "Все записи уже находятся в правильных группах.")
            return
            
        reply = QMessageBox.question(
            self, "Автораскладка",
            f"Будет переразложено {len(to_update)} записей в радиоцентре {active_profile}.\n"
            "Программа заново проверит текст каждой замены и назначит подходящую группу.\n\n"
            "Продолжить?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply != QMessageBox.Yes:
            return
            
        shortcuts = self._copy_shortcuts()
        for key, new_group in to_update:
            if key in shortcuts:
                shortcuts[key]["group"] = new_group
        if not self._commit_shortcuts(shortcuts):
            return
        if self.editing_key in self.data["shortcuts"]:
            self.combo_group.blockSignals(True)
            self.combo_group.setCurrentText(self.data["shortcuts"][self.editing_key].get("group", "Общие"))
            self.combo_group.blockSignals(False)
        self.refresh_table()
        self.show_message(f"Автораскладка завершена: обновлено {len(to_update)} записей")

    def open_emoji_dialog(self):
        selected_rows = self.table.selectionModel().selectedRows()
        targets = selected_rows or [self.proxy.index(row, 0) for row in range(self.proxy.rowCount())]
        keys_to_update = []
        for index in targets:
            source = self.proxy.mapToSource(index)
            if source.isValid():
                keys_to_update.append(self.model.row_data(source.row())[0])
        if not selected_rows:
            ans = QMessageBox.question(
                self, "Эмодзи", 
                f"Ничего не выделено. Применить действие ко всем отображаемым заменам ({len(keys_to_update)})?\nУчитываются поиск, группа и раздел «Избранное».",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if ans != QMessageBox.Yes:
                return
        count = len(keys_to_update)
        if not count:
            QMessageBox.information(self, "Эмодзи", "Нет отображаемых замен для обработки.")
            return

        dialog = EmojiDialog(count, self)
        accepted = dialog.exec_() == QDialog.Accepted
        emoji, action = dialog.get_result()
        dialog.deleteLater()
        if accepted:
            if not emoji and action != "remove":
                QMessageBox.warning(self, "Ошибка", "Вы не ввели эмодзи.")
                return

            import re
            emoji_pattern = re.compile(
                r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
                r"\U0001F1E0-\U0001F1FF\U00002700-\U000027BF\U0001F900-\U0001F9FF"
                r"\U0001FA70-\U0001FAFF\U00002600-\U000026FF]+", flags=re.UNICODE
            )

            shortcuts = self._copy_shortcuts()
            changed = 0
            for k in keys_to_update:
                if k in shortcuts:
                    info = shortcuts[k]
                    text = info.get("text", "")
                    
                    if action == "add_start":
                        new_text = f"{emoji} {text}"
                    elif action == "add_end":
                        new_text = f"{text} {emoji}"
                    else:
                        new_text = emoji_pattern.sub("", text).strip()
                        new_text = re.sub(r" +", " ", new_text)
                    
                    if new_text != text:
                        info["text"] = new_text
                        changed += 1
            
            if changed > 0:
                if not self._commit_shortcuts(shortcuts):
                    return
                self.binder.configure(self.data)
                self.refresh_table()
                self.show_message(f"Обновлено замен: {changed}")
            else:
                QMessageBox.information(self, "Эмодзи", "Текст замен не изменился (возможно, эмодзи уже были или их не было).")

    def export_data(self):
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "Экспорт автозамен", "efir_export.json",
            "Эфир JSON (*.json);;Punto Switcher DAT (*.dat)"
        )
        if not path: return
        try:
            if path.lower().endswith(".dat") or "DAT" in selected_filter:
                if not path.lower().endswith(".dat"): path += ".dat"
                active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
                filtered = {}
                for ckey, info in self.data.get("shortcuts", {}).items():
                    if info.get("profile") == active:
                        _, bare = split_ckey(ckey)
                        filtered[bare] = info
                if Path(path).resolve() == DATA_FILE.resolve():
                    raise ValueError("Нельзя перезаписывать рабочую базу экспортом. Выберите другой файл.")
                export_punto_dat(path, filtered)
                QMessageBox.information(self, "Экспорт", f"Экспортировано в DAT ({active}): {len(filtered)} замен")
            else:
                if not path.lower().endswith(".json"): path += ".json"
                if Path(path).resolve() == DATA_FILE.resolve():
                    raise ValueError("Нельзя перезаписывать рабочую базу экспортом. Выберите другой файл.")
                payload = json.dumps(self.data, ensure_ascii=False, indent=2).encode("utf-8")
                atomic_write_file(path, payload)
                QMessageBox.information(self, "Экспорт", f"Экспортировано в JSON: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка экспорта", f"Не удалось экспортировать замены:\n{exc}")

    def import_data(self):
        if self._import_is_busy():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Импорт автозамен", "",
            "Файлы автозамен (*.json *.dat);;Эфир JSON (*.json);;Punto Switcher DAT (*.dat);;Все файлы (*.*)"
        )
        if not path: return

        self.btn_import.setEnabled(False)
        self.btn_export.setEnabled(False)
        self._import_progress = QProgressDialog("Читаю файл автозамен…", "", 0, 0, self)
        self._import_progress.setWindowTitle("Импорт замен")
        self._import_progress.setWindowModality(Qt.WindowModal)
        self._import_progress.setCancelButton(None)
        self._import_progress.show()

        self._import_worker = ImportWorker(path, self)
        self._import_worker.finished_data.connect(self._finish_import_data)
        self._import_worker.failed.connect(self._fail_import_data)
        self._import_worker.finished.connect(self._cleanup_import_worker)
        self._import_worker.start()

    def _close_import_progress(self):
        progress = getattr(self, "_import_progress", None)
        if progress is not None:
            self._import_progress = None
            progress.hide()  # reject() intentionally ignores Esc while the worker is reading.
            progress.deleteLater()

    def _cleanup_import_worker(self):
        self.btn_import.setEnabled(True)
        self.btn_export.setEnabled(True)
        worker = getattr(self, "_import_worker", None)
        if worker is not None:
            worker.deleteLater()
        self._import_worker = None

    def _fail_import_data(self, message):
        self._close_import_progress()
        QMessageBox.critical(self, "Ошибка импорта", f"Файл не удалось импортировать:\n{message}")

    def _finish_import_data(self, incoming):
        self._close_import_progress()
        if not incoming:
            QMessageBox.information(self, "Импорт", "В файле не найдено автозамен для импорта.")
            return

        # JSON keeps compound storage keys; DAT contains literal trigger keys.
        # Resolve JSON's source before assigning the destination, without mutating the file data.
        records = [(info.get("_import_key", key), dict(info)) for key, info in incoming.items()]
        bare_keys = [key for key, _ in records]
        if (len(set(bare_keys)) != len(bare_keys)
                and any("_import_source_profile" in info for _, info in records)):
            sources = list(dict.fromkeys(info.get("_import_source_profile") or info.get("profile", DEFAULT_PROFILE)
                                         for _, info in records))
            source, accepted = QInputDialog.getItem(
                self, "Исходный радиоцентр",
                "В JSON есть одинаковые ключи из разных радиоцентров.\n"
                "Выберите исходный радиоцентр, замены которого нужно импортировать:",
                sources, 0, False)
            if not accepted:
                return
            records = [(key, info) for key, info in records
                       if (info.get("_import_source_profile") or info.get("profile", DEFAULT_PROFILE)) == source]
        exact = {}
        for key, info in records:
            exact.setdefault(key, []).append(info)
        repeated = {key: values for key, values in exact.items() if len(values) > 1}
        if repeated:
            different = sum(len({str(info.get("text", "")) for info in values}) > 1
                            for values in repeated.values())
            preview = ", ".join(f"«{key}»" for key in list(repeated)[:8])
            choices = ["Оставить первую запись каждого ключа",
                       "Оставить последнюю запись каждого ключа"]
            choice, accepted = QInputDialog.getItem(
                self, "Одинаковые ключи в файле",
                f"В файле повторяются ключи: {len(repeated)} ({preview}).\n"
                f"У {different} из них тексты различаются. Одинаковый ключ можно сохранить "
                "только один раз в одном радиоцентре.\n\n"
                "Выберите первый или последний вариант для всех повторов. Остальные записи "
                "не будут импортированы. Чтобы изучить их вручную, нажмите «Отмена»; "
                "исходный файл останется без изменений.",
                choices, 0, False)
            if not accepted:
                return
            records = [(key, values[0] if choice == choices[0] else values[-1])
                       for key, values in exact.items()]
        incoming = dict(records)
        target_profiles = self.data["settings"].get("profiles", RADIO_PROFILES)
        active = self.data["settings"].get("active_profile", DEFAULT_PROFILE)
        default_index = target_profiles.index(active) if active in target_profiles else 0
        radio, ok = QInputDialog.getItem(
            self, "Радиоцентр для импорта",
            f"В какой радиоцентр добавить {len(incoming)} замен?",
            target_profiles, default_index, False,
        )
        if not ok:
            return

        if not self.data["settings"].get("case_sensitive", False):
            groups = casefold_groups(incoming)
            if groups:
                extra = sum(len(group) - 1 for group in groups)
                preview = "\n".join("• " + " / ".join(f"«{key}»" for key in group[:4])
                                    for group in groups[:5])
                differing = sum(len({str(incoming[key].get("text", "")) for key in group}) > 1
                                for group in groups)
                note = (f"Групп с разными текстами: {differing}. " if differing else
                        "Текст в каждой группе совпадает. ")
                choices = ["Оставить первый вариант в каждой группе",
                           "Оставить последний вариант в каждой группе"]
                choice, accepted = QInputDialog.getItem(
                    self, "Повторы в файле",
                    f"При выключенном учёте регистра найдено групп совпадающих ключей: {len(groups)} "
                    f"(лишних вариантов: {extra}).\n{preview}\n\n{note}"
                    "Выберите, какой вариант импортировать. Остальные варианты не будут добавлены; "
                    "исходный файл не изменится.",
                    choices, 0, False)
                if not accepted:
                    return
                discard = {key for group in groups
                           for key in (group[1:] if choice == choices[0] else group[:-1])}
                incoming = {key: info for key, info in incoming.items() if key not in discard}
        target_index = self._storage_key_index(radio)
        sensitive = self.data["settings"].get("case_sensitive", False)
        destinations = {bare: target_index.get(bare if sensitive else bare.casefold(), []) for bare in incoming}
        if any(len(matches) > 1 for matches in destinations.values()):
            QMessageBox.warning(self, "Неоднозначные ключи", "В целевом радиоцентре есть неоднозначные ключи. Устраните совпадения или включите учёт регистра перед импортом.")
            return
        conflicts = [bare for bare in incoming if destinations[bare]]
        if conflicts:
            preview = ", ".join(conflicts[:8])
            answer = QMessageBox.question(
                self, "Совпадающие ключи",
                f"В радиоцентре {radio} уже есть ключи: {len(conflicts)} ({preview}).\n\n"
                "Да — заменить существующие записи.\n"
                "Нет — пропустить совпадающие ключи.\n"
                "Отмена — ничего не импортировать.",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Cancel
            )
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.No:
                incoming = {k: v for k, v in incoming.items() if k not in conflicts}

        if not incoming:
            QMessageBox.information(self, "Импорт", "Импорт отменён: все ключи уже существуют.")
            return

        QApplication.processEvents()
        try:
            new_data = {}
            known_profiles = list(dict.fromkeys([
                *RADIO_PROFILES, *self.data["settings"].get("profiles", []),
                *(str(info.get("_import_source_profile") or info.get("profile") or "")
                  for info in incoming.values()),
            ]))
            known_profiles = [profile for profile in known_profiles if profile]
            for bare, original in incoming.items():
                info = dict(original)
                source = str(info.pop("_import_source_profile", None) or info.get("profile") or DEFAULT_PROFILE)
                info.pop("_import_key", None)
                info["text"] = self._replace_radio_prefix(info.get("text", ""), source, radio, known_profiles)
                info["profile"] = radio
                if info.get("group") in ("", "Punto Switcher", "Общие", None):
                    info["group"] = guess_group(info.get("text", ""))
                destination = destinations[bare][0] if destinations[bare] else make_ckey(radio, bare)
                new_data[destination] = info
            shortcuts = self._copy_shortcuts()
            shortcuts.update(new_data)
            if not self._commit_shortcuts(shortcuts):
                return
            self.refresh_table()
            QMessageBox.information(self, "Импорт", f"Импортировано замен: {len(incoming)}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", f"Не удалось сохранить замены:\n{exc}")

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.Find):
            self.search_input.setFocus()
            self.search_input.selectAll()
            return
        if event.matches(QKeySequence.Save):
            if self.input_key.text().strip():
                self.save_shortcut()
            else:
                self._flush_save()
            return
        if event.key() == Qt.Key_Delete and self.table.hasFocus():
            self._selection_delete()
            return
        if event.key() == Qt.Key_N and event.modifiers() & Qt.ControlModifier:
            self.new_shortcut()
            return
        super().keyPressEvent(event)


INSTANCE_NAME = "Efir-" + re.sub(r"\W", "_", os.environ.get("USERNAME") or os.environ.get("USER") or "user")


def acquire_single_instance():
    """QLocalServer — если мы первая копия; False — Эфир уже запущен (его окно попросили показаться)."""
    from PyQt5.QtNetwork import QLocalServer, QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(INSTANCE_NAME)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return False
    server = QLocalServer()
    if not server.listen(INSTANCE_NAME):
        QLocalServer.removeServer(INSTANCE_NAME)  # остался после аварийного выхода
        if not server.listen(INSTANCE_NAME):
            log.warning("Не удалось включить защиту от второй копии: %s", server.errorString())
            return None
    return server


def _on_instance_ping(server, window):
    while server.hasPendingConnections():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
            conn.disconnectFromServer()
    window.restore_window()


def main():
    setup_logging()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling,True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps,True)
    if hasattr(QApplication,"setHighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setPalette(dark_startup_palette())
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Efir")
    server = acquire_single_instance()
    if server is False:
        sys.exit(0)
    app.setWindowIcon(QIcon(make_emblem(256)))
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    if server is not None:
        window.instance_server = server
        server.newConnection.connect(lambda: _on_instance_ping(server, window))
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()