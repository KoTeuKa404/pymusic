"""Direct final player fixes, independent from the older patch chain.

This module intentionally applies without waiting for any previous hotfix flag.
It fixes three device-visible issues:
- no black frame around the thumbnail/native SurfaceView;
- no phantom gap between title and view count;
- initial A/V sync uses one Android UI-thread dual seek, matching the manual
  seek operation that is known to synchronize perfectly on the target phone.
"""
from __future__ import annotations

import sys
import threading
import time
import weakref

_PATCHED = False
_PATCH_LOCK = threading.RLock()


def _patch_final_player() -> bool:
    global _PATCHED

    with _PATCH_LOCK:
        if _PATCHED:
            return True

        audio_module = sys.modules.get("audio_screen")
        video_module = sys.modules.get("video_player")
        if audio_module is None or video_module is None:
            return False

        screen_cls = getattr(audio_module, "AudioPlayerScreen", None)
        video_cls = getattr(video_module, "AndroidVideoPlayer", None)
        if screen_cls is None or video_cls is None:
            return False
        if getattr(screen_cls, "_pymusic_final_player_v1", False):
            _PATCHED = True
            return True

        Clock = audio_module.Clock
        Window = audio_module.Window
        dp = audio_module.dp
        media = audio_module.ma

        # ------------------------------------------------------------------
        # Native video: use the entire Kivy video block, without letterbox
        # geometry added by AndroidVideoPlayer._fit_rect_to_video().
        # ------------------------------------------------------------------
        def fill_entire_frame(self, left, top, width, height):
            return int(left), int(top), max(1, int(width)), max(1, int(height))

        old_apply_bounds = video_cls._apply_surface_bounds

        @video_module.run_on_ui_thread
        def apply_bounds_without_frame(self):
            try:
                if self.player is not None:
                    # SCALE_TO_FIT_WITH_CROPPING: preserve aspect ratio and fill.
                    self.player.setVideoScalingMode(2)
            except Exception:
                pass
            return old_apply_bounds(self)

        video_cls._fit_rect_to_video = fill_entire_frame
        video_cls._apply_surface_bounds = apply_bounds_without_frame

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

        def configure_video_block(self) -> None:
            block = self.ids.get("video_block")
            thumb = self.ids.get("audio_thumbnail")
            if block is None:
                return
            try:
                block.padding = (0, 0, 0, 0)
                block.spacing = 0
            except Exception:
                pass
            wanted_h = max(dp(1), float(block.width or 0) * 9.0 / 16.0)
            if abs(float(block.height or 0) - wanted_h) > 0.25:
                block.height = wanted_h
            if thumb is not None:
                try:
                    thumb.size_hint = (1, 1)
                    thumb.pos_hint = {"x": 0, "y": 0}
                    thumb.allow_stretch = True
                    thumb.keep_ratio = True
                except Exception:
                    pass
            request_layout(block)

        def align_video_to_block(self, *args):
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

                configure_video_block(self)
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

                kx = float(screen_w) / float(win_w)
                ky = float(screen_h) / float(win_h)
                wx, wy = block.to_window(block.x, block.y, relative=False)
                ww = float(block.width or 0)
                wh = float(block.height or 0)
                if ww <= 0 or wh <= 0:
                    return

                left = int(round(float(wx) * kx))
                top = int(round((float(win_h) - (float(wy) + wh)) * ky))
                width = int(round(ww * kx))
                height = int(round(wh * ky))

                # Two physical pixels hide both Kivy rounding and SurfaceView
                # compositor seams. Clamp so neighbouring UI is not covered.
                left -= 2
                top -= 2
                width += 4
                height += 4
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
                print("[FINAL] video block alignment failed:", exc)

        # ------------------------------------------------------------------
        # Metadata: real texture heights, no fixed 52dp title viewport and no
        # spacing between title and views.
        # ------------------------------------------------------------------
        def apply_metadata_layout(self, _dt=0) -> None:
            if bool(getattr(self, "_pymusic_final_layout_busy", False)):
                return
            self._pymusic_final_layout_busy = True
            try:
                title_view = self.ids.get("title_scroll")
                title = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                if title_view is None or title is None or views is None:
                    return

                width = max(dp(1), float(title_view.width or 0))
                try:
                    title.padding = (0, 0)
                except Exception:
                    pass
                title.halign = "left"
                title.valign = "top"
                title.text_size = (width, None)
                try:
                    title.texture_update()
                except Exception:
                    pass

                title_text = str(title.text or "").strip()
                title_h = float((title.texture_size or (0, 0))[1] or 0)
                title_h = title_h if title_text else 0.0
                visible_title_h = min(title_h, dp(50)) if title_h > 0 else 0.0
                title_view.height = visible_title_h
                title.height = title_h
                title_view.do_scroll_x = False
                title_view.do_scroll_y = title_h > visible_title_h + dp(1)
                title_view.scroll_y = 1.0
                try:
                    title_view.always_overscroll = False
                    title_view.bar_width = 0
                    title_view.smooth_scroll_end = 0
                except Exception:
                    pass

                try:
                    views.padding = (0, 0)
                except Exception:
                    pass
                views.text_size = (max(dp(1), float(views.width or 0)), None)
                try:
                    views.texture_update()
                except Exception:
                    pass
                views_text = str(views.text or "").strip()
                views_h = float((views.texture_size or (0, 0))[1] or 0)
                views.height = views_h if views_text else 0.0

                details = title_view.parent
                if details is not None:
                    details.spacing = 0
                    details.padding = (dp(16), dp(4), dp(16), dp(2))
                    request_layout(details)

                outer = self.ids.get("player_details_scroll")
                if outer is not None:
                    request_layout(outer.children[0] if outer.children else None)
                    request_layout(outer)
            except Exception as exc:
                print("[FINAL] metadata layout failed:", exc)
            finally:
                self._pymusic_final_layout_busy = False

        def queue_metadata_layout(self, delay=0.0) -> None:
            try:
                event = getattr(self, "_pymusic_final_layout_event", None)
                if event is not None:
                    event.cancel()
            except Exception:
                pass
            self._pymusic_final_layout_event = Clock.schedule_once(
                lambda dt: apply_metadata_layout(self, dt), delay
            )

        def bind_layout(self) -> None:
            try:
                title_view = self.ids.get("title_scroll")
                title = self.ids.get("audio_title")
                views = self.ids.get("audio_views")
                block = self.ids.get("video_block")
                if title_view is not None and not getattr(
                    title_view, "_pymusic_final_bound_v1", False
                ):
                    if title is not None:
                        title.bind(text=lambda *_a: queue_metadata_layout(self, 0.025))
                        title.bind(texture_size=lambda *_a: queue_metadata_layout(self, 0.025))
                    if views is not None:
                        views.bind(text=lambda *_a: queue_metadata_layout(self, 0.025))
                        views.bind(texture_size=lambda *_a: queue_metadata_layout(self, 0.025))
                    title_view.bind(width=lambda *_a: queue_metadata_layout(self, 0.025))
                    title_view._pymusic_final_bound_v1 = True
                if block is not None and not getattr(
                    block, "_pymusic_final_bound_v1", False
                ):
                    block.bind(
                        width=lambda *_a: Clock.schedule_once(
                            lambda _dt: (
                                configure_video_block(self),
                                align_video_to_block(self),
                            ),
                            0,
                        )
                    )
                    block._pymusic_final_bound_v1 = True
            except Exception as exc:
                print("[FINAL] layout binding failed:", exc)

            configure_video_block(self)
            apply_metadata_layout(self)
            Clock.schedule_once(lambda _dt: align_video_to_block(self), 0)

        # ------------------------------------------------------------------
        # Startup sync: perform the exact two-player seek in ONE Android UI
        # turn. This clears the video decoder buffer like the working manual
        # slider/buttons do.
        # ------------------------------------------------------------------
        @video_module.run_on_ui_thread
        def native_dual_seek(self, token: int, load_gen: int, video_url: str, stage: str):
            try:
                if int(token) != int(getattr(self, "_pymusic_final_sync_token", -1)):
                    return
                if int(load_gen) != int(getattr(self, "_load_gen", -1)):
                    return
                if str(video_url or "") != str(getattr(self, "_last_video_url", "") or ""):
                    return
                if (
                    not getattr(self, "_playback_desired", False)
                    or getattr(self, "_user_paused", False)
                    or getattr(self, "_app_in_background", False)
                    or getattr(self, "_is_scrubbing", False)
                ):
                    return

                audio_player = getattr(media, "android_player", None)
                vp = getattr(self, "_video_player", None)
                video_player = getattr(vp, "player", None) if vp is not None else None
                if (
                    audio_player is None
                    or vp is None
                    or video_player is None
                    or not getattr(vp, "_prepared", False)
                ):
                    return

                target = max(0, int(audio_player.getCurrentPosition() or 0))
                try:
                    params = video_player.getPlaybackParams()
                    params.setSpeed(1.0)
                    video_player.setPlaybackParams(params)
                except Exception:
                    pass

                try:
                    audio_player.seekTo(target, 3)
                except Exception:
                    audio_player.seekTo(target)
                try:
                    video_player.seekTo(target, 3)
                except Exception:
                    video_player.seekTo(target)

                now = time.monotonic()
                self._resume_pos_ms = target
                self._pymusic_sync_player_id = id(video_player)
                self._pymusic_sync_last_seek = now
                self._pymusic_sync_settle_until = now + 0.22
                self._pymusic_sync_outside_since = 0.0
                self._pymusic_sync_bias_ms = 0.0
                print(
                    "[FINAL] native dual seek "
                    f"stage={stage} target={target} gen={load_gen}"
                )
            except Exception as exc:
                print("[FINAL] native dual seek failed:", exc)

        def start_sync_worker(self, video_url: str) -> None:
            self._pymusic_final_sync_token = int(
                getattr(self, "_pymusic_final_sync_token", 0)
            ) + 1
            token = int(self._pymusic_final_sync_token)
            load_gen = int(getattr(self, "_load_gen", 0))
            owner_ref = weakref.ref(self)

            def worker() -> None:
                deadline = time.monotonic() + 12.0
                while time.monotonic() < deadline:
                    owner = owner_ref()
                    if owner is None:
                        return
                    if (
                        token != int(getattr(owner, "_pymusic_final_sync_token", -1))
                        or load_gen != int(getattr(owner, "_load_gen", -1))
                        or video_url != str(getattr(owner, "_last_video_url", "") or "")
                    ):
                        return
                    vp = getattr(owner, "_video_player", None)
                    if (
                        vp is not None
                        and getattr(vp, "player", None) is not None
                        and getattr(vp, "_prepared", False)
                    ):
                        break
                    time.sleep(0.025)
                else:
                    print("[FINAL] startup sync timed out")
                    return

                # First flush after preparation, then two warm-decoder flushes.
                schedule = ((0.08, "prepared"), (0.48, "warm"), (1.05, "final"))
                previous = 0.0
                for delay, stage in schedule:
                    time.sleep(max(0.0, delay - previous))
                    previous = delay
                    owner = owner_ref()
                    if owner is None:
                        return
                    native_dual_seek(owner, token, load_gen, video_url, stage)

            threading.Thread(
                target=worker,
                name="pymusic-final-startup-sync",
                daemon=True,
            ).start()

        old_play_video = screen_cls._play_video_if_screen_active
        old_init = screen_cls.__init__
        old_sync_loaded = screen_cls._sync_ui_loaded
        old_sync_loading = screen_cls._sync_ui_loading
        old_pre_enter = screen_cls.on_pre_enter
        old_resume = screen_cls.handle_app_resume

        def play_video_final(self, vurl: str, vheaders: dict):
            result = old_play_video(self, vurl, vheaders)
            start_sync_worker(self, str(getattr(self, "_last_video_url", "") or ""))
            return result

        def init_final(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            for delay in (0.0, 0.08, 0.25):
                Clock.schedule_once(lambda _dt: bind_layout(self), delay)

        def sync_loaded_final(self, *args, **kwargs):
            result = old_sync_loaded(self, *args, **kwargs)
            queue_metadata_layout(self, 0.025)
            return result

        def sync_loading_final(self, *args, **kwargs):
            result = old_sync_loading(self, *args, **kwargs)
            queue_metadata_layout(self, 0.025)
            return result

        def pre_enter_final(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18, 0.40):
                Clock.schedule_once(lambda _dt: bind_layout(self), delay)
            return result

        def resume_final(self, *args, **kwargs):
            result = old_resume(self, *args, **kwargs)
            for delay in (0.0, 0.06, 0.18, 0.40):
                Clock.schedule_once(lambda _dt: bind_layout(self), delay)
            return result

        screen_cls._play_video_if_screen_active = play_video_final
        screen_cls.__init__ = init_final
        screen_cls._sync_ui_loaded = sync_loaded_final
        screen_cls._sync_ui_loading = sync_loading_final
        screen_cls.on_pre_enter = pre_enter_final
        screen_cls.handle_app_resume = resume_final
        screen_cls._align_video_to_thumb = align_video_to_block
        screen_cls._pymusic_final_player_v1 = True

        _PATCHED = True
        print("[HOTFIX] direct final player fix v1 enabled")
        return True


_patch_final_player()
