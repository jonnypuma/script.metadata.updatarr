# -*- coding: utf-8 -*-
"""
Core video version detection and database update engine.
Supports both Movies and TV Episodes across SQLite and MySQL/MariaDB.
"""
import os
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from lib.kodi_compat import log, translate_path, make_dirs
from lib.nfo_parser import find_edition_from_nfo, find_folder_edition_from_nfo, clear_nfo_cache
from lib.filename_parser import parse_edition_from_filename, parse_quality_from_filename, normalize_edition_name
from lib.custom_mappings import clear_custom_mappings_cache
from lib.backup_manager import BackupManager


class PlannedChange:
    def __init__(self, id_media, media_type, title, id_file, filename, current_type, target_type, in_videoversion):
        self.id_media = id_media
        self.media_type = media_type  # 'movie' or 'episode'
        self.title = title
        # Backward compatibility properties:
        self.id_movie = id_media if media_type == "movie" else None
        self.movie_title = title

        self.id_file = id_file
        self.filename = filename
        self.current_type = current_type or "None"
        self.target_type = target_type
        self.in_videoversion = bool(in_videoversion)

    def __repr__(self):
        return "<Change [{}:{}] '{}' -> '{}' ({})>".format(
            self.media_type, self.title, self.current_type, self.target_type, self.filename
        )


class UpdateResult:
    def __init__(
        self,
        total_scanned=0,
        changes_planned=None,
        applied_count=0,
        new_types_count=0,
        backup_info=None,
        new_types_created=None
    ):
        self.total_scanned = total_scanned
        self.changes = changes_planned or []
        self.applied_count = applied_count
        self.new_types_count = new_types_count
        self.backup_info = backup_info
        self.new_types_created = new_types_created or []


class VersionUpdater:
    def __init__(self, adapter, settings):
        self.adapter = adapter
        self.settings = settings
        self.backup_mgr = BackupManager(self.adapter, retention=self.settings.backup_retention)
        self.version_type_cache = {}  # lower_name -> id
        self.max_type_id = 40400

    def load_version_types(self):
        """Load all version types from videoversiontype table (itemType = 1)."""
        rows = self.adapter.query("SELECT id, name, owner, itemType FROM videoversiontype WHERE itemType = 1")
        self.version_type_cache.clear()
        max_id = 40400
        for r in rows:
            tid = r["id"]
            name = (r["name"] or "").strip()
            if name:
                self.version_type_cache[name.lower()] = tid
            if tid > max_id:
                max_id = tid
        self.max_type_id = max_id
        log("Loaded {} version types from database (max id: {})".format(
            len(self.version_type_cache), self.max_type_id
        ))

    def get_or_create_type_id(self, target_name):
        """Lookup type id by name, or insert a new custom user type (owner=2, itemType=1, range 40801+)."""
        norm_name = target_name.strip()
        key = norm_name.lower()
        if key in self.version_type_cache:
            return self.version_type_cache[key], False

        # Ensure user custom IDs start in Kodi's 40801+ range
        if self.max_type_id < 40800:
            next_id = 40801
        else:
            next_id = self.max_type_id + 1

        log("Inserting new custom version type '{}' (id: {}) into videoversiontype".format(norm_name, next_id))
        self.adapter.execute(
            "INSERT INTO videoversiontype (id, name, owner, itemType) VALUES (%s, %s, 2, 1)",
            (next_id, norm_name)
        )
        self.max_type_id = next_id
        self.version_type_cache[key] = next_id
        return next_id, True

    def fetch_all_movie_assets(self):
        """Query movie assets (both in videoversion and standalone movies)."""
        sql = """
        SELECT 
            m.idMovie AS idMedia,
            'movie' AS media_type,
            m.c00 AS title,
            f.idFile,
            f.strFilename,
            p.strPath,
            v.idType,
            vt.name AS type_name,
            v.itemType,
            1 AS in_videoversion
        FROM movie m
        JOIN videoversion v ON m.idMovie = v.idMedia AND v.media_type = 'movie'
        JOIN files f ON v.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN videoversiontype vt ON v.idType = vt.id

        UNION ALL

        SELECT 
            m.idMovie AS idMedia,
            'movie' AS media_type,
            m.c00 AS title,
            f.idFile,
            f.strFilename,
            p.strPath,
            NULL AS idType,
            NULL AS type_name,
            1 AS itemType,
            0 AS in_videoversion
        FROM movie m
        JOIN files f ON m.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN videoversion v ON m.idFile = v.idFile
        WHERE v.idFile IS NULL
        ORDER BY 1, 3
        """
        return self.adapter.query(sql)

    def fetch_all_episode_assets(self):
        """
        Query candidate episode assets:
        1. Episodes already registered in videoversion table.
        2. Unregistered episodes whose filename contains release/edition keywords.
        """
        sql = """
        SELECT 
            e.idEpisode AS idMedia,
            'episode' AS media_type,
            t.c00 AS show_title,
            e.c12 AS season_num,
            e.c13 AS episode_num,
            e.c00 AS ep_title,
            f.idFile,
            f.strFilename,
            p.strPath,
            v.idType,
            vt.name AS type_name,
            v.itemType,
            1 AS in_videoversion
        FROM episode e
        JOIN tvshow t ON e.idShow = t.idShow
        JOIN videoversion v ON e.idEpisode = v.idMedia AND v.media_type = 'episode'
        JOIN files f ON v.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN videoversiontype vt ON v.idType = vt.id

        UNION ALL

        SELECT 
            e.idEpisode AS idMedia,
            'episode' AS media_type,
            t.c00 AS show_title,
            e.c12 AS season_num,
            e.c13 AS episode_num,
            e.c00 AS ep_title,
            f.idFile,
            f.strFilename,
            p.strPath,
            NULL AS idType,
            NULL AS type_name,
            1 AS itemType,
            0 AS in_videoversion
        FROM episode e
        JOIN tvshow t ON e.idShow = t.idShow
        JOIN files f ON e.idFile = f.idFile
        JOIN path p ON f.idPath = p.idPath
        LEFT JOIN videoversion v ON e.idFile = v.idFile
        WHERE v.idFile IS NULL
          AND (
              f.strFilename LIKE '%cut%' 
           OR f.strFilename LIKE '%extended%'
           OR f.strFilename LIKE '%uncut%'
           OR f.strFilename LIKE '%remastered%'
           OR f.strFilename LIKE '%edition%'
           OR f.strFilename LIKE '%directors%'
           OR f.strFilename LIKE '%unrated%'
           OR f.strFilename LIKE '%imax%'
           OR f.strFilename LIKE '%special%'
           OR f.strFilename LIKE '%criterion%'
          )
        ORDER BY 1, 7
        """
        try:
            raw_rows = self.adapter.query(sql)
            results = []
            for r in raw_rows:
                s_num = r.get("season_num") or "0"
                e_num = r.get("episode_num") or "0"
                try:
                    ep_tag = "S{:02d}E{:02d}".format(int(s_num), int(e_num))
                except ValueError:
                    ep_tag = "S{}E{}".format(s_num, e_num)

                ep_title = u"{} - {} - {}".format(
                    r.get("show_title") or "Unknown Show",
                    ep_tag,
                    r.get("ep_title") or "Episode"
                )
                r["title"] = ep_title
                results.append(r)
            return results
        except Exception as e:
            log("Note: Could not query candidate episodes: {}".format(e), "warning")
            return []

    def fetch_all_video_assets(self):
        """Fetch all eligible movie and/or episode assets based on settings."""
        assets = []
        if getattr(self.settings, "scan_movies", True):
            movie_assets = self.fetch_all_movie_assets()
            log("Loaded {} movie assets.".format(len(movie_assets)))
            assets.extend(movie_assets)

        if getattr(self.settings, "scan_episodes", True):
            episode_assets = self.fetch_all_episode_assets()
            log("Loaded {} candidate TV episode assets.".format(len(episode_assets)))
            assets.extend(episode_assets)

        return assets

    def _get_scan_state_path(self):
        addon_data = translate_path("special://profile/addon_data/script.metadata.updatarr/")
        return os.path.join(addon_data, "scan_state.json")

    def _load_scanned_ids(self, db_key):
        path = self._get_scan_state_path()
        if not os.path.exists(path):
            return set()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return set(data.get(db_key, []))
        except Exception:
            return set()

    def _save_scanned_ids(self, db_key, ids_set):
        path = self._get_scan_state_path()
        try:
            make_dirs(os.path.dirname(path))
            data = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            data[db_key] = sorted(ids_set)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception as e:
            log("Could not save scan_state.json: {}".format(e), "debug")

    def _inspect_single_asset(self, item, shared_folders, multiversion_keys):
        """Inspect one video asset (safe to run in worker threads)."""
        id_media = item["idMedia"]
        media_type = item["media_type"]
        title = item["title"] or "Unknown"
        id_file = item["idFile"]
        filename = item["strFilename"] or ""
        path = item["strPath"] or ""
        current_type = (item["type_name"] or "").strip()
        in_videoversion = bool(item["in_videoversion"])

        if self.settings.only_standard_edition:
            if in_videoversion and current_type and current_type.lower() != "standard edition":
                return None

        folder_key = path.rstrip("/\\").lower()
        is_shared = folder_key in shared_folders
        detected_edition = None

        if is_shared:
            # Shared folder: sidecar NFO -> filename -> folder movie.nfo
            if self.settings.scan_nfo:
                detected_edition = find_edition_from_nfo(path, filename, allow_folder_nfo=False)
            if not detected_edition and self.settings.scan_filenames:
                detected_edition = parse_edition_from_filename(filename)
            if not detected_edition and self.settings.scan_nfo:
                detected_edition = find_folder_edition_from_nfo(path)
        else:
            # Dedicated folder: sidecar/folder NFO -> filename -> folder name
            if self.settings.scan_nfo:
                detected_edition = find_edition_from_nfo(path, filename, allow_folder_nfo=True)
            if not detected_edition and self.settings.scan_filenames:
                detected_edition = parse_edition_from_filename(filename)
                if not detected_edition and path and media_type == "movie":
                    folder_name = os.path.basename(path.rstrip("/\\"))
                    detected_edition = parse_edition_from_filename(folder_name)

        # Quality fallback ONLY for grouped multi-version items when no edition was found
        if (
            not detected_edition
            and getattr(self.settings, "label_multiversion_quality", True)
            and (media_type, id_media) in multiversion_keys
        ):
            detected_edition = parse_quality_from_filename(filename)

        if not detected_edition:
            return None

        if current_type and current_type.lower() == detected_edition.lower():
            return None

        return PlannedChange(
            id_media=id_media,
            media_type=media_type,
            title=title,
            id_file=id_file,
            filename=filename,
            current_type=current_type,
            target_type=detected_edition,
            in_videoversion=in_videoversion
        )

    def scan_for_changes(self, progress_callback=None, force_full=False):
        """
        Scans movies and TV episodes concurrently, checks NFOs/filenames,
        and identifies planned changes. Supports incremental scanning.
        """
        clear_nfo_cache()
        clear_custom_mappings_cache()
        self.load_version_types()
        assets = self.fetch_all_video_assets()

        # Deduplicate by idFile and build folder + multi-version maps
        dedup_assets = []
        seen_file_ids = set()
        folder_counts = {}
        media_file_counts = {}

        for item in assets:
            id_file = item["idFile"]
            if id_file in seen_file_ids:
                continue
            seen_file_ids.add(id_file)
            dedup_assets.append(item)

            fk = (item["strPath"] or "").rstrip("/\\").lower()
            if fk:
                folder_counts[fk] = folder_counts.get(fk, 0) + 1
            mkey = (item["media_type"], item["idMedia"])
            media_file_counts[mkey] = media_file_counts.get(mkey, 0) + 1

        shared_folders = {k for k, v in folder_counts.items() if v > 1}
        multiversion_keys = {k for k, v in media_file_counts.items() if v > 1}

        db_key = self.adapter.get_display_name() if hasattr(self.adapter, "get_display_name") else "default"
        use_incremental = getattr(self.settings, "incremental_scan", True) and not force_full
        previously_scanned = self._load_scanned_ids(db_key) if use_incremental else set()

        if use_incremental and previously_scanned:
            work_items = [it for it in dedup_assets if it["idFile"] not in previously_scanned]
            log("Incremental scan: {} new/unscanned of {} total items (skipped {}).".format(
                len(work_items), len(dedup_assets), len(dedup_assets) - len(work_items)
            ))
        else:
            work_items = dedup_assets
            log("Full scan: {} total video asset entries in library...".format(len(work_items)))

        total = len(work_items)
        planned_changes = []
        newly_scanned = set()
        cancelled = False

        if total > 0:
            batch_size = 30
            with ThreadPoolExecutor(max_workers=6) as executor:
                processed = 0
                for start in range(0, total, batch_size):
                    batch = work_items[start:start + batch_size]
                    futures = {
                        executor.submit(self._inspect_single_asset, item, shared_folders, multiversion_keys): item
                        for item in batch
                    }
                    for fut in as_completed(futures):
                        item = futures[fut]
                        processed += 1
                        newly_scanned.add(item["idFile"])
                        try:
                            change = fut.result()
                            if change:
                                planned_changes.append(change)
                        except Exception as e:
                            log("Error scanning {}: {}".format(item.get("strFilename"), e), "debug")

                    if progress_callback:
                        last_title = batch[-1]["title"] if batch else ""
                        keep_going = progress_callback(processed, total, last_title, len(planned_changes))
                        if keep_going is False:
                            log("Scan cancelled by user.")
                            cancelled = True
                            break

        if not cancelled and work_items:
            all_scanned = previously_scanned | newly_scanned
            self._save_scanned_ids(db_key, all_scanned)

        if progress_callback:
            progress_callback(total, total, "Scan complete", len(planned_changes))

        log("Scan found {} version updates to apply.".format(len(planned_changes)))
        return UpdateResult(total_scanned=total, changes_planned=planned_changes)

    def apply_changes(self, planned_changes, progress_callback=None):
        """
        Takes a backup and applies planned changes inside a database transaction.
        Handles both 'movie' and 'episode' records.
        """
        if not planned_changes:
            return UpdateResult(total_scanned=0, applied_count=0)

        # Step 1: Create backup first!
        log("Taking backup before applying {} changes...".format(len(planned_changes)))
        backup_info = self.backup_mgr.create_backup()
        log("Backup completed: {}".format(backup_info.label))

        applied_count = 0
        new_types_count = 0
        new_types_created = []
        total = len(planned_changes)

        try:
            existing_rows = self.adapter.query("SELECT idFile FROM videoversion")
            existing_file_ids = {r["idFile"] for r in existing_rows}

            for idx, change in enumerate(planned_changes):
                if progress_callback and idx % 10 == 0:
                    keep_going = progress_callback(
                        idx, total, u"{} -> {}".format(change.title, change.target_type)
                    )
                    if keep_going is False:
                        log("Update cancelled by user, rolling back transaction...")
                        self.adapter.rollback()
                        raise RuntimeError("Update cancelled by user.")

                # Get or create videoversiontype
                type_id, was_created = self.get_or_create_type_id(change.target_type)
                if was_created:
                    new_types_count += 1
                    new_types_created.append((type_id, change.target_type))

                if change.in_videoversion or change.id_file in existing_file_ids:
                    # Update existing record (idFile is PRIMARY KEY in videoversion)
                    self.adapter.execute(
                        "UPDATE videoversion SET idType = %s WHERE idFile = %s",
                        (type_id, change.id_file)
                    )
                else:
                    # Insert new record into videoversion
                    self.adapter.execute(
                        "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (%s, %s, %s, 1, %s)",
                        (change.id_file, change.id_media, change.media_type, type_id)
                    )
                    existing_file_ids.add(change.id_file)

                applied_count += 1

            self.adapter.commit()
            log("Successfully applied {} version changes ({} new types created).".format(
                applied_count, new_types_count
            ))

            return UpdateResult(
                total_scanned=total,
                changes_planned=planned_changes,
                applied_count=applied_count,
                new_types_count=new_types_count,
                backup_info=backup_info,
                new_types_created=new_types_created
            )
        except Exception as e:
            log("Error applying updates, rolling back transaction: {}".format(e), "error")
            self.adapter.rollback()
            raise


PLACEHOLDER_LABELS = {"none", "n/a", "na", "null", "unknown", "default"}


def find_normalizable_labels(adapter):
    """
    Inspect existing videoversiontype entries in use and return a list of
    (old_id, old_name, target_name, item_count) tuples that can be normalized.
    """
    rows = adapter.query("""
        SELECT vt.id, vt.name, COUNT(v.idFile) AS cnt
        FROM videoversiontype vt
        JOIN videoversion v ON vt.id = v.idType
        WHERE vt.itemType = 1 AND vt.name IS NOT NULL
        GROUP BY vt.id, vt.name
        ORDER BY cnt DESC, vt.id ASC
    """)
    mappings = []
    for r in rows:
        old_name = (r["name"] or "").strip()
        if not old_name:
            continue
        if old_name.lower() in PLACEHOLDER_LABELS:
            target_name = "Standard Edition"
        else:
            norm = normalize_edition_name(old_name)
            if not norm or norm == old_name:
                continue
            target_name = norm
        mappings.append((r["id"], old_name, target_name, r["cnt"]))
    return mappings


def apply_label_normalization(adapter, mappings, retention=3):
    """
    Backup database, remap non-standard/duplicate videoversiontype IDs to
    normalized types, and remove orphaned custom types (id >= 40801).
    Returns (updated_rows_count, removed_types_count, backup_info).
    """
    if not mappings:
        return 0, 0, None

    backup_info = BackupManager(adapter, retention=retention).create_backup()
    try:
        type_rows = adapter.query("SELECT id, name FROM videoversiontype WHERE itemType = 1")
        type_dict = {r["name"].lower(): r["id"] for r in type_rows if r.get("name")}
        max_id = max([r["id"] for r in type_rows] + [40400])

        updated_rows = 0
        old_custom_ids = []

        for old_id, _old_name, target_name, _cnt in mappings:
            key = target_name.lower()
            if key in type_dict:
                target_id = type_dict[key]
            else:
                next_id = 40801 if max_id < 40800 else (max_id + 1)
                adapter.execute(
                    "INSERT INTO videoversiontype (id, name, owner, itemType) VALUES (%s, %s, 2, 1)",
                    (next_id, target_name)
                )
                type_dict[key] = next_id
                max_id = next_id
                target_id = next_id

            if target_id != old_id:
                adapter.execute(
                    "UPDATE videoversion SET idType = %s WHERE idType = %s",
                    (target_id, old_id)
                )
                updated_rows += _cnt
                if old_id >= 40801:
                    old_custom_ids.append(old_id)

        removed_types = 0
        for old_id in old_custom_ids:
            rem = adapter.query("SELECT COUNT(*) AS cnt FROM videoversion WHERE idType = %s", (old_id,))
            if rem and rem[0]["cnt"] == 0:
                adapter.execute("DELETE FROM videoversiontype WHERE id = %s AND id >= 40801", (old_id,))
                removed_types += 1

        adapter.commit()
        log("Normalized {} videoversion rows and removed {} orphaned custom types.".format(
            updated_rows, removed_types
        ))
        return updated_rows, removed_types, backup_info
    except Exception as e:
        log("Error normalizing DB labels, rolling back: {}".format(e), "error")
        adapter.rollback()
        raise
