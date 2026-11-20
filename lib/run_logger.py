# -*- coding: utf-8 -*-
"""
Dedicated file logger for Metadata Updatarr runs.
Writes structured run reports and error logs to Kodi's addon_data/logs folder.
"""
import os
import datetime
from lib.kodi_compat import translate_path, make_dirs, log


MAX_LOG_FILES = 20


def get_log_dir():
    return translate_path("special://profile/addon_data/script.metadata.updatarr/logs/")


def _prune_old_logs(log_dir):
    try:
        files = [
            f for f in os.listdir(log_dir)
            if f.startswith("updatarr_") and f.endswith(".log")
        ]
        files.sort(reverse=True)
        for old_file in files[MAX_LOG_FILES:]:
            try:
                os.remove(os.path.join(log_dir, old_file))
            except Exception:
                pass
    except Exception:
        pass


def build_summary_lines(
    config,
    total_scanned,
    changes,
    applied_count=0,
    new_types=None,
    backup_info=None,
    error_msg=None,
    log_path=None,
    is_dry_run=False
):
    """Build a structured, readable report for both the GUI modal and log file."""
    new_types = new_types or []
    title = "Dry Run Report" if is_dry_run else "Update Summary"
    lines = [
        "=== Metadata Updatarr: {} ===".format(title),
        "Timestamp: {}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "Database: {}".format(config),
    ]

    if is_dry_run:
        lines.extend([
            "Total items scanned: {}".format(total_scanned),
            "Changes detected: {}".format(len(changes)),
        ])
    else:
        status = "FAILED (Rolled back)" if error_msg else "Success"
        lines.extend([
            "Status: {}".format(status),
            "Total items scanned: {}".format(total_scanned),
            "Applied updates: {}".format(applied_count),
            "New custom types created: {}".format(len(new_types)),
            "Backup: {}".format(backup_info.label if backup_info else "None"),
        ])

    if log_path:
        lines.append("Log file: {}".format(log_path))

    if error_msg:
        lines.extend(["", "ERROR: {}".format(error_msg)])

    lines.append("--------------------------------------------------")

    if new_types:
        lines.append("")
        lines.append("New Custom Version Types:")
        for type_id, type_name in new_types:
            lines.append(u"  + {} (ID: {})".format(type_name, type_id))

    if changes:
        lines.append("")
        lines.append("Version Changes ({}):".format(len(changes)))
        for ch in changes:
            tag = "[Movie]" if ch.media_type == "movie" else "[Episode]"
            lines.append(u"• {} {} [{}]\n  File: {}\n  '{}' -> '{}'\n".format(
                tag, ch.title, ch.id_media, ch.filename, ch.current_type, ch.target_type
            ))

    return lines


def write_run_log(lines):
    """Write report lines to a timestamped log file and return its path (or None on failure)."""
    try:
        log_dir = get_log_dir()
        make_dirs(log_dir)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(log_dir, "updatarr_{}.log".format(ts))
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        _prune_old_logs(log_dir)
        log("Run log written to: {}".format(log_path))
        return log_path
    except Exception as e:
        log("Failed to write run log: {}".format(e), "warning")
        return None
