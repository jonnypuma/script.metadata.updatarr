# script.metadata.updatarr

**Metadata Updatarr** is a unified Kodi add-on (compatible with Kodi 21 Omega, Kodi 22 Piers, and Kodi 23+) that combines **Video Versions** management with **Missing Metadata** recovery (Season plots/names, Movie plots/taglines) directly in Kodi's database.

- **Author:** Skippy-McSkipface
- **Website:** [jonnypuma/script.metadata.updatarr](https://github.com/jonnypuma/script.metadata.updatarr)

---

## Features

### 1. Missing Metadata Recovery (Seasons & Movies)
- **Season Plots & Names (`seasons.plot`, `seasons.name`):** Fills missing season overviews and generic/empty season titles.
- **Missing Movie Plots & Taglines (`movie.c01`, `movie.c03`):** Populates missing movie summaries and taglines.
- **Local NFO First, TMDbHelper Fallback:**
  1. Checks local `seasonXX.nfo`, `tvshow.nfo` (`<namedseason>`, `<season>`), `<filename>.nfo`, and `movie.nfo`.
  2. Falls back to **TMDbHelper** (`plugin.video.themoviedb.helper`) or an optional user-configured TMDB API key using Kodi's `uniqueid` `tmdb` ID.

### 2. Video Versions Management
- **Movies & TV Episodes:** Detects and assigns Video Versions from `<videoassettitle>`, `<edition>`, filename patterns, and `custom_mappings.json`.
- **Fast Incremental & Concurrent Scanning:** Uses cached folder listings (`list_dir`), 6-worker threaded NFO reads, and `scan_state.json`.
- **Shared-Folder NFO Smart Precedence:** Prioritizes sidecar `<filename>.nfo` and filename edition tags before a shared `movie.nfo`.
- **Resolution / HDR Quality Fallback:** Labels multi-version duplicates with no edition tag (e.g., `4K DV/HDR` vs `1080p`).
- **Auto-Group Duplicate Movies:** Merges separate movie entries sharing a TMDB/IMDB ID into a single Kodi Video Version entry.
- **Default Version Swapping & Label Normalization:** Swap the active default version (with optional watch-count sync) or normalize enum/placeholder labels in the DB.
- **Context Menu Integration:** Right-click or press `C` on any movie or episode to assign custom versions or swap the default.

### 3. Database, Backups & Logging
- **Kodi-Version-Aware Database Detection & Switcher:** Auto-detects MariaDB/MySQL (`advancedsettings.xml`) or SQLite (`MyVideos*.db`), scopes to the running Kodi version's schema, and lets you switch target schemas from the menu.
- **Guaranteed 3+ Backups & Restore:** Snapshots `videoversion`, `videoversiontype`, and `seasons` (or SQLite `.db`) before every write, retaining at least 3 backups.
- **GUI Run Log Viewer & Background Service:** View `updatarr_*.log` reports inside Kodi and optionally run automatically after library scans or on a schedule.

---

## CLI / JSON-RPC Shortcuts
```bash
python main.py --all
python main.py --scan
python main.py --full-scan
python main.py --dry-run
python main.py --metadata
python main.py --metadata-dry-run
python main.py --defaults
python main.py --normalize
python main.py --group-duplicates
python main.py --switch-db
python main.py --logs
python main.py --backup
python main.py --restore
```

---

## License

MIT License. See [addon.xml](addon.xml) for metadata.
