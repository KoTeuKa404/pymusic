"""Runtime player fixes loaded by ``recent_utils`` on Android."""

from __future__ import annotations

import sys
import threading

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_audio_screen() -> bool:
    global _PATCHED
    with _PATCH_LOCK:
        if _PATCHED:
            return True
        module = sys.modules.get("audio_screen")
        helper = sys.modules.get("ytdlp_helpers")
        if module is None or helper is None:
            return False
        player_cls = getattr(module, "AudioPlayerScreen", None)
        thumb_cls = getattr(module, "ThumbImage", None)
        if player_cls is None or thumb_cls is None:
            return False
        if getattr(player_cls, "_pymusic_hotfix_v3", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        dp = module.dp

        def ensure_state(self):
            if not hasattr(self, "_hotfix_related_cache"):
                self._hotfix_related_cache = {}
            if not hasattr(self, "_hotfix_related_inflight"):
                self._hotfix_related_inflight = set()
            if not hasattr(self, "_hotfix_related_attempts"):
                self._hotfix_related_attempts = {}
            if not hasattr(self, "_hotfix_playlist_sig"):
                self._hotfix_playlist_sig = None
            if not hasattr(self, "_hotfix_similar_sig"):
                self._hotfix_similar_sig = None

        old_thumb_loader = thumb_cls.set_local_file

        def fast_thumb_loader(self, path):
            try:
                if (
                    path
                    and str(getattr(self, "source", "") or "") == str(path)
                    and getattr(self, "texture", None) is not None
                ):
                    return True
            except Exception:
                pass
            return old_thumb_loader(self, path)

        thumb_cls.set_local_file = fast_thumb_loader

        old_init = player_cls.__init__

        def fast_init(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            ensure_state(self)

        player_cls.__init__ = fast_init

        def playlist_visibility(self):
            ensure_state(self)
            scroll = self.ids.get("playlist_scroll")
            visible = bool(self.playlist and self.playlist.tracks)
            collapsed = bool(getattr(self, "_playlist_collapsed", False))
            self._set_collapsible_header(
                self.ids.get("playlist_header_row"),
                self.ids.get("playlist_header"),
                self.ids.get("playlist_toggle_btn"),
                visible,
                self.playlist.name or "Черга",
                collapsed,
            )
            expanded = visible and not collapsed
            if scroll is not None:
                scroll.height = dp(240) if expanded else 0
                scroll.opacity = 1 if expanded else 0
                scroll.disabled = not expanded

        def similar_visibility(self):
            ensure_state(self)
            scroll = self.ids.get("similar_scroll")
            visible = bool(getattr(self, "_related_items", None))
            collapsed = bool(getattr(self, "_similar_collapsed", False))
            self._set_collapsible_header(
                self.ids.get("similar_header_row"),
                self.ids.get("similar_header"),
                self.ids.get("similar_toggle_btn"),
                visible,
                "Схожі відео",
                collapsed,
            )
            expanded = visible and not collapsed
            if scroll is not None:
                scroll.height = dp(220) if expanded else 0
                scroll.opacity = 1 if expanded else 0
                scroll.disabled = not expanded

        def toggle_playlist(self):
            self._playlist_collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            playlist_visibility(self)

        def toggle_similar(self):
            self._similar_collapsed = not bool(
                getattr(self, "_similar_collapsed", False)
            )
            similar_visibility(self)

        player_cls.toggle_playlist_collapsed = toggle_playlist
        player_cls.toggle_similar_collapsed = toggle_similar

        old_render_playlist = player_cls._render_playlist_ui

        def render_playlist(self, force=False):
            ensure_state(self)
            try:
                signature = tuple(
                    (
                        str(item.get("url") or ""),
                        str(item.get("video_id") or ""),
                        str(item.get("thumb") or ""),
                    )
                    for item in (self.playlist.tracks if self.playlist else [])
                )
            except Exception:
                signature = None
            rows = self.ids.get("playlist_list")
            if signature == self._hotfix_playlist_sig and rows and rows.children:
                playlist_visibility(self)
                return None
            result = old_render_playlist(self, force=force)
            self._hotfix_playlist_sig = signature
            return result

        player_cls._render_playlist_ui = render_playlist

        old_render_similar = player_cls._render_similar_ui

        def render_similar(self, force=False):
            ensure_state(self)
            try:
                signature = tuple(
                    (
                        str(item.get("url") or ""),
                        str(item.get("title") or ""),
                        str(item.get("thumb") or ""),
                    )
                    for item in (self._related_items or [])
                )
            except Exception:
                signature = None
            rows = self.ids.get("similar_list")
            if signature == self._hotfix_similar_sig and rows and rows.children:
                similar_visibility(self)
                return None
            result = old_render_similar(self, force=force)
            self._hotfix_similar_sig = signature
            return result

        player_cls._render_similar_ui = render_similar

        # Keep the old HTML fallback out of the audio-extraction critical path.
        old_extract = helper.extract_audio_info
        extract_lock = threading.RLock()

        def fast_extract(video_url, *args, **kwargs):
            with extract_lock:
                fallback = getattr(helper, "_extract_related_from_watch_page", None)
                try:
                    if fallback is not None:
                        helper._extract_related_from_watch_page = (
                            lambda *_args, **_kwargs: []
                        )
                    return old_extract(video_url, *args, **kwargs)
                finally:
                    if fallback is not None:
                        helper._extract_related_from_watch_page = fallback

        helper.extract_audio_info = fast_extract

        def apply_related(self, video_url, items):
            if video_url != str(getattr(self, "_last_video_url", "") or ""):
                return
            normalized = self._normalize_related(items or [])
            if not normalized:
                return
            self._related_items = normalized
            self._hotfix_similar_sig = None
            self._render_similar_ui(force=True)
            print(f"[RELATED] rendered {len(normalized)} items")

        def ensure_related(self, video_url, title="", channel="", retry=False):
            ensure_state(self)
            video_url = str(video_url or "")
            if not video_url:
                return
            cached = self._hotfix_related_cache.get(video_url)
            if cached:
                Clock.schedule_once(
                    lambda _dt: apply_related(self, video_url, cached), 0
                )
                return
            if video_url in self._hotfix_related_inflight:
                return
            self._hotfix_related_inflight.add(video_url)
            title = str(title or getattr(self, "_title", "") or "")
            channel = str(channel or getattr(self, "_channel", "") or "")

            def worker():
                items = []
                try:
                    from related_videos import fetch_related_videos

                    items = fetch_related_videos(
                        video_url, title, channel, limit=12
                    )
                    if items:
                        self._hotfix_related_cache[video_url] = list(items)
                        self._hotfix_related_attempts.pop(video_url, None)
                        Clock.schedule_once(
                            lambda _dt: apply_related(self, video_url, items), 0
                        )
                except Exception as exc:
                    print("[RELATED] worker failed:", exc)
                finally:
                    self._hotfix_related_inflight.discard(video_url)

                if not items:
                    attempts = int(
                        self._hotfix_related_attempts.get(video_url, 0)
                    ) + 1
                    self._hotfix_related_attempts[video_url] = attempts
                    if attempts < 2 and not retry:
                        Clock.schedule_once(
                            lambda _dt: ensure_related(
                                self, video_url, title, channel, retry=True
                            ),
                            3.0,
                        )

            threading.Thread(
                target=worker,
                name="pymusic-related-videos",
                daemon=True,
            ).start()

        player_cls._ensure_related_fast = ensure_related

        old_play_audio = player_cls.play_audio

        def fast_play_audio(
            self,
            video_url,
            title="",
            channel="",
            duration_or_thumb=None,
            thumb=None,
            **kwargs,
        ):
            ensure_state(self)
            result = old_play_audio(
                self,
                video_url,
                title,
                channel,
                duration_or_thumb,
                thumb,
                **kwargs,
            )
            ensure_related(self, video_url, title, channel)
            return result

        player_cls.play_audio = fast_play_audio
        player_cls._pymusic_hotfix_v3 = True
        _PATCHED = True
        print("[HOTFIX] player performance and related videos v3 enabled")
        return True


_patch_audio_screen()
