# -*- coding: utf-8 -*-
"""
Context menu handler for updating video versions and swapping default versions
of a single movie or TV episode in Kodi.
"""
import sys
import os

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

from lib.kodi_compat import (
    is_kodi, log, show_notification, show_ok_dialog,
    show_yesno_dialog, show_select_dialog
)
from lib.config import AddonSettings, resolve_database_config
from lib.db_adapter import get_adapter_for_config
from lib.nfo_parser import find_edition_from_nfo
from lib.filename_parser import parse_edition_from_filename
from lib.backup_manager import BackupManager
from lib.default_version_manager import swap_default_version

try:
    import xbmc
    import xbmcgui
    HAS_XBMC = True
except ImportError:
    HAS_XBMC = False


def run_context_update():
    if not HAS_XBMC:
        log("Context menu can only be run inside Kodi.", "warning")
        return

    # Identify selected item from Kodi ListItem
    db_id_str = xbmc.getInfoLabel("ListItem.DBID")
    raw_type = (xbmc.getInfoLabel("ListItem.DBType") or "").lower()
    title = xbmc.getInfoLabel("ListItem.Title") or xbmc.getInfoLabel("ListItem.Label") or "Selected Item"

    if not db_id_str or not db_id_str.isdigit():
        show_notification("Metadata Updatarr", "Could not identify selected video.")
        return

    id_media = int(db_id_str)
    media_type = "episode" if raw_type == "episode" else "movie"

    settings = AddonSettings()
    config = resolve_database_config(settings)

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Context menu DB error: {}".format(e), "error")
        show_ok_dialog("Connection Error", "Could not connect to database:\n{}".format(e))
        return

    try:
        # Query existing version associations and files for this media item
        if media_type == "movie":
            sql = """
            SELECT 
                m.idMovie AS idMedia,
                m.c00 AS title,
                m.idFile AS default_file_id,
                f.idFile,
                f.strFilename,
                p.strPath,
                v.idType,
                vt.name AS type_name,
                1 AS in_videoversion
            FROM movie m
            JOIN videoversion v ON m.idMovie = v.idMedia AND v.media_type = 'movie'
            JOIN files f ON v.idFile = f.idFile
            JOIN path p ON f.idPath = p.idPath
            LEFT JOIN videoversiontype vt ON v.idType = vt.id
            WHERE m.idMovie = %s

            UNION ALL

            SELECT 
                m.idMovie AS idMedia,
                m.c00 AS title,
                m.idFile AS default_file_id,
                f.idFile,
                f.strFilename,
                p.strPath,
                NULL AS idType,
                NULL AS type_name,
                0 AS in_videoversion
            FROM movie m
            JOIN files f ON m.idFile = f.idFile
            JOIN path p ON f.idPath = p.idPath
            LEFT JOIN videoversion v ON m.idFile = v.idFile
            WHERE m.idMovie = %s AND v.idFile IS NULL
            """
        else:
            sql = """
            SELECT 
                e.idEpisode AS idMedia,
                e.c00 AS title,
                e.idFile AS default_file_id,
                f.idFile,
                f.strFilename,
                p.strPath,
                v.idType,
                vt.name AS type_name,
                1 AS in_videoversion
            FROM episode e
            JOIN videoversion v ON e.idEpisode = v.idMedia AND v.media_type = 'episode'
            JOIN files f ON v.idFile = f.idFile
            JOIN path p ON f.idPath = p.idPath
            LEFT JOIN videoversiontype vt ON v.idType = vt.id
            WHERE e.idEpisode = %s

            UNION ALL

            SELECT 
                e.idEpisode AS idMedia,
                e.c00 AS title,
                e.idFile AS default_file_id,
                f.idFile,
                f.strFilename,
                p.strPath,
                NULL AS idType,
                NULL AS type_name,
                0 AS in_videoversion
            FROM episode e
            JOIN files f ON e.idFile = f.idFile
            JOIN path p ON f.idPath = p.idPath
            LEFT JOIN videoversion v ON e.idFile = v.idFile
            WHERE e.idEpisode = %s AND v.idFile IS NULL
            """

        rows = adapter.query(sql, (id_media, id_media))
        if not rows:
            show_notification("Metadata Updatarr", "No media files found for this item.")
            return

        # Check if caller specifically asked for swap_default via argument
        args = sys.argv[1:] if len(sys.argv) > 1 else []
        action_swap = any("swap" in a.lower() or "default" in a.lower() for a in args)

        has_multiple_versions = len(rows) > 1

        if has_multiple_versions and not action_swap:
            # Offer user action choice
            cur_default_row = next((r for r in rows if r["idFile"] == r["default_file_id"]), None)
            cur_default_name = cur_default_row["type_name"] if (cur_default_row and cur_default_row["type_name"]) else "Default"
            main_menu = [
                u"🌟 Swap Default Version (Active: {})".format(cur_default_name),
                u"🏷️ Update Version Names / Tags"
            ]
            action_choice = show_select_dialog("Metadata Updatarr: {}".format(title), main_menu)
            if action_choice == 0:
                action_swap = True
            elif action_choice < 0:
                return

        # 1. SWAP DEFAULT VERSION FLOW
        if action_swap and has_multiple_versions:
            ver_options = []
            for r in rows:
                is_def = (r["idFile"] == r["default_file_id"])
                star = "★ " if is_def else "  "
                tag = " [ACTIVE DEFAULT]" if is_def else ""
                v_name = r["type_name"] or "Standard Edition"
                ver_options.append(u"{}{}{} ({})".format(star, v_name, tag, r["strFilename"]))

            v_choice = show_select_dialog("Select New Default Version: {}".format(title), ver_options)
            if v_choice < 0 or v_choice >= len(rows):
                return

            chosen_row = rows[v_choice]
            if chosen_row["idFile"] == chosen_row["default_file_id"]:
                show_notification("Metadata Updatarr", "Already the default version.")
                return

            confirm = show_yesno_dialog(
                "Confirm Default Swap",
                u"Set '{}' as the new default for:\n\n{}\n\nFile: {}".format(
                    chosen_row["type_name"] or "Selected Version",
                    title,
                    chosen_row["strFilename"]
                )
            )
            if not confirm:
                return

            swap_default_version(
                adapter,
                id_media=id_media,
                media_type=media_type,
                target_file_id=chosen_row["idFile"],
                sync_watched=settings.sync_watched_on_swap,
                retention=settings.backup_retention
            )
            show_notification("Metadata Updatarr", "Default version updated!")
            xbmc.executebuiltin("Container.Refresh")
            return

        # 2. UPDATE VERSION NAMES / TAGS FLOW
        type_rows = adapter.query("SELECT id, name FROM videoversiontype WHERE itemType = 1 ORDER BY name ASC;")
        type_dict = {r["name"].lower(): r["id"] for r in type_rows if r["name"]}
        max_type_id = max([r["id"] for r in type_rows] + [40400])

        for item in rows:
            filename = item["strFilename"]
            path = item["strPath"]
            current_type = item["type_name"] or "None"
            in_videoversion = bool(item["in_videoversion"])
            id_file = item["idFile"]

            detected = None
            if settings.scan_nfo:
                detected = find_edition_from_nfo(path, filename)
            if not detected and settings.scan_filenames:
                detected = parse_edition_from_filename(filename)
                if not detected and path:
                    detected = parse_edition_from_filename(os.path.basename(path.rstrip("/\\")))

            options = []
            if detected:
                options.append(u"✅ Set to detected: '{}'".format(detected))
            options.append(u"📋 Choose from existing version types...")
            options.append(u"✏️ Enter custom version name...")

            dlg_title = "Update Version: {} ({})".format(title, filename)
            choice = show_select_dialog(dlg_title, options)
            if choice < 0:
                continue

            target_name = None
            if detected and choice == 0:
                target_name = detected
            elif (detected and choice == 1) or (not detected and choice == 0):
                type_names = [r["name"] for r in type_rows if r["name"]]
                t_choice = show_select_dialog("Select Version Type", type_names)
                if t_choice >= 0:
                    target_name = type_names[t_choice]
            else:
                dialog = xbmcgui.Dialog()
                custom_name = dialog.input("Enter version name", defaultt=detected or "")
                if custom_name and custom_name.strip():
                    target_name = custom_name.strip()

            if not target_name:
                continue

            norm_target = target_name.strip()
            if norm_target.lower() in type_dict:
                target_id = type_dict[norm_target.lower()]
            else:
                next_id = 40801 if max_type_id < 40800 else (max_type_id + 1)
                adapter.execute(
                    "INSERT INTO videoversiontype (id, name, owner, itemType) VALUES (%s, %s, 2, 1)",
                    (next_id, norm_target)
                )
                target_id = next_id
                max_type_id = next_id
                type_dict[norm_target.lower()] = next_id

            mgr = BackupManager(adapter, retention=settings.backup_retention)
            mgr.create_backup()

            if in_videoversion:
                adapter.execute(
                    "UPDATE videoversion SET idType = %s WHERE idFile = %s AND media_type = %s",
                    (target_id, id_file, media_type)
                )
            else:
                adapter.execute(
                    "INSERT INTO videoversion (idFile, idMedia, media_type, itemType, idType) VALUES (%s, %s, %s, 1, %s)",
                    (id_file, id_media, media_type, target_id)
                )
            adapter.commit()

            show_notification("Metadata Updatarr", u"Updated to '{}'".format(norm_target))
            xbmc.executebuiltin("Container.Refresh")

    except Exception as e:
        log("Context menu update error: {}".format(e), "error")
        show_ok_dialog("Error", "Failed to update version:\n{}".format(e))
    finally:
        adapter.close()


if __name__ == "__main__":
    run_context_update()
