"""YouTube-like lower player panel: autoplay, comments and recommendations.

This patch deliberately reuses the existing ``similar_scroll/similar_list``
slot from youtube_gui.kv instead of creating another nested scrolling surface.
The outer player_details_scroll remains the only vertical scroller.
"""
from __future__ import annotations

import threading
import time

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import AsyncImage
from kivy.uix.label import Label
from kivy.uix.widget import Widget

try:
    from kivymd.uix.selectioncontrol import MDSwitch
except Exception:
    MDSwitch = None

_INSTALLED = False
_LOCK = threading.RLock()


class _TapRow(ButtonBehavior, BoxLayout):
    pass


def _bind_white_bg(widget, radius=0):
    try:
        with widget.canvas.before:
            color = Color(1, 1, 1, 1)
            if radius:
                rect = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[dp(radius)] * 4)
            else:
                rect = Rectangle(pos=widget.pos, size=widget.size)

        def sync(*_args):
            rect.pos = widget.pos
            rect.size = widget.size

        widget.bind(pos=sync, size=sync)
        widget._yt_bg_color = color
        widget._yt_bg_rect = rect
    except Exception:
        pass


def _divider():
    line = Widget(size_hint_y=None, height=dp(8))
    try:
        with line.canvas.before:
            c = Color(0.94, 0.94, 0.94, 1)
            r = Rectangle(pos=line.pos, size=line.size)

        def sync(*_args):
            r.pos = line.pos
            r.size = line.size

        line.bind(pos=sync, size=sync)
        line._yt_bg_color = c
        line._yt_bg_rect = r
    except Exception:
        pass
    return line


def _text_label(text="", *, font_size="15sp", color=(0.1, 0.1, 0.1, 1), bold=False,
                height=32, halign="left", valign="middle"):
    lbl = Label(
        text=str(text or ""),
        size_hint_y=None,
        height=dp(height),
        font_size=font_size,
        color=color,
        bold=bold,
        halign=halign,
        valign=valign,
    )

    def sync_width(instance, width):
        instance.text_size = (max(1, width), None)

    lbl.bind(width=sync_width)
    return lbl


def _fmt_count(value) -> str:
    try:
        n = int(value or 0)
    except Exception:
        n = 0
    if n >= 1_000_000:
        s = f"{n / 1_000_000:.1f}".replace(".0", "").replace(".", ",")
        return f"{s} млн"
    if n >= 1_000:
        s = f"{n / 1_000:.1f}".replace(".0", "").replace(".", ",")
        return f"{s} тис."
    return str(n) if n else ""


def _extract_comments(video_url: str, limit: int = 6):
    """Fetch a small top-comment sample without touching the playback extractor."""
    try:
        import ytdlp_helpers as ydlh

        opts = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "logger": ydlh.YDLLogger(),
            "getcomments": True,
            "extractor_retries": 1,
            "socket_timeout": 9,
            "ignoreerrors": "only_download",
            "http_headers": {
                "User-Agent": getattr(ydlh, "_ANDROID_WEB_UA", "Mozilla/5.0"),
                "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.7",
                "Referer": "https://www.youtube.com",
                "Connection": "keep-alive",
            },
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

        raw_comments = info.get("comments") or []
        out = []
        for raw in raw_comments[: max(1, int(limit))]:
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
                    "thumb": str(raw.get("author_thumbnail") or ""),
                }
            )

        count = info.get("comment_count")
        try:
            count = int(count) if count is not None else None
        except Exception:
            count = None
        return out, count
    except Exception as exc:
        try:
            print("[YT-LOWER] comments failed:", exc)
        except Exception:
            pass
        return [], None


def install_youtube_lower_panel_fix() -> bool:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
        except Exception as exc:
            print("[YT-LOWER] import failed:", exc)
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False
        if bool(getattr(cls, "_pymusic_youtube_lower_panel_v1", False)):
            _INSTALLED = True
            return True

        old_play_audio = cls.play_audio
        old_sync_ui_loaded = cls._sync_ui_loaded
        old_on_kv_post = cls.on_kv_post
        old_toggle_autoskip = cls.toggle_autoskip
        old_advance = cls._advance_to_next_track

        def ensure_state(self):
            if not hasattr(self, "_yt_comments"):
                self._yt_comments = []
            if not hasattr(self, "_yt_comment_count"):
                self._yt_comment_count = None
            if not hasattr(self, "_yt_comments_url"):
                self._yt_comments_url = ""
            if not hasattr(self, "_yt_comments_loading"):
                self._yt_comments_loading = False
            if not hasattr(self, "_yt_comments_failed"):
                self._yt_comments_failed = False

        def style_lower_slot(self):
            try:
                header_row = self.ids.get("similar_header_row")
                if header_row is not None:
                    header_row.height = 0
                    header_row.opacity = 0
                    header_row.disabled = True

                scroll = self.ids.get("similar_scroll")
                lst = self.ids.get("similar_list")
                if scroll is None or lst is None:
                    return None, None

                scroll.opacity = 1
                scroll.disabled = False
                scroll.do_scroll_x = False
                scroll.do_scroll_y = False
                scroll.bar_width = 0
                try:
                    scroll.scroll_type = ["content"]
                except Exception:
                    pass

                if not bool(getattr(scroll, "_yt_height_bound", False)):
                    def sync_height(_instance, value):
                        try:
                            scroll.height = max(0, float(value or 0))
                        except Exception:
                            pass
                    lst.bind(minimum_height=sync_height)
                    scroll._yt_height_bound = True

                return scroll, lst
            except Exception:
                return None, None

        def add_autoplay_row(self, lst):
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(54),
                padding=(dp(16), 0, dp(10), 0),
                spacing=dp(8),
            )
            _bind_white_bg(row)

            labels = BoxLayout(orientation="vertical")
            labels.add_widget(_text_label("Автовідтворення", font_size="16sp", bold=True, height=28))
            labels.add_widget(
                _text_label(
                    "Автоматично вмикати наступне схоже відео",
                    font_size="11sp",
                    color=(0.45, 0.45, 0.45, 1),
                    height=22,
                )
            )
            row.add_widget(labels)

            if MDSwitch is not None:
                switch = MDSwitch(size_hint=(None, None), size=(dp(52), dp(40)))
                try:
                    switch.active = bool(getattr(self, "_auto_skip", True))
                except Exception:
                    pass

                def changed(_instance, active):
                    self._auto_skip = bool(active)
                    try:
                        btn = self.ids.get("autoskip_btn")
                        if btn:
                            btn.source = "ico/icoauto_on.png" if active else "ico/icoauto_off.png"
                    except Exception:
                        pass

                switch.bind(active=changed)
                row.add_widget(switch)
            else:
                state = "Увімк." if bool(getattr(self, "_auto_skip", True)) else "Вимк."
                btn = _text_label(state, font_size="13sp", height=40, halign="center")
                btn.size_hint_x = None
                btn.width = dp(64)
                row.add_widget(btn)

            lst.add_widget(row)

        def add_comment_row(self, lst, item):
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(76),
                padding=(dp(16), dp(6), dp(16), dp(6)),
                spacing=dp(10),
            )
            _bind_white_bg(row)

            thumb = str(item.get("thumb") or "")
            avatar = AsyncImage(
                source=thumb,
                size_hint=(None, None),
                size=(dp(34), dp(34)),
                allow_stretch=True,
                keep_ratio=True,
            )
            row.add_widget(avatar)

            body = BoxLayout(orientation="vertical", spacing=0)
            author = _text_label(
                item.get("author") or "Користувач YouTube",
                font_size="11sp",
                color=(0.45, 0.45, 0.45, 1),
                height=20,
            )
            text = _text_label(item.get("text") or "", font_size="14sp", height=38, valign="top")
            text.shorten = True
            text.shorten_from = "right"
            body.add_widget(author)
            body.add_widget(text)

            likes = int(item.get("likes") or 0)
            if likes:
                body.add_widget(
                    _text_label(
                        f"👍 {_fmt_count(likes)}",
                        font_size="10sp",
                        color=(0.5, 0.5, 0.5, 1),
                        height=16,
                    )
                )
                row.height = dp(88)
            row.add_widget(body)
            lst.add_widget(row)

        def add_recommendation(self, lst, idx, item):
            title = str(item.get("title") or item.get("url") or "Відео")
            channel = str(item.get("channel") or "")
            thumb_url = str(item.get("thumb") or item.get("thumbnail") or "")

            row = _TapRow(
                orientation="vertical",
                size_hint_y=None,
                height=dp(270),
                padding=(dp(12), dp(4), dp(12), dp(8)),
                spacing=dp(4),
            )
            _bind_white_bg(row)

            image = AsyncImage(
                source="",
                size_hint_y=None,
                height=dp(190),
                allow_stretch=True,
                keep_ratio=True,
            )
            row.add_widget(image)

            title_lbl = _text_label(title, font_size="16sp", bold=False, height=44, valign="top")
            channel_lbl = _text_label(channel, font_size="12sp", color=(0.45, 0.45, 0.45, 1), height=22)
            row.add_widget(title_lbl)
            row.add_widget(channel_lbl)

            def resize(_instance, width):
                try:
                    usable = max(dp(180), float(width) - dp(24))
                    img_h = usable * 9.0 / 16.0
                    image.height = img_h
                    row.height = img_h + dp(78)
                except Exception:
                    pass

            row.bind(width=resize)
            Clock.schedule_once(lambda _dt: resize(row, row.width), 0)

            if thumb_url:
                try:
                    self._set_playlist_thumb(image, thumb_url)
                except Exception:
                    image.source = thumb_url

            row.bind(on_release=lambda _inst, i=idx: self._play_from_related_index(i))
            lst.add_widget(row)

        def render_lower_panel(self, force=False):
            ensure_state(self)
            scroll, lst = style_lower_slot(self)
            if scroll is None or lst is None:
                return

            try:
                lst.clear_widgets()
            except Exception:
                return

            if not str(getattr(self, "_last_video_url", "") or ""):
                scroll.height = 0
                scroll.opacity = 0
                return

            scroll.opacity = 1
            add_autoplay_row(self, lst)
            lst.add_widget(_divider())

            count = getattr(self, "_yt_comment_count", None)
            count_text = _fmt_count(count) if count else ""
            comments_title = "Коментарі" + (f"  {count_text}" if count_text else "")
            comments_header = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(44),
                padding=(dp(16), 0, dp(16), 0),
            )
            _bind_white_bg(comments_header)
            comments_header.add_widget(_text_label(comments_title, font_size="16sp", bold=True, height=44))
            lst.add_widget(comments_header)

            comments = list(getattr(self, "_yt_comments", []) or [])
            if comments:
                for item in comments[:3]:
                    add_comment_row(self, lst, item)
            elif bool(getattr(self, "_yt_comments_loading", False)):
                holder = _text_label(
                    "Завантаження коментарів…",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=46,
                )
                holder.padding = (dp(16), 0)
                lst.add_widget(holder)
            elif bool(getattr(self, "_yt_comments_failed", False)):
                holder = _text_label(
                    "Коментарі недоступні для цього відео",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=46,
                )
                holder.padding = (dp(16), 0)
                lst.add_widget(holder)
            else:
                holder = _text_label(
                    "Коментарі",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=40,
                )
                holder.padding = (dp(16), 0)
                lst.add_widget(holder)

            lst.add_widget(_divider())

            related_header = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(44),
                padding=(dp(16), 0, dp(16), 0),
            )
            _bind_white_bg(related_header)
            related_header.add_widget(_text_label("Схожі відео", font_size="17sp", bold=True, height=44))
            lst.add_widget(related_header)

            related = list(getattr(self, "_related_items", []) or [])
            if related:
                for idx, item in enumerate(related[:8]):
                    add_recommendation(self, lst, idx, item)
            else:
                holder = _text_label(
                    "Завантаження рекомендацій…",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=54,
                )
                holder.padding = (dp(16), 0)
                lst.add_widget(holder)

            def finish(_dt=0):
                try:
                    scroll.height = max(0, float(lst.minimum_height or 0))
                    scroll.opacity = 1
                    scroll.disabled = False
                except Exception:
                    pass

            Clock.schedule_once(finish, 0)
            Clock.schedule_once(finish, 0.05)

        def start_comments_load(self):
            ensure_state(self)
            video_url = str(getattr(self, "_last_video_url", "") or "")
            if not video_url:
                return
            if bool(getattr(self, "_app_in_background", False)):
                return
            if getattr(self, "_yt_comments_url", "") == video_url and (
                getattr(self, "_yt_comments_loading", False)
                or getattr(self, "_yt_comments", None)
                or getattr(self, "_yt_comments_failed", False)
            ):
                return

            self._yt_comments_url = video_url
            self._yt_comments = []
            self._yt_comment_count = None
            self._yt_comments_loading = True
            self._yt_comments_failed = False
            render_lower_panel(self, force=True)

            expected_url = video_url

            def job():
                time.sleep(0.8)
                if str(getattr(self, "_last_video_url", "") or "") != expected_url:
                    return
                comments, count = _extract_comments(expected_url, limit=6)
                if str(getattr(self, "_last_video_url", "") or "") != expected_url:
                    return
                self._yt_comments = comments
                self._yt_comment_count = count
                self._yt_comments_loading = False
                self._yt_comments_failed = not bool(comments)
                Clock.schedule_once(lambda _dt: render_lower_panel(self, force=True), 0)

            threading.Thread(target=job, daemon=True).start()

        def on_kv_post_lower(self, base_widget):
            result = old_on_kv_post(self, base_widget)
            ensure_state(self)
            Clock.schedule_once(lambda _dt: render_lower_panel(self, force=True), 0)
            Clock.schedule_once(lambda _dt: start_comments_load(self), 1.0)
            return result

        def play_audio_lower(self, video_url, *args, **kwargs):
            ensure_state(self)
            previous = str(getattr(self, "_last_video_url", "") or "")
            result = old_play_audio(self, video_url, *args, **kwargs)
            current = str(getattr(self, "_last_video_url", "") or video_url or "")
            if current and current != previous:
                self._yt_comments = []
                self._yt_comment_count = None
                self._yt_comments_url = ""
                self._yt_comments_loading = False
                self._yt_comments_failed = False
            Clock.schedule_once(lambda _dt: render_lower_panel(self, force=True), 0)
            if not bool(getattr(self, "_app_in_background", False)):
                Clock.schedule_once(lambda _dt: start_comments_load(self), 1.0)
            return result

        def sync_ui_lower(self):
            result = old_sync_ui_loaded(self)
            Clock.schedule_once(lambda _dt: render_lower_panel(self, force=True), 0)
            if not bool(getattr(self, "_app_in_background", False)):
                Clock.schedule_once(lambda _dt: start_comments_load(self), 0.6)
            return result

        def toggle_autoskip_lower(self, *args):
            result = old_toggle_autoskip(self, *args)
            Clock.schedule_once(lambda _dt: render_lower_panel(self, force=True), 0)
            return result

        def advance_with_related(self) -> bool:
            try:
                playlist = getattr(self, "playlist", None)
                if playlist and len(playlist) > 1:
                    return old_advance(self)
            except Exception:
                pass

            related = list(getattr(self, "_related_items", []) or [])
            if related:
                item = related[0] or {}
                url = str(item.get("url") or "")
                if url:
                    self.play_audio(
                        url,
                        str(item.get("title") or ""),
                        str(item.get("channel") or ""),
                        str(item.get("thumb") or item.get("thumbnail") or ""),
                        clear_playlist=True,
                        hard_reset=True,
                    )
                    return True
            return old_advance(self)

        cls._render_similar_ui = render_lower_panel
        cls._start_youtube_comments_load = start_comments_load
        cls.on_kv_post = on_kv_post_lower
        cls.play_audio = play_audio_lower
        cls._sync_ui_loaded = sync_ui_lower
        cls.toggle_autoskip = toggle_autoskip_lower
        cls._advance_to_next_track = advance_with_related
        cls._pymusic_youtube_lower_panel_v1 = True

        _INSTALLED = True
        print("[YT-LOWER] YouTube-style lower panel enabled")
        return True
