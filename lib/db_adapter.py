# -*- coding: utf-8 -*-
"""
Database adapter supporting SQLite and MariaDB / MySQL.
"""
import re
import sqlite3
from lib.kodi_compat import log, is_kodi

try:
    import pymysql
except ImportError:
    from lib import pymysql


class BaseDatabaseAdapter:
    def connect(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    def query(self, sql, params=None):
        raise NotImplementedError

    def execute(self, sql, params=None):
        raise NotImplementedError

    def commit(self):
        raise NotImplementedError

    def rollback(self):
        raise NotImplementedError

    def get_tables(self):
        raise NotImplementedError


class SQLiteAdapter(BaseDatabaseAdapter):
    def __init__(self, db_path):
        self.db_path = db_path
        self.conn = None

    def connect(self):
        log("Connecting to SQLite database: {}".format(self.db_path))
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        return self

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _convert_sql(self, sql):
        # Convert %s placeholders to ?
        return sql.replace("%s", "?")

    def query(self, sql, params=None):
        sql = self._convert_sql(sql)
        cursor = self.conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def execute(self, sql, params=None):
        sql = self._convert_sql(sql)
        cursor = self.conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return cursor.lastrowid
        finally:
            cursor.close()

    def executemany(self, sql, params_list):
        if not params_list:
            return 0
        sql = self._convert_sql(sql)
        cursor = self.conn.cursor()
        try:
            cursor.executemany(sql, params_list)
            return cursor.rowcount
        finally:
            cursor.close()

    def commit(self):
        if self.conn:
            self.conn.commit()

    def rollback(self):
        if self.conn:
            self.conn.rollback()

    def get_tables(self):
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table'")
        return [r["name"] for r in rows]

    def get_display_name(self):
        import os
        return os.path.basename(self.db_path) if self.db_path else "SQLite"


def _get_kodi_max_video_schema():
    """Return the highest video DB schema version for the running Kodi major version, or None."""
    if not is_kodi():
        return None
    try:
        import xbmc
        ver_str = xbmc.getInfoLabel("System.BuildVersion") or ""
        m = re.match(r"^\s*(\d+)\.", ver_str)
        if not m:
            return None
        major = int(m.group(1))
        if major <= 21:
            return 131
        if major == 22:
            return 150
        return None  # Kodi 23+ uses latest
    except Exception:
        return None


def list_mysql_video_databases(host, port, user, password, base_name=None):
    """List video databases on the MySQL/MariaDB server, sorted newest schema first."""
    temp_conn = pymysql.connect(host=host, port=int(port or 3306), user=user, password=password)
    try:
        cursor = temp_conn.cursor()
        cursor.execute("SHOW DATABASES;")
        all_dbs = [row[0] for row in cursor.fetchall()]
        if base_name:
            base_clean = re.sub(r"\d+$", "", base_name)
            pat = re.compile(r"^" + re.escape(base_clean) + r"(\d+)$", re.IGNORECASE)
            matches = []
            for db in all_dbs:
                m = pat.match(db)
                if m:
                    matches.append((int(m.group(1)), db))
            if matches:
                matches.sort(key=lambda x: x[0], reverse=True)
                return [m[1] for m in matches]
        # Fallback: any *videos* or MyVideos* DB
        vpat = re.compile(r".*videos?(\d+)$", re.IGNORECASE)
        matches = []
        for db in all_dbs:
            m = vpat.match(db)
            if m:
                matches.append((int(m.group(1)), db))
        matches.sort(key=lambda x: x[0], reverse=True)
        return [m[1] for m in matches]
    finally:
        temp_conn.close()


class MySQLAdapter(BaseDatabaseAdapter):
    def __init__(self, host, port, user, password, db_name):
        self.host = host
        self.port = int(port) if port else 3306
        self.user = user
        self.password = password
        self.db_name = db_name
        self.resolved_db_name = None
        self.conn = None

    def _resolve_target_database(self):
        """
        Connect without db first (or with specified name), and if name has no schema number,
        resolve the active database with the highest schema version.
        """
        log("Resolving MySQL database for base name '{}' at {}:{}".format(
            self.db_name, self.host, self.port
        ))
        temp_conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password
        )
        try:
            cursor = temp_conn.cursor()
            cursor.execute("SHOW DATABASES;")
            all_dbs = [row[0] for row in cursor.fetchall()]

            # 1. Exact match
            if self.db_name in all_dbs:
                self.resolved_db_name = self.db_name
                return self.resolved_db_name

            # 2. Check for <db_name><version> (e.g. kodi22_videos154, MyVideos131)
            pattern = re.compile(r"^" + re.escape(self.db_name) + r"(\d+)$", re.IGNORECASE)
            candidates = []
            for db in all_dbs:
                m = pattern.match(db)
                if m:
                    candidates.append((int(m.group(1)), db))

            if candidates:
                candidates.sort(key=lambda x: x[0], reverse=True)
                max_schema = _get_kodi_max_video_schema()
                if max_schema is not None:
                    scoped = [c for c in candidates if c[0] <= max_schema]
                    if scoped:
                        self.resolved_db_name = scoped[0][1]
                        log("Resolved database for Kodi schema <= {}: {}".format(
                            max_schema, self.resolved_db_name
                        ))
                        return self.resolved_db_name
                self.resolved_db_name = candidates[0][1]
                log("Resolved database to latest version: {}".format(self.resolved_db_name))
                return self.resolved_db_name

            # Fallback to configured db_name
            self.resolved_db_name = self.db_name
            return self.resolved_db_name
        finally:
            temp_conn.close()

    def connect(self):
        if not self.resolved_db_name:
            self._resolve_target_database()

        log("Connecting to MariaDB/MySQL database '{}' on {}:{}".format(
            self.resolved_db_name, self.host, self.port
        ))
        self.conn = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.resolved_db_name,
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False
        )
        return self

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def get_display_name(self):
        db = self.resolved_db_name or self.db_name
        return "{} ({})".format(db, self.host) if self.host else db

    def _convert_sql(self, sql):
        # Convert ? placeholders to %s
        return sql.replace("?", "%s")

    def query(self, sql, params=None):
        sql = self._convert_sql(sql)
        cursor = self.conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return cursor.fetchall()
        finally:
            cursor.close()

    def execute(self, sql, params=None):
        sql = self._convert_sql(sql)
        cursor = self.conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            return cursor.lastrowid
        finally:
            cursor.close()

    def executemany(self, sql, params_list):
        if not params_list:
            return 0
        sql = self._convert_sql(sql)
        cursor = self.conn.cursor()
        try:
            cursor.executemany(sql, params_list)
            return cursor.rowcount
        finally:
            cursor.close()

    def commit(self):
        if self.conn:
            self.conn.commit()

    def rollback(self):
        if self.conn:
            self.conn.rollback()

    def get_tables(self):
        cursor = self.conn.cursor()
        try:
            cursor.execute("SHOW TABLES;")
            rows = cursor.fetchall()
            tables = []
            for r in rows:
                tables.extend(r.values())
            return tables
        finally:
            cursor.close()


def get_adapter_for_config(config):
    if config.db_type == "sqlite":
        return SQLiteAdapter(config.sqlite_path)
    elif config.db_type in ("mysql", "mariadb"):
        return MySQLAdapter(
            host=config.host,
            port=config.port,
            user=config.user,
            password=config.password,
            db_name=config.name
        )
    raise ValueError("Unsupported database type: {}".format(config.db_type))
