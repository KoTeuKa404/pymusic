"""Stable dedicated YouTube-style lower player panel.

The legacy UI owns ``similar_scroll`` and may set its height to zero while
recommendations are refreshed. Reusing that widget made the whole lower
section disappear. V2 creates its own non-scrolling container directly inside
``player_details_scroll`` so autoplay, comments and recommendations are always
part of the main page layout.
"""
from __future__ import annotations

import threading

from kivy.clock import Clock
from kivy.graphics import Color, Rectangle
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.image import AsyncImage
from kivy.uix.label import Label
from kivy.uix.widget import Widget

try:
    from kivymd.uix.selectioncontrol import MDSwitch
except Exception:
    MDSwitch = None


_INSTALLED = False
_LOCK = threading.RLock()


class _TapCard(ButtonBehavior, BoxLayout):
    pass


def _background(widget, rgba=(1, 1, 1, 1)):
    try:
        with widget.canvas.before:
            color = Color(*rgba)
            rect = Rectangle(pos=widget.pos, size=widget.size)

        def sync(*_args):
            rect.pos = widget.pos
            rect.size = widget.size

        widget.bind(pos=sync, size=sync)
        widget._yt_v2_bg_color = color
        widget._yt_v2_bg_rect = rect
    except Exception:
        pass


def _label(text="", *, font_size="15sp", color=(0.10, 0.10, 0.10, 1), bold=False,
           height=30, valign="middle"):
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
        instance.text_size = (max(dp(1), float(width or 0)), None)

    label.bind(width=sync_width)
    return label


def _divider(height=8):
    widget = Widget(size_hint_y=None, height=dp(height))
    _background(widget, (0.94, 0.94, 0.94, 1))
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

        def hide_legacy_similar(self):
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

        def ensure_panel(self):
            existing = getattr(self, "_yt_lower_v2_panel", None)
            if existing is not None and getattr(existing, "parent", None) is not None:
                hide_legacy_similar(self)
                return existing

            outer = self.ids.get("player_details_scroll")
            if outer is None or not getattr(outer, "children", None):
                return None
            content = outer.children[0]
            if content is None:
                return None

            panel = GridLayout(
                cols=1,
                size_hint_y=None,
                spacing=0,
                padding=(0, 0, 0, dp(12)),
            )
            panel.height = 0
            panel.opacity = 0
            _background(panel, (1, 1, 1, 1))

            def sync_height(_instance, value):
                try:
                    panel.height = max(0, float(value or 0))
                except Exception:
                    pass

            panel.bind(minimum_height=sync_height)
            content.add_widget(panel, index=0)
            self._yt_lower_v2_panel = panel
            hide_legacy_similar(self)
            print("[YT-LOWER-V2] dedicated panel attached")
            return panel

        def add_autoplay(self, panel):
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(56),
                padding=(dp(16), dp(2), dp(12), dp(2)),
                spacing=dp(8),
            )
            _background(row)

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

            if MDSwitch is not None:
                switch = MDSwitch(size_hint=(None, None), size=(dp(52), dp(40)))
                try:
                    switch.active = bool(getattr(self, "_auto_skip", True))
                except Exception:
                    pass

                def changed(_instance, active):
                    self._auto_skip = bool(active)
                    try:
                        button = self.ids.get("autoskip_btn")
                        if button is not None:
                            button.source = (
                                "ico/icoauto_on.png" if active else "ico/icoauto_off.png"
                            )
                    except Exception:
                        pass

                switch.bind(active=changed)
                row.add_widget(switch)
            else:
                state = "Увімк." if bool(getattr(self, "_auto_skip", True)) else "Вимк."
                fallback = _label(state, font_size="12sp", height=40)
                fallback.size_hint_x = None
                fallback.width = dp(58)
                row.add_widget(fallback)

            panel.add_widget(row)

        def add_comment(self, panel, item):
            row = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(78),
                padding=(dp(16), dp(7), dp(16), dp(7)),
                spacing=dp(10),
            )
            _background(row)

            avatar = AsyncImage(
                source=str(item.get("thumb") or ""),
                size_hint=(None, None),
                size=(dp(34), dp(34)),
                allow_stretch=True,
                keep_ratio=True,
            )
            row.add_widget(avatar)

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
                height=42,
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
                row.height = dp(92)

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
                height=dp(260),
                padding=(dp(12), dp(5), dp(12), dp(8)),
                spacing=dp(4),
            )
            _background(row)

            image = AsyncImage(
                source="",
                size_hint_y=None,
                height=dp(180),
                allow_stretch=True,
                keep_ratio=True,
            )
            try:
                if hasattr(image, "fit_mode"):
                    image.fit_mode = "contain"
            except Exception:
                pass

            title = _label(
                item.get("title") or "Відео",
                font_size="16sp",
                height=46,
                valign="top",
            )
            channel = _label(
                item.get("channel") or "",
                font_size="12sp",
                color=(0.45, 0.45, 0.45, 1),
                height=22,
            )

            row.add_widget(image)
            row.add_widget(title)
            row.add_widget(channel)

            def resize(_instance, width):
                try:
                    usable = max(dp(180), float(width or 0) - dp(24))
                    image_h = usable * 9.0 / 16.0
                    image.height = image_h
                    row.height = image_h + dp(80)
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
            panel.add_widget(row)

        def render(self, *_args, **_kwargs):
            panel = ensure_panel(self)
            if panel is None:
                return

            hide_legacy_similar(self)
            try:
                panel.clear_widgets()
            except Exception:
                return

            video_url = str(getattr(self, "_last_video_url", "") or "")
            if not video_url:
                panel.opacity = 0
                panel.height = 0
                return

            panel.opacity = 1
            add_autoplay(self, panel)
            panel.add_widget(_divider())

            count = getattr(self, "_yt_comment_count", None)
            count_text = _fmt_count(count)
            comments_title = "Коментарі" + (f"  {count_text}" if count_text else "")
            comments_header = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(44),
                padding=(dp(16), 0, dp(16), 0),
            )
            _background(comments_header)
            comments_header.add_widget(
                _label(comments_title, font_size="16sp", bold=True, height=44)
            )
            panel.add_widget(comments_header)

            comments = list(getattr(self, "_yt_comments", []) or [])
            if comments:
                for item in comments[:3]:
                    add_comment(self, panel, item)
            elif bool(getattr(self, "_yt_comments_loading", False)):
                loading = _label(
                    "Завантаження коментарів…",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=48,
                )
                loading.padding = (dp(16), 0)
                panel.add_widget(loading)
            elif bool(getattr(self, "_yt_comments_failed", False)):
                unavailable = _label(
                    "Коментарі недоступні для цього відео",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=48,
                )
                unavailable.padding = (dp(16), 0)
                panel.add_widget(unavailable)
            else:
                placeholder = _label(
                    "Коментарі завантажуються у фоні",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=44,
                )
                placeholder.padding = (dp(16), 0)
                panel.add_widget(placeholder)

            panel.add_widget(_divider())

            related_header = BoxLayout(
                orientation="horizontal",
                size_hint_y=None,
                height=dp(48),
                padding=(dp(16), 0, dp(16), 0),
            )
            _background(related_header)
            related_header.add_widget(
                _label("Схожі відео", font_size="17sp", bold=True, height=48)
            )
            panel.add_widget(related_header)

            related = list(getattr(self, "_related_items", []) or [])
            if related:
                for index, item in enumerate(related[:8]):
                    add_recommendation(self, panel, index, item)
            else:
                loading = _label(
                    "Завантаження рекомендацій…",
                    font_size="13sp",
                    color=(0.48, 0.48, 0.48, 1),
                    height=56,
                )
                loading.padding = (dp(16), 0)
                panel.add_widget(loading)

            def finish(_dt=0):
                try:
                    panel.height = max(dp(210), float(panel.minimum_height or 0))
                    panel.opacity = 1
                    hide_legacy_similar(self)

                    outer = self.ids.get("player_details_scroll")
                    if outer is not None:
                        outer.do_scroll_y = True
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
                except Exception:
                    pass

            Clock.schedule_once(finish, 0)
            Clock.schedule_once(finish, 0.05)

        old_on_kv_post = getattr(cls, "on_kv_post", None)
        old_play_audio = cls.play_audio
        old_sync_loaded = cls._sync_ui_loaded
        old_render_similar = cls._render_similar_ui
        old_resume = cls.handle_app_resume
        old_toggle_autoskip = cls.toggle_autoskip
        old_start_comments = getattr(cls, "_start_youtube_comments_load", None)

        def on_kv_post_v2(self, base_widget):
            result = old_on_kv_post(self, base_widget) if callable(old_on_kv_post) else None
            for delay in (0.0, 0.12, 0.5):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def play_audio_v2(self, *args, **kwargs):
            result = old_play_audio(self, *args, **kwargs)
            for delay in (0.0, 0.25, 0.8, 2.0):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        def sync_loaded_v2(self, *args, **kwargs):
            result = old_sync_loaded(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
            Clock.schedule_once(lambda _dt: render(self), 0.5)
            return result

        def render_similar_v2(self, *args, **kwargs):
            result = old_render_similar(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt: render(self), 0)
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

        def start_comments_v2(self, *args, **kwargs):
            result = old_start_comments(self, *args, **kwargs) if callable(old_start_comments) else None
            for delay in (0.1, 1.2, 3.0, 7.0):
                Clock.schedule_once(lambda _dt: render(self), delay)
            return result

        cls.on_kv_post = on_kv_post_v2
        cls.play_audio = play_audio_v2
        cls._sync_ui_loaded = sync_loaded_v2
        cls._render_similar_ui = render_similar_v2
        cls.handle_app_resume = resume_v2
        cls.toggle_autoskip = toggle_autoskip_v2
        if callable(old_start_comments):
            cls._start_youtube_comments_load = start_comments_v2
        cls._render_youtube_lower_v2 = render
        cls._pymusic_youtube_lower_panel_v2 = True

        _INSTALLED = True
        print("[YT-LOWER-V2] dedicated autoplay/comments/recommendations panel enabled")
        return True
