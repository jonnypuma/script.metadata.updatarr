# Changelog

All notable changes to **Metadata Updatarr** (`script.metadata.updatarr`) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.3] - 2026-11-21

### Fixed
- **Accent-Insensitive Actor/Genre/Studio Lookup (`lib/metadata_updater.py`):** Folded Unicode accents/diacritics in `actor_cache`, `genre_cache`, and `studio_cache` keys (`_norm_lookup_key`) and added duplicate-key fallback on `INSERT` so MariaDB's accent-insensitive unique indexes (`ix_actor_1`, etc., e.g. `Sinéad Matthews` vs `Sinead Matthews`) never fail with `1062 Duplicate entry`.

---

## [0.1.2] - 2026-11-21

### Fixed
- **Missing Metadata Preview Values (`lib/metadata_updater.py`):** `MetadataChange.summary_parts()` now displays human-readable field names and the actual detected values (e.g., `name: 'Season 1: Water'`, `plot: '...'`, `cast(12): ...`) in the Dry Run Preview and Summary modals instead of only bare column names.

---

## [0.1.1] - 2026-11-21

### Added
- **Expanded Metadata & Media Info (`lib/metadata_updater.py`):** Added missing TV show & episode metadata recovery (plot, MPAA, runtime, genres, studios, cast) and `streamdetails` media info recovery from `.nfo` `<fileinfo><streamdetails>`.
- **Missing Metadata Scope Picker & Performance:**
  - Added per-scope selection (**Seasons Only**, **Movies Only**, **TV Shows Only**, **Episodes Only**, **All Incremental**, **All Full Rescan**).
  - Added per-folder `list_dir` NFO caching, incremental `metadata_scan_state.json`, thread-safety locks in `MetadataUpdater` and `TMDbFallbackClient`, IMDB-to-TMDB `/find` fallback resolution, and preloaded link caches (`genre_link`, `studio_link`, `actor_link`).
- **TMDB Rate Limiting (`lib/tmdbhelper_client.py`):** Added a thread-safe rolling-window `RateLimiter` capped at **47 req/s** (`50 req/s - 5%` safety margin) across both direct TMDB API calls and TMDbHelper fallback, plus `Retry-After` / exponential backoff on `HTTP 429`.
- **OMDb Tertiary Fallback & Batch DB Writes (`lib/omdb_client.py`, `lib/db_adapter.py`, `lib/metadata_updater.py`):**
  - Added `OMDbFallbackClient` (reads `omdb_api_key` or TMDbHelper's `omdb_apikey`) with a persisted daily cap of **950 requests/day** (`1,000 - 5%`) in `omdb_quota.json`.
  - Added `executemany()` batching in `SQLiteAdapter` and `MySQLAdapter` for `genre_link`, `studio_link`, `actor_link`, and `streamdetails` inserts.
- **Full Table Backup Coverage (`lib/backup_manager.py`):** MariaDB/MySQL backups snapshot and restore all touched metadata tables (`videoversion`, `videoversiontype`, `seasons`, `movie`, `tvshow`, `episode`, `streamdetails`).
- **Custom Artwork:** Generated distinct 512x512 `icon.png` and 1920x1080 `fanart.jpg` for **Metadata Updatarr**.

---

## [0.1.0] - 2026-11-20

### Added
- **Unified Add-on (`script.metadata.updatarr`):** Combines full Video Versions management (from `script.versions.updatarr` v0.2.0) with Missing Metadata recovery.
- **Season Plots & Names Updater (`lib/metadata_updater.py`):**
  - Scans `seasons` for missing plots (`seasons.plot`) and empty/generic names (`seasons.name`).
  - Reads local `seasonXX.nfo` and `tvshow.nfo` (`<namedseason>`, `<season>`) first, with automatic fallback to **TMDbHelper** (`plugin.video.themoviedb.helper`) or an optional TMDB API key (`lib/tmdbhelper_client.py`).
- **Missing Movie Plots & Taglines:** Fills missing `movie.c01` (plot) and `movie.c03` (tagline) from local `.nfo` files with TMDbHelper fallback.
- **Combined Run & Service (`main.py`, `service.py`):** Added **Run All (Video Versions + Missing Metadata)** plus dedicated **Missing Metadata** scan & dry-run menu items and background service support.
