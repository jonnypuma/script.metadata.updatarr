# -*- coding: utf-8 -*-
"""
Configuration reader and Kodi database detector.
"""
import os
import re
import xml.etree.ElementTree as ET
from lib.kodi_compat import (
    get_addon, translate_path, file_exists, read_text_file, list_dir, log
)


class DatabaseConfig:
    def __init__(self, db_type="sqlite", host=None, port=3306, name="MyVideos", user=None, password=None, sqlite_path=None):
        self.db_type = db_type.lower()  # 'sqlite' or 'mysql'
        self.host = host
        self.port = int(port) if port else 3306
        self.name = name
        self.user = user
        self.password = password
        self.sqlite_path = sqlite_path

    def __repr__(self):
        if self.db_type == "sqlite":
            return "<DatabaseConfig SQLite path='{}'>".format(self.sqlite_path)
        return "<DatabaseConfig MySQL host='{}:{}' db='{}' user='{}'>".format(
            self.host, self.port, self.name, self.user
        )


def _get_bool(addon, setting_id, default=True):
    try:
        val = addon.getSetting(setting_id)
        if isinstance(val, str):
            if val.lower() in ("true", "1", "yes"):
                return True
            if val.lower() in ("false", "0", "no"):
                return False
        return addon.getSettingBool(setting_id)
    except Exception:
        return default


def _get_int(addon, setting_id, default=0):
    try:
        val = addon.getSetting(setting_id)
        if isinstance(val, str) and val.strip().isdigit():
            return int(val.strip())
        return addon.getSettingInt(setting_id)
    except Exception:
        return default


def _get_str(addon, setting_id, default=""):
    try:
        val = addon.getSetting(setting_id)
        return val if val is not None and val != "" else default
    except Exception:
        return default


class AddonSettings:
    def __init__(self):
        addon = get_addon()
        if addon:
            self.scan_movies = _get_bool(addon, "scan_movies", True)
            self.scan_episodes = _get_bool(addon, "scan_episodes", True)
            self.sync_watched_on_swap = _get_bool(addon, "sync_watched_on_swap", True)
            self.scan_nfo = _get_bool(addon, "scan_nfo", True)
            self.scan_filenames = _get_bool(addon, "scan_filenames", True)
            self.only_standard_edition = _get_bool(addon, "only_standard_edition", True)
            self.incremental_scan = _get_bool(addon, "incremental_scan", True)
            self.label_multiversion_quality = _get_bool(addon, "label_multiversion_quality", True)
            self.notify_on_update = _get_bool(addon, "notify_on_update", True)

            # Missing Metadata / Season Plots
            self.season_scan_nfo = _get_bool(addon, "season_scan_nfo", True)
            self.season_scan_tmdb = _get_bool(addon, "season_scan_tmdb", True)
            self.tmdb_api_key = _get_str(addon, "tmdb_api_key", "")
            self.tmdb_language = _get_str(addon, "tmdb_language", "en-US")
            self.only_missing_season_plots = _get_bool(addon, "only_missing_season_plots", True)
            self.update_season_names = _get_bool(addon, "update_season_names", True)
            self.include_specials = _get_bool(addon, "include_specials", False)
            self.update_missing_movie_plots = _get_bool(addon, "update_missing_movie_plots", True)
            self.update_missing_tv_metadata = _get_bool(addon, "update_missing_tv_metadata", True)
            self.update_missing_streamdetails = _get_bool(addon, "update_missing_streamdetails", True)

            # Schedule settings
            self.schedule_enabled = _get_bool(addon, "schedule_enabled", False)
            self.schedule_interval = _get_int(addon, "schedule_interval", 24)
            self.run_on_scan = _get_bool(addon, "run_on_scan", True)
            self.run_on_startup = _get_bool(addon, "run_on_startup", False)

            # Backups
            self.backup_retention = max(3, _get_int(addon, "backup_retention", 3))

            # Database settings
            self.db_auto_detect = _get_bool(addon, "db_auto_detect", True)
            self.db_name_override = _get_str(addon, "db_name_override", "").strip()
            self.db_type = _get_str(addon, "db_type", "mysql")
            self.db_host = _get_str(addon, "db_host", "localhost")
            self.db_port = _get_int(addon, "db_port", 3306)
            self.db_name = _get_str(addon, "db_name", "kodi22_videos")
            self.db_user = _get_str(addon, "db_user", "kodi")
            self.db_pass = _get_str(addon, "db_pass", "kodi")
        else:
            # Defaults for testing / CLI
            self.scan_movies = True
            self.scan_episodes = True
            self.sync_watched_on_swap = True
            self.scan_nfo = True
            self.scan_filenames = True
            self.only_standard_edition = True
            self.incremental_scan = True
            self.label_multiversion_quality = True
            self.notify_on_update = True
            self.season_scan_nfo = True
            self.season_scan_tmdb = True
            self.tmdb_api_key = ""
            self.tmdb_language = "en-US"
            self.only_missing_season_plots = True
            self.update_season_names = True
            self.include_specials = False
            self.update_missing_movie_plots = True
            self.update_missing_tv_metadata = True
            self.update_missing_streamdetails = True
            self.schedule_enabled = False
            self.schedule_interval = 24
            self.run_on_scan = True
            self.run_on_startup = False
            self.backup_retention = 3
            self.db_auto_detect = True
            self.db_name_override = ""
            self.db_type = "mysql"
            self.db_host = "192.168.0.26"
            self.db_port = 3306
            self.db_name = "kodi22_videos"
            self.db_user = "Kodi"
            self.db_pass = "Kodi"


def parse_advanced_settings():
    """Parse advancedsettings.xml if present to detect MySQL/MariaDB config."""
    adv_path = translate_path("special://profile/advancedsettings.xml")
    if not file_exists(adv_path):
        log("No advancedsettings.xml found at {}".format(adv_path), "debug")
        return None

    try:
        content = read_text_file(adv_path)
        root = ET.fromstring(content)
        videodb = root.find("videodatabase")
        if videodb is None:
            return None

        db_type = (videodb.findtext("type") or "").strip().lower()
        if db_type in ("mysql", "mariadb"):
            host = (videodb.findtext("host") or "localhost").strip()
            port = int((videodb.findtext("port") or "3306").strip())
            name = (videodb.findtext("name") or "MyVideos").strip()
            user = (videodb.findtext("user") or "kodi").strip()
            password = (videodb.findtext("pass") or "kodi").strip()
            return DatabaseConfig(
                db_type="mysql",
                host=host,
                port=port,
                name=name,
                user=user,
                password=password
            )
    except Exception as e:
        log("Error parsing advancedsettings.xml: {}".format(e), "warning")
    return None


def find_latest_sqlite_db():
    """Find the latest MyVideos*.db in special://database/."""
    db_dir = translate_path("special://database/")
    if not file_exists(db_dir):
        return None

    _, files = list_dir(db_dir)
    pattern = re.compile(r"^MyVideos(\d+)\.db$", re.IGNORECASE)
    candidates = []
    for f in files:
        m = pattern.match(f)
        if m:
            version = int(m.group(1))
            candidates.append((version, os.path.join(db_dir, f)))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        latest_version, latest_path = candidates[0]
        log("Found latest SQLite video database: MyVideos{}.db at {}".format(latest_version, latest_path))
        return DatabaseConfig(db_type="sqlite", sqlite_path=latest_path, name="MyVideos{}".format(latest_version))
    return None


def resolve_database_config(settings=None):
    """
    Resolve active database configuration using settings and auto-detection.
    """
    if settings is None:
        settings = AddonSettings()

    if settings.db_auto_detect:
        # Check advancedsettings.xml first for MySQL
        cfg = parse_advanced_settings()
        if cfg and cfg.db_type == "mysql":
            log("Auto-detected MySQL configuration from advancedsettings.xml")
            if getattr(settings, "db_name_override", ""):
                cfg.name = settings.db_name_override.strip()
                log("Using database name override: {}".format(cfg.name))
            return cfg

        # Otherwise look for SQLite database file
        cfg = find_latest_sqlite_db()
        if cfg:
            log("Auto-detected SQLite configuration")
            return cfg

    # Manual fallback from addon settings
    if settings.db_type == "sqlite":
        cfg = find_latest_sqlite_db()
        if not cfg:
            db_dir = translate_path("special://database/")
            path = os.path.join(db_dir, "MyVideos.db")
            cfg = DatabaseConfig(db_type="sqlite", sqlite_path=path, name="MyVideos")
        return cfg
    else:
        return DatabaseConfig(
            db_type="mysql",
            host=settings.db_host,
            port=settings.db_port,
            name=settings.db_name,
            user=settings.db_user,
            password=settings.db_pass
        )
