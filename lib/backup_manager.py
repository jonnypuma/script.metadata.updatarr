# -*- coding: utf-8 -*-
"""
Backup and Restore manager for Kodi video library databases (SQLite and MySQL/MariaDB).
Maintains at least 3 backups at all times and enables safe database restoration.
"""
import os
import re
from datetime import datetime
from lib.kodi_compat import (
    translate_path, make_dirs, copy_file, delete_file, list_dir, log
)
from lib.db_adapter import SQLiteAdapter, MySQLAdapter

MIN_BACKUPS_RETAINED = 3


class BackupInfo:
    def __init__(self, identifier, timestamp_str, label, extra_info=""):
        self.identifier = identifier  # File path (SQLite) or timestamp token (MySQL)
        self.timestamp_str = timestamp_str
        self.label = label
        self.extra_info = extra_info

    def __repr__(self):
        return "<BackupInfo {} ({})>".format(self.label, self.identifier)


class BackupManager:
    def __init__(self, adapter, retention=5):
        self.adapter = adapter
        self.retention = max(MIN_BACKUPS_RETAINED, int(retention))
        self.backup_dir = translate_path("special://profile/addon_data/script.metadata.updatarr/backups/")

    def _ensure_backup_dir(self):
        make_dirs(self.backup_dir)

    # -------------------------------------------------------------
    # SQLite Backup and Restore
    # -------------------------------------------------------------
    def _create_sqlite_backup(self):
        self._ensure_backup_dir()
        db_path = self.adapter.db_path
        if not os.path.exists(db_path):
            raise FileNotFoundError("Active SQLite database file not found at: {}".format(db_path))

        base_name = os.path.basename(db_path)
        name_root, ext = os.path.splitext(base_name)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_filename = "{}_bak_{}{}".format(name_root, ts, ext)
        bak_path = os.path.join(self.backup_dir, bak_filename)

        log("Creating SQLite database backup to: {}".format(bak_path))
        copy_file(db_path, bak_path)

        if not os.path.exists(bak_path) or os.path.getsize(bak_path) == 0:
            raise RuntimeError("Backup copy failed or backup file is empty.")

        self._prune_sqlite_backups()
        return BackupInfo(
            identifier=bak_path,
            timestamp_str=ts,
            label="SQLite Backup {}".format(ts),
            extra_info="{} KB".format(os.path.getsize(bak_path) // 1024)
        )

    def _list_sqlite_backups(self):
        self._ensure_backup_dir()
        _, files = list_dir(self.backup_dir)
        pattern = re.compile(r"^(.+)_bak_(\d{8}_\d{6})\.db$", re.IGNORECASE)
        backups = []
        for f in files:
            m = pattern.match(f)
            if m:
                ts_str = m.group(2)
                full_path = os.path.join(self.backup_dir, f)
                size_kb = os.path.getsize(full_path) // 1024 if os.path.exists(full_path) else 0
                try:
                    dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
                    formatted_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    formatted_date = ts_str

                backups.append(BackupInfo(
                    identifier=full_path,
                    timestamp_str=ts_str,
                    label="{} ({})".format(formatted_date, f),
                    extra_info="{} KB".format(size_kb)
                ))

        backups.sort(key=lambda b: b.timestamp_str, reverse=True)
        return backups

    def _restore_sqlite_backup(self, backup_item):
        target_path = self.adapter.db_path
        src_path = backup_item.identifier

        if not os.path.exists(src_path):
            raise FileNotFoundError("Backup file not found: {}".format(src_path))

        # Close existing DB connection before replacing file
        self.adapter.close()

        # Create emergency pre-restore snapshot
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        emergency_bak = target_path + ".pre_restore_{}.bak".format(ts)
        if os.path.exists(target_path):
            copy_file(target_path, emergency_bak)

        log("Restoring SQLite DB from '{}' to '{}'".format(src_path, target_path))
        copy_file(src_path, target_path)

        # Reconnect
        self.adapter.connect()
        return True

    def _prune_sqlite_backups(self):
        backups = self._list_sqlite_backups()
        if len(backups) > self.retention:
            to_delete = backups[self.retention:]
            for item in to_delete:
                log("Pruning old SQLite backup (retaining min {}): {}".format(self.retention, item.identifier))
                delete_file(item.identifier)

        # Also clean up old pre-restore safety snapshots (keep latest 2)
        _, files = list_dir(self.backup_dir)
        pre_files = sorted([f for f in files if "pre_restore" in f], reverse=True)
        if len(pre_files) > 2:
            for old_pre in pre_files[2:]:
                delete_file(os.path.join(self.backup_dir, old_pre))

    # -------------------------------------------------------------
    # MySQL / MariaDB Backup and Restore
    # -------------------------------------------------------------
    def _create_mysql_backup(self, extra_tables=None):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        v_bak = "videoversion_bak_{}".format(ts)
        vt_bak = "videoversiontype_bak_{}".format(ts)
        tables = self.adapter.get_tables()

        base_tables = ["videoversion", "videoversiontype"]
        if "seasons" in tables:
            base_tables.append("seasons")
        for et in (extra_tables or []):
            if et in tables and et not in base_tables:
                base_tables.append(et)

        backed_tables = []
        for tbl in base_tables:
            bak_tbl = "{}_bak_{}".format(tbl, ts)
            self.adapter.execute("CREATE TABLE IF NOT EXISTS `{}` LIKE `{}`;".format(bak_tbl, tbl))
            self.adapter.execute("INSERT INTO `{}` SELECT * FROM `{}`;".format(bak_tbl, tbl))
            backed_tables.append(bak_tbl)

        log("Creating MySQL/MariaDB backup tables: {}".format(", ".join(backed_tables)))
        self.adapter.commit()
        self._prune_mysql_backups()

        return BackupInfo(
            identifier=ts,
            timestamp_str=ts,
            label="MariaDB/MySQL Backup {}".format(ts),
            extra_info="Tables: {}".format(", ".join(backed_tables))
        )

    def _list_mysql_backups(self):
        tables = self.adapter.get_tables()
        pattern = re.compile(r"^videoversion_bak_(\d{8}_\d{6})$", re.IGNORECASE)
        backups = []

        for tbl in tables:
            m = pattern.match(tbl)
            if m:
                ts_str = m.group(1)
                vt_tbl = "videoversiontype_bak_{}".format(ts_str)
                has_vt = vt_tbl in tables

                try:
                    dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
                    formatted_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                except ValueError:
                    formatted_date = ts_str

                # Get row counts
                counts = self.adapter.query("SELECT COUNT(*) AS c FROM `{}`".format(tbl))
                row_count = counts[0]["c"] if counts else 0

                backups.append(BackupInfo(
                    identifier=ts_str,
                    timestamp_str=ts_str,
                    label="{} ({} rows)".format(formatted_date, row_count),
                    extra_info="videoversion: {}, videoversiontype: {}".format(
                        tbl, vt_tbl if has_vt else "missing"
                    )
                ))

        backups.sort(key=lambda b: b.timestamp_str, reverse=True)
        return backups

    def _restore_mysql_backup(self, backup_item):
        ts = backup_item.identifier
        v_bak = "videoversion_bak_{}".format(ts)
        vt_bak = "videoversiontype_bak_{}".format(ts)
        s_bak = "seasons_bak_{}".format(ts)

        tables = self.adapter.get_tables()
        if v_bak not in tables:
            raise RuntimeError("Backup table '{}' does not exist in database.".format(v_bak))

        # Create emergency pre-restore snapshot
        pre_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        pre_v = "videoversion_bak_pre_restore_{}".format(pre_ts)
        pre_vt = "videoversiontype_bak_pre_restore_{}".format(pre_ts)
        pre_s = "seasons_bak_pre_restore_{}".format(pre_ts)

        log("Creating safety pre-restore MySQL tables: {} and {}".format(pre_v, pre_vt))
        self.adapter.execute("CREATE TABLE IF NOT EXISTS `{}` LIKE `videoversion`;".format(pre_v))
        self.adapter.execute("INSERT INTO `{}` SELECT * FROM `videoversion`;".format(pre_v))
        self.adapter.execute("CREATE TABLE IF NOT EXISTS `{}` LIKE `videoversiontype`;".format(pre_vt))
        self.adapter.execute("INSERT INTO `{}` SELECT * FROM `videoversiontype`;".format(pre_vt))
        if "seasons" in tables:
            self.adapter.execute("CREATE TABLE IF NOT EXISTS `{}` LIKE `seasons`;".format(pre_s))
            self.adapter.execute("INSERT INTO `{}` SELECT * FROM `seasons`;".format(pre_s))
        self.adapter.commit()

        log("Restoring MySQL tables from {} and {}".format(v_bak, vt_bak))
        # Clear and restore videoversion
        self.adapter.execute("DELETE FROM `videoversion`;")
        self.adapter.execute("INSERT INTO `videoversion` SELECT * FROM `{}`;".format(v_bak))

        # Clear and restore videoversiontype if snapshot exists
        if vt_bak in tables:
            self.adapter.execute("DELETE FROM `videoversiontype`;")
            self.adapter.execute("INSERT INTO `videoversiontype` SELECT * FROM `{}`;".format(vt_bak))

        # Clear and restore any additional metadata snapshots (seasons, movie, tvshow, episode, streamdetails)
        for extra_tbl in ("seasons", "movie", "tvshow", "episode", "streamdetails"):
            e_bak = "{}_bak_{}".format(extra_tbl, ts)
            if e_bak in tables and extra_tbl in tables:
                self.adapter.execute("DELETE FROM `{}`;".format(extra_tbl))
                self.adapter.execute("INSERT INTO `{}` SELECT * FROM `{}`;".format(extra_tbl, e_bak))

        self.adapter.commit()
        return True

    def _prune_mysql_backups(self):
        backups = self._list_mysql_backups()
        if len(backups) > self.retention:
            to_delete = backups[self.retention:]
            for item in to_delete:
                ts = item.identifier
                log("Pruning old MySQL backup snapshot (retaining min {}): {}".format(
                    self.retention, ts
                ))
                try:
                    for tbl in ("videoversion", "videoversiontype", "seasons", "movie", "tvshow", "episode", "streamdetails"):
                        self.adapter.execute("DROP TABLE IF EXISTS `{}_bak_{}`;".format(tbl, ts))
                    self.adapter.commit()
                except Exception as e:
                    log("Error dropping old backup tables: {}".format(e), "warning")

        # Also clean up old pre-restore safety tables (keep latest 2)
        tables = self.adapter.get_tables()
        pre_v = sorted([t for t in tables if t.startswith("videoversion_bak_pre_restore_")], reverse=True)
        if len(pre_v) > 2:
            for old_pre in pre_v[2:]:
                ts = old_pre[len("videoversion_bak_pre_restore_"):]
                vt = "videoversiontype_bak_pre_restore_{}".format(ts)
                try:
                    self.adapter.execute("DROP TABLE IF EXISTS `{}`;".format(old_pre))
                    self.adapter.execute("DROP TABLE IF EXISTS `{}`;".format(vt))
                    self.adapter.commit()
                except Exception as e:
                    log("Error dropping old pre-restore tables: {}".format(e), "warning")

    # -------------------------------------------------------------
    # Public Unified Interface
    # -------------------------------------------------------------
    def create_backup(self, extra_tables=None):
        """Creates a backup snapshot. Ensures at least 3 backups are retained."""
        if isinstance(self.adapter, SQLiteAdapter):
            return self._create_sqlite_backup()
        elif isinstance(self.adapter, MySQLAdapter):
            return self._create_mysql_backup(extra_tables=extra_tables)
        raise ValueError("Unsupported adapter for backup: {}".format(type(self.adapter)))

    def list_backups(self):
        """Returns sorted list of available BackupInfo items."""
        if isinstance(self.adapter, SQLiteAdapter):
            return self._list_sqlite_backups()
        elif isinstance(self.adapter, MySQLAdapter):
            return self._list_mysql_backups()
        return []

    def restore_backup(self, backup_item):
        """Restores a selected BackupInfo item."""
        if isinstance(self.adapter, SQLiteAdapter):
            return self._restore_sqlite_backup(backup_item)
        elif isinstance(self.adapter, MySQLAdapter):
            return self._restore_mysql_backup(backup_item)
        raise ValueError("Unsupported adapter for restore: {}".format(type(self.adapter)))
