"""Hard final layout fix for the Android player.

This patch deliberately does not depend on the older layout monkey-patches:
- SurfaceView uses the whole 16:9 video block and center-crops when needed;
- video bounds come from video_block itself, not from its padded thumbnail;
- title and views use their real texture heights with zero inter-item spacing.
"""
from __future__ import annotations

import sys
import threading

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_visual_fill() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        audio_module = sys.modules.get("audio_screen")
        video_module = sys.modules.get("video_player")
        if audio_module is None or video_module is None:
            return False

        player_cls = getattr(audio_module, "AudioPlayerScreen", None)
        video_cls = getattr(video_module, "AndroidVideoPlayer", None)
        if player_cls is None or video_cls is None:
            return False
        if getattr(player_cls, "_pymusic_visual_fill_v2", False):
            _PATCHED = True
            return True

        Clock = audio_module.Clock
        Window = audio_module.Window
        dp = audio_module.dp
        media = audio_module.ma

        # -------------------- Native video: fill, do not letterbox --------------------
        def full_frame_rect(self, left, top, width, height):
            return int(left), int(top), max(1, int(width)), max(1, int(height))

        old_apply_surface_bounds = video_cls._apply_surface_bounds

        @video_module.run_on_ui_thread
        def apply_surface_bounds_fill(self):
            try:
                if self.player is not None:
                    # VIDEO_SCALING_MODE_SCALE_TO_FIT_WITH_CROPPING.
                    # This is the Android equivalent of YouTube's zoom-to-fill:
                    # no black bars, with a small edge crop only when ratios differ.
                    self.player.setVideoScalingMode(2)
            except Exception:
                pass
            try:
                return old_apply_surface_bounds(self)
            except Exception as exc:
                print("[VIDEO] fill bounds failed:", exc)
                return None

        video_cls._fit_rect_to_video = full_frame_rect
        video_cls._apply_surface_bounds = apply_surface_bounds_fill

        # -------------------- Kivy video block: exact edge-to-edge 16:9 --------------------
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

        def apply_video_block(self, _dt=0) -> None:
            try:
                block = self.ids.get("video_block")
                thumb = self.ids.get("audio_thumbnail")
                if block is None:
                    return

                block.padding = [0, 0, 0, 0]
                block.spacing = 0
                target_h = max(dp(1), float(block.width or 0) * 9.0 / 16.0)
                if abs(float(block.height or 0) - target_h) > 0.25:
                    block.height = target_h
                request_layout(block)

                if thumb is not None:
                    thumb.allow_stretch = True
                    # Fill the same rectangle while the native video is preparing.
                    thumb.keep_ratio = False
                    try:
                        thumb.size_hint = (1, 1)
                        thumb.pos_hint = {"x": 0, "y": 0}
                    except Exception:
                        pass
            except Exception as exc:
                print("[VIDEO] edge-to-edge block failed:", exc)

        def align_video_to_block(self, *args):
            """Use video_block bounds directly, bypassing old KV padding."""
            try:
                vp = getattr(self, "_video_player", None)
                if vp is None:
                    return
                if not self._is_screen_active():
                    try:
                        self.hide_video_overlay_fast()
                    except Exception:
                        pass
                    return

                apply_video_block(self)
                block = self.ids.get("video_block")
                if block is None:
                    return

                win_w, win_h = Window.size
                if win_w <= 0 or win_h <= 0:
                    return

                activity = media.PythonActivity.mActivity
                try:
                    metrics = activity.getResources().getDisplayMetrics()
                    screen_w = int(metrics.widthPixels)
                    screen_h = int(metrics.heightPixels)
                except Exception:
                    screen_w = int(getattr(vp, "screen_w_px", 0) or 1080)
                    screen_h = int(getattr(vp, "screen_h_px", 0) or 1920)

                kx = screen_w / float(win_w)
                ky = screen_h / float(win_h)
                wx, wy = block.to_window(block.x, block.y, relative=False)
                ww = float(block.width or 0)
                wh = float(block.height or 0)
                if ww <= 0 or wh <= 0:
                    return

                left = int(round(float(wx) * kx))
                top = int(round((float(win_h) - (float(wy) + wh)) * ky))
                width = int(round(ww * kx))
                height = int(round(wh * ky))

                # One physical pixel of overscan hides rounding seams without
                # creating a visible crop or covering neighbouring controls.
                left -= 1
                top -= 1
                width += 2
                height += 2

                if left < 0:
                    width += left
                    left = 0
                if top < 0:
                    height += top
                    top = 0
                width = max(1, min(width, screen_w - left))
                height = max(1, min(height, screen_h - top))

                vp.set_bounds(left, top, width, height)
            except Exception as exc:
                print("[VIDEO] direct block alignment failed:", exc)

        # -------------------- Metadata: no phantom second title line --------------------
        def apply_metadata_geometry(self, _dt=0) -> None:
            if bool(getattr(self, "_pymusic_visual_layout_running", False)):
                return
            self._pymusic_visual_layout_running = True
            try:
                title_view = self.ids.get("title_scroll")
                title = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                if title_view is None or title is None or views is None:
                    return

                width = max(dp(1), float(title_view.width or 0))
                try:
                    title.padding = [0, 0]
                except Exception:
                    pass
                title.halign = "left"
                title.valign = "top"
                if tuple(title.text_size) != (width, None):
                    title.text_size = (width, None)
                try:
                    title.texture_update()
                except Exception:
                    pass

                title_text = str(title.text or "").strip()
                title_h = float((title.texture_size or (0, 0))[1] or 0)
                if title_text and title_h > 0:
                    visible_h = min(title_h, dp(50))
                    content_h = title_h
                else:
                    visible_h = 0.0
                    content_h = 0.0

                if abs(float(title_view.height or 0) - visible_h) > 0.25:
                    title_view.height = visible_h
                if abs(float(title.height or 0) - content_h) > 0.25:
                    title.height = content_h
                title_view.do_scroll_x = False
                title_view.do_scroll_y = content_h > visible_h + dp(1)
                title_view.scroll_y = 1.0
                try:
                    title_view.always_overscroll = False
                    title_view.bar_width = 0
                    title_view.smooth_scroll_end = 0
                except Exception:
                    pass

                try:
                    views.padding = [0, 0]
                except Exception:
                    pass
                views_width = max(dp(1), float(views.width or 0))
                if tuple(views.text_size) != (views_width, None):
                    views.text_size = (views_width, None)
                try:
                    views.texture_update()
                except Exception:
                    pass
                views_text = str(views.text or "").strip()
                views_h = float((views.texture_size or (0, 0))[1] or 0)
                wanted_views_h = views_h if views_text and views_h > 0 else 0.0
                if abs(float(views.height or 0) - wanted_views_h) > 0.25:
                    views.height = wanted_views_h

                details = title_view.parent
                if details is not None:
                    details.spacing = 0
                    details.padding = [dp(16), dp(3), dp(16), dp(3)]
                    request_layout(details)

                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    child = outer.children[0] if outer.children else None
                    request_layout(child)
                    request_layout(outer)
            except Exception as exc:
                print("[TITLE] hard compact geometry failed:", exc)
            finally:
                self._pymusic_visual_layout_running = False

        def queue_metadata_geometry(self, *_args) -> None:
            try:
                old = getattr(self, "_pymusic_visual_layout_event", None)
                if old is not None:
                    old.cancel()
            except Exception:
                pass
            # Run after all older 0/0.01 second title callbacks.
            self._pymusic_visual_layout_event = Clock.schedule_once(
                lambda dt: apply_metadata_geometry(self, dt), 0.035
            )

        def bind_final_layout(self) -> None:
            try:
                title_view = self.ids.get("title_scroll")
                title = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                block = self.ids.get("video_block")

                if title_view is not None and not bool(
                    getattr(title_view, "_pymusic_visual_fill_v2", False)
                ):
                    if title is not None:
                        title.bind(text=lambda *_a: queue_metadata_geometry(self))
                    if views is not None:
                        views.bind(text=lambda *_a: queue_metadata_geometry(self))
                    title_view.bind(width=lambda *_a: queue_metadata_geometry(self))
                    title_view._pymusic_visual_fill_v2 = True

                if block is not None and not bool(
                    getattr(block, "_pymusic_visual_fill_v2", False)
                ):
                    block.bind(
                        width=lambda *_a: Clock.schedule_once(
                            lambda dt: (
                                apply_video_block(self, dt),
                                align_video_to_block(self),
                            ),
                            0,
                        )
                    )
                    block._pymusic_visual_fill_v2 = True
            except Exception as exc:
                print("[PLAYER] hard visual bind failed:", exc)

            apply_video_block(self)
            apply_metadata_geometry(self)
            Clock.schedule_once(lambda _dt: align_video_to_block(self), 0)

        old_init = player_cls.__init__
        old_sync_loaded = player_cls._sync_ui_loaded
        old_sync_loading = player_cls._sync_ui_loading
        old_pre_enter = player_cls.on_pre_enter
        old_resume = player_cls.handle_app_resume

        def init_with_visual_fill(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.22):
                Clock.schedule_once(lambda _dt: bind_final_layout(self), delay)

        def sync_loaded_with_visual_fill(self, *args, **kwargs):
            result = old_sync_loaded(self, *args, **kwargs)
            queue_metadata_geometry(self)
            return result

        def sync_loading_with_visual_fill(self, *args, **kwargs):
            result = old_sync_loading(self, *args, **kwargs)
            queue_metadata_geometry(self)
            return result

        def pre_enter_with_visual_fill(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.05, 0.16, 0.35):
                Clock.schedule_once(lambda _dt: bind_final_layout(self), delay)
            return result

        def resume_with_visual_fill(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.05, 0.16, 0.35):
                Clock.schedule_once(lambda _dt: bind_final_layout(self), delay)
            return result

        player_cls.__init__ = init_with_visual_fill
        player_cls._sync_ui_loaded = sync_loaded_with_visual_fill
        player_cls._sync_ui_loading = sync_loading_with_visual_fill
        player_cls.on_pre_enter = pre_enter_with_visual_fill
        player_cls.handle_app_resume = resume_with_visual_fill
        player_cls._align_video_to_thumb = align_video_to_block
        player_cls._pymusic_visual_fill_v2 = True

        _PATCHED = True
        print("[HOTFIX] edge-to-edge video + zero-gap metadata v2 enabled")
        return True


_patch_visual_fill()
