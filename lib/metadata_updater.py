# -*- coding: utf-8 -*-
"""
Comprehensive missing metadata scanner and updater for Kodi:
- Seasons: plot, name
- Movies: plot (c01), tagline (c03), runtime (c11), MPAA (c12), genres (c14 + genre_link),
          studios (c18 + studio_link), cast (actor_link), media info (streamdetails)
- TV Shows: plot (c01), genres (c08 + genre_link), MPAA (c13), studios (c14 + studio_link),
            cast (actor_link)
- Episodes: plot (c01), runtime (c09), cast (actor_link), media info (streamdetails)

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
    def __init__(
        self,
        target_table,
        id_col,
        id_val,
        media_label,
        fields=None,
        genres=None,
        studios=None,
        cast=None,
        streams=None,
        id_file=None,
        media_type=None,
        source="NFO"
    ):
        self.target_table = target_table  # 'seasons', 'movie', 'tvshow', 'episode'
        self.id_col = id_col              # 'idSeason', 'idMovie', 'idShow', 'idEpisode'
        self.id_val = id_val
        self.media_label = media_label
        self.fields = fields or {}        # dict of col -> new_value
        self.genres = genres or []
        self.studios = studios or []
        self.cast = cast or []            # list of {"name", "role", "order", "thumb"}
        self.streams = streams or []      # list of stream dicts for streamdetails
        self.id_file = id_file
        self.media_type = media_type or (
            "movie" if target_table == "movie" else ("tvshow" if target_table == "tvshow" else "episode")
        )
        self.source = source

    def summary_parts(self):
        parts = list(self.fields.keys())
        if self.genres:
            parts.append("genres({})".format(len(self.genres)))
        if self.studios:
            parts.append("studios({})".format(len(self.studios)))
        if self.cast:
            parts.append("cast({})".format(len(self.cast)))
        if self.streams:
            parts.append("streamdetails({})".format(len(self.streams)))
        return ", ".join(parts)

    def __repr__(self):
        return "<MetadataChange [{}] {} ({}) via {}>".format(
            self.target_table, self.media_label, self.summary_parts(), self.source
        )


def _clean_text(val):
    if not val:
        return ""
    text = html.unescape(val.strip())
    return re.sub(r"[ \t]+", " ", text).strip()


def _safe_int(val, default=0):
    try:
        return int(float(str(val).strip()))
    except Exception:
        return default


def _safe_float(val, default=0.0):
    try:
        return float(str(val).strip())
    except Exception:
        return default


def _parse_nfo_fields(xml_content):
    """Extract plot, tagline, mpaa, runtime, genres, studios, cast, streamdetails, and seasons from NFO XML."""
    result = {
        "plot": "",
        "tagline": "",
        "title": "",
        "mpaa": "",
        "runtime_sec": "",
        "genres": [],
        "studios": [],
        "cast": [],
        "streams": [],
        "named_seasons": {},
        "season_plots": {},
    }
    if not xml_content or not xml_content.strip():
        return result
    try:
        root = ET.fromstring(xml_content)
        result["plot"] = _clean_text(root.findtext("plot") or root.findtext("outline"))
        result["tagline"] = _clean_text(root.findtext("tagline"))
        result["title"] = _clean_text(root.findtext("title"))
        result["mpaa"] = _clean_text(root.findtext("mpaa"))

        rt_raw = _clean_text(root.findtext("runtime"))
        if rt_raw and rt_raw.isdigit():
            mins = int(rt_raw)
            if mins > 0:
                result["runtime_sec"] = str(mins * 60)

        for g in root.findall("genre"):
            if g.text:
                for part in g.text.split("/"):
                    gp = _clean_text(part)
                    if gp and gp not in result["genres"]:
                        result["genres"].append(gp)

        for st in root.findall("studio"):
            if st.text:
                for part in st.text.split("/"):
                    sp = _clean_text(part)
                    if sp and sp not in result["studios"]:
                        result["studios"].append(sp)

        for idx, act in enumerate(root.findall("actor")):
            aname = _clean_text(act.findtext("name"))
            if not aname:
                continue
            arole = _clean_text(act.findtext("role"))
            aorder = _safe_int(act.findtext("order"), idx)
            athumb = _clean_text(act.findtext("thumb"))
            result["cast"].append({"name": aname, "role": arole, "order": aorder, "thumb": athumb})

        # <fileinfo><streamdetails>
        sd = root.find("./fileinfo/streamdetails")
        if sd is not None:
            for v in sd.findall("video"):
                dur = _safe_int(v.findtext("durationinseconds"), 0)
                if dur > 0 and not result["runtime_sec"]:
                    result["runtime_sec"] = str(dur)
                codec = _clean_text(v.findtext("codec"))
                width = _safe_int(v.findtext("width"), 0)
                height = _safe_int(v.findtext("height"), 0)
                if codec or width or height or dur:
                    result["streams"].append({
                        "iStreamType": 0,
                        "strVideoCodec": codec,
                        "fVideoAspect": _safe_float(v.findtext("aspect"), 0.0),
                        "iVideoWidth": width,
                        "iVideoHeight": height,
                        "iVideoDuration": dur,
                        "strStereoMode": _clean_text(v.findtext("stereomode")),
                        "strVideoLanguage": _clean_text(v.findtext("language")),
                        "strHdrType": _clean_text(v.findtext("hdrtype")),
                    })
            for a in sd.findall("audio"):
                codec = _clean_text(a.findtext("codec"))
                ch = _safe_int(a.findtext("channels"), 0)
                lang = _clean_text(a.findtext("language"))
                if codec or ch or lang:
                    result["streams"].append({
                        "iStreamType": 1,
                        "strAudioCodec": codec,
                        "iAudioChannels": ch,
                        "strAudioLanguage": lang,
                    })
            for sub in sd.findall("subtitle"):
                lang = _clean_text(sub.findtext("language"))
                if lang:
                    result["streams"].append({
                        "iStreamType": 2,
                        "strSubtitleLanguage": lang,
                    })

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
            return _parse_nfo_fields("")
        base = show_path.rstrip("/\\") + "/"
        if base in self._tvshow_nfo_cache:
            return self._tvshow_nfo_cache[base]
        nfo_path = base + "tvshow.nfo"
        parsed = _parse_nfo_fields("")
        try:
            if file_exists(nfo_path):
                parsed = _parse_nfo_fields(read_text_file(nfo_path))
        except Exception:
            pass
        self._tvshow_nfo_cache[base] = parsed
        return parsed

    def _read_season_nfo(self, show_path, season_num):
        if not show_path:
            return _parse_nfo_fields("")
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
        return _parse_nfo_fields("")

    def _read_first_nfo(self, nfo_paths):
        for p in nfo_paths:
            try:
                if file_exists(p):
                    return _parse_nfo_fields(read_text_file(p))
            except Exception:
                pass
        return _parse_nfo_fields("")

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------
    def _get_table_set(self):
        return set(self.adapter.get_tables())

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
            s.idSeason, s.idShow, s.season, s.name AS cur_name, s.plot AS cur_plot,
            t.c00 AS show_title, u.value AS tmdb_id, MIN(p.strPath) AS show_path
        FROM seasons s
        JOIN tvshow t ON s.idShow = t.idShow
        LEFT JOIN uniqueid u ON u.media_id = t.idShow AND u.media_type = 'tvshow' AND u.type = 'tmdb'
        LEFT JOIN tvshowlinkpath tlp ON tlp.idShow = t.idShow
        LEFT JOIN path p ON tlp.idPath = p.idPath
        WHERE s.season >= %s {where_missing}
        GROUP BY s.idSeason, s.idShow, s.season, s.name, s.plot, t.c00, u.value
        ORDER BY t.c00 ASC, s.season ASC
        """.format(where_missing=where_missing)
        try:
            return self.adapter.query(sql, (min_season,))
        except Exception:
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

    def _fetch_movies_missing_metadata(self, tables):
        has_sd = "streamdetails" in tables
        has_al = "actor_link" in tables
        sd_join = "LEFT JOIN (SELECT DISTINCT idFile FROM streamdetails) sd ON m.idFile = sd.idFile" if has_sd else ""
        al_join = "LEFT JOIN (SELECT DISTINCT media_id FROM actor_link WHERE media_type = 'movie') al ON m.idMovie = al.media_id" if has_al else ""
        extra_conds = []
        if has_sd and getattr(self.settings, "update_missing_streamdetails", True):
            extra_conds.append("sd.idFile IS NULL")
        if has_al:
            extra_conds.append("al.media_id IS NULL")
        extra_sql = (" OR " + " OR ".join(extra_conds)) if extra_conds else ""

        sql = """
        SELECT
            m.idMovie, m.idFile, m.c00 AS title,
            m.c01 AS cur_plot, m.c03 AS cur_tagline, m.c11 AS cur_runtime,
            m.c12 AS cur_mpaa, m.c14 AS cur_genres, m.c18 AS cur_studios,
            f.strFilename, p.strPath, u.value AS tmdb_id,
            {sd_sel} AS has_sd, {al_sel} AS has_al
        FROM movie m
        JOIN files f ON m.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN uniqueid u ON u.media_id = m.idMovie AND u.media_type = 'movie' AND u.type = 'tmdb'
        {sd_join}
        {al_join}
        WHERE (
            m.c01 IS NULL OR TRIM(m.c01) = ''
            OR m.c03 IS NULL OR TRIM(m.c03) = ''
            OR m.c11 IS NULL OR TRIM(m.c11) = '' OR m.c11 = '0'
            OR m.c12 IS NULL OR TRIM(m.c12) = ''
            OR m.c14 IS NULL OR TRIM(m.c14) = ''
            OR m.c18 IS NULL OR TRIM(m.c18) = ''
            {extra_sql}
        )
        ORDER BY m.c00 ASC
        """.format(
            sd_sel="CASE WHEN sd.idFile IS NOT NULL THEN 1 ELSE 0 END" if has_sd else "1",
            al_sel="CASE WHEN al.media_id IS NOT NULL THEN 1 ELSE 0 END" if has_al else "1",
            sd_join=sd_join,
            al_join=al_join,
            extra_sql=extra_sql
        )
        return self.adapter.query(sql)

    def _fetch_tvshows_missing_metadata(self, tables):
        has_al = "actor_link" in tables
        al_join = "LEFT JOIN (SELECT DISTINCT media_id FROM actor_link WHERE media_type = 'tvshow') al ON t.idShow = al.media_id" if has_al else ""
        extra_sql = " OR al.media_id IS NULL" if has_al else ""

        sql = """
        SELECT
            t.idShow, t.c00 AS title,
            t.c01 AS cur_plot, t.c08 AS cur_genres, t.c13 AS cur_mpaa, t.c14 AS cur_studios,
            u.value AS tmdb_id, MIN(p.strPath) AS show_path,
            {al_sel} AS has_al
        FROM tvshow t
        LEFT JOIN uniqueid u ON u.media_id = t.idShow AND u.media_type = 'tvshow' AND u.type = 'tmdb'
        LEFT JOIN tvshowlinkpath tlp ON tlp.idShow = t.idShow
        LEFT JOIN path p ON tlp.idPath = p.idPath
        {al_join}
        WHERE (
            t.c01 IS NULL OR TRIM(t.c01) = ''
            OR t.c08 IS NULL OR TRIM(t.c08) = ''
            OR t.c13 IS NULL OR TRIM(t.c13) = ''
            OR t.c14 IS NULL OR TRIM(t.c14) = ''
            {extra_sql}
        )
        GROUP BY t.idShow, t.c00, t.c01, t.c08, t.c13, t.c14, u.value {al_grp}
        ORDER BY t.c00 ASC
        """.format(
            al_sel="CASE WHEN al.media_id IS NOT NULL THEN 1 ELSE 0 END" if has_al else "1",
            al_join=al_join,
            extra_sql=extra_sql,
            al_grp=", al.media_id" if has_al else ""
        )
        return self.adapter.query(sql)

    def _fetch_episodes_missing_metadata(self, tables):
        has_sd = "streamdetails" in tables
        sd_join = "LEFT JOIN (SELECT DISTINCT idFile FROM streamdetails) sd ON e.idFile = sd.idFile" if has_sd else ""
        extra_sql = " OR sd.idFile IS NULL" if (has_sd and getattr(self.settings, "update_missing_streamdetails", True)) else ""

        sql = """
        SELECT
            e.idEpisode, e.idFile, e.idShow, e.c00 AS ep_title, e.c01 AS cur_plot, e.c09 AS cur_runtime,
            e.c12 AS season_num, e.c13 AS episode_num,
            t.c00 AS show_title, f.strFilename, p.strPath, u.value AS show_tmdb_id,
            {sd_sel} AS has_sd
        FROM episode e
        JOIN tvshow t ON e.idShow = t.idShow
        JOIN files f ON e.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN uniqueid u ON u.media_id = t.idShow AND u.media_type = 'tvshow' AND u.type = 'tmdb'
        {sd_join}
        WHERE (
            e.c01 IS NULL OR TRIM(e.c01) = ''
            OR e.c09 IS NULL OR TRIM(e.c09) = '' OR e.c09 = '0'
            {extra_sql}
        )
        ORDER BY t.c00 ASC, e.idEpisode ASC
        """.format(
            sd_sel="CASE WHEN sd.idFile IS NOT NULL THEN 1 ELSE 0 END" if has_sd else "1",
            sd_join=sd_join,
            extra_sql=extra_sql
        )
        return self.adapter.query(sql)

    # ------------------------------------------------------------------
    # Inspectors
    # ------------------------------------------------------------------
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

        new_plot, new_name, sources = "", "", []
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

        if getattr(self.settings, "season_scan_tmdb", True) and tmdb_id:
            still_plot = need_plot and not new_plot
            still_name = need_name and not new_name
            if (still_plot or still_name) and self.tmdb.is_available():
                td = self.tmdb.get_season_details(tmdb_id, season_num)
                if td:
                    if still_plot and td.get("plot"):
                        new_plot = td["plot"]
                    if still_name and td.get("name"):
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

        return MetadataChange(
            target_table="seasons",
            id_col="idSeason",
            id_val=row["idSeason"],
            media_label=u"{} - Season {}".format(row.get("show_title") or "Show", season_num),
            fields=fields,
            source="+".join(dict.fromkeys(sources)) or "NFO"
        )

    def _inspect_movie_metadata(self, row):
        need_plot = not (row.get("cur_plot") or "").strip()
        need_tagline = not (row.get("cur_tagline") or "").strip()
        cur_rt = (row.get("cur_runtime") or "").strip()
        need_runtime = (not cur_rt or cur_rt == "0")
        need_mpaa = not (row.get("cur_mpaa") or "").strip()
        need_genres = not (row.get("cur_genres") or "").strip()
        need_studios = not (row.get("cur_studios") or "").strip()
        need_cast = not bool(row.get("has_al", 1))
        need_sd = (not bool(row.get("has_sd", 1))) and getattr(self.settings, "update_missing_streamdetails", True)

        if not any((need_plot, need_tagline, need_runtime, need_mpaa, need_genres, need_studios, need_cast, need_sd)):
            return None

        path = (row.get("strPath") or "").rstrip("/\\") + "/"
        fname = row.get("strFilename") or ""
        tmdb_id = (row.get("tmdb_id") or "").strip()

        nfo_data = _parse_nfo_fields("")
        sources = []
        if getattr(self.settings, "season_scan_nfo", True) and path:
            base_name = os.path.splitext(fname)[0]
            nfo_data = self._read_first_nfo([path + base_name + ".nfo", path + "movie.nfo"])

        fields = {}
        genres, studios, cast, streams = [], [], [], []

        if need_plot and nfo_data["plot"]:
            fields["c01"] = nfo_data["plot"]
        if need_tagline and nfo_data["tagline"]:
            fields["c03"] = nfo_data["tagline"]
        if need_runtime and nfo_data["runtime_sec"]:
            fields["c11"] = nfo_data["runtime_sec"]
        if need_mpaa and nfo_data["mpaa"]:
            fields["c12"] = nfo_data["mpaa"]
        if need_genres and nfo_data["genres"]:
            genres = nfo_data["genres"]
            fields["c14"] = " / ".join(genres)
        if need_studios and nfo_data["studios"]:
            studios = nfo_data["studios"]
            fields["c18"] = " / ".join(studios)
        if need_cast and nfo_data["cast"]:
            cast = nfo_data["cast"]
        if need_sd and nfo_data["streams"]:
            streams = nfo_data["streams"]

        if fields or cast or streams:
            sources.append("NFO")

        still_needs_tmdb = (
            (need_plot and "c01" not in fields)
            or (need_tagline and "c03" not in fields)
            or (need_runtime and "c11" not in fields)
            or (need_mpaa and "c12" not in fields)
            or (need_genres and not genres)
            or (need_studios and not studios)
            or (need_cast and not cast)
        )
        if still_needs_tmdb and getattr(self.settings, "season_scan_tmdb", True) and tmdb_id and self.tmdb.is_available():
            td = self.tmdb.get_movie_details(tmdb_id)
            if td:
                added_tmdb = False
                if need_plot and "c01" not in fields and td.get("plot"):
                    fields["c01"] = td["plot"]
                    added_tmdb = True
                if need_tagline and "c03" not in fields and td.get("tagline"):
                    fields["c03"] = td["tagline"]
                    added_tmdb = True
                if need_runtime and "c11" not in fields and td.get("runtime_sec"):
                    fields["c11"] = td["runtime_sec"]
                    added_tmdb = True
                if need_mpaa and "c12" not in fields and td.get("mpaa"):
                    fields["c12"] = td["mpaa"]
                    added_tmdb = True
                if need_genres and not genres and td.get("genres"):
                    genres = td["genres"]
                    fields["c14"] = " / ".join(genres)
                    added_tmdb = True
                if need_studios and not studios and td.get("studios"):
                    studios = td["studios"]
                    fields["c18"] = " / ".join(studios)
                    added_tmdb = True
                if need_cast and not cast and td.get("cast"):
                    cast = td["cast"]
                    added_tmdb = True
                if added_tmdb:
                    sources.append("TMDbHelper")

        if not (fields or genres or studios or cast or streams):
            return None

        return MetadataChange(
            target_table="movie",
            id_col="idMovie",
            id_val=row["idMovie"],
            media_label=row.get("title") or fname,
            fields=fields,
            genres=genres,
            studios=studios,
            cast=cast,
            streams=streams,
            id_file=row.get("idFile"),
            media_type="movie",
            source="+".join(dict.fromkeys(sources)) or "NFO"
        )

    def _inspect_tvshow_metadata(self, row):
        need_plot = not (row.get("cur_plot") or "").strip()
        need_genres = not (row.get("cur_genres") or "").strip()
        need_mpaa = not (row.get("cur_mpaa") or "").strip()
        need_studios = not (row.get("cur_studios") or "").strip()
        need_cast = not bool(row.get("has_al", 1))
        if not any((need_plot, need_genres, need_mpaa, need_studios, need_cast)):
            return None

        show_path = row.get("show_path") or ""
        tmdb_id = (row.get("tmdb_id") or "").strip()
        nfo_data = self._read_tvshow_nfo(show_path) if (getattr(self.settings, "season_scan_nfo", True) and show_path) else _parse_nfo_fields("")

        fields, genres, studios, cast, sources = {}, [], [], [], []
        if need_plot and nfo_data["plot"]:
            fields["c01"] = nfo_data["plot"]
        if need_genres and nfo_data["genres"]:
            genres = nfo_data["genres"]
            fields["c08"] = " / ".join(genres)
        if need_mpaa and nfo_data["mpaa"]:
            fields["c13"] = nfo_data["mpaa"]
        if need_studios and nfo_data["studios"]:
            studios = nfo_data["studios"]
            fields["c14"] = " / ".join(studios)
        if need_cast and nfo_data["cast"]:
            cast = nfo_data["cast"]
        if fields or cast:
            sources.append("NFO")

        still_needs_tmdb = (
            (need_plot and "c01" not in fields)
            or (need_genres and not genres)
            or (need_mpaa and "c13" not in fields)
            or (need_studios and not studios)
            or (need_cast and not cast)
        )
        if still_needs_tmdb and getattr(self.settings, "season_scan_tmdb", True) and tmdb_id and self.tmdb.is_available():
            td = self.tmdb.get_tvshow_details(tmdb_id)
            if td:
                added = False
                if need_plot and "c01" not in fields and td.get("plot"):
                    fields["c01"] = td["plot"]
                    added = True
                if need_genres and not genres and td.get("genres"):
                    genres = td["genres"]
                    fields["c08"] = " / ".join(genres)
                    added = True
                if need_mpaa and "c13" not in fields and td.get("mpaa"):
                    fields["c13"] = td["mpaa"]
                    added = True
                if need_studios and not studios and td.get("studios"):
                    studios = td["studios"]
                    fields["c14"] = " / ".join(studios)
                    added = True
                if need_cast and not cast and td.get("cast"):
                    cast = td["cast"]
                    added = True
                if added:
                    sources.append("TMDbHelper")

        if not (fields or genres or studios or cast):
            return None

        return MetadataChange(
            target_table="tvshow",
            id_col="idShow",
            id_val=row["idShow"],
            media_label=row.get("title") or "TV Show",
            fields=fields,
            genres=genres,
            studios=studios,
            cast=cast,
            media_type="tvshow",
            source="+".join(dict.fromkeys(sources)) or "NFO"
        )

    def _inspect_episode_metadata(self, row):
        need_plot = not (row.get("cur_plot") or "").strip()
        cur_rt = (row.get("cur_runtime") or "").strip()
        need_runtime = (not cur_rt or cur_rt == "0")
        need_sd = (not bool(row.get("has_sd", 1))) and getattr(self.settings, "update_missing_streamdetails", True)
        if not any((need_plot, need_runtime, need_sd)):
            return None

        path = (row.get("strPath") or "").rstrip("/\\") + "/"
        fname = row.get("strFilename") or ""
        tmdb_id = (row.get("show_tmdb_id") or "").strip()
        s_num = _safe_int(row.get("season_num"), -1)
        e_num = _safe_int(row.get("episode_num"), -1)

        nfo_data = _parse_nfo_fields("")
        if getattr(self.settings, "season_scan_nfo", True) and path and fname:
            base_name = os.path.splitext(fname)[0]
            nfo_data = self._read_first_nfo([path + base_name + ".nfo"])

        fields, streams, sources = {}, [], []
        if need_plot and nfo_data["plot"]:
            fields["c01"] = nfo_data["plot"]
        if need_runtime and nfo_data["runtime_sec"]:
            fields["c09"] = nfo_data["runtime_sec"]
        if need_sd and nfo_data["streams"]:
            streams = nfo_data["streams"]
        if fields or streams:
            sources.append("NFO")

        if ((need_plot and "c01" not in fields) or (need_runtime and "c09" not in fields)) \
                and getattr(self.settings, "season_scan_tmdb", True) and tmdb_id and s_num >= 0 and e_num >= 0 and self.tmdb.is_available():
            td = self.tmdb.get_episode_details(tmdb_id, s_num, e_num)
            if td:
                added = False
                if need_plot and "c01" not in fields and td.get("plot"):
                    fields["c01"] = td["plot"]
                    added = True
                if need_runtime and "c09" not in fields and td.get("runtime_sec"):
                    fields["c09"] = td["runtime_sec"]
                    added = True
                if added:
                    sources.append("TMDbHelper")

        if not (fields or streams):
            return None

        label = u"{} - S{:02d}E{:02d} - {}".format(
            row.get("show_title") or "Show", max(0, s_num), max(0, e_num), row.get("ep_title") or fname
        )
        return MetadataChange(
            target_table="episode",
            id_col="idEpisode",
            id_val=row["idEpisode"],
            media_label=label,
            fields=fields,
            streams=streams,
            id_file=row.get("idFile"),
            media_type="episode",
            source="+".join(dict.fromkeys(sources)) or "NFO"
        )

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def scan_missing_metadata(
        self,
        progress_callback=None,
        include_seasons=True,
        include_movies=True,
        include_tvshows=True,
        include_episodes=True
    ):
        """Scan for missing season, movie, TV show, and episode metadata + streamdetails."""
        self._tvshow_nfo_cache.clear()
        tables = self._get_table_set()
        work = []

        if include_seasons and "seasons" in tables:
            for r in self._fetch_seasons_needing_metadata():
                work.append(("season", r))

        if include_tvshows and "tvshow" in tables:
            try:
                for r in self._fetch_tvshows_missing_metadata(tables):
                    work.append(("tvshow", r))
            except Exception as e:
                log("TV show metadata query note: {}".format(e), "debug")

        if include_movies and "movie" in tables:
            try:
                for r in self._fetch_movies_missing_metadata(tables):
                    work.append(("movie", r))
            except Exception as e:
                log("Movie metadata query note: {}".format(e), "debug")

        if include_episodes and "episode" in tables:
            try:
                for r in self._fetch_episodes_missing_metadata(tables):
                    work.append(("episode", r))
            except Exception as e:
                log("Episode metadata query note: {}".format(e), "debug")

        total = len(work)
        changes = []
        if total == 0:
            return 0, []

        inspectors = {
            "season": self._inspect_season,
            "movie": self._inspect_movie_metadata,
            "tvshow": self._inspect_tvshow_metadata,
            "episode": self._inspect_episode_metadata,
        }

        batch_size = 25
        with ThreadPoolExecutor(max_workers=4) as executor:
            processed = 0
            for start in range(0, total, batch_size):
                batch = work[start:start + batch_size]
                futures = {executor.submit(inspectors[kind], row): (kind, row) for kind, row in batch}
                for fut in as_completed(futures):
                    processed += 1
                    try:
                        ch = fut.result()
                        if ch:
                            changes.append(ch)
                    except Exception as e:
                        log("Metadata inspect error: {}".format(e), "debug")

                if progress_callback:
                    _, last_row = batch[-1]
                    lbl = last_row.get("show_title") or last_row.get("title") or last_row.get("ep_title") or ""
                    if progress_callback(processed, total, lbl, len(changes)) is False:
                        break

        if progress_callback:
            progress_callback(total, total, "Metadata scan complete", len(changes))
        return total, changes

    def apply_metadata_changes(self, changes, progress_callback=None):
        """Take a backup and apply MetadataChange updates (including links & streamdetails)."""
        if not changes:
            return 0, None

        backup_info = self.backup_mgr.create_backup()
        tables = self._get_table_set()

        # Preload lookup caches for genre / studio / actor
        genre_cache = {}
        if "genre" in tables:
            for r in self.adapter.query("SELECT genre_id, name FROM genre"):
                if r.get("name"):
                    genre_cache[r["name"].strip().lower()] = r["genre_id"]

        studio_cache = {}
        if "studio" in tables:
            for r in self.adapter.query("SELECT studio_id, name FROM studio"):
                if r.get("name"):
                    studio_cache[r["name"].strip().lower()] = r["studio_id"]

        actor_cache = {}
        if "actor" in tables:
            for r in self.adapter.query("SELECT actor_id, name FROM actor"):
                if r.get("name"):
                    actor_cache[r["name"].strip().lower()] = r["actor_id"]

        applied = 0
        total = len(changes)
        try:
            for idx, ch in enumerate(changes):
                if progress_callback and idx % 10 == 0:
                    if progress_callback(idx, total, ch.media_label) is False:
                        self.adapter.rollback()
                        raise RuntimeError("Metadata update cancelled by user.")

                if ch.fields:
                    set_clauses = ["{} = %s".format(col) for col in ch.fields.keys()]
                    params = list(ch.fields.values()) + [ch.id_val]
                    sql = "UPDATE {} SET {} WHERE {} = %s".format(
                        ch.target_table, ", ".join(set_clauses), ch.id_col
                    )
                    self.adapter.execute(sql, tuple(params))

                if ch.genres and "genre" in tables and "genre_link" in tables:
                    for gname in ch.genres:
                        gk = gname.lower()
                        gid = genre_cache.get(gk)
                        if not gid:
                            gid = self.adapter.execute("INSERT INTO genre (name) VALUES (%s)", (gname,))
                            genre_cache[gk] = gid
                        exists = self.adapter.query(
                            "SELECT 1 FROM genre_link WHERE genre_id = %s AND media_id = %s AND media_type = %s",
                            (gid, ch.id_val, ch.media_type)
                        )
                        if not exists:
                            self.adapter.execute(
                                "INSERT INTO genre_link (genre_id, media_id, media_type) VALUES (%s, %s, %s)",
                                (gid, ch.id_val, ch.media_type)
                            )

                if ch.studios and "studio" in tables and "studio_link" in tables:
                    for sname in ch.studios:
                        sk = sname.lower()
                        sid = studio_cache.get(sk)
                        if not sid:
                            sid = self.adapter.execute("INSERT INTO studio (name) VALUES (%s)", (sname,))
                            studio_cache[sk] = sid
                        exists = self.adapter.query(
                            "SELECT 1 FROM studio_link WHERE studio_id = %s AND media_id = %s AND media_type = %s",
                            (sid, ch.id_val, ch.media_type)
                        )
                        if not exists:
                            self.adapter.execute(
                                "INSERT INTO studio_link (studio_id, media_id, media_type) VALUES (%s, %s, %s)",
                                (sid, ch.id_val, ch.media_type)
                            )

                if ch.cast and "actor" in tables and "actor_link" in tables:
                    for act in ch.cast:
                        aname = act["name"]
                        ak = aname.lower()
                        aid = actor_cache.get(ak)
                        if not aid:
                            art_xml = '<thumb>{}</thumb>'.format(act["thumb"]) if act.get("thumb") else ""
                            aid = self.adapter.execute(
                                "INSERT INTO actor (name, art_urls) VALUES (%s, %s)",
                                (aname, art_xml)
                            )
                            actor_cache[ak] = aid
                        exists = self.adapter.query(
                            "SELECT 1 FROM actor_link WHERE actor_id = %s AND media_id = %s AND media_type = %s",
                            (aid, ch.id_val, ch.media_type)
                        )
                        if not exists:
                            self.adapter.execute(
                                "INSERT INTO actor_link (actor_id, media_id, media_type, role, cast_order) VALUES (%s, %s, %s, %s, %s)",
                                (aid, ch.id_val, ch.media_type, act.get("role", ""), act.get("order", 0))
                            )

                if ch.streams and ch.id_file and "streamdetails" in tables:
                    for st in ch.streams:
                        st_type = st["iStreamType"]
                        if st_type == 0:
                            self.adapter.execute(
                                """INSERT INTO streamdetails
                                   (idFile, iStreamType, strVideoCodec, fVideoAspect, iVideoWidth, iVideoHeight,
                                    iVideoDuration, strStereoMode, strVideoLanguage, strHdrType)
                                   VALUES (%s, 0, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (
                                    ch.id_file,
                                    st.get("strVideoCodec", ""),
                                    st.get("fVideoAspect", 0.0),
                                    st.get("iVideoWidth", 0),
                                    st.get("iVideoHeight", 0),
                                    st.get("iVideoDuration", 0),
                                    st.get("strStereoMode", ""),
                                    st.get("strVideoLanguage", ""),
                                    st.get("strHdrType", ""),
                                )
                            )
                        elif st_type == 1:
                            self.adapter.execute(
                                """INSERT INTO streamdetails
                                   (idFile, iStreamType, strAudioCodec, iAudioChannels, strAudioLanguage)
                                   VALUES (%s, 1, %s, %s, %s)""",
                                (
                                    ch.id_file,
                                    st.get("strAudioCodec", ""),
                                    st.get("iAudioChannels", 0),
                                    st.get("strAudioLanguage", ""),
                                )
                            )
                        elif st_type == 2:
                            self.adapter.execute(
                                """INSERT INTO streamdetails
                                   (idFile, iStreamType, strSubtitleLanguage)
                                   VALUES (%s, 2, %s)""",
                                (ch.id_file, st.get("strSubtitleLanguage", ""))
                            )

                applied += 1

            self.adapter.commit()
            log("Applied {} missing metadata updates.".format(applied))
            return applied, backup_info
        except Exception as e:
            log("Error applying metadata updates, rolling back: {}".format(e), "error")
            self.adapter.rollback()
            raise
