"""Final current-video/recommendations/autoplay layer.

This module deliberately owns the chain that must work together:
current video URL -> related videos -> visible lower panel -> single-video autoplay.
It installs after the core/final player patches and does not depend on the old
lower-panel bootstrap side effects.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import parse_qs, urlparse

from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label

_INSTALLED = False
_STARTED = False
_LOCK = threading.RLock()


class _TapLabel(ButtonBehavior, Label):
    pass


def _canonical_video_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        if "youtube.com" in host:
            vid = str((parse_qs(parsed.query or "").get("v") or [""])[0] or "")
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
        if "youtu.be" in host:
            vid = (parsed.path or "").strip("/").split("/")[0]
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
    except Exception:
        pass
    return raw


def _make_label(text: str, *, height=34, font_size="14sp", bold=False, color=(0.12, 0.12, 0.12, 1)):
    lbl = Label(
        text=str(text or ""),
        size_hint_y=None,
        height=dp(height),
        font_size=font_size,
        bold=bold,
        color=color,
        halign="left",
        valign="middle",
    )
    lbl.bind(width=lambda instance, width: setattr(instance, "text_size", (max(dp(1), width), None)))
    return lbl


def _current_url(owner) -> str:
    return _canonical_video_url(
        getattr(owner, "_current_video_url_v4", "")
        or getattr(owner, "_last_video_url", "")
        or ""
    )


def _fetch_comments(video_url: str, limit: int = 3):
    """Fetch a tiny top-comment sample in a background thread.

    TLS verification stays enabled; this path intentionally does not use
    nocheckcertificate/unverified SSL contexts.
    """
    try:
        import ytdlp_helpers as ydlh

        opts = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "getcomments": True,
            "extractor_retries": 1,
            "socket_timeout": 9,
            "logger": ydlh.YDLLogger(),
            "extractor_args": {
                "youtube": {
                    "comment_sort": ["top"],
                    "max_comments": [f"{int(limit)},all,all,0"],
                    "skip": ["hls", "dash"],
                }
            },
        }
        with ydlh.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False) or {}
        out = []
        for raw in (info.get("comments") or [])[: max(1, int(limit))]:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("content") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "author": str(raw.get("author") or "Користувач YouTube").strip(),
                    "text": text,
                    "likes": int(raw.get("like_count") or 0),
                }
            )
        count = info.get("comment_count")
        try:
            count = int(count) if count is not None else None
        except Exception:
            count = None
        return out, count
    except Exception as exc:
        print("[CURRENT-V4] comments failed:", exc)
        return [], None


def _install_now() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True
        try:
            import audio_screen
        except Exception:
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False
        # Let the old core/final wrappers settle first; this module must be the
        # final owner of current-video/related/autoplay behaviour.
        if not bool(getattr(cls, "_pymusic_hotfix_v4", False)):
            return False
        if not bool(getattr(cls, "_pymusic_final_player_v2", False)):
            return False
        if bool(getattr(cls, "_pymusic_current_related_v4", False)):
            _INSTALLED = True
            return True

        old_play_audio = cls.play_audio
        old_apply_info = cls._apply_info_metadata
        old_sync_loaded = cls._sync_ui_loaded
        old_resume = cls.handle_app_resume
        old_on_kv_post = cls.on_kv_post
        old_toggle_autoskip = cls.toggle_autoskip
        old_advance = cls._advance_to_next_track

        def ensure_state(self):
            if not hasattr(self, "_current_video_url_v4"):
                self._current_video_url_v4 = ""
            if not hasattr(self, "_current_related_inflight_v4"):
                self._current_related_inflight_v4 = set()
            if not hasattr(self, "_current_related_url_v4"):
                self._current_related_url_v4 = ""
            if not hasattr(self, "_current_auto_next_pending_v4"):
                self._current_auto_next_pending_v4 = False
            if not hasattr(self, "_current_comments_v4"):
                self._current_comments_v4 = []
            if not hasattr(self, "_current_comment_count_v4"):
                self._current_comment_count_v4 = None
            if not hasattr(self, "_current_comments_url_v4"):
                self._current_comments_url_v4 = ""
            if not hasattr(self, "_current_comments_loading_v4"):
                self._current_comments_loading_v4 = False

        def get_slot(self):
            try:
                scroll = self.ids.get("similar_scroll")
                listing = self.ids.get("similar_list")
                header = self.ids.get("similar_header_row")
            except Exception:
                return None, None
            if scroll is None or listing is None:
                return None, None
            try:
                if header is not None:
                    header.height = 0
                    header.opacity = 0
                    header.disabled = True
                scroll.size_hint_y = None
                scroll.do_scroll_x = False
                scroll.do_scroll_y = False
                scroll.bar_width = 0
                scroll.opacity = 1
                scroll.disabled = False
                try:
                    scroll.always_overscroll = False
                except Exception:
                    pass
                listing.size_hint_y = None
                listing.padding = (0, 0, 0, dp(12))
                listing.spacing = dp(2)
            except Exception:
                pass
            if not bool(getattr(scroll, "_current_v4_height_guard", False)):
                def guard(instance, value):
                    wanted = float(getattr(instance, "_current_v4_height", 0) or 0)
                    if wanted > 0 and float(value or 0) + 0.5 < wanted:
                        Clock.schedule_once(lambda _dt: setattr(instance, "height", wanted), 0)
                scroll.bind(height=guard)
                scroll._current_v4_height_guard = True
            return scroll, listing

        def apply_height(self, scroll, listing):
            try:
                children = list(getattr(listing, "children", []) or [])
                height = sum(max(0.0, float(getattr(child, "height", 0) or 0)) for child in children)
                height += max(0, len(children) - 1) * float(getattr(listing, "spacing", 0) or 0)
                height += dp(14)
                height = max(dp(210), height)
                listing.height = height
                scroll._current_v4_height = height
                scroll.height = height
                scroll.opacity = 1
                scroll.disabled = False
                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    outer.do_scroll_y = True
                    outer.disabled = False
                    content = outer.children[0] if outer.children else None
                    trigger = getattr(content, "_trigger_layout", None)
                    if callable(trigger):
                        trigger()
            except Exception as exc:
                print("[CURRENT-V4] panel height failed:", exc)

        def render(self, *_args, **_kwargs):
            ensure_state(self)
            scroll, listing = get_slot(self)
            if scroll is None or listing is None:
                print("[CURRENT-V4] lower KV slot missing")
                return
            url = _current_url(self)
            try:
                title = str(getattr(self.ids.get("audio_title"), "text", "") or "").strip()
            except Exception:
                title = ""
            if not url and not title:
                scroll._current_v4_height = 0
                scroll.height = 0
                scroll.opacity = 0
                return

            listing.clear_widgets()

            autoplay = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(54),
                padding=(dp(16), 0, dp(14), 0),
                spacing=dp(8),
            )
            text_box = BoxLayout(orientation="vertical")
            text_box.add_widget(_make_label("Автовідтворення", height=30, font_size="16sp", bold=True))
            text_box.add_widget(_make_label("Автоматично вмикати наступне схоже відео", height=20, font_size="11sp", color=(0.45, 0.45, 0.45, 1)))
            autoplay.add_widget(text_box)
            toggle = _TapLabel(
                text="Увімк." if bool(getattr(self, "_auto_skip", True)) else "Вимк.",
                size_hint=(None, None),
                size=(dp(72), dp(38)),
                font_size="13sp",
                halign="center",
                valign="middle",
                color=(0.12, 0.42, 0.95, 1),
            )
            toggle.text_size = toggle.size
            toggle.bind(on_release=lambda *_a: self.toggle_autoskip())
            autoplay.add_widget(toggle)
            listing.add_widget(autoplay)

            listing.add_widget(_make_label("Коментарі", height=44, font_size="16sp", bold=True))
            comments = list(getattr(self, "_current_comments_v4", []) or [])
            if comments:
                for item in comments[:3]:
                    row = BoxLayout(orientation="vertical", size_hint_y=None, height=dp(72), padding=(dp(16), dp(4), dp(16), dp(4)))
                    row.add_widget(_make_label(item.get("author") or "Користувач YouTube", height=20, font_size="11sp", color=(0.45, 0.45, 0.45, 1)))
                    text = str(item.get("text") or "").replace("\n", " ")
                    row.add_widget(_make_label(text, height=44, font_size="13sp"))
                    listing.add_widget(row)
            elif bool(getattr(self, "_current_comments_loading_v4", False)):
                listing.add_widget(_make_label("Завантаження коментарів…", height=42, font_size="13sp", color=(0.48, 0.48, 0.48, 1)))
            else:
                listing.add_widget(_make_label("Коментарі недоступні або ще не завантажені", height=42, font_size="13sp", color=(0.48, 0.48, 0.48, 1)))

            listing.add_widget(_make_label("Схожі відео", height=46, font_size="17sp", bold=True))
            related = list(getattr(self, "_related_items", []) or [])
            if related:
                for index, item in enumerate(related[:8]):
                    try:
                        row = self._make_similar_row(index, item)
                        listing.add_widget(row)
                    except Exception:
                        title_text = str(item.get("title") or "Відео")
                        row = _TapLabel(
                            text=title_text,
                            size_hint_y=None,
                            height=dp(64),
                            font_size="15sp",
                            halign="left",
                            valign="middle",
                            color=(0.12, 0.12, 0.12, 1),
                        )
                        row.bind(width=lambda instance, width: setattr(instance, "text_size", (max(dp(1), width - dp(24)), None)))
                        row.bind(on_release=lambda _instance, i=index: self._play_from_related_index(i))
                        listing.add_widget(row)
            else:
                listing.add_widget(_make_label("Завантаження рекомендацій…", height=54, font_size="13sp", color=(0.48, 0.48, 0.48, 1)))

            for delay in (0.0, 0.04, 0.14):
                Clock.schedule_once(lambda _dt, s=scroll, l=listing: apply_height(self, s, l), delay)
            print(f"[CURRENT-V4] panel rendered url={url!r} related={len(related)}")

        def play_related_item(self, item) -> bool:
            if not isinstance(item, dict):
                return False
            url = _canonical_video_url(item.get("url") or item.get("video_id") or "")
            if not url:
                return False
            self._current_auto_next_pending_v4 = False
            print(f"[CURRENT-V4] auto-next related url={url!r}")
            self.play_audio(
                url,
                str(item.get("title") or ""),
                str(item.get("channel") or ""),
                str(item.get("thumb") or item.get("thumbnail") or ""),
                clear_playlist=True,
                hard_reset=True,
            )
            return True

        def fallback_search(self, url: str, title: str, channel: str):
            output = []
            try:
                from youtube_search import fetch_youtube_results
                query = " ".join(part for part in (title.strip(), channel.strip()) if part).strip()
                if not query:
                    return []
                videos, _playlists, _cont, _cfg = fetch_youtube_results(query)
                for entry in videos or []:
                    if not isinstance(entry, (tuple, list)) or not entry:
                        continue
                    output.append(
                        {
                            "url": str(entry[0] or ""),
                            "title": str(entry[1] or "") if len(entry) > 1 else "",
                            "channel": str(entry[2] or "") if len(entry) > 2 else "",
                            "thumbnail": str(entry[3] or "") if len(entry) > 3 else "",
                            "duration": str(entry[4] or "") if len(entry) > 4 else "",
                        }
                    )
                    if len(output) >= 8:
                        break
            except Exception as exc:
                print("[CURRENT-V4] search fallback failed:", exc)
            return output

        def ensure_related(self, force=False, auto_advance=False):
            ensure_state(self)
            url = _current_url(self)
            if not url:
                print("[CURRENT-V4] related skipped: no current video URL")
                return False
            if auto_advance:
                self._current_auto_next_pending_v4 = True
            if getattr(self, "_related_items", None) and not force:
                if self._current_auto_next_pending_v4:
                    return play_related_item(self, self._related_items[0])
                Clock.schedule_once(lambda _dt: render(self), 0)
                return True
            if url in self._current_related_inflight_v4:
                return True
            self._current_related_inflight_v4.add(url)
            title = str(getattr(self, "_title", "") or "")
            channel = str(getattr(self, "_channel", "") or "")
            print(f"[CURRENT-V4] related fetch url={url!r} title={title!r} channel={channel!r}")

            def job():
                items = []
                try:
                    from related_videos import fetch_related_videos
                    items = fetch_related_videos(url, title, channel, limit=8) or []
                except Exception as exc:
                    print("[CURRENT-V4] related fetch failed:", exc)
                if not items:
                    items = fallback_search(self, url, title, channel)
                try:
                    normalized = self._normalize_related(items or [])
                except Exception:
                    normalized = []
                self._current_related_inflight_v4.discard(url)
                if _current_url(self) != url:
                    return
                self._related_items = normalized[:8]
                self._current_related_url_v4 = url
                print(f"[CURRENT-V4] related applied count={len(self._related_items)} url={url!r}")

                pending = bool(getattr(self, "_current_auto_next_pending_v4", False))
                if pending and self._related_items:
                    if bool(getattr(self, "_app_in_background", False)):
                        play_related_item(self, self._related_items[0])
                    else:
                        Clock.schedule_once(lambda _dt: play_related_item(self, self._related_items[0]), 0)
                    return
                if pending and not self._related_items:
                    self._current_auto_next_pending_v4 = False
                    self._playback_desired = False
                    self._user_paused = True
                    Clock.schedule_once(lambda _dt: self._ui_set_playing(False), 0)
                Clock.schedule_once(lambda _dt: render(self), 0)

            threading.Thread(target=job, name="pymusic-current-related-v4", daemon=True).start()
            return True

        def ensure_comments(self):
            ensure_state(self)
            if bool(getattr(self, "_app_in_background", False)):
                return
            url = _current_url(self)
            if not url:
                return
            if self._current_comments_url_v4 == url and (
                self._current_comments_v4 or self._current_comments_loading_v4
            ):
                return
            self._current_comments_url_v4 = url
            self._current_comments_v4 = []
            self._current_comment_count_v4 = None
            self._current_comments_loading_v4 = True
            Clock.schedule_once(lambda _dt: render(self), 0)

            def job():
                comments, count = _fetch_comments(url, limit=3)
                if _current_url(self) != url:
                    return
                self._current_comments_v4 = comments
                self._current_comment_count_v4 = count
                self._current_comments_loading_v4 = False
                Clock.schedule_once(lambda _dt: render(self), 0)

            threading.Thread(target=job, name="pymusic-comments-v4", daemon=True).start()

        def play_audio_v4(self, video_url, *args, **kwargs):
            ensure_state(self)
            canonical = _canonical_video_url(video_url)
            self._current_video_url_v4 = canonical
            self._current_related_url_v4 = ""
            self._current_auto_next_pending_v4 = False
            self._current_comments_url_v4 = ""
            self._current_comments_v4 = []
            print(f"[CURRENT-V4] current video set url={canonical!r}")
            result = old_play_audio(self, video_url, *args, **kwargs)
            final_url = _canonical_video_url(getattr(self, "_last_video_url", "") or canonical)
            if final_url:
                self._current_video_url_v4 = final_url
                if not getattr(self, "_last_video_url", None):
                    self._last_video_url = final_url
            ensure_related(self, force=False)
            if not bool(getattr(self, "_app_in_background", False)):
                ensure_comments(self)
            for delay in (0.0, 0.10, 0.40):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def apply_info_v4(self, info):
            result = old_apply_info(self, info)
            ensure_state(self)
            info_url = _canonical_video_url(
                (info or {}).get("webpage_url")
                or (info or {}).get("original_url")
                or ""
            )
            if info_url and not _current_url(self):
                self._current_video_url_v4 = info_url
            if not getattr(self, "_related_items", None):
                ensure_related(self, force=True)
            if not bool(getattr(self, "_app_in_background", False)):
                ensure_comments(self)
            Clock.schedule_once(lambda _dt: render(self), 0)
            return result

        def advance_v4(self) -> bool:
            ensure_state(self)
            try:
                playlist = getattr(self, "playlist", None)
                if playlist and len(playlist) > 1:
                    return bool(old_advance(self))
            except Exception:
                pass
            related = list(getattr(self, "_related_items", []) or [])
            if related:
                return play_related_item(self, related[0])
            if _current_url(self):
                print("[CURRENT-V4] auto-next waiting for related videos")
                self._playback_desired = True
                self._user_paused = False
                ensure_related(self, force=True, auto_advance=True)
                return True
            return bool(old_advance(self))

        def sync_loaded_v4(self, *args, **kwargs):
            result = old_sync_loaded(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
            return result

        def resume_v4(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            if _current_url(self):
                ensure_related(self, force=False)
                ensure_comments(self)
            for delay in (0.0, 0.12, 0.5):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def on_kv_post_v4(self, base_widget):
            result = old_on_kv_post(self, base_widget)
            for delay in (0.0, 0.10, 0.35):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def toggle_autoskip_v4(self, *args, **kwargs):
            result = old_toggle_autoskip(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
            return result

        cls.play_audio = play_audio_v4
        cls._apply_info_metadata = apply_info_v4
        cls._advance_to_next_track = advance_v4
        cls._sync_ui_loaded = sync_loaded_v4
        cls.handle_app_resume = resume_v4
        cls.on_kv_post = on_kv_post_v4
        cls.toggle_autoskip = toggle_autoskip_v4
        cls._render_similar_ui = render
        cls._ensure_related_current_v4 = ensure_related
        cls._render_current_lower_v4 = render
        cls._pymusic_current_related_v4 = True

        _INSTALLED = True
        print("[CURRENT-V4] current video + related + autoplay layer enabled")
        return True


def install_current_video_related_fix() -> bool:
    global _STARTED
    if _install_now():
        return True
    with _LOCK:
        if _STARTED:
            return True
        _STARTED = True

    def waiter():
        ready_seen = False
        for _attempt in range(500):
            try:
                import audio_screen
                cls = getattr(audio_screen, "AudioPlayerScreen", None)
                ready = bool(
                    cls is not None
                    and getattr(cls, "_pymusic_hotfix_v4", False)
                    and getattr(cls, "_pymusic_final_player_v2", False)
                )
            except Exception:
                ready = False
            if ready and not ready_seen:
                # Give the thumbnail/layout patch chain a moment to finish, then
                # install this module last so nothing can replace autoplay again.
                ready_seen = True
                time.sleep(0.45)
            if ready_seen and _install_now():
                return
            time.sleep(0.05)
        print("[CURRENT-V4] install timeout")

    threading.Thread(target=waiter, name="pymusic-current-related-installer", daemon=True).start()
    return True
