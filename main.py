# -*- coding: utf-8 -*-
"""
Main execution script and GUI controller for script.metadata.updatarr.
"""
import sys
import os

# Add addon directory to sys.path
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

from lib.kodi_compat import (
    is_kodi, get_addon, log, show_notification, show_ok_dialog,
    show_yesno_dialog, show_select_dialog, show_text_dialog, ProgressDialog
)
from lib.config import AddonSettings, resolve_database_config
from lib.db_adapter import get_adapter_for_config, list_mysql_video_databases
from lib.backup_manager import BackupManager
from lib.version_updater import VersionUpdater, find_normalizable_labels, apply_label_normalization
from lib.default_version_manager import get_multiversion_items, swap_default_version
from lib.duplicate_grouper import find_duplicate_movies, group_duplicate_movies
from lib.metadata_updater import MetadataUpdater
from lib.run_logger import build_summary_lines, write_run_log, get_log_dir


def run_scan_and_update(dry_run=False, force_full=False):
    settings = AddonSettings()
    config = resolve_database_config(settings)
    log("Resolved DB configuration: {}".format(config))

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Failed to connect to database: {}".format(e), "error")
        show_ok_dialog(
            "Connection Error",
            "Could not connect to Kodi video database:\n{}\n\nPlease check addon settings.".format(e)
        )
        return

    targets = []
    if settings.scan_movies:
        targets.append("Movies")
    if settings.scan_episodes:
        targets.append("TV")
    targets_label = " + ".join(targets) if targets else "None"

    db_label = adapter.get_display_name()
    mode_label = "Dry Run: " if dry_run else ""
    progress = ProgressDialog(
        "Metadata Updatarr",
        "{}Scanning {} [{}]...".format(mode_label, db_label, targets_label)
    )
    updater = VersionUpdater(adapter, settings)

    def on_progress(current, total, title, found_count=0):
        pct = (float(current) / float(total)) * 100 if total > 0 else 0
        progress.update(
            pct,
            "{}Scanning {} [{}] ({}/{}) | Found: {}".format(
                mode_label, db_label, targets_label, current, total, found_count
            ),
            title
        )
        return not progress.is_canceled()

    try:
        scan_res = updater.scan_for_changes(progress_callback=on_progress, force_full=force_full)
    finally:
        progress.close()

    changes = scan_res.changes
    if not changes:
        show_ok_dialog("Scan Complete", "Your video versions are already up to date! No changes needed.")
        adapter.close()
        return

    # Build summary lines for display
    summary_lines = [
        "Found {} version update(s) to apply:".format(len(changes)),
        ""
    ]
    for ch in changes[:10]:
        tag = "[Movie]" if ch.media_type == "movie" else "[Episode]"
        summary_lines.append(u"• {} {}: '{}' -> '{}'".format(
            tag, ch.title, ch.current_type, ch.target_type
        ))
    if len(changes) > 10:
        summary_lines.append("... and {} more.".format(len(changes) - 10))

    if dry_run:
        full_report = build_summary_lines(
            config=config,
            total_scanned=scan_res.total_scanned,
            changes=changes,
            is_dry_run=True
        )
        show_text_dialog("Dry Run Preview", "\n".join(full_report))
        if is_kodi():
            import xbmc
            xbmc.sleep(200)

        dry_choice = show_select_dialog(
            "Dry Run Complete ({} updates found)".format(len(changes)),
            [
                "Apply {} Changes to {}".format(len(changes), db_label),
                "Close without Applying"
            ]
        )
        if dry_choice != 0:
            adapter.close()
            return
    else:
        # Real run: confirm with user first
        confirm_msg = "{}\n\nA backup will be created before any changes are written. Proceed with updating the database?".format(
            "\n".join(summary_lines)
        )
        if not show_yesno_dialog("Confirm Database Update", confirm_msg):
            adapter.close()
            return

    # Apply updates with progress dialog
    apply_progress = ProgressDialog(
        "Metadata Updatarr",
        "Creating backup & updating {}...".format(db_label)
    )

    def on_apply_progress(current, total, title):
        pct = (float(current) / float(total)) * 100 if total > 0 else 0
        apply_progress.update(
            pct,
            "Updating {} ({}/{})...".format(db_label, current, total),
            title
        )
        return not apply_progress.is_canceled()

    try:
        res = updater.apply_changes(changes, progress_callback=on_apply_progress)
        apply_progress.close()

        summary_report = build_summary_lines(
            config=config,
            total_scanned=scan_res.total_scanned,
            changes=changes,
            applied_count=res.applied_count,
            new_types=res.new_types_created,
            backup_info=res.backup_info
        )
        log_path = write_run_log(summary_report)
        if log_path:
            summary_report = build_summary_lines(
                config=config,
                total_scanned=scan_res.total_scanned,
                changes=changes,
                applied_count=res.applied_count,
                new_types=res.new_types_created,
                backup_info=res.backup_info,
                log_path=log_path
            )

        show_text_dialog("Update Summary", "\n".join(summary_report))
        show_notification("Metadata Updatarr", "Updated {} versions".format(res.applied_count))
        if is_kodi():
            import xbmc
            xbmc.executebuiltin("Container.Refresh")
    except Exception as e:
        apply_progress.close()
        log("Error during update: {}".format(e), "error")
        err_report = build_summary_lines(
            config=config,
            total_scanned=scan_res.total_scanned,
            changes=changes,
            applied_count=0,
            error_msg=str(e)
        )
        log_path = write_run_log(err_report)
        err_msg = "An error occurred during database update:\n{}".format(e)
        if log_path:
            err_msg += "\n\nLog saved to:\n{}".format(log_path)
        show_ok_dialog("Update Failed", err_msg)
    finally:
        adapter.close()


def run_manual_backup():
    settings = AddonSettings()
    config = resolve_database_config(settings)
    log("Resolved DB configuration for manual backup: {}".format(config))

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Failed to connect to database: {}".format(e), "error")
        show_ok_dialog("Connection Error", "Could not connect to database for backup:\n{}".format(e))
        return

    progress = ProgressDialog("Metadata Updatarr", "Creating database backup...")
    try:
        mgr = BackupManager(adapter, retention=settings.backup_retention)
        bak_info = mgr.create_backup()
        progress.close()
        show_ok_dialog(
            "Backup Successful",
            "Database backup created successfully!\n\nIdentifier: {}\nDetails: {}".format(
                bak_info.label, bak_info.extra_info
            )
        )
        show_notification("Metadata Updatarr", "Backup created")
    except Exception as e:
        progress.close()
        log("Error during backup: {}".format(e), "error")
        show_ok_dialog("Backup Failed", "Failed to create database backup:\n{}".format(e))
    finally:
        adapter.close()


def run_restore():
    settings = AddonSettings()
    config = resolve_database_config(settings)
    log("Resolving DB for restore: {}".format(config))

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Failed to connect to database: {}".format(e), "error")
        show_ok_dialog("Connection Error", "Could not connect to database for restore:\n{}".format(e))
        return

    mgr = BackupManager(adapter, retention=settings.backup_retention)
    backups = mgr.list_backups()

    if not backups:
        adapter.close()
        show_ok_dialog("Restore Backup", "No database backups were found.")
        return

    menu_items = []
    for b in backups:
        menu_items.append("{} | {}".format(b.label, b.extra_info))

    idx = show_select_dialog("Select a Backup to Restore", menu_items)
    if idx < 0 or idx >= len(backups):
        adapter.close()
        return

    selected_backup = backups[idx]
    confirm_text = "Are you sure you want to restore the database to:\n\n{}\n\n(A safety pre-restore backup will be created automatically before restoring)".format(
        selected_backup.label
    )
    if not show_yesno_dialog("Confirm Restore", confirm_text):
        adapter.close()
        return

    progress = ProgressDialog("Restoring Backup", "Restoring video version database...")
    try:
        mgr.restore_backup(selected_backup)
        progress.close()
        show_ok_dialog(
            "Restore Complete",
            "Database has been successfully restored to:\n{}".format(selected_backup.label)
        )
        show_notification("Metadata Updatarr", "Restore complete")
        if is_kodi():
            import xbmc
            xbmc.executebuiltin("Container.Refresh")
    except Exception as e:
        progress.close()
        log("Error during restore: {}".format(e), "error")
        show_ok_dialog("Restore Failed", "Failed to restore backup:\n{}".format(e))
    finally:
        adapter.close()


def run_manage_default_versions():
    settings = AddonSettings()
    config = resolve_database_config(settings)
    log("Resolving DB for manage default versions: {}".format(config))

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Failed to connect to database: {}".format(e), "error")
        show_ok_dialog("Connection Error", "Could not connect to database:\n{}".format(e))
        return

    try:
        while True:
            # Fetch all multi-version items
            items = get_multiversion_items(adapter)
            if not items:
                show_ok_dialog("Default Versions", "No multi-version movies or episodes found in the database.")
                break

            display_items = []
            for it in items:
                tag = "🎬 [Movie]" if it.media_type == "movie" else "📺 [TV]"
                def_ver = it.get_default_version()
                def_name = def_ver.type_name if def_ver else "Unknown"
                display_items.append(u"{} {}  (Default: {})".format(tag, it.title, def_name))

            selected_idx = show_select_dialog("Select Title to Manage Versions", display_items)
            if selected_idx < 0 or selected_idx >= len(items):
                break

            chosen_item = items[selected_idx]

            # Show versions for chosen item
            ver_labels = []
            for v in chosen_item.versions:
                star = "★ " if v.is_default else "  "
                status = " [ACTIVE DEFAULT]" if v.is_default else ""
                ver_labels.append(u"{}{}{} ({})".format(star, v.type_name, status, v.filename))

            v_idx = show_select_dialog(
                u"Select Default Version for: {}".format(chosen_item.title),
                ver_labels
            )
            if v_idx < 0 or v_idx >= len(chosen_item.versions):
                continue

            chosen_ver = chosen_item.versions[v_idx]
            if chosen_ver.is_default:
                show_notification("Metadata Updatarr", "'{}' is already the default version.".format(chosen_ver.type_name))
                continue

            confirm_msg = u"Set '{}' as the new default version for:\n\n{}\n\nFile: {}\n\n(A database backup will be created before updating)".format(
                chosen_ver.type_name, chosen_item.title, chosen_ver.filename
            )
            if not show_yesno_dialog("Confirm Default Version Change", confirm_msg):
                continue

            try:
                swap_default_version(
                    adapter,
                    id_media=chosen_item.id_media,
                    media_type=chosen_item.media_type,
                    target_file_id=chosen_ver.id_file,
                    sync_watched=settings.sync_watched_on_swap,
                    retention=settings.backup_retention
                )
                show_ok_dialog(
                    "Default Version Updated",
                    u"Successfully set default version to:\n'{}'\n\nTitle: {}".format(
                        chosen_ver.type_name, chosen_item.title
                    )
                )
                show_notification("Metadata Updatarr", u"Default set to '{}'".format(chosen_ver.type_name))
                if is_kodi():
                    import xbmc
                    xbmc.executebuiltin("Container.Refresh")
            except Exception as e:
                log("Error swapping default version: {}".format(e), "error")
                show_ok_dialog("Update Failed", "Could not swap default version:\n{}".format(e))

    finally:
        adapter.close()


def run_normalize_db_labels():
    settings = AddonSettings()
    config = resolve_database_config(settings)
    log("Resolving DB for normalizing version labels: {}".format(config))

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Failed to connect to database: {}".format(e), "error")
        show_ok_dialog("Connection Error", "Could not connect to database:\n{}".format(e))
        return

    db_label = adapter.get_display_name()
    try:
        mappings = find_normalizable_labels(adapter)
        if not mappings:
            show_ok_dialog(
                "Normalize Version Labels",
                "All version labels in {} are already normalized!".format(db_label)
            )
            return

        total_rows = sum(m[3] for m in mappings)
        preview_lines = [
            "=== Normalize Version Labels: {} ===".format(db_label),
            "Label groups to normalize: {}".format(len(mappings)),
            "Total video version rows affected: {}".format(total_rows),
            "--------------------------------------------------",
            ""
        ]
        for old_id, old_name, target_name, cnt in mappings:
            preview_lines.append(u"• '{}' (ID: {}, {} item(s)) -> '{}'".format(
                old_name, old_id, cnt, target_name
            ))

        show_text_dialog("Normalize Version Labels Preview", "\n".join(preview_lines))
        if is_kodi():
            import xbmc
            xbmc.sleep(200)

        choice = show_select_dialog(
            "Apply Label Normalization ({} rows)?".format(total_rows),
            [
                "Apply Normalization to {}".format(db_label),
                "Cancel"
            ]
        )
        if choice != 0:
            return

        updated_rows, removed_types, backup_info = apply_label_normalization(
            adapter, mappings, retention=settings.backup_retention
        )

        report_lines = [
            "=== Normalize Version Labels: Complete ===",
            "Database: {}".format(db_label),
            "Rows updated: {}".format(updated_rows),
            "Orphaned custom types removed: {}".format(removed_types),
            "Backup: {}".format(backup_info.label if backup_info else "None"),
            "--------------------------------------------------",
            ""
        ] + [
            u"• '{}' -> '{}' ({} item(s))".format(old_name, target_name, cnt)
            for _oid, old_name, target_name, cnt in mappings
        ]
        log_path = write_run_log(report_lines)
        if log_path:
            report_lines.insert(5, "Log file: {}".format(log_path))

        show_text_dialog("Normalization Complete", "\n".join(report_lines))
        show_notification("Metadata Updatarr", "Normalized {} version row(s)".format(updated_rows))
        if is_kodi():
            import xbmc
            xbmc.executebuiltin("Container.Refresh")
    except Exception as e:
        log("Error normalizing DB labels: {}".format(e), "error")
        show_ok_dialog("Normalization Failed", "An error occurred while normalizing labels:\n{}".format(e))
    finally:
        adapter.close()


def run_group_duplicates():
    settings = AddonSettings()
    config = resolve_database_config(settings)
    log("Resolving DB for grouping duplicate movies: {}".format(config))

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Failed to connect to database: {}".format(e), "error")
        show_ok_dialog("Connection Error", "Could not connect to database:\n{}".format(e))
        return

    db_label = adapter.get_display_name()
    try:
        groups = find_duplicate_movies(adapter)
        if not groups:
            show_ok_dialog(
                "Group Duplicate Movies",
                "No duplicate movie entries (matching TMDB/IMDB ID) were found in {}.".format(db_label)
            )
            return

        total_extra = sum(len(g.secondaries) for g in groups)
        preview_lines = [
            "=== Group Duplicate Movies Preview: {} ===".format(db_label),
            "Duplicate movie groups found: {}".format(len(groups)),
            "Extra movie entries to merge into versions: {}".format(total_extra),
            "--------------------------------------------------",
            ""
        ]
        for g in groups:
            preview_lines.append(u"• {} [{}] ({} files)".format(
                g.title, g.key_label, 1 + len(g.secondaries)
            ))
            preview_lines.append(u"  Primary: {}".format(g.primary["strFilename"]))
            for s in g.secondaries:
                preview_lines.append(u"  + Merge: {}".format(s["strFilename"]))
            preview_lines.append("")

        show_text_dialog("Group Duplicate Movies Preview", "\n".join(preview_lines))
        if is_kodi():
            import xbmc
            xbmc.sleep(200)

        choice = show_select_dialog(
            "Group {} Duplicate Movies?".format(len(groups)),
            [
                "Merge {} Duplicate Groups in {}".format(len(groups), db_label),
                "Cancel"
            ]
        )
        if choice != 0:
            return

        merged_movies, merged_files, backup_info = group_duplicate_movies(
            adapter, groups, retention=settings.backup_retention
        )

        report_lines = [
            "=== Group Duplicate Movies: Complete ===",
            "Database: {}".format(db_label),
            "Movie groups merged: {}".format(merged_movies),
            "Files linked as versions: {}".format(merged_files),
            "Backup: {}".format(backup_info.label if backup_info else "None"),
            "--------------------------------------------------",
            ""
        ] + [
            u"• {} ({} files)".format(g.title, 1 + len(g.secondaries))
            for g in groups
        ]
        log_path = write_run_log(report_lines)
        if log_path:
            report_lines.insert(5, "Log file: {}".format(log_path))

        show_text_dialog("Grouping Complete", "\n".join(report_lines))
        show_notification("Metadata Updatarr", "Grouped {} duplicate movies".format(merged_movies))
        if is_kodi():
            import xbmc
            xbmc.executebuiltin("Container.Refresh")
    except Exception as e:
        log("Error grouping duplicate movies: {}".format(e), "error")
        show_ok_dialog("Grouping Failed", "An error occurred while grouping duplicate movies:\n{}".format(e))
    finally:
        adapter.close()


def run_switch_database():
    settings = AddonSettings()
    config = resolve_database_config(settings)
    if config.db_type != "mysql":
        show_ok_dialog("Switch Database", "Database switching is only available for MariaDB/MySQL setups.")
        return

    try:
        dbs = list_mysql_video_databases(
            config.host, config.port, config.user, config.password, base_name=config.name
        )
    except Exception as e:
        show_ok_dialog("Connection Error", "Could not list databases on {}:\n{}".format(config.host, e))
        return

    if not dbs:
        show_ok_dialog("Switch Database", "No Kodi video databases found on {}.".format(config.host))
        return

    current_override = getattr(settings, "db_name_override", "") or ""
    options = ["Auto-detect (by running Kodi version)" + (" [ACTIVE]" if not current_override else "")]
    for db in dbs:
        tag = " [ACTIVE]" if current_override == db else ""
        options.append("{}{}".format(db, tag))

    idx = show_select_dialog("Select Target Database ({})".format(config.host), options)
    if idx < 0:
        return

    addon = get_addon()
    if not addon:
        return

    if idx == 0:
        addon.setSetting("db_auto_detect", "true")
        addon.setSetting("db_name_override", "")
        show_notification("Metadata Updatarr", "Database set to Auto-detect")
    else:
        chosen_db = dbs[idx - 1]
        addon.setSetting("db_auto_detect", "true")
        addon.setSetting("db_name_override", chosen_db)
        show_notification("Metadata Updatarr", "Target DB set to {}".format(chosen_db))


def run_view_logs():
    log_dir = get_log_dir()
    if not os.path.exists(log_dir):
        show_ok_dialog("Run Logs", "No run logs found yet.")
        return

    files = [
        f for f in os.listdir(log_dir)
        if f.startswith("updatarr_") and f.endswith(".log")
    ]
    files.sort(reverse=True)
    if not files:
        show_ok_dialog("Run Logs", "No run logs found yet.")
        return

    idx = show_select_dialog("Select a Run Log to View", files[:20])
    if idx < 0 or idx >= len(files):
        return

    chosen_path = os.path.join(log_dir, files[idx])
    try:
        with open(chosen_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        show_text_dialog(files[idx], content)
    except Exception as e:
        show_ok_dialog("Error", "Could not read log file:\n{}".format(e))


def run_missing_metadata_scan(dry_run=False, scope=None, force_full=False):
    settings = AddonSettings()

    if scope is None:
        scope_options = [
            "Seasons Only (Plots & Names - Fast)",
            "Movies Only (Metadata + Media Info)",
            "TV Shows Only (Plots, MPAA, Genres, Studios, Cast)",
            "Episodes Only (Plots, Runtime, Media Info)",
            "All Missing Metadata (Incremental)",
            "All Missing Metadata (Full Rescan)"
        ]
        s_idx = show_select_dialog(
            "Select Missing Metadata Scope{}".format(" (Dry Run)" if dry_run else ""),
            scope_options
        )
        if s_idx < 0:
            return
        scope_map = {
            0: ("seasons", False),
            1: ("movies", False),
            2: ("tvshows", False),
            3: ("episodes", False),
            4: ("all", False),
            5: ("all", True),
        }
        scope, force_full = scope_map[s_idx]

    include_seasons = scope in ("all", "seasons")
    include_movies = scope in ("all", "movies") and (scope == "movies" or getattr(settings, "update_missing_movie_plots", True))
    include_tvshows = scope in ("all", "tvshows") and (scope == "tvshows" or getattr(settings, "update_missing_tv_metadata", True))
    include_episodes = scope in ("all", "episodes") and (scope == "episodes" or getattr(settings, "update_missing_tv_metadata", True))

    config = resolve_database_config(settings)
    log("Resolved DB configuration for missing metadata (scope={}): {}".format(scope, config))

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Failed to connect to database: {}".format(e), "error")
        show_ok_dialog("Connection Error", "Could not connect to database:\n{}".format(e))
        return

    db_label = adapter.get_display_name()
    mode_label = "Dry Run: " if dry_run else ""
    progress = ProgressDialog(
        "Metadata Updatarr",
        "{}Scanning {} [{}]...".format(mode_label, db_label, scope)
    )
    updater = MetadataUpdater(adapter, settings)

    def on_progress(current, total, title, found_count=0):
        pct = (float(current) / float(total)) * 100 if total > 0 else 0
        progress.update(
            pct,
            "{}Metadata [{}] {} ({}/{}) | Found: {}".format(mode_label, scope, db_label, current, total, found_count),
            title
        )
        return not progress.is_canceled()

    try:
        total_scanned, changes = updater.scan_missing_metadata(
            progress_callback=on_progress,
            include_seasons=include_seasons,
            include_movies=include_movies,
            include_tvshows=include_tvshows,
            include_episodes=include_episodes,
            force_full=force_full
        )
    finally:
        progress.close()

    if not changes:
        adapter.close()
        show_ok_dialog("Missing Metadata", "No missing metadata found in {}!".format(db_label))
        return

    preview_lines = [
        "=== Missing Metadata {}: {} ===".format("Preview" if dry_run else "Changes", db_label),
        "Items checked: {}".format(total_scanned),
        "Updates found: {}".format(len(changes)),
        "--------------------------------------------------",
        ""
    ]
    for ch in changes:
        preview_lines.append(u"• [{}] {} ({})\n  {}\n".format(
            ch.target_table, ch.media_label, ch.source, ch.summary_parts()
        ))

    if dry_run:
        show_text_dialog("Missing Metadata Preview", "\n".join(preview_lines))
        if is_kodi():
            import xbmc
            xbmc.sleep(200)
        choice = show_select_dialog(
            "Missing Metadata ({} updates found)".format(len(changes)),
            [
                "Apply {} Metadata Updates to {}".format(len(changes), db_label),
                "Close without Applying"
            ]
        )
        if choice != 0:
            adapter.close()
            return
    else:
        choice = show_select_dialog(
            "Apply {} Missing Metadata Updates?".format(len(changes)),
            [
                "Apply {} Updates to {} (Backup First)".format(len(changes), db_label),
                "Cancel"
            ]
        )
        if choice != 0:
            adapter.close()
            return

    apply_progress = ProgressDialog("Metadata Updatarr", "Backing up & updating {}...".format(db_label))

    def on_apply(current, total, label):
        pct = (float(current) / float(total)) * 100 if total > 0 else 0
        apply_progress.update(pct, "Updating metadata ({}/{})...".format(current, total), label)
        return not apply_progress.is_canceled()

    try:
        applied, backup_info = updater.apply_metadata_changes(changes, progress_callback=on_apply)
        apply_progress.close()

        report_lines = [
            "=== Missing Metadata Update Summary ===",
            "Database: {}".format(db_label),
            "Updates applied: {}".format(applied),
            "Backup: {}".format(backup_info.label if backup_info else "None"),
            "--------------------------------------------------",
            ""
        ] + preview_lines[5:]
        log_path = write_run_log(report_lines)
        if log_path:
            report_lines.insert(4, "Log file: {}".format(log_path))

        show_text_dialog("Metadata Update Summary", "\n".join(report_lines))
        show_notification("Metadata Updatarr", "Updated {} metadata item(s)".format(applied))
        if is_kodi():
            import xbmc
            xbmc.executebuiltin("Container.Refresh")
    except Exception as e:
        apply_progress.close()
        log("Error updating missing metadata: {}".format(e), "error")
        show_ok_dialog("Update Failed", "An error occurred:\n{}".format(e))
    finally:
        adapter.close()


def run_all_updatarr():
    run_scan_and_update(dry_run=False, force_full=False)
    run_missing_metadata_scan(dry_run=False, scope="all", force_full=False)


def run_test_db_connection():
    settings = AddonSettings()
    config = resolve_database_config(settings)
    log("Testing DB connection: {}".format(config))
    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
        db_label = adapter.get_display_name()
        tables = set(adapter.get_tables())
        movie_cnt = adapter.query("SELECT COUNT(*) AS c FROM movie")[0]["c"] if "movie" in tables else 0
        show_cnt = adapter.query("SELECT COUNT(*) AS c FROM tvshow")[0]["c"] if "tvshow" in tables else 0
        season_cnt = adapter.query("SELECT COUNT(*) AS c FROM seasons WHERE season >= 0")[0]["c"] if "seasons" in tables else 0
        ver_str = "SQLite"
        if config.db_type == "mysql":
            try:
                vrow = adapter.query("SELECT VERSION() AS v")
                ver_str = "MariaDB / MySQL {}".format(vrow[0]["v"]) if vrow else "MariaDB / MySQL"
            except Exception:
                ver_str = "MariaDB / MySQL"
        adapter.close()
        show_ok_dialog(
            "Connection Successful",
            "Connected to: {}\nEngine: {}\nMovies: {} | TV Shows: {} | Seasons: {}".format(
                db_label, ver_str, movie_cnt, show_cnt, season_cnt
            )
        )
    except Exception as e:
        log("DB connection test failed: {}".format(e), "error")
        show_ok_dialog(
            "Connection Failed",
            "Could not connect using current settings:\n{}\n\nTarget: {}".format(e, config)
        )


def open_settings():
    addon = get_addon()
    if addon:
        addon.openSettings()


def main():
    args = sys.argv[1:] if len(sys.argv) > 1 else []

    if "--all" in args or "action=all" in args:
        run_all_updatarr()
        return
    elif "--scan" in args or "action=scan" in args:
        run_scan_and_update(dry_run=False)
        return
    elif "--full-scan" in args or "action=fullscan" in args:
        run_scan_and_update(dry_run=False, force_full=True)
        return
    elif "--dry-run" in args or "action=dryrun" in args:
        run_scan_and_update(dry_run=True)
        return
    elif "--metadata" in args or "action=metadata" in args:
        run_missing_metadata_scan(dry_run=False)
        return
    elif "--metadata-dry-run" in args or "action=metadatadryrun" in args:
        run_missing_metadata_scan(dry_run=True)
        return
    elif "--defaults" in args or "action=defaults" in args:
        run_manage_default_versions()
        return
    elif "--normalize" in args or "action=normalize" in args:
        run_normalize_db_labels()
        return
    elif "--group-duplicates" in args or "action=groupduplicates" in args:
        run_group_duplicates()
        return
    elif "--switch-db" in args or "action=switchdb" in args:
        run_switch_database()
        return
    elif "--logs" in args or "action=logs" in args:
        run_view_logs()
        return
    elif "--backup" in args or "action=backup" in args:
        run_manual_backup()
        return
    elif "--restore" in args or "action=restore" in args:
        run_restore()
        return
    elif "--test-db" in args or "action=testdb" in args:
        run_test_db_connection()
        return

    # Interactive GUI Menu
    menu_options = [
        "Run All (Video Versions + Missing Metadata)",
        "Video Versions: Scan & Update (Incremental)",
        "Video Versions: Full Rescan & Update",
        "Video Versions: Dry Run (Preview Changes)",
        "Video Versions: Manage Default Versions",
        "Video Versions: Normalize Labels in DB",
        "Video Versions: Group Duplicate Movies",
        "Missing Metadata: Scan & Update (Seasons + Movies)",
        "Missing Metadata: Dry Run (Preview)",
        "Switch Target Database",
        "View Run Logs",
        "Create Database Backup",
        "Restore Database Backup",
        "Settings"
    ]

    choice = show_select_dialog("Metadata Updatarr", menu_options)
    if choice == 0:
        run_all_updatarr()
    elif choice == 1:
        run_scan_and_update(dry_run=False, force_full=False)
    elif choice == 2:
        run_scan_and_update(dry_run=False, force_full=True)
    elif choice == 3:
        run_scan_and_update(dry_run=True, force_full=True)
    elif choice == 4:
        run_manage_default_versions()
    elif choice == 5:
        run_normalize_db_labels()
    elif choice == 6:
        run_group_duplicates()
    elif choice == 7:
        run_missing_metadata_scan(dry_run=False)
    elif choice == 8:
        run_missing_metadata_scan(dry_run=True)
    elif choice == 9:
        run_switch_database()
    elif choice == 10:
        run_view_logs()
    elif choice == 11:
        run_manual_backup()
    elif choice == 12:
        run_restore()
    elif choice == 13:
        open_settings()


if __name__ == "__main__":
    main()
