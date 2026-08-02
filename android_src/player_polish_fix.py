"""Final player interaction/layout polish.

- Playlist rows require two taps on the same item before playback starts.
- The title block uses exactly one line of height for a one-line title and up to
  two lines only when the text actually wraps.
- The video frame is a padding-free 16:9 viewport like the YouTube player.
"""
from __future__ import annotations

import math
import sys
import threading
import time

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_player_polish() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        if module is None:
            return False
        player_cls = getattr(module, "AudioPlayerScreen", None)
        if player_cls is None:
            return False
        if not getattr(player_cls, "_pymusic_scroll_bounds_v1", False):
            return False
        if getattr(player_cls, "_pymusic_player_polish_v1", False):
            _PATCHED = True
            return True

        Clock = module.Clock
        dp = module.dp
        old_play_index = player_cls._play_from_playlist_index
        old_sync_ui = player_cls._sync_ui_loaded
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume

        def request_layout(widget) -> None:
            if widget is None:
                return
            try:
                trigger = getattr(widget, "_trigger_layout", None)
                if callable(trigger):
                    trigger()
            except Exception:
                pass
            try:
                widget.canvas.ask_update()
            except Exception:
                pass

        def stop_scroll(scroll) -> None:
            try:
                effect = getattr(scroll, "effect_y", None)
                if effect is not None:
                    try:
                        effect.velocity = 0
                    except Exception:
                        pass
                    try:
                        effect.is_manual = False
                    except Exception:
                        pass
            except Exception:
                pass

        def apply_title_geometry(self, _dt=0) -> None:
            try:
                view = self.ids.get("title_scroll")
                label = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                if view is None or label is None or views is None:
                    return

                width = max(dp(1), float(view.width or 0))
                try:
                    label.padding = (0, 0)
                except Exception:
                    pass
                label.halign = "left"
                label.valign = "top"
                label.text_size = (width, None)
                try:
                    label.texture_update()
                except Exception:
                    pass

                text = str(label.text or "").strip()
                texture_h = float((label.texture_size or (0, 0))[1] or 0)
                font_px = float(getattr(label, "font_size", dp(20)) or dp(20))
                one_line_h = max(dp(21), font_px * 1.18)

                if not text:
                    viewport_h = 0.0
                    content_h = 0.0
                else:
                    # A small tolerance prevents font rounding from turning a
                    # true one-line title into a fake two-line empty area.
                    lines = max(1, int(math.ceil(texture_h / (one_line_h * 1.12))))
                    visible_lines = min(2, lines)
                    if lines == 1:
                        viewport_h = max(one_line_h, texture_h)
                    else:
                        viewport_h = min(texture_h, one_line_h * visible_lines)
                    content_h = max(texture_h, viewport_h)

                view.height = viewport_h
                label.height = content_h
                view.do_scroll_x = False
                view.do_scroll_y = content_h > viewport_h + dp(1)
                view.scroll_y = 1.0
                try:
                    view.always_overscroll = False
                    view.bar_width = 0
                    view.smooth_scroll_end = 0
                except Exception:
                    pass
                stop_scroll(view)

                views_text = str(views.text or "").strip()
                try:
                    views.padding = (0, 0)
                except Exception:
                    pass
                views.text_size = (max(dp(1), float(views.width or 0)), None)
                try:
                    views.texture_update()
                except Exception:
                    pass
                views_texture_h = float((views.texture_size or (0, 0))[1] or 0)
                views.height = max(dp(17), views_texture_h) if views_text else 0

                details = view.parent
                if details is not None:
                    details.spacing = 0
                    details.padding = (dp(16), dp(3), dp(16), dp(3))
                    request_layout(details)

                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    request_layout(outer.children[0] if outer.children else None)
                    request_layout(outer)
            except Exception as exc:
                print("[TITLE] final compact layout failed:", exc)

        def queue_title_geometry(self, delay=0.0) -> None:
            try:
                event = getattr(self, "_pymusic_final_title_event", None)
                if event is not None:
                    event.cancel()
            except Exception:
                pass
            self._pymusic_final_title_event = Clock.schedule_once(
                lambda dt: apply_title_geometry(self, dt), delay
            )

        def apply_video_geometry(self, _dt=0) -> None:
            try:
                block = self.ids.get("video_block")
                thumb = self.ids.get("audio_thumbnail")
                if block is None:
                    return
                block.padding = (0, 0, 0, 0)
                target_h = max(dp(1), float(block.width or 0) * 9.0 / 16.0)
                if abs(float(block.height or 0) - target_h) > 0.5:
                    block.height = target_h
                request_layout(block)
                if thumb is not None:
                    thumb.allow_stretch = True
                    thumb.keep_ratio = True
                try:
                    self._align_video_to_thumb()
                except Exception:
                    pass
            except Exception as exc:
                print("[VIDEO] 16:9 layout failed:", exc)

        def bind_layout(self) -> None:
            try:
                view = self.ids.get("title_scroll")
                label = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                block = self.ids.get("video_block")
                if view is not None and not getattr(view, "_pymusic_final_title_v1", False):
                    if label is not None:
                        label.bind(text=lambda *_a: queue_title_geometry(self, 0.01))
                        label.bind(texture_size=lambda *_a: queue_title_geometry(self, 0.01))
                    if views is not None:
                        views.bind(text=lambda *_a: queue_title_geometry(self, 0.01))
                        views.bind(texture_size=lambda *_a: queue_title_geometry(self, 0.01))
                    view.bind(width=lambda *_a: queue_title_geometry(self, 0.01))
                    view._pymusic_final_title_v1 = True
                if block is not None and not getattr(block, "_pymusic_16_9_v1", False):
                    block.bind(width=lambda *_a: Clock.schedule_once(
                        lambda dt: apply_video_geometry(self, dt), 0
                    ))
                    block._pymusic_16_9_v1 = True
            except Exception as exc:
                print("[PLAYER] final layout bind failed:", exc)

            apply_title_geometry(self)
            apply_video_geometry(self)

        def require_double_tap(self, idx: int):
            now = time.monotonic()
            idx = int(idx)
            previous_idx = int(getattr(self, "_pymusic_playlist_tap_idx", -1))
            previous_ts = float(getattr(self, "_pymusic_playlist_tap_ts", 0.0) or 0.0)

            if idx == previous_idx and 0.06 <= now - previous_ts <= 0.55:
                self._pymusic_playlist_tap_idx = -1
                self._pymusic_playlist_tap_ts = 0.0
                print(f"[PLAYLIST] double tap play index={idx}")
                return old_play_index(self, idx)

            self._pymusic_playlist_tap_idx = idx
            self._pymusic_playlist_tap_ts = now
            print(f"[PLAYLIST] first tap ignored index={idx}")
            return None

        def sync_ui_with_final_layout(self, *args, **kwargs):
            result = old_sync_ui(self, *args, **kwargs)
            queue_title_geometry(self, 0.01)
            return result

        def pre_enter_with_final_layout(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            bind_layout(self)
            for delay in (0.03, 0.12, 0.30):
                Clock.schedule_once(lambda _dt: bind_layout(self), delay)
            return result

        def resume_with_final_layout(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18, 0.40):
                Clock.schedule_once(lambda _dt: bind_layout(self), delay)
            return result

        player_cls._play_from_playlist_index = require_double_tap
        player_cls._sync_ui_loaded = sync_ui_with_final_layout
        player_cls.on_pre_enter = pre_enter_with_final_layout
        player_cls.handle_app_resume = resume_with_final_layout
        player_cls._pymusic_player_polish_v1 = True
        _PATCHED = True
        print("[HOTFIX] double-tap playlist + compact title + 16:9 video v1 enabled")
        return True


_patch_player_polish()
