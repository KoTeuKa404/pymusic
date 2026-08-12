"""Stable YouTube-style native timeline for the Android video overlay.

Keep Android's original progress row intact.  Some vendor Android builds do not
render our custom LayerDrawable track even though the SeekBar thumb is visible.
Using the platform SeekBar drawable with tint keeps the track, progress and time
labels reliable while preserving the existing audio-master seek callback.
"""
from __future__ import annotations

import threading

from kivy.clock import Clock

_PATCHED = False
_LOCK = threading.RLock()


def install_youtube_timeline_fix() -> bool:
    global _PATCHED
    with _LOCK:
        if _PATCHED:
            return True

        try:
            import video_player as vpmod

            cls = getattr(vpmod, "AndroidVideoPlayer", None)
            if cls is None:
                return False
            if bool(getattr(cls, "_pymusic_youtube_timeline_v3", False)):
                _PATCHED = True
                return True

            def px(activity, dp_value: float) -> int:
                try:
                    density = float(
                        activity.getResources().getDisplayMetrics().density or 1.0
                    )
                except Exception:
                    density = 1.0
                return max(1, int(round(float(dp_value) * density)))

            def make_thumb(active: bool):
                activity = vpmod.PythonActivity.mActivity
                size = px(activity, 13 if active else 7)
                thumb = vpmod.GradientDrawable()
                thumb.setShape(vpmod.GradientDrawable.OVAL)
                thumb.setColor(vpmod.Color.argb(255, 255, 0, 0))
                try:
                    thumb.setSize(size, size)
                except Exception:
                    pass
                return thumb, size

            def style_seekbar(seek_bar, active: bool = False):
                """Reliable platform track + YouTube-like red thumb."""
                try:
                    activity = vpmod.PythonActivity.mActivity

                    # Do not replace progressDrawable.  The stock Android drawable
                    # is reliable across MIUI/HyperOS/Samsung/etc.; tint only.
                    seek_bar.setProgressTintList(
                        vpmod.ColorStateList.valueOf(
                            vpmod.Color.argb(255, 255, 0, 0)
                        )
                    )
                    seek_bar.setProgressBackgroundTintList(
                        vpmod.ColorStateList.valueOf(
                            vpmod.Color.argb(145, 225, 225, 225)
                        )
                    )
                    try:
                        seek_bar.setSecondaryProgressTintList(
                            vpmod.ColorStateList.valueOf(
                                vpmod.Color.argb(120, 255, 255, 255)
                            )
                        )
                    except Exception:
                        pass

                    thumb, thumb_size = make_thumb(active)
                    seek_bar.setThumb(thumb)
                    try:
                        seek_bar.setThumbOffset(thumb_size // 2)
                        seek_bar.setSplitTrack(False)
                        # Large hit box, small visual track/thumb.
                        hit_h = px(activity, 32)
                        seek_bar.setMinHeight(hit_h)
                        seek_bar.setMaxHeight(hit_h)
                    except Exception:
                        pass
                    seek_bar.setPadding(0, 0, 0, 0)
                    seek_bar.setClickable(True)
                    seek_bar.setFocusable(False)
                    seek_bar.setVisibility(vpmod.View.VISIBLE)
                    seek_bar.setAlpha(1.0)
                    try:
                        seek_bar.requestLayout()
                        seek_bar.invalidate()
                    except Exception:
                        pass
                except Exception as exc:
                    try:
                        print("[YT-TIMELINE] seek style failed:", exc)
                    except Exception:
                        pass

            class YoutubeSeekListener(vpmod.PythonJavaClass):
                __javainterfaces__ = [
                    "android/widget/SeekBar$OnSeekBarChangeListener"
                ]
                __javacontext__ = "app"

                def __init__(self, owner):
                    super().__init__()
                    self._owner = owner

                @vpmod.java_method("(Landroid/widget/SeekBar;IZ)V")
                def onProgressChanged(self, seek_bar, progress, from_user):
                    if not from_user:
                        return
                    try:
                        self._owner._set_native_time_text(
                            current_ms=int(progress or 0)
                        )
                    except Exception:
                        pass

                @vpmod.java_method("(Landroid/widget/SeekBar;)V")
                def onStartTrackingTouch(self, seek_bar):
                    try:
                        self._owner._native_seek_dragging = True
                        style_seekbar(seek_bar, True)
                        self._owner._set_native_time_text(
                            current_ms=int(seek_bar.getProgress() or 0)
                        )
                    except Exception:
                        pass

                @vpmod.java_method("(Landroid/widget/SeekBar;)V")
                def onStopTrackingTouch(self, seek_bar):
                    try:
                        position = int(seek_bar.getProgress() or 0)
                    except Exception:
                        position = 0
                    try:
                        self._owner._native_seek_dragging = False
                        style_seekbar(seek_bar, False)
                    except Exception:
                        pass
                    try:
                        cb = self._owner._native_seek_callback
                        if callable(cb):
                            cb(position)
                    except Exception:
                        pass

            cls._OnSeekBarChangeListener = YoutubeSeekListener
            cls._style_youtube_seekbar = (
                lambda self, seek_bar: style_seekbar(
                    seek_bar,
                    bool(getattr(self, "_native_seek_dragging", False)),
                )
            )

            @vpmod.run_on_ui_thread
            def apply_timeline(owner):
                try:
                    overlay = getattr(owner, "controls_overlay", None)
                    seek_bar = getattr(owner, "_native_seek_bar", None)
                    current = getattr(owner, "_native_current_time", None)
                    total = getattr(owner, "_native_total_time", None)
                    if overlay is None or seek_bar is None:
                        return

                    activity = vpmod.PythonActivity.mActivity
                    row = None
                    try:
                        row = seek_bar.getParent()
                    except Exception:
                        row = None

                    # The base video_player creates exactly the layout we want:
                    # current time | seek bar | total time.  Keep it instead of
                    # replacing/removing it as v1/v2 did.
                    if row is None:
                        return

                    for label, alpha in ((current, 245), (total, 220)):
                        if label is None:
                            continue
                        try:
                            label.setVisibility(vpmod.View.VISIBLE)
                            label.setAlpha(1.0)
                            label.setTextColor(
                                vpmod.Color.argb(alpha, 255, 255, 255)
                            )
                            label.setTextSize(12.0)
                            label.setGravity(vpmod.Gravity.CENTER)
                            label.setShadowLayer(
                                3.0,
                                0.0,
                                1.0,
                                vpmod.Color.argb(220, 0, 0, 0),
                            )
                        except Exception:
                            pass

                    # Restore explicit child sizing.  The times remain readable
                    # while the seekbar takes every remaining pixel.
                    try:
                        if current is not None:
                            current.setLayoutParams(
                                vpmod.LinearLayoutLayoutParams(
                                    px(activity, 46), px(activity, 32)
                                )
                            )
                        seek_bar.setLayoutParams(
                            vpmod.LinearLayoutLayoutParams(
                                0, px(activity, 32), 1.0
                            )
                        )
                        if total is not None:
                            total.setLayoutParams(
                                vpmod.LinearLayoutLayoutParams(
                                    px(activity, 46), px(activity, 32)
                                )
                            )
                    except Exception:
                        pass

                    try:
                        row.setOrientation(vpmod.LinearLayout.HORIZONTAL)
                        row.setGravity(vpmod.Gravity.CENTER_VERTICAL)
                        row.setPadding(
                            px(activity, 4), 0, px(activity, 4), 0
                        )
                        row.setVisibility(vpmod.View.VISIBLE)
                        row.setAlpha(1.0)
                    except Exception:
                        pass

                    try:
                        params = row.getLayoutParams()
                        if params is not None:
                            params.width = vpmod.FrameLayoutLayoutParams.MATCH_PARENT
                            params.height = px(activity, 36)
                            params.gravity = vpmod.Gravity.BOTTOM
                            params.bottomMargin = 0
                            row.setLayoutParams(params)
                    except Exception:
                        pass

                    owner._native_seek_listener = YoutubeSeekListener(owner)
                    seek_bar.setOnSeekBarChangeListener(
                        owner._native_seek_listener
                    )
                    style_seekbar(
                        seek_bar,
                        bool(getattr(owner, "_native_seek_dragging", False)),
                    )

                    try:
                        owner._set_native_time_text(
                            current_ms=int(
                                getattr(owner, "_pymusic_timeline_current_ms", 0)
                                or 0
                            ),
                            total_ms=int(
                                getattr(owner, "_pymusic_timeline_total_ms", 0)
                                or 0
                            ),
                        )
                    except Exception:
                        pass

                    try:
                        row.bringToFront()
                        overlay.bringToFront()
                        row.requestLayout()
                        row.invalidate()
                        overlay.requestLayout()
                        overlay.invalidate()
                    except Exception:
                        pass
                except Exception as exc:
                    try:
                        print("[YT-TIMELINE] apply failed:", exc)
                    except Exception:
                        pass

            # Remember latest times without replacing the original TextViews.
            old_time_text = cls._set_native_time_text

            def time_text_v3(self, current_ms=None, total_ms=None):
                if current_ms is not None:
                    self._pymusic_timeline_current_ms = max(
                        0, int(current_ms or 0)
                    )
                if total_ms is not None:
                    self._pymusic_timeline_total_ms = max(
                        0, int(total_ms or 0)
                    )
                return old_time_text(
                    self,
                    current_ms=current_ms,
                    total_ms=total_ms,
                )

            cls._set_native_time_text = time_text_v3

            old_ensure = cls._ensure_controls_overlay
            old_visible = cls.set_native_controls_visible

            def ensure_with_timeline(self, *args, **kwargs):
                result = old_ensure(self, *args, **kwargs)
                for delay in (0.0, 0.03, 0.10, 0.25):
                    Clock.schedule_once(
                        lambda _dt, owner=self: apply_timeline(owner), delay
                    )
                return result

            def visible_with_timeline(self, visible):
                result = old_visible(self, visible)
                if visible:
                    for delay in (0.0, 0.03, 0.10):
                        Clock.schedule_once(
                            lambda _dt, owner=self: apply_timeline(owner), delay
                        )
                return result

            cls._ensure_controls_overlay = ensure_with_timeline
            cls.set_native_controls_visible = visible_with_timeline
            cls._pymusic_youtube_timeline_v3 = True

            _PATCHED = True
            print("[YT-TIMELINE] stable native timeline v3 enabled")
            return True
        except Exception as exc:
            try:
                print("[YT-TIMELINE] install failed:", exc)
            except Exception:
                pass
            return False
