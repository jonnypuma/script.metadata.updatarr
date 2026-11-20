# -*- coding: utf-8 -*-
"""
Compatibility layer for running inside Kodi or standalone in CLI/tests.
"""
import os
import sys
import shutil
import logging

try:
    import xbmc
    import xbmcgui
    import xbmcvfs
    import xbmcaddon
    HAS_KODI = True
except ImportError:
    HAS_KODI = False
    xbmc = None
    xbmcgui = None
    xbmcvfs = None
    xbmcaddon = None

logger = logging.getLogger("script.metadata.updatarr")
if not HAS_KODI and not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

ADDON_ID = "script.metadata.updatarr"


def is_kodi():
    return HAS_KODI


def get_addon():
    if HAS_KODI:
        return xbmcaddon.Addon(ADDON_ID)
    return None


def log(msg, level="info"):
    prefix = "[script.metadata.updatarr] "
    full_msg = prefix + str(msg)
    if HAS_KODI:
        kodi_level = xbmc.LOGINFO
        if level == "debug":
            kodi_level = xbmc.LOGDEBUG
        elif level == "warning":
            kodi_level = xbmc.LOGWARNING
        elif level == "error":
            kodi_level = xbmc.LOGERROR
        xbmc.log(full_msg, level=kodi_level)
    else:
        if level == "debug":
            logger.debug(full_msg)
        elif level == "warning":
            logger.warning(full_msg)
        elif level == "error":
            logger.error(full_msg)
        else:
            logger.info(full_msg)


def translate_path(path):
    if HAS_KODI:
        return xbmcvfs.translatePath(path)
    
    # Standalone fallback mapping for Windows / Linux / macOS
    if path.startswith("special://"):
        appdata = os.environ.get("APPDATA")
        if sys.platform == "win32" and appdata:
            kodi_home = os.path.join(appdata, "Kodi")
        elif sys.platform == "darwin":
            kodi_home = os.path.expanduser("~/Library/Application Support/Kodi")
        else:
            kodi_home = os.path.expanduser("~/.kodi")
        
        rel = path[len("special://"):]
        if rel.startswith("profile/"):
            return os.path.join(kodi_home, "userdata", rel[len("profile/"):])
        if rel.startswith("database/"):
            return os.path.join(kodi_home, "userdata", "Database", rel[len("database/"):])
        if rel.startswith("home/"):
            return os.path.join(kodi_home, rel[len("home/"):])
        return os.path.join(kodi_home, rel)
    return path


def file_exists(path):
    if HAS_KODI:
        return xbmcvfs.exists(path)
    return os.path.exists(path)


def make_dirs(path):
    if HAS_KODI:
        if not xbmcvfs.exists(path):
            xbmcvfs.mkdirs(path)
    else:
        os.makedirs(path, exist_ok=True)


def read_text_file(path):
    if HAS_KODI:
        f = xbmcvfs.File(path, 'r')
        try:
            content = f.read()
            if isinstance(content, bytes):
                return content.decode('utf-8', errors='replace')
            return content
        finally:
            f.close()
    else:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()


def copy_file(src, dst):
    if HAS_KODI:
        return xbmcvfs.copy(src, dst)
    shutil.copy2(src, dst)
    return True


def delete_file(path):
    if HAS_KODI:
        return xbmcvfs.delete(path)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def list_dir(path):
    if HAS_KODI:
        dirs, files = xbmcvfs.listdir(path)
        return dirs, files
    if not os.path.exists(path):
        return [], []
    dirs = []
    files = []
    for item in os.listdir(path):
        full = os.path.join(path, item)
        if os.path.isdir(full):
            dirs.append(item)
        else:
            files.append(item)
    return dirs, files


# Dialog wrappers
def show_notification(title, msg, time_ms=3500):
    if HAS_KODI:
        dialog = xbmcgui.Dialog()
        dialog.notification(title, msg, xbmcgui.NOTIFICATION_INFO, time_ms)
    else:
        log("[NOTIFICATION] {}: {}".format(title, msg))


def show_ok_dialog(title, text):
    if HAS_KODI:
        dialog = xbmcgui.Dialog()
        dialog.ok(title, text)
    else:
        print("\n=== {} ===\n{}\n==================\n".format(title, text))


def show_yesno_dialog(title, text, nolabel=None, yeslabel=None):
    if HAS_KODI:
        dialog = xbmcgui.Dialog()
        kwargs = {}
        if nolabel is not None:
            kwargs["nolabel"] = nolabel
        if yeslabel is not None:
            kwargs["yeslabel"] = yeslabel
        return dialog.yesno(title, text, **kwargs)
    print("\n=== {} ===\n{} [y/N]: ".format(title, text), end="")
    try:
        ans = input().strip().lower()
        return ans in ("y", "yes")
    except EOFError:
        return True


def show_select_dialog(title, items):
    if HAS_KODI:
        dialog = xbmcgui.Dialog()
        return dialog.select(title, items)
    print("\n=== {} ===".format(title))
    for idx, item in enumerate(items):
        print(" [{}] {}".format(idx, item))
    print("Select index (or empty to cancel): ", end="")
    try:
        ans = input().strip()
        if ans.isdigit() and int(ans) < len(items):
            return int(ans)
    except EOFError:
        pass
    return -1


def show_text_dialog(title, text):
    if HAS_KODI:
        dialog = xbmcgui.Dialog()
        dialog.textviewer(title, text)
    else:
        print("\n=== {} ===\n{}\n==================\n".format(title, text))


class ProgressDialog:
    def __init__(self, title, line1=""):
        self.title = title
        self.dialog = None
        if HAS_KODI:
            self.dialog = xbmcgui.DialogProgress()
            self.dialog.create(title, line1)
        else:
            print("Starting: {} - {}".format(title, line1))

    def update(self, percent, line1="", line2=""):
        if HAS_KODI and self.dialog:
            msg = line1
            if line2:
                msg = "{}\n{}".format(line1, line2)
            self.dialog.update(int(percent), msg)
        else:
            if int(percent) % 20 == 0:
                print("[{:3d}%] {} {}".format(int(percent), line1, line2))

    def is_canceled(self):
        if HAS_KODI and self.dialog:
            return self.dialog.iscanceled()
        return False

    def close(self):
        if HAS_KODI and self.dialog:
            self.dialog.close()
            self.dialog = None
