# -*- coding: utf-8 -*-
import unittest
import os
import shutil
import tempfile
import sqlite3
from unittest.mock import patch

from lib.nfo_parser import parse_edition_from_xml, parse_edition_with_regex
from lib.filename_parser import parse_edition_from_filename
from lib.db_adapter import SQLiteAdapter
from lib.backup_manager import BackupManager, MIN_BACKUPS_RETAINED


class TestNfoParser(unittest.TestCase):
    def test_videoassettitle_tag(self):
        xml = "<movie><title>Lawrence of Arabia</title><videoassettitle>50th Anniversary Edition</videoassettitle></movie>"
        self.assertEqual(parse_edition_from_xml(xml), "50th Anniversary Edition")

    def test_edition_tag(self):
        xml = "<movie><title>Leon</title><edition>International Cut</edition></movie>"
        self.assertEqual(parse_edition_from_xml(xml), "International Cut")

    def test_nested_videoasset_title(self):
        xml = "<movie><videoasset><title>Director's Cut</title></videoasset></movie>"
        self.assertEqual(parse_edition_from_xml(xml), "Director's Cut")

    def test_regex_fallback(self):
        malformed = "<movie><unclosed><edition>Collector's Edition</edition>"
        self.assertEqual(parse_edition_with_regex(malformed), "Collector's Edition")

    def test_enum_and_placeholder_normalization(self):
        self.assertEqual(
            parse_edition_from_xml("<movie><edition>DIRECTORS_CUT</edition></movie>"),
            "Director's Cut"
        )
        self.assertEqual(
            parse_edition_from_xml("<movie><edition>THEATRICAL_EDITION</edition></movie>"),
            "Theatrical Cut"
        )
        self.assertEqual(
            parse_edition_from_xml("<movie><edition>EXTENDED_EDITION</edition></movie>"),
            "Extended Edition"
        )
        self.assertEqual(
            parse_edition_from_xml("<movie><edition>SPECIAL_EDITION</edition></movie>"),
            "Special Edition"
        )
        self.assertEqual(
            parse_edition_from_xml("<movie><edition>ULTIMATE_EDITION</edition></movie>"),
            "Ultimate Edition"
        )
        self.assertIsNone(parse_edition_from_xml("<movie><edition>NONE</edition></movie>"))
        self.assertIsNone(parse_edition_from_xml("<movie><edition>N/A</edition></movie>"))


class TestFilenameParser(unittest.TestCase):
    def test_anniversary_edition(self):
        filename = "Lawrence.of.Arabia.1962.50th.Anniversary.Edition.2160p.UHD.HDR.DV.BD.Remux.mkv"
        self.assertEqual(parse_edition_from_filename(filename), "50th Anniversary Edition")
        # Test ordinals
        self.assertEqual(parse_edition_from_filename("Movie.21st.Anniversary.Edition.mkv"), "21st Anniversary Edition")
        self.assertEqual(parse_edition_from_filename("Movie.22nd.Anniversary.Edition.mkv"), "22nd Anniversary Edition")
        self.assertEqual(parse_edition_from_filename("Movie.23rd.Anniversary.Edition.mkv"), "23rd Anniversary Edition")

    def test_compound_and_custom_cuts(self):
        self.assertEqual(
            parse_edition_from_filename("Movie.1990.Unrated.Directors.Cut.1080p.mkv"),
            "Unrated Director's Cut"
        )
        self.assertEqual(
            parse_edition_from_filename("Star.Wars.1977.Despecialized.Edition.1080p.mkv"),
            "Despecialized Edition"
        )

    def test_international_cut(self):
        filename = "Leon.1994.International.Cut.UHD.BluRay.2160p.mkv"
        self.assertEqual(parse_edition_from_filename(filename), "International Cut")

    def test_directors_cut(self):
        filename = "Leon.aka.The.Professional.Directors.Cut.1994.BluRay.1080p.mkv"
        self.assertEqual(parse_edition_from_filename(filename), "Director's Cut")

    def test_explicit_edition_token(self):
        filename = "Blade.Runner.1982.{edition-The Final Cut}.2160p.mkv"
        self.assertEqual(parse_edition_from_filename(filename), "The Final Cut")

    def test_criterion_edition(self):
        filename = "Seven.Samurai.1954.Criterion.Collection.1080p.mkv"
        self.assertEqual(parse_edition_from_filename(filename), "Criterion Collection Edition")


class TestBackupAndRestoreSQLite(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "MyVideos146.db")

        # Create dummy SQLite database
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE videoversion (idFile INT PRIMARY KEY, idType INT);")
        cur.execute("CREATE TABLE videoversiontype (id INT PRIMARY KEY, name TEXT);")
        cur.execute("INSERT INTO videoversiontype VALUES (40400, 'Standard Edition');")
        cur.execute("INSERT INTO videoversion VALUES (1, 40400);")
        conn.commit()
        conn.close()

        self.adapter = SQLiteAdapter(self.db_path)
        self.adapter.connect()
        self.backup_mgr = BackupManager(self.adapter, retention=3)
        self.backup_mgr.backup_dir = os.path.join(self.temp_dir, "backups")
        self.backup_mgr._ensure_backup_dir()

    def tearDown(self):
        self.adapter.close()
        shutil.rmtree(self.temp_dir)

    def test_backup_creation_and_listing(self):
        info = self.backup_mgr.create_backup()
        self.assertTrue(os.path.exists(info.identifier))
        backups = self.backup_mgr.list_backups()
        self.assertEqual(len(backups), 1)

    def test_minimum_retention_enforced(self):
        self.assertEqual(self.backup_mgr.retention, MIN_BACKUPS_RETAINED)
        self.assertGreaterEqual(self.backup_mgr.retention, 3)

        # Create 5 backups
        import time
        for i in range(5):
            # simulate different timestamps
            ts = "20261119_19000{}".format(i)
            bak_path = os.path.join(self.backup_mgr.backup_dir, "MyVideos146_bak_{}.db".format(ts))
            shutil.copy2(self.db_path, bak_path)

        # Prune
        self.backup_mgr._prune_sqlite_backups()
        remaining = self.backup_mgr.list_backups()
        self.assertEqual(len(remaining), 3)

    def test_restore_sqlite(self):
        # 1. Take initial backup with standard edition
        bak_info = self.backup_mgr.create_backup()

        # 2. Modify active DB
        self.adapter.execute("UPDATE videoversion SET idType = 999 WHERE idFile = 1;")
        self.adapter.commit()
        rows = self.adapter.query("SELECT idType FROM videoversion WHERE idFile = 1;")
        self.assertEqual(rows[0]["idType"], 999)

        # 3. Restore backup
        success = self.backup_mgr.restore_backup(bak_info)
        self.assertTrue(success)

        # 4. Verify restored value
        restored_rows = self.adapter.query("SELECT idType FROM videoversion WHERE idFile = 1;")
        self.assertEqual(restored_rows[0]["idType"], 40400)


class TestDefaultVersionManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "MyVideos146.db")

        # Create schema for movies, episodes, files, paths, videoversion, videoversiontype
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE path (idPath INT PRIMARY KEY, strPath TEXT);")
        cur.execute("CREATE TABLE files (idFile INT PRIMARY KEY, idPath INT, strFilename TEXT, playCount INT, lastPlayed TEXT);")
        cur.execute("CREATE TABLE movie (idMovie INT PRIMARY KEY, idFile INT, c00 TEXT);")
        cur.execute("CREATE TABLE tvshow (idShow INT PRIMARY KEY, c00 TEXT);")
        cur.execute("CREATE TABLE episode (idEpisode INT PRIMARY KEY, idFile INT, idShow INT, c00 TEXT, c12 TEXT, c13 TEXT);")
        cur.execute("CREATE TABLE videoversion (idFile INT PRIMARY KEY, idMedia INT, media_type TEXT, itemType INT, idType INT);")
        cur.execute("CREATE TABLE videoversiontype (id INT PRIMARY KEY, name TEXT, owner INT, itemType INT);")

        # Insert reference data
        cur.execute("INSERT INTO videoversiontype VALUES (40400, 'Standard Edition', 1, 1);")
        cur.execute("INSERT INTO videoversiontype VALUES (40401, 'Director''s Cut', 2, 1);")
        cur.execute("INSERT INTO videoversiontype VALUES (40402, 'Extended Cut', 2, 1);")

        cur.execute("INSERT INTO path VALUES (1, 'smb://server/Movies/Blade Runner/');")
        cur.execute("INSERT INTO path VALUES (2, 'smb://server/TV/Twin Peaks/Season 2/');")

        # Movie with 2 versions
        cur.execute("INSERT INTO files VALUES (101, 1, 'Blade Runner Theatrical.mkv', 5, '2026-01-01 20:00:00');")
        cur.execute("INSERT INTO files VALUES (102, 1, 'Blade Runner Directors Cut.mkv', 0, NULL);")
        cur.execute("INSERT INTO movie VALUES (1, 101, 'Blade Runner');")
        cur.execute("INSERT INTO videoversion VALUES (101, 1, 'movie', 1, 40400);")
        cur.execute("INSERT INTO videoversion VALUES (102, 1, 'movie', 1, 40401);")

        # TV Episode with 2 versions
        cur.execute("INSERT INTO tvshow VALUES (10, 'Twin Peaks');")
        cur.execute("INSERT INTO files VALUES (201, 2, 'Twin Peaks S02E01 Broadcast.mkv', 2, '2026-02-01 21:00:00');")
        cur.execute("INSERT INTO files VALUES (202, 2, 'Twin Peaks S02E01 Extended.mkv', 0, NULL);")
        cur.execute("INSERT INTO episode VALUES (50, 201, 10, 'May the Giant Be with You', '2', '1');")
        cur.execute("INSERT INTO videoversion VALUES (201, 50, 'episode', 1, 40400);")
        cur.execute("INSERT INTO videoversion VALUES (202, 50, 'episode', 1, 40402);")

        conn.commit()
        conn.close()

        self.adapter = SQLiteAdapter(self.db_path)
        self.adapter.connect()
        self.bak_patcher = patch(
            "lib.backup_manager.translate_path", return_value=os.path.join(self.temp_dir, "backups")
        )
        self.vu_patcher = patch(
            "lib.version_updater.translate_path", return_value=os.path.join(self.temp_dir, "addon_data")
        )
        self.bak_patcher.start()
        self.vu_patcher.start()

    def tearDown(self):
        self.vu_patcher.stop()
        self.bak_patcher.stop()
        self.adapter.close()
        shutil.rmtree(self.temp_dir)

    def test_get_multiversion_items(self):
        from lib.default_version_manager import get_multiversion_items
        items = get_multiversion_items(self.adapter)
        self.assertEqual(len(items), 2)

        movie_item = next(it for it in items if it.media_type == "movie")
        self.assertEqual(movie_item.id_media, 1)
        self.assertEqual(movie_item.title, "Blade Runner")
        self.assertEqual(len(movie_item.versions), 2)
        self.assertEqual(movie_item.default_file_id, 101)
        self.assertEqual(movie_item.get_default_version().id_file, 101)

        ep_item = next(it for it in items if it.media_type == "episode")
        self.assertEqual(ep_item.id_media, 50)
        self.assertIn("Twin Peaks", ep_item.title)
        self.assertEqual(len(ep_item.versions), 2)
        self.assertEqual(ep_item.default_file_id, 201)

    def test_swap_default_version_movie(self):
        from lib.default_version_manager import swap_default_version
        success, bak_info = swap_default_version(
            self.adapter,
            id_media=1,
            media_type="movie",
            target_file_id=102,
            sync_watched=True,
            retention=3
        )
        self.assertTrue(success)

        # Verify movie table now points to 102
        rows = self.adapter.query("SELECT idFile FROM movie WHERE idMovie = 1")
        self.assertEqual(rows[0]["idFile"], 102)

        # Verify watched count transferred to new default file 102
        file_rows = self.adapter.query("SELECT playCount, lastPlayed FROM files WHERE idFile = 102")
        self.assertEqual(file_rows[0]["playCount"], 5)
        self.assertEqual(file_rows[0]["lastPlayed"], "2026-01-01 20:00:00")

    def test_swap_default_version_episode(self):
        from lib.default_version_manager import swap_default_version
        success, bak_info = swap_default_version(
            self.adapter,
            id_media=50,
            media_type="episode",
            target_file_id=202,
            sync_watched=True,
            retention=3
        )
        self.assertTrue(success)

        # Verify episode table now points to 202
        rows = self.adapter.query("SELECT idFile FROM episode WHERE idEpisode = 50")
        self.assertEqual(rows[0]["idFile"], 202)

        # Verify watched count transferred to new default file 202
        file_rows = self.adapter.query("SELECT playCount, lastPlayed FROM files WHERE idFile = 202")
        self.assertEqual(file_rows[0]["playCount"], 2)


class TestEpisodeVersionScan(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "MyVideos146.db")

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE path (idPath INT PRIMARY KEY, strPath TEXT);")
        cur.execute("CREATE TABLE files (idFile INT PRIMARY KEY, idPath INT, strFilename TEXT, playCount INT, lastPlayed TEXT);")
        cur.execute("CREATE TABLE tvshow (idShow INT PRIMARY KEY, c00 TEXT);")
        cur.execute("CREATE TABLE episode (idEpisode INT PRIMARY KEY, idFile INT, idShow INT, c00 TEXT, c12 TEXT, c13 TEXT);")
        cur.execute("CREATE TABLE videoversion (idFile INT PRIMARY KEY, idMedia INT, media_type TEXT, itemType INT, idType INT);")
        cur.execute("CREATE TABLE videoversiontype (id INT PRIMARY KEY, name TEXT, owner INT, itemType INT);")

        cur.execute("INSERT INTO videoversiontype VALUES (40400, 'Standard Edition', 1, 1);")
        cur.execute("INSERT INTO path VALUES (1, 'smb://server/TV/Twin Peaks/Season 2/');")
        cur.execute("INSERT INTO tvshow VALUES (10, 'Twin Peaks');")

        # Episode file with an edition keyword in filename
        cur.execute("INSERT INTO files VALUES (301, 1, 'Twin.Peaks.S02E01.European.Cut.1080p.mkv', 0, NULL);")
        cur.execute("INSERT INTO episode VALUES (70, 301, 10, 'May the Giant Be with You', '2', '1');")

        # Pre-assign standard edition
        cur.execute("INSERT INTO videoversion VALUES (301, 70, 'episode', 1, 40400);")

        conn.commit()
        conn.close()

        self.adapter = SQLiteAdapter(self.db_path)
        self.adapter.connect()
        self.bak_patcher = patch(
            "lib.backup_manager.translate_path", return_value=os.path.join(self.temp_dir, "backups")
        )
        self.vu_patcher = patch(
            "lib.version_updater.translate_path", return_value=os.path.join(self.temp_dir, "addon_data")
        )
        self.bak_patcher.start()
        self.vu_patcher.start()

    def tearDown(self):
        self.vu_patcher.stop()
        self.bak_patcher.stop()
        self.adapter.close()
        shutil.rmtree(self.temp_dir)

    def test_scan_and_update_episode(self):
        from lib.config import AddonSettings
        from lib.version_updater import VersionUpdater

        settings = AddonSettings()
        settings.scan_movies = False
        settings.scan_episodes = True
        settings.scan_filenames = True
        settings.scan_nfo = False
        settings.backup_retention = 3

        updater = VersionUpdater(self.adapter, settings)
        scan_res = updater.scan_for_changes()

        self.assertEqual(len(scan_res.changes), 1)
        change = scan_res.changes[0]
        self.assertEqual(change.media_type, "episode")
        self.assertEqual(change.id_media, 70)
        self.assertEqual(change.target_type, "European Cut")

        # Apply changes
        update_res = updater.apply_changes(scan_res.changes)
        self.assertEqual(update_res.applied_count, 1)

        # Verify database record updated
        rows = self.adapter.query("""
            SELECT v.idFile, v.idType, vt.name 
            FROM videoversion v 
            JOIN videoversiontype vt ON v.idType = vt.id 
            WHERE v.idFile = 301 AND v.media_type = 'episode'
        """)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "European Cut")
        self.assertGreaterEqual(rows[0]["idType"], 40801)

    def test_duplicate_idfile_updates_instead_of_insert_error(self):
        from lib.config import AddonSettings
        from lib.version_updater import VersionUpdater, PlannedChange
        from lib.run_logger import build_summary_lines, write_run_log

        # Insert an existing videoversion row with a different media_type or stale entry for idFile 302
        self.adapter.execute("INSERT INTO files VALUES (302, 1, 'Twin.Peaks.S02E02.Extended.Cut.1080p.mkv', 0, NULL);")
        self.adapter.execute("INSERT INTO episode VALUES (71, 302, 10, 'Coma', '2', '2');")
        self.adapter.execute("INSERT INTO videoversion VALUES (302, 71, 'video', 1, 40400);")
        self.adapter.commit()

        settings = AddonSettings()
        settings.scan_movies = False
        settings.scan_episodes = True
        settings.scan_filenames = True
        settings.scan_nfo = False
        settings.backup_retention = 3

        updater = VersionUpdater(self.adapter, settings)
        updater.load_version_types()
        change = PlannedChange(
            id_media=71,
            media_type="episode",
            title="Twin Peaks - S02E02 - Coma",
            id_file=302,
            filename="Twin.Peaks.S02E02.Extended.Cut.1080p.mkv",
            current_type="None",
            target_type="Extended Edition",
            in_videoversion=False  # Force code path where caller thought it was an INSERT
        )
        res = updater.apply_changes([change])
        self.assertEqual(res.applied_count, 1)

        rows = self.adapter.query("SELECT idType FROM videoversion WHERE idFile = 302")
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0]["idType"], 40801)

        with patch("lib.run_logger.translate_path", return_value=os.path.join(self.temp_dir, "logs")):
            lines = build_summary_lines(
                config="SQLite Test",
                total_scanned=1,
                changes=[change],
                applied_count=res.applied_count,
                new_types=res.new_types_created,
                backup_info=res.backup_info
            )
            log_path = write_run_log(lines)
            self.assertTrue(os.path.exists(log_path))
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("Status: Success", content)
            self.assertIn("Extended Edition", content)

    def test_db_label_normalization(self):
        from lib.version_updater import find_normalizable_labels, apply_label_normalization

        self.adapter.execute("INSERT INTO videoversiontype VALUES (40407, 'Director''s Cut', 1, 1);")
        self.adapter.execute("INSERT INTO videoversiontype VALUES (40813, 'DIRECTORS_CUT', 2, 1);")
        self.adapter.execute("INSERT INTO videoversiontype VALUES (40814, 'NONE', 2, 1);")
        self.adapter.execute("INSERT INTO files VALUES (303, 1, 'Movie.A.mkv', 0, NULL);")
        self.adapter.execute("INSERT INTO files VALUES (304, 1, 'Movie.B.mkv', 0, NULL);")
        self.adapter.execute("INSERT INTO videoversion VALUES (303, 72, 'movie', 1, 40813);")
        self.adapter.execute("INSERT INTO videoversion VALUES (304, 73, 'movie', 1, 40814);")
        self.adapter.commit()

        mappings = find_normalizable_labels(self.adapter)
        self.assertEqual(len(mappings), 2)

        updated_rows, removed_types, _ = apply_label_normalization(self.adapter, mappings, retention=3)
        self.assertEqual(updated_rows, 2)
        self.assertEqual(removed_types, 2)

        r_a = self.adapter.query("SELECT idType FROM videoversion WHERE idFile = 303")
        r_b = self.adapter.query("SELECT idType FROM videoversion WHERE idFile = 304")
        self.assertEqual(r_a[0]["idType"], 40407)
        self.assertEqual(r_b[0]["idType"], 40400)


class TestAdvancedFeatures(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "MyVideos146.db")
        self.media_dir = os.path.join(self.temp_dir, "Movies", "Donnie Darko")
        self.hot_fuzz_dir = os.path.join(self.temp_dir, "Movies", "Hot Fuzz")
        os.makedirs(self.media_dir, exist_ok=True)
        os.makedirs(self.hot_fuzz_dir, exist_ok=True)

        # Shared folder with movie.nfo saying Theatrical Cut, plus a Directors Cut mkv
        with open(os.path.join(self.media_dir, "movie.nfo"), "w", encoding="utf-8") as f:
            f.write("<movie><title>Donnie Darko</title><edition>Theatrical Cut</edition></movie>")
        with open(os.path.join(self.media_dir, "Donnie.Darko.2001.Directors.Cut.2160p.mkv"), "w") as f:
            f.write("")
        with open(os.path.join(self.media_dir, "Donnie.Darko.2001.2160p.mkv"), "w") as f:
            f.write("")

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE path (idPath INT PRIMARY KEY, strPath TEXT);")
        cur.execute("CREATE TABLE files (idFile INT PRIMARY KEY, idPath INT, strFilename TEXT, playCount INT, lastPlayed TEXT);")
        cur.execute("CREATE TABLE movie (idMovie INT PRIMARY KEY, idFile INT, c00 TEXT);")
        cur.execute("CREATE TABLE uniqueid (uniqueid_id INT PRIMARY KEY, media_id INT, media_type TEXT, value TEXT, type TEXT);")
        cur.execute("CREATE TABLE videoversion (idFile INT PRIMARY KEY, idMedia INT, media_type TEXT, itemType INT, idType INT);")
        cur.execute("CREATE TABLE videoversiontype (id INT PRIMARY KEY, name TEXT, owner INT, itemType INT);")

        cur.execute("INSERT INTO videoversiontype VALUES (40400, 'Standard Edition', 1, 1);")
        cur.execute("INSERT INTO videoversiontype VALUES (40406, 'Theatrical Cut', 1, 1);")
        cur.execute("INSERT INTO videoversiontype VALUES (40407, 'Director''s Cut', 1, 1);")

        cur.execute("INSERT INTO path VALUES (1, ?);", (self.media_dir + os.sep,))
        cur.execute("INSERT INTO path VALUES (2, ?);", (self.hot_fuzz_dir + os.sep,))
        cur.execute("INSERT INTO files VALUES (1, 1, 'Donnie.Darko.2001.Directors.Cut.2160p.mkv', 1, '2026-01-01');")
        cur.execute("INSERT INTO files VALUES (2, 1, 'Donnie.Darko.2001.2160p.mkv', 0, NULL);")
        cur.execute("INSERT INTO files VALUES (3, 2, 'Hot.Fuzz.2007.2160p.DV.HDR.mkv', 0, NULL);")
        cur.execute("INSERT INTO files VALUES (4, 2, 'Hot.Fuzz.2007.1080p.mkv', 0, NULL);")

        # Donnie Darko as 2 duplicate movies sharing TMDB 141
        cur.execute("INSERT INTO movie VALUES (10, 1, 'Donnie Darko');")
        cur.execute("INSERT INTO movie VALUES (11, 2, 'Donnie Darko');")
        cur.execute("INSERT INTO uniqueid VALUES (1, 10, 'movie', '141', 'tmdb');")
        cur.execute("INSERT INTO uniqueid VALUES (2, 11, 'movie', '141', 'tmdb');")

        # Hot Fuzz already grouped multi-version without edition tags
        cur.execute("INSERT INTO movie VALUES (20, 3, 'Hot Fuzz');")
        cur.execute("INSERT INTO videoversion VALUES (3, 20, 'movie', 1, 40400);")
        cur.execute("INSERT INTO videoversion VALUES (4, 20, 'movie', 1, 40400);")

        conn.commit()
        conn.close()

        self.adapter = SQLiteAdapter(self.db_path)
        self.adapter.connect()
        self.bak_patcher = patch("lib.backup_manager.translate_path", return_value=os.path.join(self.temp_dir, "backups"))
        self.vu_patcher = patch("lib.version_updater.translate_path", return_value=os.path.join(self.temp_dir, "addon_data"))
        self.cm_patcher = patch("lib.custom_mappings.translate_path", return_value=os.path.join(self.temp_dir, "addon_data"))
        self.bak_patcher.start()
        self.vu_patcher.start()
        self.cm_patcher.start()

    def tearDown(self):
        self.cm_patcher.stop()
        self.vu_patcher.stop()
        self.bak_patcher.stop()
        self.adapter.close()
        shutil.rmtree(self.temp_dir)

    def test_quality_parser_and_custom_mappings(self):
        from lib.filename_parser import parse_quality_from_filename, normalize_edition_name
        from lib.custom_mappings import clear_custom_mappings_cache

        self.assertEqual(parse_quality_from_filename("Movie.2020.2160p.DV.HDR.mkv"), "4K DV/HDR")
        self.assertEqual(parse_quality_from_filename("Movie.2020.1080p.BluRay.mkv"), "1080p")

        addon_data = os.path.join(self.temp_dir, "addon_data")
        os.makedirs(addon_data, exist_ok=True)
        import json
        with open(os.path.join(addon_data, "custom_mappings.json"), "w", encoding="utf-8") as f:
            json.dump({"rename": {"MY_RARE_CUT": "Rare Studio Cut"}, "ignore": ["JUNK_TAG"]}, f)
        clear_custom_mappings_cache()

        self.assertEqual(normalize_edition_name("MY_RARE_CUT"), "Rare Studio Cut")
        self.assertIsNone(normalize_edition_name("JUNK_TAG"))

    def test_shared_folder_precedence_quality_and_incremental(self):
        from lib.config import AddonSettings
        from lib.version_updater import VersionUpdater

        settings = AddonSettings()
        settings.scan_movies = True
        settings.scan_episodes = False
        settings.scan_nfo = True
        settings.scan_filenames = True
        settings.incremental_scan = True
        settings.label_multiversion_quality = True

        updater = VersionUpdater(self.adapter, settings)
        res = updater.scan_for_changes()
        by_file = {c.id_file: c.target_type for c in res.changes}

        # Shared folder: Directors.Cut filename beats shared movie.nfo (Theatrical Cut)
        self.assertEqual(by_file[1], "Director's Cut")
        # Hot Fuzz multi-version gets quality fallback
        self.assertEqual(by_file[3], "4K DV/HDR")
        self.assertEqual(by_file[4], "1080p")

        # Second incremental scan should skip previously scanned files
        res2 = updater.scan_for_changes()
        self.assertEqual(len(res2.changes), 0)

        # Force full rescan finds them again
        res3 = updater.scan_for_changes(force_full=True)
        self.assertGreaterEqual(len(res3.changes), 3)

    def test_group_duplicate_movies(self):
        from lib.duplicate_grouper import find_duplicate_movies, group_duplicate_movies

        groups = find_duplicate_movies(self.adapter)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].primary["idMovie"], 10)

        merged_movies, merged_files, _ = group_duplicate_movies(self.adapter, groups, retention=3)
        self.assertEqual(merged_movies, 1)
        self.assertEqual(merged_files, 1)

        movies_left = self.adapter.query("SELECT idMovie FROM movie WHERE idMovie IN (10, 11)")
        self.assertEqual(len(movies_left), 1)
        vv_rows = self.adapter.query("SELECT idFile, idMedia FROM videoversion WHERE idMedia = 10")
        self.assertEqual(len(vv_rows), 2)


class TestMissingMetadataUpdater(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "MyVideos146.db")
        self.show_dir = os.path.join(self.temp_dir, "TV", "Penny Dreadful")
        self.movie_dir = os.path.join(self.temp_dir, "Movies", "Alien")
        os.makedirs(self.show_dir, exist_ok=True)
        os.makedirs(self.movie_dir, exist_ok=True)

        # Local tvshow.nfo + season01.nfo + episode.nfo
        with open(os.path.join(self.show_dir, "tvshow.nfo"), "w", encoding="utf-8") as f:
            f.write(
                '<tvshow><title>Penny Dreadful</title><plot>Dark Victorian tale.</plot>'
                '<mpaa>US:TV-MA</mpaa><genre>Drama</genre><genre>Horror</genre>'
                '<studio>Showtime</studio><actor><name>Eva Green</name><role>Vanessa Ives</role></actor>'
                '<namedseason number="1">Origin</namedseason></tvshow>'
            )
        with open(os.path.join(self.show_dir, "season01.nfo"), "w", encoding="utf-8") as f:
            f.write("<season><plot>Victorian London horrors unfold.</plot></season>")
        with open(os.path.join(self.show_dir, "Penny.Dreadful.S01E01.nfo"), "w", encoding="utf-8") as f:
            f.write(
                "<episodedetails><plot>Night Work begins.</plot><runtime>55</runtime>"
                "<fileinfo><streamdetails><video><codec>hevc</codec><width>1920</width><height>1080</height><durationinseconds>3300</durationinseconds></video>"
                "<audio><codec>eac3</codec><channels>6</channels><language>eng</language></audio></streamdetails></fileinfo>"
                "</episodedetails>"
            )

        # Local movie.nfo with full metadata + streamdetails
        with open(os.path.join(self.movie_dir, "movie.nfo"), "w", encoding="utf-8") as f:
            f.write(
                "<movie><plot>In space no one can hear you scream.</plot><tagline>The eighth passenger.</tagline>"
                "<mpaa>Rated R</mpaa><runtime>117</runtime><genre>Sci-Fi</genre><genre>Horror</genre>"
                "<studio>20th Century Fox</studio><actor><name>Sigourney Weaver</name><role>Ripley</role></actor>"
                "<fileinfo><streamdetails><video><codec>hevc</codec><width>3840</width><height>2160</height><hdrtype>dolbyvision</hdrtype><durationinseconds>7020</durationinseconds></video>"
                "<audio><codec>truehd</codec><channels>8</channels><language>eng</language></audio>"
                "<subtitle><language>eng</language></subtitle></streamdetails></fileinfo>"
                "</movie>"
            )

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE path (idPath INT PRIMARY KEY, strPath TEXT);")
        cur.execute("CREATE TABLE files (idFile INT PRIMARY KEY, idPath INT, strFilename TEXT, playCount INT, lastPlayed TEXT);")
        cur.execute("CREATE TABLE movie (idMovie INT PRIMARY KEY, idFile INT, c00 TEXT, c01 TEXT, c03 TEXT, c11 TEXT, c12 TEXT, c14 TEXT, c18 TEXT);")
        cur.execute("CREATE TABLE tvshow (idShow INT PRIMARY KEY, c00 TEXT, c01 TEXT, c08 TEXT, c13 TEXT, c14 TEXT);")
        cur.execute("CREATE TABLE episode (idEpisode INT PRIMARY KEY, idFile INT, idShow INT, c00 TEXT, c01 TEXT, c09 TEXT, c12 TEXT, c13 TEXT);")
        cur.execute("CREATE TABLE tvshowlinkpath (idShow INT, idPath INT);")
        cur.execute("CREATE TABLE seasons (idSeason INT PRIMARY KEY, idShow INT, season INT, name TEXT, userrating INT, plot TEXT);")
        cur.execute("CREATE TABLE uniqueid (uniqueid_id INT PRIMARY KEY, media_id INT, media_type TEXT, value TEXT, type TEXT);")
        cur.execute("CREATE TABLE genre (genre_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);")
        cur.execute("CREATE TABLE genre_link (genre_id INT, media_id INT, media_type TEXT);")
        cur.execute("CREATE TABLE studio (studio_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT);")
        cur.execute("CREATE TABLE studio_link (studio_id INT, media_id INT, media_type TEXT);")
        cur.execute("CREATE TABLE actor (actor_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, art_urls TEXT);")
        cur.execute("CREATE TABLE actor_link (actor_id INT, media_id INT, media_type TEXT, role TEXT, cast_order INT);")
        cur.execute(
            "CREATE TABLE streamdetails (idFile INT, iStreamType INT, strVideoCodec TEXT, fVideoAspect REAL, "
            "iVideoWidth INT, iVideoHeight INT, strAudioCodec TEXT, iAudioChannels INT, strAudioLanguage TEXT, "
            "strSubtitleLanguage TEXT, iVideoDuration INT, strStereoMode TEXT, strVideoLanguage TEXT, strHdrType TEXT);"
        )
        cur.execute("CREATE TABLE videoversion (idFile INT PRIMARY KEY, idMedia INT, media_type TEXT, itemType INT, idType INT);")
        cur.execute("CREATE TABLE videoversiontype (id INT PRIMARY KEY, name TEXT, owner INT, itemType INT);")

        cur.execute("INSERT INTO path VALUES (1, ?);", (self.show_dir + os.sep,))
        cur.execute("INSERT INTO path VALUES (2, ?);", (self.movie_dir + os.sep,))
        cur.execute("INSERT INTO tvshow VALUES (1, 'Penny Dreadful', '', '', '', '');")
        cur.execute("INSERT INTO tvshowlinkpath VALUES (1, 1);")
        cur.execute("INSERT INTO uniqueid VALUES (1, 1, 'tvshow', '54671', 'tmdb');")

        # Season 1 has NFO, Season 2 has no NFO (uses TMDbHelper fallback)
        cur.execute("INSERT INTO seasons VALUES (10, 1, 1, 'Season 1', 0, NULL);")
        cur.execute("INSERT INTO seasons VALUES (11, 1, 2, '', 0, '');")

        # Episode 1 missing plot/runtime/streamdetails
        cur.execute("INSERT INTO files VALUES (99, 1, 'Penny.Dreadful.S01E01.mkv', 0, NULL);")
        cur.execute("INSERT INTO episode VALUES (20, 99, 1, 'Night Work', '', '0', '1', '1');")

        # Movie missing all fields + streamdetails
        cur.execute("INSERT INTO files VALUES (100, 2, 'Alien.1979.mkv', 0, NULL);")
        cur.execute("INSERT INTO movie VALUES (50, 100, 'Alien', '', NULL, '', '', '', '');")

        conn.commit()
        conn.close()

        self.adapter = SQLiteAdapter(self.db_path)
        self.adapter.connect()
        self.bak_patcher = patch("lib.backup_manager.translate_path", return_value=os.path.join(self.temp_dir, "backups"))
        self.meta_patcher = patch("lib.metadata_updater.translate_path", return_value=os.path.join(self.temp_dir, "addon_data"))
        self.bak_patcher.start()
        self.meta_patcher.start()

    def tearDown(self):
        self.meta_patcher.stop()
        self.bak_patcher.stop()
        self.adapter.close()
        shutil.rmtree(self.temp_dir)

    def test_scan_and_apply_missing_metadata_nfo_and_tmdbhelper(self):
        from lib.config import AddonSettings
        from lib.metadata_updater import MetadataUpdater

        settings = AddonSettings()
        settings.season_scan_nfo = True
        settings.season_scan_tmdb = True
        settings.only_missing_season_plots = True
        settings.update_season_names = True
        settings.update_missing_movie_plots = True
        settings.update_missing_tv_metadata = True
        settings.update_missing_streamdetails = True

        updater = MetadataUpdater(self.adapter, settings)
        with patch.object(updater.tmdb, "is_available", return_value=True), \
             patch.object(updater.tmdb, "get_season_details", return_value={
                 "name": "Season 2: Witches",
                 "plot": "Evelyn Poole targets Vanessa."
             }):
            total, changes = updater.scan_missing_metadata(
                include_seasons=True, include_movies=True, include_tvshows=True, include_episodes=True
            )
            self.assertEqual(total, 5)
            self.assertEqual(len(changes), 5)

            applied, _ = updater.apply_metadata_changes(changes)
            self.assertEqual(applied, 5)

        s1 = self.adapter.query("SELECT name, plot FROM seasons WHERE idSeason = 10")[0]
        self.assertEqual(s1["name"], "Origin")
        self.assertEqual(s1["plot"], "Victorian London horrors unfold.")

        s2 = self.adapter.query("SELECT name, plot FROM seasons WHERE idSeason = 11")[0]
        self.assertEqual(s2["name"], "Season 2: Witches")
        self.assertEqual(s2["plot"], "Evelyn Poole targets Vanessa.")

        mov = self.adapter.query("SELECT c01, c03, c11, c12, c14, c18 FROM movie WHERE idMovie = 50")[0]
        self.assertEqual(mov["c01"], "In space no one can hear you scream.")
        self.assertEqual(mov["c03"], "The eighth passenger.")
        self.assertEqual(mov["c11"], "7020")
        self.assertEqual(mov["c12"], "Rated R")
        self.assertEqual(mov["c14"], "Sci-Fi / Horror")
        self.assertEqual(mov["c18"], "20th Century Fox")

        tv = self.adapter.query("SELECT c01, c08, c13, c14 FROM tvshow WHERE idShow = 1")[0]
        self.assertEqual(tv["c01"], "Dark Victorian tale.")
        self.assertEqual(tv["c08"], "Drama / Horror")
        self.assertEqual(tv["c13"], "US:TV-MA")
        self.assertEqual(tv["c14"], "Showtime")

        ep = self.adapter.query("SELECT c01, c09 FROM episode WHERE idEpisode = 20")[0]
        self.assertEqual(ep["c01"], "Night Work begins.")
        self.assertEqual(ep["c09"], "3300")

        mov_sd = self.adapter.query("SELECT * FROM streamdetails WHERE idFile = 100")
        self.assertEqual(len(mov_sd), 3)  # video + audio + subtitle
        ep_sd = self.adapter.query("SELECT * FROM streamdetails WHERE idFile = 99")
        self.assertEqual(len(ep_sd), 2)   # video + audio
        mov_cast = self.adapter.query("SELECT * FROM actor_link WHERE media_id = 50 AND media_type = 'movie'")
        self.assertEqual(len(mov_cast), 1)

        # Verify IMDB -> TMDB fallback helper
        with patch.object(updater.tmdb, "_request_json", return_value={"movie_results": [{"id": 348}]}):
            self.assertEqual(updater.tmdb.resolve_tmdb_id_from_imdb("tt0078748", "movie"), "348")


if __name__ == "__main__":
    unittest.main()
