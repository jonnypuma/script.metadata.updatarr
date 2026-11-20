# -*- coding: utf-8 -*-
"""
Client for fetching fallback metadata (season plots/names, movie/show/episode plots,
taglines, MPAA, runtime, genres, studios, cast) via installed
plugin.video.themoviedb.helper (TMDbHelper) or an optional TMDB API key.
"""
import json
import os
import sys
import threading
import time
from collections import deque
import urllib.request
import urllib.parse
import urllib.error
from lib.kodi_compat import translate_path, log


TMDB_IMG_BASE = "https://image.tmdb.org/t/p/original"
# TMDB official limit: ~50 req/s -> 50 * 0.95 = 47.5 -> 47 req/s (-5% safety margin)
TMDB_SAFE_RPS = 47


class RateLimiter:
    """Thread-safe rolling-window rate limiter."""

    def __init__(self, max_requests=TMDB_SAFE_RPS, window_seconds=1.0):
        self.max_requests = max(1, int(max_requests))
        self.window_seconds = float(window_seconds)
        self._timestamps = deque()
        self._lock = threading.Lock()

    def acquire(self):
        while True:
            with self._lock:
                now = time.monotonic()
                cutoff = now - self.window_seconds
                while self._timestamps and self._timestamps[0] <= cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_requests:
                    self._timestamps.append(now)
                    return
                sleep_for = (self._timestamps[0] + self.window_seconds) - now
            if sleep_for > 0:
                time.sleep(min(sleep_for, self.window_seconds))


class TMDbFallbackClient:
    def __init__(self, api_key="", language="en-US", max_rps=TMDB_SAFE_RPS):
        self.api_key = (api_key or "").strip()
        self.language = (language or "en-US").strip()
        self._tmdbhelper_api = None
        self._tmdbhelper_checked = False
        self._cache = {}
        self._lock = threading.Lock()
        self._rate_limiter = RateLimiter(max_requests=max_rps, window_seconds=1.0)

    def _init_tmdbhelper(self):
        with self._lock:
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
        with self._lock:
            if endpoint in self._cache:
                return self._cache[endpoint]

        data_result = None
        if self.api_key:
            sep = "&" if "?" in endpoint else "?"
            url = "https://api.themoviedb.org/3/{}{}api_key={}&language={}".format(
                endpoint.lstrip("/"),
                sep,
                urllib.parse.quote(self.api_key),
                urllib.parse.quote(self.language)
            )
            for attempt in range(3):
                try:
                    self._rate_limiter.acquire()
                    req = urllib.request.Request(url, headers={"Accept": "application/json"})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data_result = json.loads(resp.read().decode("utf-8"))
                        break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and attempt < 2:
                        retry_after = e.headers.get("Retry-After") if e.headers else None
                        try:
                            delay = float(retry_after) if retry_after else (1.5 * (2 ** attempt))
                        except ValueError:
                            delay = 1.5 * (2 ** attempt)
                        log("TMDB rate limited (429), retrying in {:.1f}s...".format(delay), "debug")
                        time.sleep(min(delay, 10.0))
                        continue
                    log("TMDB HTTP {} for {}: {}".format(e.code, endpoint, e.reason), "debug")
                    break
                except Exception as e:
                    log("TMDB request failed for {}: {}".format(endpoint, e), "debug")
                    break

        if data_result is None:
            tmdb_helper = self._init_tmdbhelper()
            if tmdb_helper is not None:
                try:
                    self._rate_limiter.acquire()
                    base_ep, _, qs = endpoint.lstrip("/").partition("?")
                    kwargs = {"language": self.language}
                    if qs:
                        for k, vals in urllib.parse.parse_qs(qs).items():
                            if vals:
                                kwargs[k] = vals[0]
                    data = tmdb_helper.get_request_sc(base_ep, **kwargs)
                    if isinstance(data, dict):
                        data_result = data
                except Exception as e:
                    log("TMDbHelper request failed for {}: {}".format(endpoint, e), "debug")

        with self._lock:
            self._cache[endpoint] = data_result
        return data_result

    def resolve_tmdb_id_from_imdb(self, imdb_id, media_type="movie"):
        """Resolve a TMDB ID from an IMDB ID (e.g. tt0137523) via /find."""
        imdb_id = (imdb_id or "").strip()
        if not imdb_id:
            return ""
        data = self._request_json("find/{}?external_source=imdb_id".format(imdb_id))
        if not isinstance(data, dict):
            return ""
        key = "movie_results" if media_type == "movie" else "tv_results"
        results = data.get(key) or []
        if results and results[0].get("id"):
            return str(results[0]["id"])
        return ""

    def _extract_cast(self, credits_dict, limit=15):
        cast_list = []
        for idx, c in enumerate((credits_dict or {}).get("cast") or []):
            name = (c.get("name") or "").strip()
            if not name:
                continue
            thumb = (TMDB_IMG_BASE + c["profile_path"]) if c.get("profile_path") else ""
            cast_list.append({
                "name": name,
                "role": (c.get("character") or "").strip(),
                "order": c.get("order") if c.get("order") is not None else idx,
                "thumb": thumb,
            })
            if len(cast_list) >= limit:
                break
        return cast_list

    def _extract_movie_mpaa(self, data):
        for entry in (data.get("release_dates") or {}).get("results") or []:
            if entry.get("iso_3166_1") == "US":
                for rd in entry.get("release_dates") or []:
                    cert = (rd.get("certification") or "").strip()
                    if cert:
                        return "Rated " + cert if not cert.lower().startswith("rated") else cert
        return ""

    def _extract_tv_mpaa(self, data):
        for entry in (data.get("content_ratings") or {}).get("results") or []:
            if entry.get("iso_3166_1") == "US":
                rating = (entry.get("rating") or "").strip()
                if rating:
                    return "US:" + rating if not rating.startswith("US:") else rating
        return ""

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
        data = self._request_json("movie/{}?append_to_response=credits,release_dates".format(tmdb_id))
        if not isinstance(data, dict):
            return None
        runtime_min = data.get("runtime") or 0
        genres = [g["name"].strip() for g in (data.get("genres") or []) if g.get("name")]
        studios = [p["name"].strip() for p in (data.get("production_companies") or []) if p.get("name")]
        return {
            "plot": (data.get("overview") or "").strip(),
            "tagline": (data.get("tagline") or "").strip(),
            "runtime_sec": str(int(runtime_min) * 60) if runtime_min else "",
            "mpaa": self._extract_movie_mpaa(data),
            "genres": genres,
            "studios": studios,
            "cast": self._extract_cast(data.get("credits")),
        }

    def get_tvshow_details(self, tmdb_id):
        if not tmdb_id:
            return None
        data = self._request_json("tv/{}?append_to_response=credits,content_ratings".format(tmdb_id))
        if not isinstance(data, dict):
            return None
        genres = [g["name"].strip() for g in (data.get("genres") or []) if g.get("name")]
        studios = [n["name"].strip() for n in (data.get("networks") or []) if n.get("name")]
        if not studios:
            studios = [p["name"].strip() for p in (data.get("production_companies") or []) if p.get("name")]
        return {
            "plot": (data.get("overview") or "").strip(),
            "mpaa": self._extract_tv_mpaa(data),
            "genres": genres,
            "studios": studios,
            "cast": self._extract_cast(data.get("credits")),
        }

    def get_episode_details(self, show_tmdb_id, season_num, episode_num):
        if not show_tmdb_id or season_num is None or episode_num is None:
            return None
        data = self._request_json(
            "tv/{}/season/{}/episode/{}".format(show_tmdb_id, int(season_num), int(episode_num))
        )
        if not isinstance(data, dict):
            return None
        runtime_min = data.get("runtime") or 0
        return {
            "plot": (data.get("overview") or "").strip(),
            "runtime_sec": str(int(runtime_min) * 60) if runtime_min else "",
            "cast": self._extract_cast({"cast": data.get("guest_stars") or []}, limit=10),
        }
