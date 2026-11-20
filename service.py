# -*- coding: utf-8 -*-
"""
Background service for script.metadata.updatarr.
Handles scheduled runs, scan-completion triggers, and startup runs.
"""
import sys
import os
import time

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if ADDON_DIR not in sys.path:
    sys.path.insert(0, ADDON_DIR)

from lib.kodi_compat import is_kodi, log, show_notification
from lib.config import AddonSettings, resolve_database_config
from lib.db_adapter import get_adapter_for_config
from lib.version_updater import VersionUpdater
from lib.metadata_updater import MetadataUpdater

try:
    import xbmc
    HAS_XBMC = True
except ImportError:
    HAS_XBMC = False


def run_silent_update():
    """Performs an automated background scan and update."""
    log("Running automated background version update...")
    settings = AddonSettings()
    config = resolve_database_config(settings)

    try:
        adapter = get_adapter_for_config(config)
        adapter.connect()
    except Exception as e:
        log("Background update: database connection failed: {}".format(e), "error")
        return 0

    try:
        total_updated = 0
        updater = VersionUpdater(adapter, settings)
        scan_res = updater.scan_for_changes()
        changes = scan_res.changes
        if changes:
            res = updater.apply_changes(changes)
            total_updated += res.applied_count
            log("Background update applied: {} version(s) updated.".format(res.applied_count))

        meta_updater = MetadataUpdater(adapter, settings)
        _, meta_changes = meta_updater.scan_missing_metadata(
            include_seasons=True,
            include_movies=getattr(settings, "update_missing_movie_plots", True),
            include_tvshows=getattr(settings, "update_missing_tv_metadata", True),
            include_episodes=getattr(settings, "update_missing_tv_metadata", True)
        )
        if meta_changes:
            meta_applied, _ = meta_updater.apply_metadata_changes(meta_changes)
            total_updated += meta_applied
            log("Background update applied: {} missing metadata item(s).".format(meta_applied))

        if settings.notify_on_update and total_updated > 0:
            show_notification(
                "Metadata Updatarr",
                "Updated {} metadata/version item(s)".format(total_updated)
            )
        if HAS_XBMC and total_updated > 0:
            xbmc.executebuiltin("Container.Refresh")
        return total_updated
    except Exception as e:
        log("Background update failed with exception: {}".format(e), "error")
        return 0
    finally:
        adapter.close()


if HAS_XBMC:
    class UpdatarrMonitor(xbmc.Monitor):
        def __init__(self):
            super().__init__()
            self.scan_triggered = False

        def onScanFinished(self, library):
            if library == "video":
                log("Video library scan finished detected by service.")
                self.scan_triggered = True


def service_main():
    log("Metadata Updatarr service started.")
    if not HAS_XBMC:
        log("Not running inside Kodi, service exiting.", "debug")
        return

    monitor = UpdatarrMonitor()
    settings = AddonSettings()

    # Startup run if enabled
    if settings.schedule_enabled and settings.run_on_startup:
        # Give Kodi a moment to initialize
        if not monitor.waitForAbort(15):
            run_silent_update()

    last_schedule_run = time.time()

    # Main loop (checks every 10 seconds)
    while not monitor.abortRequested():
        if monitor.waitForAbort(10):
            break

        settings = AddonSettings()
        if not settings.schedule_enabled:
            monitor.scan_triggered = False
            continue

        # Check library scan trigger
        if monitor.scan_triggered:
            monitor.scan_triggered = False
            if settings.run_on_scan:
                # Wait 5 seconds after scan ends before updating
                if not monitor.waitForAbort(5):
                    run_silent_update()
                    last_schedule_run = time.time()
                continue

        # Check periodic schedule interval
        interval_seconds = max(1, settings.schedule_interval) * 3600
        if (time.time() - last_schedule_run) >= interval_seconds:
            run_silent_update()
            last_schedule_run = time.time()

    log("Metadata Updatarr service stopped.")


if __name__ == "__main__":
    service_main()
