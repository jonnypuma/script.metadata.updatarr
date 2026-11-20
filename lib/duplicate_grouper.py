# -*- coding: utf-8 -*-
"""
Detects duplicate movie entries (sharing the same TMDB or IMDB uniqueid)
and groups them into a single Kodi Video Version movie entry.
"""
import os
from lib.kodi_compat import log
from lib.backup_manager import BackupManager
from lib.nfo_parser import find_edition_from_nfo, find_folder_edition_from_nfo
from lib.filename_parser import parse_edition_from_filename, parse_quality_from_filename


class DuplicateGroup:
    def __init__(self, key_label, title, primary, secondaries):
        self.key_label = key_label      # e.g. "tmdb:550"
        self.title = title
        self.primary = primary          # dict with idMovie, idFile, strFilename, strPath, ...
        self.secondaries = secondaries  # list of dicts

    def __repr__(self):
        return "<DuplicateGroup {} '{}' ({} files)>".format(
            self.key_label, self.title, 1 + len(self.secondaries)
        )


def find_duplicate_movies(adapter):
    """
    Find movies in the database that share the same TMDB (preferred) or IMDB ID.
    Returns a list of DuplicateGroup objects.
    """
    sql = """
    SELECT
        m.idMovie,
        m.idFile,
        m.c00 AS title,
        f.strFilename,
        p.strPath,
        f.playCount,
        u.type AS uid_type,
        u.value AS uid_value,
        v.idType AS existing_type_id
    FROM movie m
    JOIN files f ON m.idFile = f.idFile
    JOIN path p ON f.idPath = p.idPath
    JOIN uniqueid u ON u.media_id = m.idMovie AND u.media_type = 'movie' AND u.type IN ('tmdb', 'imdb')
    LEFT JOIN videoversion v ON m.idFile = v.idFile
    WHERE u.value IS NOT NULL AND u.value != ''
    ORDER BY m.idMovie ASC
    """
    rows = adapter.query(sql)

    # Group by tmdb first, then imdb for movies without tmdb
    by_movie = {}
    for r in rows:
        mid = r["idMovie"]
        if mid not in by_movie:
            by_movie[mid] = dict(r)
            by_movie[mid]["uids"] = {}
        by_movie[mid]["uids"][r["uid_type"].lower()] = r["uid_value"].strip()

    buckets = {}
    for mid, info in by_movie.items():
        uids = info["uids"]
        if "tmdb" in uids and uids["tmdb"]:
            key = "tmdb:" + uids["tmdb"]
        elif "imdb" in uids and uids["imdb"]:
            key = "imdb:" + uids["imdb"]
        else:
            continue
        buckets.setdefault(key, []).append(info)

    groups = []
    for key, entries in buckets.items():
        if len(entries) < 2:
            continue
        # Ensure distinct idMovie entries
        unique_movies = {}
        for e in entries:
            unique_movies[e["idMovie"]] = e
        if len(unique_movies) < 2:
            continue

        # Sort: watched items first, then lowest idMovie
        ordered = sorted(
            unique_movies.values(),
            key=lambda x: (0 if (x.get("playCount") or 0) > 0 else 1, x["idMovie"])
        )
        primary = ordered[0]
        secondaries = ordered[1:]
        groups.append(DuplicateGroup(
            key_label=key,
            title=primary["title"] or "Unknown Movie",
            primary=primary,
            secondaries=secondaries
        ))

    groups.sort(key=lambda g: g.title.lower())
    return groups


def _detect_label_for_entry(entry, shared_folders):
    path = entry.get("strPath") or ""
    filename = entry.get("strFilename") or ""
    folder_key = path.rstrip("/\\").lower()
    is_shared = folder_key in shared_folders

    if is_shared:
        label = find_edition_from_nfo(path, filename, allow_folder_nfo=False)
        if not label:
            label = parse_edition_from_filename(filename)
        if not label:
            label = find_folder_edition_from_nfo(path)
    else:
        label = find_edition_from_nfo(path, filename, allow_folder_nfo=True)
        if not label:
            label = parse_edition_from_filename(filename)
        if not label and path:
            label = parse_edition_from_filename(os.path.basename(path.rstrip("/\\")))

    if not label:
        label = parse_quality_from_filename(filename)
    return label or "Standard Edition"


def group_duplicate_movies(adapter, groups, retention=3):
    """
    Merge each DuplicateGroup into a single primary movie with multiple videoversion rows.
    Takes a backup before modifying and runs inside a transaction.
    Returns (merged_movies_count, merged_files_count, backup_info).
    """
    if not groups:
        return 0, 0, None

    backup_info = BackupManager(adapter, retention=retention).create_backup()

    try:
        type_rows = adapter.query("SELECT id, name FROM videoversiontype WHERE itemType = 1")
        type_dict = {r["name"].lower(): r["id"] for r in type_rows if r.get("name")}
        max_id = max([r["id"] for r in type_rows] + [40400])

        def resolve_type_id(name):
            nonlocal max_id
            key = name.lower()
            if key in type_dict:
                return type_dict[key]
            next_id = 40801 if max_id < 40800 else (max_id + 1)
            adapter.execute(
                "INSERT INTO videoversiontype (id, name, owner, itemType) VALUES (%s, %s, 2, 1)",
                (next_id, name)
            )
            type_dict[key] = next_id
            max_id = next_id
            return next_id

        # Track shared folders across all entries
        folder_counts = {}
        for g in groups:
            for e in [g.primary] + g.secondaries:
                fk = (e.get("strPath") or "").rstrip("/\\").lower()
                if fk:
                    folder_counts[fk] = folder_counts.get(fk, 0) + 1
        shared_folders = {k for k, v in folder_counts.items() if v > 1}

        existing_vv = {r["idFile"] for r in adapter.query("SELECT idFile FROM videoversion")}

        merged_movies = 0
        merged_files = 0

        for g in groups:
            primary_id = g.primary["idMovie"]
            # Ensure primary file has a videoversion entry
            if g.primary["idFile"] not in existing_vv:
                p_label = _detect_label_for_entry(g.primary, shared_folders)
                p_type_id = resolve_type_id(p_label)
                adapter.execute(
                    "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (%s, %s, 'movie', 1, %s)",
                    (g.primary["idFile"], primary_id, p_type_id)
                )
                existing_vv.add(g.primary["idFile"])

            for sec in g.secondaries:
                sec_id = sec["idMovie"]
                sec_file = sec["idFile"]

                if sec_file in existing_vv:
                    # Keep existing custom type if already non-standard, else detect
                    if sec.get("existing_type_id") and sec["existing_type_id"] != 40400:
                        adapter.execute(
                            "UPDATE videoversion SET idMedia = %s, media_type = 'movie', itemType = 1 WHERE idFile = %s",
                            (primary_id, sec_file)
                        )
                    else:
                        s_label = _detect_label_for_entry(sec, shared_folders)
                        s_type_id = resolve_type_id(s_label)
                        adapter.execute(
                            "UPDATE videoversion SET idMedia = %s, media_type = 'movie', itemType = 1, idType = %s WHERE idFile = %s",
                            (primary_id, s_type_id, sec_file)
                        )
                else:
                    s_label = _detect_label_for_entry(sec, shared_folders)
                    s_type_id = resolve_type_id(s_label)
                    adapter.execute(
                        "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (%s, %s, 'movie', 1, %s)",
                        (sec_file, primary_id, s_type_id)
                    )
                    existing_vv.add(sec_file)

                # Remove duplicate movie entry and its uniqueid metadata
                adapter.execute("DELETE FROM uniqueid WHERE media_id = %s AND media_type = 'movie'", (sec_id,))
                adapter.execute("DELETE FROM movie WHERE idMovie = %s", (sec_id,))
                merged_files += 1

            merged_movies += 1

        adapter.commit()
        log("Grouped {} duplicate movies ({} extra files merged into versions).".format(
            merged_movies, merged_files
        ))
        return merged_movies, merged_files, backup_info
    except Exception as e:
        log("Error grouping duplicate movies, rolling back: {}".format(e), "error")
        adapter.rollback()
        raise
