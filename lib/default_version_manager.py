# -*- coding: utf-8 -*-
"""
Manager for inspecting and swapping the default video version of movies and episodes.
In Kodi, the default version is determined by movie.idFile or episode.idFile.
"""
from lib.kodi_compat import log
from lib.backup_manager import BackupManager


class VersionOption:
    def __init__(self, id_file, filename, path, type_name, is_default, play_count=0, last_played=None):
        self.id_file = id_file
        self.filename = filename
        self.path = path
        self.type_name = type_name or "Standard Edition"
        self.is_default = is_default
        self.play_count = play_count or 0
        self.last_played = last_played

    def __repr__(self):
        star = "★ " if self.is_default else "  "
        return "<VersionOption {}{}: {} ({})>".format(star, self.type_name, self.filename, self.id_file)


class MultiVersionItem:
    def __init__(self, id_media, media_type, title, default_file_id, versions=None):
        self.id_media = id_media
        self.media_type = media_type  # 'movie' or 'episode'
        self.title = title
        self.default_file_id = default_file_id
        self.versions = versions or []

    def get_default_version(self):
        for v in self.versions:
            if v.is_default:
                return v
        return None

    def __repr__(self):
        return "<MultiVersionItem [{}] {} ({} versions, default: {})>".format(
            self.media_type, self.title, len(self.versions), self.default_file_id
        )


def get_multiversion_items(adapter, media_type=None):
    """
    Returns all movies and/or episodes that have multiple versions configured.
    """
    items = []

    # 1. Multi-version Movies
    if media_type in (None, "movie"):
        movie_sql = """
        SELECT 
            m.idMovie AS idMedia,
            'movie' AS media_type,
            m.c00 AS title,
            m.idFile AS default_file_id,
            v.idFile,
            f.strFilename,
            p.strPath,
            v.idType,
            vt.name AS type_name,
            f.playCount,
            f.lastPlayed
        FROM movie m
        JOIN videoversion v ON m.idMovie = v.idMedia AND v.media_type = 'movie'
        JOIN files f ON v.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN videoversiontype vt ON v.idType = vt.id
        WHERE m.idMovie IN (
            SELECT idMedia FROM videoversion WHERE media_type = 'movie' AND itemType = 1 GROUP BY idMedia HAVING COUNT(*) > 1
        )
        ORDER BY m.c00 ASC, v.idFile ASC
        """
        rows = adapter.query(movie_sql)
        items_dict = {}
        for r in rows:
            mid = r["idMedia"]
            if mid not in items_dict:
                items_dict[mid] = MultiVersionItem(
                    id_media=mid,
                    media_type="movie",
                    title=r["title"] or "Unknown Movie",
                    default_file_id=r["default_file_id"]
                )
            is_def = (r["idFile"] == r["default_file_id"])
            items_dict[mid].versions.append(VersionOption(
                id_file=r["idFile"],
                filename=r["strFilename"],
                path=r["strPath"],
                type_name=r["type_name"],
                is_default=is_def,
                play_count=r.get("playCount", 0),
                last_played=r.get("lastPlayed")
            ))
        items.extend(items_dict.values())

    # 2. Multi-version TV Episodes
    if media_type in (None, "episode"):
        episode_sql = """
        SELECT 
            e.idEpisode AS idMedia,
            'episode' AS media_type,
            t.c00 AS show_title,
            e.c12 AS season_num,
            e.c13 AS episode_num,
            e.c00 AS ep_title,
            e.idFile AS default_file_id,
            v.idFile,
            f.strFilename,
            p.strPath,
            v.idType,
            vt.name AS type_name,
            f.playCount,
            f.lastPlayed
        FROM episode e
        JOIN tvshow t ON e.idShow = t.idShow
        JOIN videoversion v ON e.idEpisode = v.idMedia AND v.media_type = 'episode'
        JOIN files f ON v.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN videoversiontype vt ON v.idType = vt.id
        WHERE e.idEpisode IN (
            SELECT idMedia FROM videoversion WHERE media_type = 'episode' AND itemType = 1 GROUP BY idMedia HAVING COUNT(*) > 1
        )
        ORDER BY t.c00 ASC, CAST(e.c12 AS SIGNED) ASC, CAST(e.c13 AS SIGNED) ASC, v.idFile ASC
        """
        try:
            ep_rows = adapter.query(episode_sql)
            ep_dict = {}
            for r in ep_rows:
                eid = r["idMedia"]
                if eid not in ep_dict:
                    s_num = r.get("season_num") or "0"
                    e_num = r.get("episode_num") or "0"
                    try:
                        ep_label = "S{:02d}E{:02d}".format(int(s_num), int(e_num))
                    except ValueError:
                        ep_label = "S{}E{}".format(s_num, e_num)

                    full_title = u"{} - {} - {}".format(
                        r.get("show_title") or "Unknown Show",
                        ep_label,
                        r.get("ep_title") or "Episode"
                    )
                    ep_dict[eid] = MultiVersionItem(
                        id_media=eid,
                        media_type="episode",
                        title=full_title,
                        default_file_id=r["default_file_id"]
                    )
                is_def = (r["idFile"] == r["default_file_id"])
                ep_dict[eid].versions.append(VersionOption(
                    id_file=r["idFile"],
                    filename=r["strFilename"],
                    path=r["strPath"],
                    type_name=r["type_name"],
                    is_default=is_def,
                    play_count=r.get("playCount", 0),
                    last_played=r.get("lastPlayed")
                ))
            items.extend(ep_dict.values())
        except Exception as e:
            log("Note: Could not query multi-version episodes: {}".format(e), "debug")

    return items


def swap_default_version(adapter, id_media, media_type, target_file_id, sync_watched=True, retention=3):
    """
    Swaps the default version (idFile) for a movie or episode.
    Creates a backup snapshot before writing.
    Optionally syncs watch status/playCount to the new default file.
    """
    log("Swapping default version for {} ID {} -> target idFile: {}".format(
        media_type, id_media, target_file_id
    ))

    # Take backup first
    mgr = BackupManager(adapter, retention=retention)
    backup_info = mgr.create_backup()

    try:
        old_file_id = None
        old_play_count = 0
        old_last_played = None

        if media_type == "movie":
            rows = adapter.query("SELECT idFile FROM movie WHERE idMovie = %s", (id_media,))
            if rows:
                old_file_id = rows[0]["idFile"]
            adapter.execute("UPDATE movie SET idFile = %s WHERE idMovie = %s", (target_file_id, id_media))
        elif media_type == "episode":
            rows = adapter.query("SELECT idFile FROM episode WHERE idEpisode = %s", (id_media,))
            if rows:
                old_file_id = rows[0]["idFile"]
            adapter.execute("UPDATE episode SET idFile = %s WHERE idEpisode = %s", (target_file_id, id_media))
        else:
            raise ValueError("Unsupported media_type: {}".format(media_type))

        # Sync watch status if requested
        if sync_watched and old_file_id and old_file_id != target_file_id:
            old_info = adapter.query("SELECT playCount, lastPlayed FROM files WHERE idFile = %s", (old_file_id,))
            target_info = adapter.query("SELECT playCount FROM files WHERE idFile = %s", (target_file_id,))

            if old_info and target_info:
                old_cnt = old_info[0].get("playCount") or 0
                old_lp = old_info[0].get("lastPlayed")
                tgt_cnt = target_info[0].get("playCount") or 0

                if old_cnt > 0 and tgt_cnt == 0:
                    log("Syncing watched status from old default ({}) to new default ({})".format(old_file_id, target_file_id))
                    adapter.execute(
                        "UPDATE files SET playCount = %s, lastPlayed = %s WHERE idFile = %s",
                        (old_cnt, old_lp, target_file_id)
                    )

        adapter.commit()
        log("Successfully swapped default version for {} {} to idFile {}".format(media_type, id_media, target_file_id))
        return True, backup_info
    except Exception as e:
        log("Error swapping default version, rolling back: {}".format(e), "error")
        adapter.rollback()
        raise
