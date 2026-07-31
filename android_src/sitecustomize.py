"""Runtime fixes for the Android player.

The module is imported by ``recent_utils``.  A short poll there waits until
``AudioPlayerScreen`` has been fully defined before calling
``_patch_audio_screen``.
"""

from __future__ import annotations

import sys
import threading
import time

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_audio_screen() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        helper = sys.modules.get("ytdlp_helpers")
        video_module = sys.modules.get("video_player")
        if module is None or helper is None or video_module is None:
            return False

        player_cls = getattr(module, "AudioPlayerScreen", None)
        thumb_cls = getattr(module, "ThumbImage", None)
        video_cls = getattr(video_module, "AndroidVideoPlayer", None)
        if player_cls is None or thumb_cls is None or video_cls is None:
            return False
        if getattr(player_cls, "_pymusic_hotfix_v4", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        dp = module.dp

        def ensure_state(self) -> None:
            if not hasattr(self, "_hotfix_related_cache"):
                self._hotfix_related_cache = {}
            if not hasattr(self, "_hotfix_related_inflight"):
                self._hotfix_related_inflight = set()
            if not hasattr(self, "_hotfix_related_attempts"):
                self._hotfix_related_attempts = {}
            if not hasattr(self, "_hotfix_related_loading"):
                self._hotfix_related_loading = False
            if not hasattr(self, "_hotfix_related_failed"):
                self._hotfix_related_failed = False
            if not hasattr(self, "_hotfix_playlist_sig"):
                self._hotfix_playlist_sig = None
            if not hasattr(self, "_hotfix_playlist_window"):
                self._hotfix_playlist_window = (0, 0)
            if not hasattr(self, "_hotfix_similar_sig"):
                self._hotfix_similar_sig = None
            if not hasattr(self, "_hotfix_cache_pending"):
                self._hotfix_cache_pending = set()

        # The old audio extraction path fetched the watch page only to obtain
        # recommendations.  That could block playback for up to eight seconds.
        # Recommendations are now loaded independently below.
        if not getattr(helper, "_pymusic_related_fallback_disabled", False):
            try:
                helper._pymusic_original_related_fallback = getattr(
                    helper, "_extract_related_from_watch_page", None
                )
                helper._extract_related_from_watch_page = (
                    lambda *_args, **_kwargs: []
                )
                helper._pymusic_related_fallback_disabled = True
            except Exception:
                pass

        # Avoid decoding an already loaded local thumbnail again.
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

        # ------------------------------------------------------------------
        # Native video controls: rounded semi-transparent black backing under
        # the three touch buttons.
        # ------------------------------------------------------------------
        @video_module.run_on_ui_thread
        def style_native_control_bar(video_player):
            try:
                overlay = getattr(video_player, "controls_overlay", None)
                if overlay is None:
                    return
                count = int(overlay.getChildCount() or 0)
                for index in range(count):
                    child = overlay.getChildAt(index)
                    try:
                        if int(child.getChildCount() or 0) != 3:
                            continue
                    except Exception:
                        continue
                    background = video_module.GradientDrawable()
                    background.setShape(
                        video_module.GradientDrawable.RECTANGLE
                    )
                    background.setColor(
                        video_module.Color.argb(150, 0, 0, 0)
                    )
                    background.setCornerRadius(58.0)
                    child.setBackground(background)
                    try:
                        child.setPadding(22, 8, 22, 8)
                    except Exception:
                        pass
                    child.invalidate()
                    break
            except Exception as exc:
                print("[VIDEO] control backing style failed:", exc)

        old_ensure_overlay = video_cls._ensure_controls_overlay

        def ensure_overlay_with_backing(self, *args, **kwargs):
            result = old_ensure_overlay(self, *args, **kwargs)
            Clock.schedule_once(
                lambda _dt: style_native_control_bar(self), 0.08
            )
            return result

        video_cls._ensure_controls_overlay = ensure_overlay_with_backing

        old_controls_visible = video_cls.set_native_controls_visible

        def controls_visible_with_backing(self, visible):
            result = old_controls_visible(self, visible)
            if visible:
                Clock.schedule_once(
                    lambda _dt: style_native_control_bar(self), 0.05
                )
            return result

        video_cls.set_native_controls_visible = controls_visible_with_backing

        # ------------------------------------------------------------------
        # Section geometry and lightweight playlist rendering.
        # ------------------------------------------------------------------
        def set_playlist_visibility(self) -> None:
            ensure_state(self)
            visible = bool(self.playlist and self.playlist.tracks)
            collapsed = bool(getattr(self, "_playlist_collapsed", False))
            start, end = self._hotfix_playlist_window
            total = len(self.playlist.tracks) if visible else 0
            title = self.playlist.name or "Черга"
            if total > 72 and end > start:
                title = f"{title} · {start + 1}–{end} / {total}"
            self._set_collapsible_header(
                self.ids.get("playlist_header_row"),
                self.ids.get("playlist_header"),
                self.ids.get("playlist_toggle_btn"),
                visible,
                title,
                collapsed,
            )
            scroll = self.ids.get("playlist_scroll")
            expanded = bool(visible and not collapsed)
            if scroll is not None:
                scroll.height = dp(240) if expanded else 0
                scroll.opacity = 1 if expanded else 0
                scroll.disabled = not expanded

        def set_similar_visibility(self) -> None:
            ensure_state(self)
            has_items = bool(getattr(self, "_related_items", None))
            loading = bool(self._hotfix_related_loading)
            failed = bool(self._hotfix_related_failed)
            visible = bool(has_items or loading or failed)
            collapsed = bool(getattr(self, "_similar_collapsed", False))
            if loading and not has_items:
                title = "Схожі відео · завантаження…"
            elif failed and not has_items:
                title = "Схожі відео · повторна спроба…"
            else:
                title = "Схожі відео"
            self._set_collapsible_header(
                self.ids.get("similar_header_row"),
                self.ids.get("similar_header"),
                self.ids.get("similar_toggle_btn"),
                visible,
                title,
                collapsed,
            )
            scroll = self.ids.get("similar_scroll")
            expanded = bool(has_items and not collapsed)
            if scroll is not None:
                scroll.height = dp(220) if expanded else 0
                scroll.opacity = 1 if expanded else 0
                scroll.disabled = not expanded

        def toggle_playlist(self):
            self._playlist_collapsed = not bool(
                getattr(self, "_playlist_collapsed", False)
            )
            set_playlist_visibility(self)

        def toggle_similar(self):
            self._similar_collapsed = not bool(
                getattr(self, "_similar_collapsed", False)
            )
            set_similar_visibility(self)

        player_cls.toggle_playlist_collapsed = toggle_playlist
        player_cls.toggle_similar_collapsed = toggle_similar

        def playlist_signature(self):
            try:
                return tuple(
                    (
                        str(item.get("url") or ""),
                        str(item.get("video_id") or ""),
                        str(item.get("thumb") or ""),
                        str(item.get("duration") or ""),
                    )
                    for item in (
                        self.playlist.tracks if self.playlist else []
                    )
                )
            except Exception:
                return None

        def render_playlist(self, force=False):
            ensure_state(self)
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None

            tracks = list(
                self.playlist.tracks
                if self.playlist and self.playlist.tracks
                else []
            )
            signature = playlist_signature(self)
            current_index = int(
                getattr(self.playlist, "index", 0) or 0
            )
            win_start, win_end = self._hotfix_playlist_window
            current_in_window = win_start <= current_index < win_end
            has_rows = bool(getattr(listing, "children", None))

            if (
                signature == self._hotfix_playlist_sig
                and has_rows
                and (current_in_window or len(tracks) <= 72)
            ):
                set_playlist_visibility(self)
                return None

            self._playlist_render_gen = int(
                getattr(self, "_playlist_render_gen", 0)
            ) + 1
            render_gen = self._playlist_render_gen
            listing.clear_widgets()

            if not tracks:
                self._hotfix_playlist_window = (0, 0)
                self._hotfix_playlist_sig = signature
                set_playlist_visibility(self)
                return None

            if len(tracks) <= 72:
                start = 0
                end = len(tracks)
            else:
                start = max(0, current_index - 14)
                end = min(len(tracks), start + 54)
                start = max(0, end - 54)

            self._hotfix_playlist_window = (start, end)
            self._hotfix_playlist_sig = signature
            set_playlist_visibility(self)
            if bool(getattr(self, "_playlist_collapsed", False)):
                return None

            indices = list(range(start, end))
            chunk_size = 4

            def add_chunk(offset):
                if render_gen != int(
                    getattr(self, "_playlist_render_gen", -1)
                ):
                    return
                stop = min(len(indices), offset + chunk_size)
                for position in range(offset, stop):
                    actual_index = indices[position]
                    row = self._make_playlist_row(
                        actual_index, tracks[actual_index]
                    )
                    listing.add_widget(row)
                if stop < len(indices):
                    Clock.schedule_once(
                        lambda _dt, next_offset=stop: add_chunk(
                            next_offset
                        ),
                        0.016,
                    )

            Clock.schedule_once(lambda _dt: add_chunk(0), 0)
            return None

        player_cls._render_playlist_ui = render_playlist

        old_render_similar = player_cls._render_similar_ui

        def related_signature(self):
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

        def render_similar(self, force=False):
            ensure_state(self)
            listing = self.ids.get("similar_list")
            if listing is None:
                return None

            if not getattr(self, "_related_items", None):
                # Hiding is instant.  Keep the old widgets alive until the new
                # recommendations arrive instead of destroying many images on
                # every track change.
                self._hotfix_similar_sig = None
                set_similar_visibility(self)
                return None

            signature = related_signature(self)
            if (
                signature == self._hotfix_similar_sig
                and getattr(listing, "children", None)
            ):
                set_similar_visibility(self)
                return None

            result = old_render_similar(self, force=True)
            self._hotfix_similar_sig = signature
            set_similar_visibility(self)
            return result

        player_cls._render_similar_ui = render_similar

        # ------------------------------------------------------------------
        # Recommendations.
        # ------------------------------------------------------------------
        def matching_track(self, video_url):
            try:
                target_id = module.Playlist._normalize_video_id(
                    video_url
                )
                for track in (
                    self.playlist.tracks if self.playlist else []
                ):
                    if not isinstance(track, dict):
                        continue
                    track_url = str(track.get("url") or "")
                    track_id = module.Playlist._normalize_video_id(
                        track_url
                    ) or module.Playlist._normalize_video_id(
                        str(track.get("video_id") or "")
                    )
                    if (
                        track_url == video_url
                        or (target_id and track_id == target_id)
                    ):
                        return track
            except Exception:
                pass
            return {}

        def playlist_related_fallback(self, video_url, limit=8):
            output = []
            try:
                current_id = module.Playlist._normalize_video_id(
                    video_url
                )
                tracks = (
                    self.playlist.tracks if self.playlist else []
                )
                start = int(getattr(self.playlist, "index", 0) or 0)
                order = list(range(start + 1, len(tracks))) + list(
                    range(0, start + 1)
                )
                for index in order:
                    track = tracks[index]
                    if not isinstance(track, dict):
                        continue
                    url = str(track.get("url") or "")
                    video_id = (
                        module.Playlist._normalize_video_id(url)
                        or module.Playlist._normalize_video_id(
                            str(track.get("video_id") or "")
                        )
                    )
                    if not url or (current_id and video_id == current_id):
                        continue
                    output.append(
                        {
                            "id": video_id,
                            "video_id": video_id,
                            "url": url,
                            "title": str(track.get("title") or ""),
                            "channel": str(
                                track.get("channel") or ""
                            ),
                            "thumbnail": str(
                                track.get("thumb") or ""
                            ),
                            "duration": str(
                                track.get("duration") or ""
                            ),
                        }
                    )
                    if len(output) >= limit:
                        break
            except Exception:
                return []
            return output

        def apply_related(self, video_url, items):
            ensure_state(self)
            if video_url != str(
                getattr(self, "_last_video_url", "") or ""
            ):
                return
            normalized = self._normalize_related(items or [])
            self._hotfix_related_loading = False
            self._hotfix_related_failed = not bool(normalized)
            if not normalized:
                set_similar_visibility(self)
                return
            self._related_items = normalized[:8]
            self._hotfix_related_cache[video_url] = list(items)[:8]
            while len(self._hotfix_related_cache) > 24:
                self._hotfix_related_cache.pop(
                    next(iter(self._hotfix_related_cache)), None
                )
            self._hotfix_related_attempts.pop(video_url, None)
            self._hotfix_similar_sig = None
            self._render_similar_ui(force=True)
            print(
                f"[RELATED] rendered {len(self._related_items)} items"
            )

        def ensure_related(
            self,
            video_url,
            title="",
            channel="",
            delay=0.7,
            force=False,
        ):
            ensure_state(self)
            video_url = str(video_url or "")
            if not video_url:
                return

            cached = self._hotfix_related_cache.get(video_url)
            if cached and not force:
                Clock.schedule_once(
                    lambda _dt: apply_related(
                        self, video_url, cached
                    ),
                    0,
                )
                return

            known = matching_track(self, video_url)
            query_title = str(
                title
                or known.get("title")
                or getattr(self, "_title", "")
                or ""
            )
            query_channel = str(
                channel
                or known.get("channel")
                or getattr(self, "_channel", "")
                or ""
            )

            self._hotfix_related_loading = True
            self._hotfix_related_failed = False
            Clock.schedule_once(
                lambda _dt: set_similar_visibility(self), 0
            )

            def start_worker(_dt=0):
                if video_url != str(
                    getattr(self, "_last_video_url", "") or ""
                ):
                    return
                if video_url in self._hotfix_related_inflight:
                    return
                self._hotfix_related_inflight.add(video_url)

                def worker():
                    items = []
                    try:
                        latest = matching_track(self, video_url)
                        latest_title = str(
                            query_title
                            or latest.get("title")
                            or getattr(self, "_title", "")
                            or ""
                        )
                        latest_channel = str(
                            query_channel
                            or latest.get("channel")
                            or getattr(self, "_channel", "")
                            or ""
                        )
                        from related_videos import (
                            fetch_related_videos,
                        )

                        items = fetch_related_videos(
                            video_url,
                            latest_title,
                            latest_channel,
                            limit=8,
                        )
                    except Exception as exc:
                        print("[RELATED] worker failed:", exc)

                    if not items:
                        items = playlist_related_fallback(
                            self, video_url, limit=8
                        )
                        if items:
                            print(
                                "[RELATED] using playlist fallback "
                                f"({len(items)} items)"
                            )

                    self._hotfix_related_inflight.discard(
                        video_url
                    )
                    if items:
                        Clock.schedule_once(
                            lambda _dt: apply_related(
                                self, video_url, items
                            ),
                            0,
                        )
                        return

                    attempts = int(
                        self._hotfix_related_attempts.get(
                            video_url, 0
                        )
                    ) + 1
                    self._hotfix_related_attempts[
                        video_url
                    ] = attempts
                    self._hotfix_related_loading = False
                    self._hotfix_related_failed = True
                    Clock.schedule_once(
                        lambda _dt: set_similar_visibility(self),
                        0,
                    )
                    if attempts < 3:
                        Clock.schedule_once(
                            lambda _dt: ensure_related(
                                self,
                                video_url,
                                "",
                                "",
                                delay=0,
                                force=True,
                            ),
                            2.0 * attempts,
                        )

                threading.Thread(
                    target=worker,
                    name="pymusic-related-videos",
                    daemon=True,
                ).start()

            Clock.schedule_once(start_worker, max(0.0, float(delay)))

        player_cls._ensure_related_fast = ensure_related

        # New metadata can arrive after the first fast playlist start.  Trigger
        # recommendations again with the now-known title/channel.
        old_apply_metadata = player_cls._apply_info_metadata

        def apply_metadata_and_related(self, info):
            result = old_apply_metadata(self, info)
            try:
                ensure_related(
                    self,
                    self._last_video_url,
                    str((info or {}).get("title") or ""),
                    str(
                        (info or {}).get("channel")
                        or (info or {}).get("uploader")
                        or ""
                    ),
                    delay=0.15,
                )
            except Exception:
                pass
            return result

        player_cls._apply_info_metadata = apply_metadata_and_related

        old_extract_and_start = player_cls._extract_and_start

        def extract_and_start_with_related(
            self, video_url, gen, *args, **kwargs
        ):
            result = old_extract_and_start(
                self, video_url, gen, *args, **kwargs
            )
            try:
                if (
                    video_url
                    == str(
                        getattr(self, "_last_video_url", "") or ""
                    )
                ):
                    ensure_related(
                        self,
                        video_url,
                        getattr(self, "_title", ""),
                        getattr(self, "_channel", ""),
                        delay=0.15,
                    )
            except Exception:
                pass
            return result

        player_cls._extract_and_start = (
            extract_and_start_with_related
        )

        old_play_playlist = player_cls.play_playlist

        def play_playlist_fast(self, *args, **kwargs):
            result = old_play_playlist(self, *args, **kwargs)
            try:
                ensure_related(
                    self,
                    self._last_video_url,
                    getattr(self, "_title", ""),
                    getattr(self, "_channel", ""),
                    delay=0.2,
                    force=True,
                )
            except Exception:
                pass
            return result

        player_cls.play_playlist = play_playlist_fast

        old_play_audio = player_cls.play_audio

        def play_audio_fast(
            self,
            video_url,
            title="",
            channel="",
            duration_or_thumb=None,
            thumb=None,
            **kwargs,
        ):
            ensure_state(self)
            previous_url = str(
                getattr(self, "_last_video_url", "") or ""
            )
            # A new URL already performs the normal cleanup in play_audio.
            # The extra hard reset stopped/released everything twice.
            if (
                video_url
                and previous_url
                and str(video_url) != previous_url
                and kwargs.get("hard_reset")
            ):
                kwargs["hard_reset"] = False

            self._hotfix_related_loading = True
            self._hotfix_related_failed = False
            result = old_play_audio(
                self,
                video_url,
                title,
                channel,
                duration_or_thumb,
                thumb,
                **kwargs,
            )
            ensure_related(
                self,
                video_url,
                title,
                channel,
                delay=0.8 if not title else 0.45,
            )
            return result

        player_cls.play_audio = play_audio_fast

        # Prefetch only the direct URL.  Downloading the whole next song while
        # the current one was playing caused bandwidth and I/O contention.
        def prefetch_next_url_only(self):
            if not self.playlist or len(self.playlist) < 2:
                return
            try:
                next_index = (
                    int(self.playlist.index) + 1
                ) % len(self.playlist.tracks)
                next_track = self.playlist.tracks[next_index]
                next_url = str(next_track.get("url") or "")
            except Exception:
                return
            if (
                not next_url
                or next_url == self._last_video_url
                or next_url in self._prefetch_inflight
            ):
                return
            cached = self._url_cache.get(next_url) or {}
            if cached.get("audio_url"):
                return
            self._prefetch_inflight.add(next_url)

            def worker():
                try:
                    info = helper.extract_audio_info(next_url)
                    audio_url = str(info.get("audio_url") or "")
                    if audio_url:
                        self._put_cache(
                            next_url,
                            audio_url,
                            info.get("http_headers") or {},
                            info.get("expire_ts"),
                        )
                except Exception:
                    pass
                finally:
                    self._prefetch_inflight.discard(next_url)

            threading.Thread(
                target=worker,
                name="pymusic-next-url-prefetch",
                daemon=True,
            ).start()

        player_cls._prefetch_next_track_audio = (
            prefetch_next_url_only
        )

        # Keep normal cache behaviour, but delay it until playback is stable.
        old_cache_audio = player_cls._cache_audio_async

        def delayed_cache_audio(self, video_url, audio_url, headers=None):
            ensure_state(self)
            key = str(video_url or "")
            if not key or key in self._hotfix_cache_pending:
                return
            if (
                key == str(
                    getattr(self, "_last_video_url", "") or ""
                )
                and bool(getattr(self, "_favorite", False))
            ):
                return old_cache_audio(
                    self, video_url, audio_url, headers
                )

            self._hotfix_cache_pending.add(key)

            def run_later():
                try:
                    if (
                        key
                        == str(
                            getattr(
                                self, "_last_video_url", ""
                            )
                            or ""
                        )
                        and bool(
                            getattr(
                                self, "_playback_desired", False
                            )
                        )
                    ):
                        old_cache_audio(
                            self, video_url, audio_url, headers
                        )
                finally:
                    self._hotfix_cache_pending.discard(key)

            timer = threading.Timer(8.0, run_later)
            timer.daemon = True
            timer.start()

        player_cls._cache_audio_async = delayed_cache_audio

        player_cls._pymusic_hotfix_v4 = True
        _PATCHED = True
        print(
            "[HOTFIX] player performance, controls and "
            "related videos v4 enabled"
        )
        return True


_patch_audio_screen()
