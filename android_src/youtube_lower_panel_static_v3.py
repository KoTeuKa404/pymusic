"""Stable YouTube-style lower panel rendered in the KV-owned similar slot.

The player already has ``similar_scroll``/``similar_list`` in youtube_gui.kv.
Using that permanent widget tree is more reliable than attaching a new runtime
panel to ScrollView.children.  This module keeps the outer player ScrollView as
the only vertical scroller and expands the static slot to the exact content
height.
"""
from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, Rectangle, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import AsyncImage
from kivy.uix.label import Label
from kivy.uix.widget import Widget

_INSTALLED = False
_LOCK = threading.RLock()


class _TapCard(ButtonBehavior, BoxLayout):
    pass


class _ToggleSwitch(ButtonBehavior, Widget):
    def __init__(self, active=True, on_change=None, **kwargs):
        kwargs.setdefault("size_hint", (None, None))
        kwargs.setdefault("size", (dp(44), dp(28)))
        super().__init__(**kwargs)
        self.active = bool(active)
        self._on_change = on_change
        with self.canvas:
            self._track_color = Color(0.55, 0.55, 0.55, 1)
            self._track = RoundedRectangle(radius=[dp(14)])
            Color(1, 1, 1, 1)
            self._knob = Ellipse()
        self.bind(pos=self._sync, size=self._sync)
        self._sync()

    def _sync(self, *_args):
        try:
            self._track_color.rgba = (
                (0.12, 0.42, 0.95, 1)
                if self.active
                else (0.65, 0.65, 0.65, 1)
            )
            self._track.pos = self.pos
            self._track.size = self.size
            diameter = max(dp(20), self.height - dp(6))
            self._knob.size = (diameter, diameter)
            self._knob.pos = (
                self.right - diameter - dp(3)
                if self.active
                else self.x + dp(3),
                self.center_y - diameter / 2.0,
            )
        except Exception:
            pass

    def on_release(self):
        self.active = not bool(self.active)
        self._sync()
        try:
            if callable(self._on_change):
                self._on_change(bool(self.active))
        except Exception:
            pass


def _paint(widget, rgba=(1, 1, 1, 1), radius=0):
    try:
        with widget.canvas.before:
            color = Color(*rgba)
            if radius:
                rect = RoundedRectangle(
                    pos=widget.pos,
                    size=widget.size,
                    radius=[dp(radius)] * 4,
                )
            else:
                rect = Rectangle(pos=widget.pos, size=widget.size)

        def sync(*_args):
            rect.pos = widget.pos
            rect.size = widget.size

        widget.bind(pos=sync, size=sync)
        widget._yt_static_bg_color = color
        widget._yt_static_bg_rect = rect
    except Exception:
        pass


def _label(
    text="",
    *,
    font_size="15sp",
    color=(0.10, 0.10, 0.10, 1),
    bold=False,
    height=30,
    valign="middle",
):
    label = Label(
        text=str(text or ""),
        size_hint_y=None,
        height=dp(height),
        font_size=font_size,
        color=color,
        bold=bold,
        halign="left",
        valign=valign,
    )

    def sync_width(instance, width):
        try:
            instance.text_size = (max(dp(1), float(width or 0)), None)
        except Exception:
            pass

    label.bind(width=sync_width)
    return label


def _divider(height=8):
    widget = Widget(size_hint_y=None, height=dp(height))
    _paint(widget, (0.94, 0.94, 0.94, 1))
    return widget


def _fmt_count(value) -> str:
    try:
        n = int(value or 0)
    except Exception:
        n = 0
    if n >= 1_000_000:
        text = f"{n / 1_000_000:.1f}".replace(".0", "").replace(".", ",")
        return f"{text} млн"
    if n >= 1_000:
        text = f"{n / 1_000:.1f}".replace(".0", "").replace(".", ",")
        return f"{text} тис."
    return str(n) if n else ""


def install_youtube_lower_panel_static_v3() -> bool:
    global _INSTALLED

    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
        except Exception as exc:
            print("[YT-LOWER-V3] import failed:", exc)
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False
        if bool(getattr(cls, "_pymusic_youtube_lower_static_v3", False)):
            _INSTALLED = True
            return True

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
            except Exception:
                pass

            try:
                scroll.size_hint_y = None
                scroll.opacity = 1
                scroll.disabled = False
                scroll.do_scroll_x = False
                scroll.do_scroll_y = False
                scroll.bar_width = 0
                scroll.scroll_type = ["content"]
                try:
                    scroll.always_overscroll = False
                except Exception:
                    pass
            except Exception:
                pass

            try:
                listing.size_hint_y = None
                listing.spacing = dp(2)
                listing.padding = (0, 0, 0, dp(12))
            except Exception:
                pass

            # Protect the slot from older visibility code that still tries to
            # collapse similar_scroll while recommendations are loading.
            if not bool(getattr(scroll, "_pymusic_lower_v3_guard", False)):
                def guard_height(instance, value):
                    if not bool(getattr(self, "_pymusic_lower_v3_active", False)):
                        return
                    try:
                        wanted = max(
                            dp(210),
                            float(getattr(listing, "minimum_height", 0) or 0),
                            float(getattr(listing, "height", 0) or 0),
                        )
                        if float(value or 0) + 0.5 < wanted:
                            Clock.schedule_once(
                                lambda _dt: setattr(instance, "height", wanted),
                                0,
                            )
                    except Exception:
                        pass

                scroll.bind(height=guard_height)
                scroll._pymusic_lower_v3_guard = True

            return scroll, listing

        def content_height(listing) -> float:
            try:
                min_h = float(getattr(listing, "minimum_height", 0) or 0)
            except Exception:
                min_h = 0.0
            try:
                child_h = sum(
                    max(0.0, float(getattr(child, "height", 0) or 0))
                    for child in list(getattr(listing, "children", []) or [])
                )
                child_h += max(0, len(listing.children) - 1) * float(
                    getattr(listing, "spacing", 0) or 0
                )
            except Exception:
                child_h = 0.0
            return max(dp(210), min_h, child_h + dp(12))

        def apply_height(self, scroll, listing):
            try:
                wanted = content_height(listing)
                listing.height = wanted
                scroll.height = wanted
                scroll.opacity = 1
                scroll.disabled = False
                self._pymusic_lower_v3_active = True

                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    outer.do_scroll_y = True
                    outer.disabled = False
                    try:
                        outer.always_overscroll = False
                    except Exception:
                        pass
                    content = outer.children[0] if outer.children else None
                    trigger = getattr(content, "_trigger_layout", None)
                    if callable(trigger):
                        trigger()
                    trigger = getattr(outer, "_trigger_layout", None)
                    if callable(trigger):
                        trigger()
            except Exception as exc:
                print("[YT-LOWER-V3] height sync failed:", exc)

        def add_autoplay(self, listing):
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(58),
                padding=(dp(16), dp(3), dp(14), dp(3)),
                spacing=dp(10),
            )
            _paint(row)

            text_box = BoxLayout(orientation="vertical")
            text_box.add_widget(
                _label("Автовідтворення", font_size="16sp", bold=True, height=30)
            )
            text_box.add_widget(
                _label(
                    "Наступне схоже відео відтвориться автоматично",
                    font_size="11sp",
                    color=(0.45, 0.45, 0.45, 1),
                    height=22,
                )
            )
            row.add_widget(text_box)

            def changed(active):
                current = bool(getattr(self, "_auto_skip", True))
                if current == bool(active):
                    return
                try:
                    self.toggle_autoskip()
                except Exception:
                    self._auto_skip = bool(active)

            row.add_widget(
                _ToggleSwitch(
                    active=bool(getattr(self, "_auto_skip", True)),
                    on_change=changed,
                )
            )
            listing.add_widget(row)

        def add_comment(self, listing, item):
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(82),
                padding=(dp(16), dp(7), dp(16), dp(7)),
                spacing=dp(10),
            )
            _paint(row)
            row.add_widget(
                AsyncImage(
                    source=str(item.get("thumb") or ""),
                    size_hint=(None, None),
                    size=(dp(34), dp(34)),
                    allow_stretch=True,
                    keep_ratio=True,
                )
            )

            body = BoxLayout(orientation="vertical", spacing=0)
            body.add_widget(
                _label(
                    item.get("author") or "Користувач YouTube",
                    font_size="11sp",
                    color=(0.45, 0.45, 0.45, 1),
                    height=20,
                )
            )
            comment = _label(
                item.get("text") or "",
                font_size="14sp",
                height=44,
                valign="top",
            )
            comment.shorten = True
            comment.shorten_from = "right"
            body.add_widget(comment)
            likes = int(item.get("likes") or 0)
            if likes:
                body.add_widget(
                    _label(
                        f"👍 {_fmt_count(likes)}",
                        font_size="10sp",
                        color=(0.5, 0.5, 0.5, 1),
                        height=16,
                    )
                )
                row.height = dp(96)
            row.add_widget(body)
            listing.add_widget(row)

        def play_related(self, index, item):
            try:
                fn = getattr(self, "_play_from_related_index", None)
                if callable(fn):
                    fn(int(index))
                    return
            except Exception:
                pass
            try:
                url = str(item.get("url") or "")
                if not url:
                    return
                self.play_audio(
                    url,
                    str(item.get("title") or ""),
                    str(item.get("channel") or ""),
                    str(item.get("thumb") or item.get("thumbnail") or ""),
                    clear_playlist=True,
                    hard_reset=True,
                )
            except Exception as exc:
                print("[YT-LOWER-V3] related click failed:", exc)

        def add_recommendation(self, listing, index, item):
            row = _TapCard(
                orientation="vertical",
                size_hint_y=None,
                height=dp(270),
                padding=(dp(12), dp(5), dp(12), dp(8)),
                spacing=dp(4),
            )
            _paint(row)

            image = AsyncImage(
                source="",
                size_hint_y=None,
                height=dp(188),
                allow_stretch=True,
                keep_ratio=True,
            )
            try:
                if hasattr(image, "fit_mode"):
                    image.fit_mode = "contain"
            except Exception:
                pass
            row.add_widget(image)
            row.add_widget(
                _label(
                    item.get("title") or "Відео",
                    font_size="16sp",
                    height=48,
                    valign="top",
                )
            )
            row.add_widget(
                _label(
                    item.get("channel") or "",
                    font_size="12sp",
                    color=(0.45, 0.45, 0.45, 1),
                    height=22,
                )
            )

            def resize(_instance, width):
                try:
                    usable = max(dp(180), float(width or 0) - dp(24))
                    image.height = usable * 9.0 / 16.0
                    row.height = image.height + dp(78)
                except Exception:
                    pass

            row.bind(width=resize)
            Clock.schedule_once(lambda _dt: resize(row, row.width), 0)

            thumb = str(item.get("thumb") or item.get("thumbnail") or "")
            if thumb:
                try:
                    setter = getattr(self, "_set_playlist_thumb", None)
                    if callable(setter):
                        setter(image, thumb)
                    else:
                        image.source = thumb
                except Exception:
                    image.source = thumb

            row.bind(
                on_release=lambda _instance, i=index, current=item: play_related(
                    self, i, current
                )
            )
            listing.add_widget(row)

        def render(self, *_args, **_kwargs):
            scroll, listing = get_slot(self)
            if scroll is None or listing is None:
                print("[YT-LOWER-V3] static KV slot missing")
                return

            try:
                title_widget = self.ids.get("audio_title")
                title_visible = bool(
                    str(getattr(title_widget, "text", "") or "").strip()
                )
            except Exception:
                title_visible = False
            video_url = str(getattr(self, "_last_video_url", "") or "")
            if not video_url and not title_visible:
                self._pymusic_lower_v3_active = False
                scroll.height = 0
                scroll.opacity = 0
                return

            self._pymusic_lower_v3_active = True
            try:
                listing.clear_widgets()
            except Exception:
                return

            add_autoplay(self, listing)
            listing.add_widget(_divider())

            count_text = _fmt_count(getattr(self, "_yt_comment_count", None))
            listing.add_widget(
                _label(
                    "Коментарі" + (f"  {count_text}" if count_text else ""),
                    font_size="16sp",
                    bold=True,
                    height=46,
                )
            )

            comments = list(getattr(self, "_yt_comments", []) or [])
            if comments:
                for item in comments[:3]:
                    add_comment(self, listing, item)
            elif bool(getattr(self, "_yt_comments_failed", False)):
                listing.add_widget(
                    _label(
                        "Коментарі недоступні для цього відео",
                        font_size="13sp",
                        color=(0.48, 0.48, 0.48, 1),
                        height=48,
                    )
                )
            else:
                listing.add_widget(
                    _label(
                        "Завантаження коментарів…",
                        font_size="13sp",
                        color=(0.48, 0.48, 0.48, 1),
                        height=48,
                    )
                )

            listing.add_widget(_divider())
            listing.add_widget(
                _label("Схожі відео", font_size="17sp", bold=True, height=48)
            )

            related = list(getattr(self, "_related_items", []) or [])
            if related:
                for index, item in enumerate(related[:8]):
                    add_recommendation(self, listing, index, item)
            else:
                listing.add_widget(
                    _label(
                        "Завантаження рекомендацій…",
                        font_size="13sp",
                        color=(0.48, 0.48, 0.48, 1),
                        height=58,
                    )
                )

            for delay in (0.0, 0.04, 0.15, 0.45):
                Clock.schedule_once(
                    lambda _dt, s=scroll, l=listing: apply_height(self, s, l),
                    delay,
                )

            try:
                start_comments = getattr(self, "_start_youtube_comments_load", None)
                if callable(start_comments) and video_url and not bool(
                    getattr(self, "_yt_comments_loading", False)
                ) and getattr(self, "_yt_comments_url", "") != video_url:
                    Clock.schedule_once(lambda _dt: start_comments(), 0.6)
            except Exception:
                pass

        old_play_audio = cls.play_audio
        old_sync_loaded = cls._sync_ui_loaded
        old_render_similar = cls._render_similar_ui
        old_resume = cls.handle_app_resume
        old_toggle_autoskip = cls.toggle_autoskip
        old_on_kv_post = cls.on_kv_post

        def on_kv_post_v3(self, base_widget):
            result = old_on_kv_post(self, base_widget)
            for delay in (0.0, 0.08, 0.3):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def play_audio_v3(self, *args, **kwargs):
            result = old_play_audio(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.3, 0.9, 2.0):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def sync_loaded_v3(self, *args, **kwargs):
            result = old_sync_loaded(self, *args, **kwargs)
            for delay in (0.0, 0.12, 0.5):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def render_similar_v3(self, *args, **kwargs):
            result = old_render_similar(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
            Clock.schedule_once(lambda _dt: render(self), 0.10)
            return result

        def resume_v3(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.15, 0.6):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def toggle_autoskip_v3(self, *args, **kwargs):
            result = old_toggle_autoskip(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
            return result

        cls.on_kv_post = on_kv_post_v3
        cls.play_audio = play_audio_v3
        cls._sync_ui_loaded = sync_loaded_v3
        cls._render_similar_ui = render_similar_v3
        cls.handle_app_resume = resume_v3
        cls.toggle_autoskip = toggle_autoskip_v3
        cls._render_youtube_lower_static_v3 = render
        cls._pymusic_youtube_lower_static_v3 = True

        _INSTALLED = True
        print("[YT-LOWER-V3] static KV autoplay/comments/recommendations enabled")
        return True
