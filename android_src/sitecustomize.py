"""Runtime hotfixes for the Android player.

Python imports ``sitecustomize`` automatically during startup.  Keeping these
small overrides separate avoids duplicating the large player module while the
fixes are tested on-device.
"""

from __future__ import annotations

import builtins
import re
import sys
import threading
import time

_PATCHED = False
_ORIGINAL_IMPORT = builtins.__import__


def _patch_audio_screen() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    screen_module = sys.modules.get("audio_screen")
    helper = sys.modules.get("ytdlp_helpers")
    if screen_module is None or helper is None:
        return False

    player_cls = getattr(screen_module, "AudioPlayerScreen", None)
    thumb_cls = getattr(screen_module, "ThumbImage", None)
    if player_cls is None or thumb_cls is None:
        return False

    Clock = screen_module.Clock
    dp = screen_module.dp

    # ------------------------------------------------------------------
    # Avoid decoding the same cached thumbnail on every scheduled retry.
    # The old implementation could decode each row six or more times.
    # ------------------------------------------------------------------
    original_thumb_set_local_file = thumb_cls.set_local_file

    def fast_thumb_set_local_file(self, path: str):
        try:
            if (
                path
                and str(getattr(self, "source", "") or "") == str(path)
                and getattr(self, "texture", None) is not None
            ):
                return True
        except Exception:
            pass
        return original_thumb_set_local_file(self, path)

    thumb_cls.set_local_file = fast_thumb_set_local_file

    # ------------------------------------------------------------------
    # Per-player caches and in-flight guards.
    # ------------------------------------------------------------------
    original_init = player_cls.__init__

    def fast_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._hotfix_metadata_cache = {}
        self._hotfix_related_cache = {}
        self._hotfix_related_inflight = set()
        self._hotfix_meta_inflight = set()
        self._hotfix_suppress_metadata_url = ""
        self._hotfix_playlist_sig = None
        self._hotfix_similar_sig = None

    player_cls.__init__ = fast_init

    # ------------------------------------------------------------------
    # Collapsing a section must only change its geometry.  Previously it
    # cleared and rebuilt every row and thumbnail, causing a 1-2 second lag.
    # ------------------------------------------------------------------
    def update_playlist_visibility(self) -> bool:
        lst = self.ids.get("playlist_list")
        scroll = self.ids.get("playlist_scroll")
        header = self.ids.get("playlist_header")
        header_row = self.ids.get("playlist_header_row")
        toggle_btn = self.ids.get("playlist_toggle_btn")

        visible = bool(self.playlist and self.playlist.tracks)
        collapsed = bool(getattr(self, "_playlist_collapsed", False))
        title = self.playlist.name or "Черга"
        self._set_collapsible_header(
            header_row, header, toggle_btn, visible, title, collapsed
        )

        expanded = bool(visible and not collapsed)
        if scroll is not None:
            scroll.height = dp(240) if expanded else 0
            scroll.opacity = 1 if expanded else 0
            scroll.disabled = not expanded

        return bool(
            expanded
            and lst is not None
            and not list(getattr(lst, "children", []) or [])
        )

    def update_similar_visibility(self) -> bool:
        lst = self.ids.get("similar_list")
        scroll = self.ids.get("similar_scroll")
        header = self.ids.get("similar_header")
        header_row = self.ids.get("similar_header_row")
        toggle_btn = self.ids.get("similar_toggle_btn")

        visible = bool(getattr(self, "_related_items", None))
        collapsed = bool(getattr(self, "_similar_collapsed", False))
        self._set_collapsible_header(
            header_row,
            header,
            toggle_btn,
            visible,
            "Схожі відео",
            collapsed,
        )

        expanded = bool(visible and not collapsed)
        if scroll is not None:
            scroll.height = dp(220) if expanded else 0
            scroll.opacity = 1 if expanded else 0
            scroll.disabled = not expanded

        return bool(
            expanded
            and lst is not None
            and not list(getattr(lst, "children", []) or [])
        )

    def fast_toggle_playlist_collapsed(self):
        self._playlist_collapsed = not bool(
            getattr(self, "_playlist_collapsed", False)
        )
        if update_playlist_visibility(self):
            Clock.schedule_once(
                lambda _dt: self._render_playlist_ui(force=True), 0
            )

    def fast_toggle_similar_collapsed(self):
        self._similar_collapsed = not bool(
            getattr(self, "_similar_collapsed", False)
        )
        if update_similar_visibility(self):
            Clock.schedule_once(
                lambda _dt: self._render_similar_ui(force=True), 0
            )

    player_cls.toggle_playlist_collapsed = fast_toggle_playlist_collapsed
    player_cls.toggle_similar_collapsed = fast_toggle_similar_collapsed

    # Avoid another full rebuild when the app resumes or metadata refreshes
    # without changing the section contents.
    original_render_playlist = player_cls._render_playlist_ui

    def playlist_signature(self):
        try:
            return tuple(
                (
                    str(item.get("url") or ""),
                    str(item.get("video_id") or ""),
                    str(item.get("thumb") or ""),
                    str(item.get("duration") or ""),
                )
                for item in (self.playlist.tracks if self.playlist else [])
            )
        except Exception:
            return None

    def fast_render_playlist(self, force: bool = False):
        sig = playlist_signature(self)
        lst = self.ids.get("playlist_list")
        has_rows = bool(lst is not None and getattr(lst, "children", None))
        if sig == getattr(self, "_hotfix_playlist_sig", None) and has_rows:
            update_playlist_visibility(self)
            return
        result = original_render_playlist(self, force=force)
        self._hotfix_playlist_sig = sig
        return result

    player_cls._render_playlist_ui = fast_render_playlist

    original_render_similar = player_cls._render_similar_ui

    def similar_signature(self):
        try:
            return tuple(
                (
                    str(item.get("url") or ""),
                    str(item.get("video_id") or ""),
                    str(item.get("title") or ""),
                    str(item.get("thumb") or ""),
                )
                for item in (self._related_items or [])
            )
        except Exception:
            return None

    def fast_render_similar(self, force: bool = False):
        sig = similar_signature(self)
        lst = self.ids.get("similar_list")
        has_rows = bool(lst is not None and getattr(lst, "children", None))
        if sig == getattr(self, "_hotfix_similar_sig", None) and has_rows:
            update_similar_visibility(self)
            return
        result = original_render_similar(self, force=force)
        self._hotfix_similar_sig = sig
        return result

    player_cls._render_similar_ui = fast_render_similar

    # ------------------------------------------------------------------
    # Keep slow recommendation fallbacks away from audio extraction.
    # ------------------------------------------------------------------
    original_extract_audio_info = helper.extract_audio_info
    original_watch_related = getattr(
        helper, "_extract_related_from_watch_page", None
    )
    extract_lock = threading.RLock()
    shared_metadata_cache = {}

    def fast_extract_audio_info(video_url: str, *args, **kwargs):
        # yt-dlp already returns related_videos when available.  The HTML
        # fallback may block for up to eight seconds and is now loaded by a
        # separate background task below.
        with extract_lock:
            current_related_fallback = getattr(
                helper, "_extract_related_from_watch_page", None
            )
            try:
                if current_related_fallback is not None:
                    helper._extract_related_from_watch_page = (
                        lambda *_args, **_kwargs: []
                    )
                info = original_extract_audio_info(video_url, *args, **kwargs)
            finally:
                if current_related_fallback is not None:
                    helper._extract_related_from_watch_page = (
                        current_related_fallback
                    )

        if isinstance(info, dict) and video_url:
            shared_metadata_cache[str(video_url)] = dict(info)
            while len(shared_metadata_cache) > 30:
                shared_metadata_cache.pop(next(iter(shared_metadata_cache)), None)
        return info

    helper.extract_audio_info = fast_extract_audio_info

    def clean_query_part(value: str) -> str:
        value = re.sub(
            r"[^\w\s-]+", " ", str(value or ""), flags=re.UNICODE
        )
        return re.sub(r"\s+", " ", value).strip()

    def search_related(video_url: str, title: str, channel: str):
        try:
            from youtube_search import fetch_youtube_results

            current_id = screen_module.Playlist._normalize_video_id(video_url)
            clean_title = clean_query_part(title)
            clean_channel = clean_query_part(channel)
            queries = []
            if clean_title and clean_channel:
                queries.append(f"{clean_title} {clean_channel}")
            if clean_title:
                queries.append(clean_title)
            if clean_channel:
                queries.append(clean_channel)

            output = []
            seen = set()
            for query in queries:
                videos, _playlists, _continuation, _cfg = (
                    fetch_youtube_results(query)
                )
                for entry in videos or []:
                    if not isinstance(entry, (tuple, list)) or not entry:
                        continue
                    url = str(entry[0] or "")
                    video_id = screen_module.Playlist._normalize_video_id(url)
                    if (
                        not video_id
                        or video_id == current_id
                        or video_id in seen
                    ):
                        continue
                    seen.add(video_id)
                    output.append(
                        {
                            "id": video_id,
                            "url": url,
                            "title": (
                                str(entry[1] or "")
                                if len(entry) > 1
                                else ""
                            ),
                            "channel": (
                                str(entry[2] or "")
                                if len(entry) > 2
                                else ""
                            ),
                            "thumbnail": (
                                str(entry[3] or "")
                                if len(entry) > 3
                                else ""
                            ),
                            "duration": (
                                str(entry[4] or "")
                                if len(entry) > 4
                                else ""
                            ),
                        }
                    )
                    if len(output) >= 12:
                        return output
                if output:
                    break
            return output
        except Exception as exc:
            print("[HOTFIX] related search failed:", exc)
            return []

    def ensure_related(self, video_url: str, title: str = "", channel: str = ""):
        video_url = str(video_url or "")
        if not video_url:
            return

        cached = self._hotfix_related_cache.get(video_url)
        if cached:
            def apply_cached(_dt):
                if video_url != self._last_video_url:
                    return
                self._related_items = self._normalize_related(cached)
                self._hotfix_similar_sig = None
                self._render_similar_ui(force=True)

            Clock.schedule_once(apply_cached, 0)
            return

        if video_url in self._hotfix_related_inflight:
            return

        query_title = str(title or self._title or "")
        query_channel = str(channel or self._channel or "")
        self._hotfix_related_inflight.add(video_url)

        def job():
            try:
                items = search_related(video_url, query_title, query_channel)
                if not items and callable(original_watch_related):
                    try:
                        items = original_watch_related(
                            video_url,
                            getattr(helper, "_ANDROID_WEB_UA", ""),
                            limit=12,
                        )
                    except Exception:
                        items = []

                if items:
                    self._hotfix_related_cache[video_url] = list(items)
                    while len(self._hotfix_related_cache) > 30:
                        self._hotfix_related_cache.pop(
                            next(iter(self._hotfix_related_cache)), None
                        )

                    def apply_items(_dt):
                        if video_url != self._last_video_url:
                            return
                        self._related_items = self._normalize_related(items)
                        self._hotfix_similar_sig = None
                        self._render_similar_ui(force=True)

                    Clock.schedule_once(apply_items, 0)
            except Exception as exc:
                print("[HOTFIX] related extraction failed:", exc)
            finally:
                self._hotfix_related_inflight.discard(video_url)

        threading.Thread(target=job, daemon=True).start()

    player_cls._ensure_related_fast = ensure_related

    # ------------------------------------------------------------------
    # Metadata cache fixes the case where the old URL set prevented the UI
    # from restoring recommendations after revisiting a video.
    # ------------------------------------------------------------------
    def fast_ensure_metadata_async(self, video_url: str):
        video_url = str(video_url or "")
        if not video_url:
            return
        if video_url == getattr(self, "_hotfix_suppress_metadata_url", ""):
            return

        cached = self._hotfix_metadata_cache.get(video_url)
        if cached is None:
            cached = shared_metadata_cache.get(video_url)
        if isinstance(cached, dict):
            self._hotfix_metadata_cache[video_url] = dict(cached)
            self._apply_info_metadata(dict(cached))
            ensure_related(
                self,
                video_url,
                str(cached.get("title") or self._title or ""),
                str(cached.get("channel") or self._channel or ""),
            )
            return

        if video_url in self._hotfix_meta_inflight:
            return
        self._hotfix_meta_inflight.add(video_url)

        def job():
            try:
                info = helper.extract_audio_info(video_url)
                if not isinstance(info, dict):
                    return
                self._hotfix_metadata_cache[video_url] = dict(info)
                while len(self._hotfix_metadata_cache) > 30:
                    self._hotfix_metadata_cache.pop(
                        next(iter(self._hotfix_metadata_cache)), None
                    )
                if video_url != self._last_video_url:
                    return
                self._apply_info_metadata(info)
                ensure_related(
                    self,
                    video_url,
                    str(info.get("title") or self._title or ""),
                    str(info.get("channel") or self._channel or ""),
                )
            except Exception as exc:
                print("[HOTFIX] metadata extraction failed:", exc)
            finally:
                self._hotfix_meta_inflight.discard(video_url)

        threading.Thread(target=job, daemon=True).start()

    player_cls._ensure_metadata_async = fast_ensure_metadata_async

    # The fresh-stream path already extracts metadata.  Suppress the second
    # simultaneous yt-dlp call while preserving metadata loading for cached
    # audio and cached direct URLs.
    original_play_audio = player_cls.play_audio

    def fast_play_audio(
        self,
        video_url: str,
        title: str = "",
        channel: str = "",
        duration_or_thumb=None,
        thumb=None,
        **kwargs,
    ):
        fresh_stream = True
        try:
            if self._find_cached_audio(video_url):
                fresh_stream = False
            else:
                fast = self._url_cache.get(video_url) or {}
                expires = fast.get("expire_ts")
                if fast.get("audio_url") and (
                    not expires or (int(time.time()) + 120) < int(expires)
                ):
                    fresh_stream = False
        except Exception:
            fresh_stream = True

        if fresh_stream:
            self._hotfix_suppress_metadata_url = str(video_url or "")
        try:
            result = original_play_audio(
                self,
                video_url,
                title,
                channel,
                duration_or_thumb,
                thumb,
                **kwargs,
            )
        finally:
            if self._hotfix_suppress_metadata_url == str(video_url or ""):
                self._hotfix_suppress_metadata_url = ""

        ensure_related(self, video_url, title, channel)
        return result

    player_cls.play_audio = fast_play_audio

    _PATCHED = True
    print("[HOTFIX] player performance and related-video fixes enabled")
    return True


def _import_with_hotfix(name, globals=None, locals=None, fromlist=(), level=0):
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if not _PATCHED:
            _patch_audio_screen()
        if _PATCHED:
            builtins.__import__ = _ORIGINAL_IMPORT
    except Exception as exc:
        print("[HOTFIX] patch install failed:", exc)
    return module


builtins.__import__ = _import_with_hotfix
_patch_audio_screen()
