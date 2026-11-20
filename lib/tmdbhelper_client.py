# -*- coding: utf-8 -*-
"""
Client for fetching fallback metadata (season plots/names, movie/show/episode plots & taglines)
via installed plugin.video.themoviedb.helper (TMDbHelper) or a user-configured TMDB API key.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from lib.kodi_compat import translate_path, log


class TMDbFallbackClient:
    def __init__(self, api_key="", language="en-US"):
        self.api_key = (api_key or "").strip()
        self.language = (language or "en-US").strip()
        self._tmdbhelper_api = None
        self._tmdbhelper_checked = False
        self._cache = {}

    def _init_tmdbhelper(self):
        if self._tmdbhelper_checked:
            return self._tmdbhelper_api
        self._tmdbhelper_checked = True
        try:
            res_path = translate_path(
                "special://home/addons/plugin.video.themoviedb.helper/resources"
            )
            if res_path and os.path.isdir(res_path):
                if res_path not in sys.path:
                    sys.path.append(res_path)
                from tmdbhelper.lib.api.tmdb.api import TMDb
                self._tmdbhelper_api = TMDb()
                log("Initialized TMDbHelper fallback API client.")
        except Exception as e:
            log("TMDbHelper Python API unavailable ({}); checking API key fallback.".format(e), "debug")
            self._tmdbhelper_api = None
        return self._tmdbhelper_api

    def is_available(self):
        return bool(self.api_key) or (self._init_tmdbhelper() is not None)

    def _request_json(self, endpoint):
        if endpoint in self._cache:
            return self._cache[endpoint]

        # 1. Try user-configured TMDB API key first if explicitly set
        if self.api_key:
            sep = "&" if "?" in endpoint else "?"
            url = "https://api.themoviedb.org/3/{}{}api_key={}&language={}".format(
                endpoint.lstrip("/"),
                sep,
                urllib.parse.quote(self.api_key),
                urllib.parse.quote(self.language)
            )
            for attempt in range(2):
                try:
                    req = urllib.request.Request(url, headers={"Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        self._cache[endpoint] = data
                        return data
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt == 0:
                        time.sleep(1.5)
                        continue
                    log("TMDB HTTP {} for {}: {}".format(e.code, endpoint, e.reason), "debug")
                    break
                except Exception as e:
                    log("TMDB request failed for {}: {}".format(endpoint, e), "debug")
                    break

        # 2. Fallback to installed plugin.video.themoviedb.helper
        tmdb_helper = self._init_tmdbhelper()
        if tmdb_helper is not None:
            try:
                data = tmdb_helper.get_request_sc(endpoint.lstrip("/"), language=self.language)
                if isinstance(data, dict):
                    self._cache[endpoint] = data
                    return data
            except Exception as e:
                log("TMDbHelper request failed for {}: {}".format(endpoint, e), "debug")

        self._cache[endpoint] = None
        return None

    def get_season_details(self, tmdb_id, season_num):
        if not tmdb_id or season_num is None:
            return None
        data = self._request_json("tv/{}/season/{}".format(tmdb_id, int(season_num)))
        if not isinstance(data, dict):
            return None
        return {
            "name": (data.get("name") or "").strip(),
            "plot": (data.get("overview") or "").strip(),
        }

    def get_movie_details(self, tmdb_id):
        if not tmdb_id:
            return None
        data = self._request_json("movie/{}".format(tmdb_id))
        if not isinstance(data, dict):
            return None
        return {
            "plot": (data.get("overview") or "").strip(),
            "tagline": (data.get("tagline") or "").strip(),
        }

    def get_tvshow_details(self, tmdb_id):
        if not tmdb_id:
            return None
        data = self._request_json("tv/{}".format(tmdb_id))
        if not isinstance(data, dict):
            return None
        return {
            "plot": (data.get("overview") or "").strip(),
            "tagline": (data.get("tagline") or "").strip(),
        }

    def get_episode_details(self, show_tmdb_id, season_num, episode_num):
        if not show_tmdb_id or season_num is None or episode_num is None:
            return None
        data = self._request_json(
            "tv/{}/season/{}/episode/{}".format(show_tmdb_id, int(season_num), int(episode_num))
        )
        if not isinstance(data, dict):
            return None
        return {
            "plot": (data.get("overview") or "").strip(),
        }
