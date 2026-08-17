"""Reliable YouTube-style lower player panel.

The previous implementation could install after ``AudioPlayerScreen.on_kv_post``
and then never get a lifecycle callback that mounted the panel.  It also relied
on GridLayout.minimum_height while the surrounding legacy recommendation
widgets were frequently resized by old hotfixes.

This version is deliberately simple:
* it finds the live ``audio`` screen after the Kivy app starts;
* mounts one plain BoxLayout directly into player_details_scroll content;
* computes its height explicitly from visible rows;
* refreshes from the existing comments/related data hooks;
* never depends on the legacy similar_scroll geometry.
"""
from __future__ import annotations

import threading

from kivy.app import App
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
            self._knob_color = Color(1, 1, 1, 1)
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
        widget._yt_panel_bg_color = color
        widget._yt_panel_bg_rect = rect
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


def install_youtube_lower_panel_v2() -> bool:
    global _INSTALLED

    with _LOCK:
        if _INSTALLED:
            return True

        try:
            import audio_screen
        except Exception as exc:
            print("[YT-LOWER-V2] import failed:", exc)
            return False

        cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if cls is None:
            return False
        if bool(getattr(cls, "_pymusic_youtube_lower_panel_v2", False)):
            _INSTALLED = True
            return True

        def hide_legacy(self):
            for key in ("similar_header_row", "similar_scroll"):
                try:
                    widget = self.ids.get(key)
                    if widget is None:
                        continue
                    widget.height = 0
                    widget.opacity = 0
                    widget.disabled = True
                    try:
                        widget.do_scroll_y = False
                    except Exception:
                        pass
                except Exception:
                    pass

        def get_content(self):
            try:
                outer = self.ids.get("player_details_scroll")
                if outer is None or not outer.children:
                    return None, None
                return outer, outer.children[0]
            except Exception:
                return None, None

        def mount(self):
            existing = getattr(self, "_yt_lower_v2_panel", None)
            if existing is not None and getattr(existing, "parent", None) is not None:
                hide_legacy(self)
                return existing

            outer, content = get_content(self)
            if outer is None or content is None:
                return None

            panel = BoxLayout(
                orientation="vertical",
                size_hint_y=None,
                height=dp(220),
                spacing=0,
                padding=(0, 0, 0, dp(12)),
            )
            _paint(panel, (1, 1, 1, 1))
            try:
                # index=0 is the newest/bottom child for the vertical BoxLayout
                # used by player_details_scroll, so the panel follows the player
                # metadata/playlist instead of floating outside the page.
                content.add_widget(panel, index=0)
            except Exception as exc:
                print("[YT-LOWER-V2] attach failed:", exc)
                return None

            self._yt_lower_v2_panel = panel
            hide_legacy(self)
            try:
                outer.do_scroll_y = True
                outer.disabled = False
                outer.always_overscroll = False
            except Exception:
                pass
            print("[YT-LOWER-V2] live panel attached")
            return panel

        def panel_height(panel):
            total = dp(12)
            try:
                total += sum(max(0.0, float(getattr(child, "height", 0) or 0)) for child in panel.children)
                total += max(0, len(panel.children) - 1) * float(panel.spacing or 0)
            except Exception:
                pass
            return max(dp(210), total)

        def add_autoplay(self, panel):
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
            panel.add_widget(row)

        def add_comment(self, panel, item):
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
            panel.add_widget(row)

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
                print("[YT-LOWER-V2] related click failed:", exc)

        def add_recommendation(self, panel, index, item):
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
            panel.add_widget(row)

        def render(self, *_args, **_kwargs):
            panel = mount(self)
            if panel is None:
                return
            hide_legacy(self)
            try:
                panel.clear_widgets()
            except Exception:
                return

            # If metadata is already visible, keep the lower panel visible even
            # if _last_video_url is assigned a frame later. This avoids the old
            # "blank forever" race after opening a video.
            video_url = str(getattr(self, "_last_video_url", "") or "")
            try:
                title_visible = bool(str(self.ids.get("audio_title").text or "").strip())
            except Exception:
                title_visible = False
            if not video_url and not title_visible:
                panel.height = 0
                panel.opacity = 0
                return

            panel.opacity = 1
            add_autoplay(self, panel)
            panel.add_widget(_divider())

            count = getattr(self, "_yt_comment_count", None)
            count_text = _fmt_count(count)
            panel.add_widget(
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
                    add_comment(self, panel, item)
            elif bool(getattr(self, "_yt_comments_failed", False)):
                panel.add_widget(
                    _label(
                        "Коментарі недоступні для цього відео",
                        font_size="13sp",
                        color=(0.48, 0.48, 0.48, 1),
                        height=48,
                    )
                )
            else:
                panel.add_widget(
                    _label(
                        "Завантаження коментарів…",
                        font_size="13sp",
                        color=(0.48, 0.48, 0.48, 1),
                        height=48,
                    )
                )

            panel.add_widget(_divider())
            panel.add_widget(
                _label("Схожі відео", font_size="17sp", bold=True, height=48)
            )

            related = list(getattr(self, "_related_items", []) or [])
            if related:
                for index, item in enumerate(related[:8]):
                    add_recommendation(self, panel, index, item)
            else:
                panel.add_widget(
                    _label(
                        "Завантаження рекомендацій…",
                        font_size="13sp",
                        color=(0.48, 0.48, 0.48, 1),
                        height=58,
                    )
                )

            panel.height = panel_height(panel)
            panel.opacity = 1

            outer, content = get_content(self)
            try:
                if outer is not None:
                    outer.do_scroll_y = True
                    outer.disabled = False
                    outer.always_overscroll = False
                trigger = getattr(content, "_trigger_layout", None)
                if callable(trigger):
                    trigger()
                trigger = getattr(outer, "_trigger_layout", None)
                if callable(trigger):
                    trigger()
            except Exception:
                pass

            # Recalculate after Kivy has assigned final child sizes.
            def settle(_dt=0):
                try:
                    panel.height = panel_height(panel)
                    panel.opacity = 1
                    hide_legacy(self)
                except Exception:
                    pass

            Clock.schedule_once(settle, 0)
            Clock.schedule_once(settle, 0.08)

            # Start comments after the visible shell exists. The old data layer
            # owns the actual background extraction.
            try:
                start_comments = getattr(self, "_start_youtube_comments_load", None)
                if callable(start_comments) and video_url and not bool(
                    getattr(self, "_yt_comments_loading", False)
                ) and getattr(self, "_yt_comments_url", "") != video_url:
                    Clock.schedule_once(lambda _dt: start_comments(), 0.45)
            except Exception:
                pass

        old_play_audio = cls.play_audio
        old_sync_loaded = cls._sync_ui_loaded
        old_render_similar = cls._render_similar_ui
        old_resume = cls.handle_app_resume
        old_toggle_autoskip = cls.toggle_autoskip

        def play_audio_v2(self, *args, **kwargs):
            result = old_play_audio(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.30, 0.9, 2.0):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def sync_loaded_v2(self, *args, **kwargs):
            result = old_sync_loaded(self, *args, **kwargs)
            for delay in (0.0, 0.12, 0.5):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def render_similar_v2(self, *args, **kwargs):
            result = old_render_similar(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
            Clock.schedule_once(lambda _dt: render(self), 0.12)
            return result

        def resume_v2(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.15, 0.6):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def toggle_autoskip_v2(self, *args, **kwargs):
            result = old_toggle_autoskip(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
            return result

        cls.play_audio = play_audio_v2
        cls._sync_ui_loaded = sync_loaded_v2
        cls._render_similar_ui = render_similar_v2
        cls.handle_app_resume = resume_v2
        cls.toggle_autoskip = toggle_autoskip_v2
        cls._render_youtube_lower_v2 = render
        cls._pymusic_youtube_lower_panel_v2 = True

        # Crucial: install may happen after the screen instance has already been
        # constructed, so class-method wrappers alone are not sufficient. Poll
        # the live ScreenManager briefly and mount the panel on the existing
        # instance as soon as the Kivy app root is available.
        attempts = {"count": 0}

        def mount_live(_dt):
            attempts["count"] += 1
            try:
                app = App.get_running_app()
                root = getattr(app, "root", None) if app is not None else None
                screen = None
                if root is not None:
                    getter = getattr(root, "get_screen", None)
                    if callable(getter):
                        try:
                            screen = getter("audio")
                        except Exception:
                            screen = None
                    if screen is None:
                        for child in list(getattr(root, "children", []) or []):
                            if getattr(child, "name", None) == "audio":
                                screen = child
                                break
                if screen is not None:
                    render(screen)
                    if getattr(screen, "_yt_lower_v2_panel", None) is not None:
                        print("[YT-LOWER-V2] live screen mounted")
                        return False
            except Exception as exc:
                if attempts["count"] in (1, 20, 60):
                    print("[YT-LOWER-V2] live mount retry:", exc)

            return attempts["count"] < 160

        Clock.schedule_interval(mount_live, 0.10)

        _INSTALLED = True
        print("[YT-LOWER-V2] reliable autoplay/comments/recommendations panel enabled")
        return True
