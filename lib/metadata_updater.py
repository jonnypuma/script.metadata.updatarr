# -*- coding: utf-8 -*-
"""
Missing metadata scanner and updater for Kodi:
- Season plots & names (seasons.plot, seasons.name)
- Missing movie, TV show, and episode plots & taglines (c01, c03)
Priority: Local .nfo files -> TMDbHelper / TMDB API fallback.
"""
import os
import re
import html
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from lib.kodi_compat import log, file_exists, read_text_file
from lib.backup_manager import BackupManager
from lib.tmdbhelper_client import TMDbFallbackClient


class MetadataChange:
    def __init__(self, target_table, id_col, id_val, media_label, fields, source):
        self.target_table = target_table  # 'seasons', 'movie', 'tvshow', 'episode'
        self.id_col = id_col              # 'idSeason', 'idMovie', 'idShow', 'idEpisode'
        self.id_val = id_val
        self.media_label = media_label    # e.g. 'Penny Dreadful - Season 1'
        self.fields = fields              # dict of col -> new_value
        self.source = source              # 'NFO' or 'TMDbHelper'

    def __repr__(self):
        return "<MetadataChange [{}] {} ({}) via {}>".format(
            self.target_table, self.media_label, ", ".join(self.fields.keys()), self.source
        )


def _clean_text(val):
    if not val:
        return ""
    text = html.unescape(val.strip())
    return re.sub(r"[ \t]+", " ", text).strip()


def _parse_nfo_fields(xml_content):
    """Extract plot, tagline, title, and season maps from an NFO XML string."""
    result = {"plot": "", "tagline": "", "title": "", "named_seasons": {}, "season_plots": {}}
    if not xml_content or not xml_content.strip():
        return result
    try:
        root = ET.fromstring(xml_content)
        result["plot"] = _clean_text(root.findtext("plot") or root.findtext("outline"))
        result["tagline"] = _clean_text(root.findtext("tagline"))
        result["title"] = _clean_text(root.findtext("title"))

        for ns in root.findall("namedseason"):
            num = ns.get("number")
            if num is not None and ns.text:
                try:
                    result["named_seasons"][int(num)] = _clean_text(ns.text)
                except ValueError:
                    pass

        for st in root.findall("season"):
            num = st.get("number")
            if num is not None:
                try:
                    s_num = int(num)
                    s_plot = _clean_text(st.findtext("plot"))
                    s_name = _clean_text(st.findtext("name") or st.findtext("title"))
                    if s_plot or s_name:
                        result["season_plots"][s_num] = {"plot": s_plot, "name": s_name}
                except ValueError:
                    pass
    except Exception as e:
        log("XML parse fallback in metadata NFO: {}".format(e), "debug")
        m_plot = re.search(r"<plot>\s*(.*?)\s*</plot>", xml_content, re.IGNORECASE | re.DOTALL)
        if m_plot:
            result["plot"] = _clean_text(m_plot.group(1))
        m_tag = re.search(r"<tagline>\s*(.*?)\s*</tagline>", xml_content, re.IGNORECASE | re.DOTALL)
        if m_tag:
            result["tagline"] = _clean_text(m_tag.group(1))
    return result


class MetadataUpdater:
    def __init__(self, adapter, settings):
        self.adapter = adapter
        self.settings = settings
        self.backup_mgr = BackupManager(self.adapter, retention=self.settings.backup_retention)
        self.tmdb = TMDbFallbackClient(
            api_key=getattr(settings, "tmdb_api_key", ""),
            language=getattr(settings, "tmdb_language", "en-US")
        )
        self._tvshow_nfo_cache = {}

    def _read_tvshow_nfo(self, show_path):
        if not show_path:
            return {"plot": "", "tagline": "", "title": "", "named_seasons": {}, "season_plots": {}}
        base = show_path.rstrip("/\\") + "/"
        if base in self._tvshow_nfo_cache:
            return self._tvshow_nfo_cache[base]
        nfo_path = base + "tvshow.nfo"
        parsed = {"plot": "", "tagline": "", "title": "", "named_seasons": {}, "season_plots": {}}
        try:
            if file_exists(nfo_path):
                parsed = _parse_nfo_fields(read_text_file(nfo_path))
        except Exception:
            pass
        self._tvshow_nfo_cache[base] = parsed
        return parsed

    def _read_season_nfo(self, show_path, season_num):
        if not show_path:
            return {"plot": "", "title": ""}
        base = show_path.rstrip("/\\") + "/"
        candidates = (
            ["season-specials.nfo", "season00.nfo"]
            if season_num == 0
            else ["season{:02d}.nfo".format(season_num), "season{}.nfo".format(season_num)]
        )
        for fname in candidates:
            p = base + fname
            try:
                if file_exists(p):
                    data = _parse_nfo_fields(read_text_file(p))
                    if data["plot"] or data["title"]:
                        return data
            except Exception:
                pass
        return {"plot": "", "title": ""}

    def _fetch_seasons_needing_metadata(self):
        only_missing = getattr(self.settings, "only_missing_season_plots", True)
        include_specials = getattr(self.settings, "include_specials", False)
        min_season = 0 if include_specials else 1

        where_missing = (
            "AND (s.plot IS NULL OR TRIM(s.plot) = '' OR s.name IS NULL OR TRIM(s.name) = '')"
            if only_missing else ""
        )
        sql = """
        SELECT
            s.idSeason,
            s.idShow,
            s.season,
            s.name AS cur_name,
            s.plot AS cur_plot,
            t.c00 AS show_title,
            u.value AS tmdb_id,
            MIN(p.strPath) AS show_path
        FROM seasons s
        JOIN tvshow t ON s.idShow = t.idShow
        LEFT JOIN uniqueid u ON u.media_id = t.idShow AND u.media_type = 'tvshow' AND u.type = 'tmdb'
        LEFT JOIN tvshowlinkpath tlp ON tlp.idShow = t.idShow
        LEFT JOIN path p ON tlp.idPath = p.idPath
        WHERE s.season >= %s
          {where_missing}
        GROUP BY s.idSeason, s.idShow, s.season, s.name, s.plot, t.c00, u.value
        ORDER BY t.c00 ASC, s.season ASC
        """.format(where_missing=where_missing)
        try:
            return self.adapter.query(sql, (min_season,))
        except Exception:
            # Fallback if tvshowlinkpath isn't populated (e.g. minimal SQLite test DB)
            sql_simple = """
            SELECT
                s.idSeason, s.idShow, s.season, s.name AS cur_name, s.plot AS cur_plot,
                t.c00 AS show_title, u.value AS tmdb_id, '' AS show_path
            FROM seasons s
            JOIN tvshow t ON s.idShow = t.idShow
            LEFT JOIN uniqueid u ON u.media_id = t.idShow AND u.media_type = 'tvshow' AND u.type = 'tmdb'
            WHERE s.season >= %s {where_missing}
            ORDER BY t.c00 ASC, s.season ASC
            """.format(where_missing=where_missing)
            return self.adapter.query(sql_simple, (min_season,))

    def _inspect_season(self, row):
        season_num = int(row["season"])
        cur_plot = (row.get("cur_plot") or "").strip()
        cur_name = (row.get("cur_name") or "").strip()
        show_path = row.get("show_path") or ""
        tmdb_id = (row.get("tmdb_id") or "").strip()
        update_names = getattr(self.settings, "update_season_names", True)
        only_missing = getattr(self.settings, "only_missing_season_plots", True)

        need_plot = (not cur_plot) or (not only_missing)
        is_generic_name = (not cur_name) or cur_name.lower() == "season {}".format(season_num)
        need_name = update_names and (is_generic_name or not only_missing)

        if not need_plot and not need_name:
            return None

        new_plot = ""
        new_name = ""
        sources = []

        # 1. Local NFOs
        if getattr(self.settings, "season_scan_nfo", True) and show_path:
            s_nfo = self._read_season_nfo(show_path, season_num)
            tv_nfo = self._read_tvshow_nfo(show_path)
            if need_plot:
                new_plot = s_nfo.get("plot") or tv_nfo["season_plots"].get(season_num, {}).get("plot", "")
            if need_name:
                new_name = (
                    s_nfo.get("title")
                    or tv_nfo["named_seasons"].get(season_num, "")
                    or tv_nfo["season_plots"].get(season_num, {}).get("name", "")
                )
            if new_plot or new_name:
                sources.append("NFO")

        # 2. TMDbHelper / TMDB fallback
        if getattr(self.settings, "season_scan_tmdb", True) and tmdb_id:
            still_need_plot = need_plot and not new_plot
            still_need_name = need_name and not new_name
            if (still_need_plot or still_need_name) and self.tmdb.is_available():
                td = self.tmdb.get_season_details(tmdb_id, season_num)
                if td:
                    if still_need_plot and td.get("plot"):
                        new_plot = td["plot"]
                    if still_need_name and td.get("name"):
                        new_name = td["name"]
                    if new_plot or new_name:
                        sources.append("TMDbHelper")

        fields = {}
        if need_plot and new_plot and new_plot != cur_plot:
            fields["plot"] = new_plot
        if need_name and new_name and new_name != cur_name:
            fields["name"] = new_name

        if not fields:
            return None

        label = u"{} - Season {}".format(row.get("show_title") or "Show", season_num)
        return MetadataChange(
            target_table="seasons",
            id_col="idSeason",
            id_val=row["idSeason"],
            media_label=label,
            fields=fields,
            source="+".join(dict.fromkeys(sources)) or "NFO"
        )

    def _fetch_movies_missing_metadata(self):
        sql = """
        SELECT
            m.idMovie, m.c00 AS title, m.c01 AS cur_plot, m.c03 AS cur_tagline,
            f.strFilename, p.strPath, u.value AS tmdb_id
        FROM movie m
        JOIN files f ON m.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN uniqueid u ON u.media_id = m.idMovie AND u.media_type = 'movie' AND u.type = 'tmdb'
        WHERE (m.c01 IS NULL OR TRIM(m.c01) = '' OR m.c03 IS NULL OR TRIM(m.c03) = '')
        ORDER BY m.c00 ASC
        """
        return self.adapter.query(sql)

    def _inspect_movie_metadata(self, row):
        cur_plot = (row.get("cur_plot") or "").strip()
        cur_tagline = (row.get("cur_tagline") or "").strip()
        need_plot = not cur_plot
        need_tagline = not cur_tagline
        if not need_plot and not need_tagline:
            return None

        path = (row.get("strPath") or "").rstrip("/\\") + "/"
        fname = row.get("strFilename") or ""
        tmdb_id = (row.get("tmdb_id") or "").strip()

        new_plot = ""
        new_tagline = ""
        sources = []

        if getattr(self.settings, "season_scan_nfo", True) and path:
            base_name = os.path.splitext(fname)[0]
            for nfo in (path + base_name + ".nfo", path + "movie.nfo"):
                try:
                    if file_exists(nfo):
                        d = _parse_nfo_fields(read_text_file(nfo))
                        if need_plot and not new_plot and d["plot"]:
                            new_plot = d["plot"]
                        if need_tagline and not new_tagline and d["tagline"]:
                            new_tagline = d["tagline"]
                        if new_plot and new_tagline:
                            break
                except Exception:
                    pass
            if new_plot or new_tagline:
                sources.append("NFO")

        if getattr(self.settings, "season_scan_tmdb", True) and tmdb_id:
            if ((need_plot and not new_plot) or (need_tagline and not new_tagline)) and self.tmdb.is_available():
                td = self.tmdb.get_movie_details(tmdb_id)
                if td:
                    if need_plot and not new_plot and td.get("plot"):
                        new_plot = td["plot"]
                    if need_tagline and not new_tagline and td.get("tagline"):
                        new_tagline = td["tagline"]
                    if new_plot or new_tagline:
                        sources.append("TMDbHelper")

        fields = {}
        if need_plot and new_plot:
            fields["c01"] = new_plot
        if need_tagline and new_tagline:
            fields["c03"] = new_tagline
        if not fields:
            return None

        return MetadataChange(
            target_table="movie",
            id_col="idMovie",
            id_val=row["idMovie"],
            media_label=row.get("title") or fname,
            fields=fields,
            source="+".join(dict.fromkeys(sources)) or "NFO"
        )

    def scan_missing_metadata(self, progress_callback=None, include_seasons=True, include_movies=True):
        """Scan for missing season plots/names and missing movie plots/taglines."""
        self._tvshow_nfo_cache.clear()
        work = []

        if include_seasons:
            seasons = self._fetch_seasons_needing_metadata()
            for s in seasons:
                work.append(("season", s))

        if include_movies:
            try:
                movies = self._fetch_movies_missing_metadata()
                for m in movies:
                    work.append(("movie", m))
            except Exception:
                pass

        total = len(work)
        changes = []
        if total == 0:
            return 0, []

        batch_size = 20
        with ThreadPoolExecutor(max_workers=4) as executor:
            processed = 0
            for start in range(0, total, batch_size):
                batch = work[start:start + batch_size]
                futures = {}
                for kind, row in batch:
                    fn = self._inspect_season if kind == "season" else self._inspect_movie_metadata
                    futures[executor.submit(fn, row)] = (kind, row)

                for fut in as_completed(futures):
                    processed += 1
                    try:
                        ch = fut.result()
                        if ch:
                            changes.append(ch)
                    except Exception as e:
                        log("Metadata inspect error: {}".format(e), "debug")

                if progress_callback:
                    last_kind, last_row = batch[-1]
                    lbl = last_row.get("show_title") or last_row.get("title") or ""
                    if progress_callback(processed, total, lbl, len(changes)) is False:
                        break

        if progress_callback:
            progress_callback(total, total, "Metadata scan complete", len(changes))
        return total, changes

    def apply_metadata_changes(self, changes, progress_callback=None):
        """Take a backup and apply MetadataChange updates inside a transaction."""
        if not changes:
            return 0, None

        backup_info = self.backup_mgr.create_backup()
        applied = 0
        total = len(changes)
        try:
            for idx, ch in enumerate(changes):
                if progress_callback and idx % 10 == 0:
                    if progress_callback(idx, total, ch.media_label) is False:
                        self.adapter.rollback()
                        raise RuntimeError("Metadata update cancelled by user.")

                set_clauses = []
                params = []
                for col, val in ch.fields.items():
                    set_clauses.append("{} = %s".format(col))
                    params.append(val)
                params.append(ch.id_val)

                sql = "UPDATE {} SET {} WHERE {} = %s".format(
                    ch.target_table, ", ".join(set_clauses), ch.id_col
                )
                self.adapter.execute(sql, tuple(params))
                applied += 1

            self.adapter.commit()
            log("Applied {} missing metadata updates.".format(applied))
            return applied, backup_info
        except Exception as e:
            log("Error applying metadata updates, rolling back: {}".format(e), "error")
            self.adapter.rollback()
            raise
