"""YouTube-style native video timeline for the Android SurfaceView overlay.

This patch owns only the native timeline UI. Audio remains the master
MediaPlayer and the existing seek callback still performs the real seek.
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
            if bool(getattr(cls, "_pymusic_youtube_timeline_v2", False)):
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
                size = px(activity, 12 if active else 6)
                thumb = vpmod.GradientDrawable()
                thumb.setShape(vpmod.GradientDrawable.OVAL)
                thumb.setColor(vpmod.Color.argb(255, 255, 0, 0))
                try:
                    thumb.setSize(size, size)
                except Exception:
                    pass
                return thumb, size

            def style_seekbar(seek_bar, active: bool = False):
                """YouTube-like 2dp track + small idle thumb / large drag thumb."""
                try:
                    activity = vpmod.PythonActivity.mActivity
                    hit_h = px(activity, 28)
                    track_h = px(activity, 2)
                    radius = max(1, track_h // 2)
                    inset_y = max(0, (hit_h - track_h) // 2)

                    background = vpmod.GradientDrawable()
                    background.setShape(vpmod.GradientDrawable.RECTANGLE)
                    background.setColor(vpmod.Color.argb(115, 255, 255, 255))
                    background.setCornerRadius(float(radius))
                    try:
                        background.setSize(1, track_h)
                    except Exception:
                        pass

                    progress = vpmod.GradientDrawable()
                    progress.setShape(vpmod.GradientDrawable.RECTANGLE)
                    progress.setColor(vpmod.Color.argb(255, 255, 0, 0))
                    progress.setCornerRadius(float(radius))
                    try:
                        progress.setSize(1, track_h)
                    except Exception:
                        pass

                    progress_clip = vpmod.ClipDrawable(
                        progress,
                        vpmod.Gravity.LEFT,
                        1,
                    )
                    layers = vpmod.LayerDrawable([background, progress_clip])
                    try:
                        layers.setId(0, vpmod.R_id.background)
                        layers.setId(1, vpmod.R_id.progress)
                        layers.setLayerInset(0, 0, inset_y, 0, inset_y)
                        layers.setLayerInset(1, 0, inset_y, 0, inset_y)
                    except Exception:
                        pass
                    seek_bar.setProgressDrawable(layers)

                    thumb, thumb_size = make_thumb(active)
                    seek_bar.setThumb(thumb)
                    try:
                        seek_bar.setThumbOffset(thumb_size // 2)
                        seek_bar.setMinHeight(hit_h)
                        seek_bar.setMaxHeight(hit_h)
                        seek_bar.setSplitTrack(False)
                    except Exception:
                        pass
                    seek_bar.setPadding(px(activity, 1), 0, px(activity, 1), 0)
                    seek_bar.setClickable(True)
                    seek_bar.setFocusable(False)
                    try:
                        seek_bar.invalidate()
                    except Exception:
                        pass
                except Exception:
                    try:
                        seek_bar.setProgressTintList(
                            vpmod.ColorStateList.valueOf(
                                vpmod.Color.argb(255, 255, 0, 0)
                            )
                        )
                        seek_bar.setProgressBackgroundTintList(
                            vpmod.ColorStateList.valueOf(
                                vpmod.Color.argb(115, 255, 255, 255)
                            )
                        )
                        seek_bar.setThumbTintList(
                            vpmod.ColorStateList.valueOf(
                                vpmod.Color.argb(255, 255, 0, 0)
                            )
                        )
                    except Exception:
                        pass

            old_time_text = cls._set_native_time_text

            def set_time_text(self, current_ms=None, total_ms=None):
                try:
                    if current_ms is not None:
                        self._pymusic_timeline_current_ms = max(
                            0, int(current_ms or 0)
                        )
                    if total_ms is not None:
                        self._pymusic_timeline_total_ms = max(
                            0, int(total_ms or 0)
                        )

                    label = getattr(self, "_pymusic_timeline_time_label", None)
                    if label is not None:
                        current = int(
                            getattr(self, "_pymusic_timeline_current_ms", 0) or 0
                        )
                        total = int(
                            getattr(self, "_pymusic_timeline_total_ms", 0) or 0
                        )
                        label.setText(
                            vpmod.String(
                                f"{self._format_time_ms(current)} / "
                                f"{self._format_time_ms(total)}"
                            )
                        )
                        return
                except Exception:
                    pass
                return old_time_text(
                    self, current_ms=current_ms, total_ms=total_ms
                )

            cls._set_native_time_text = set_time_text
            cls._style_youtube_seekbar = (
                lambda self, seek_bar: style_seekbar(
                    seek_bar,
                    bool(getattr(self, "_native_seek_dragging", False)),
                )
            )

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
                    try:
                        current = int(progress or 0)
                        self._owner._pymusic_timeline_current_ms = current
                        if from_user:
                            self._owner._set_native_time_text(
                                current_ms=current
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

            @vpmod.run_on_ui_thread
            def apply_timeline(owner):
                try:
                    overlay = getattr(owner, "controls_overlay", None)
                    seek_bar = getattr(owner, "_native_seek_bar", None)
                    if overlay is None or seek_bar is None:
                        return

                    activity = vpmod.PythonActivity.mActivity

                    for attr in ("_native_current_time", "_native_total_time"):
                        old_label = getattr(owner, attr, None)
                        if old_label is not None:
                            try:
                                old_label.setVisibility(vpmod.View.GONE)
                            except Exception:
                                pass

                    container = getattr(
                        owner, "_pymusic_timeline_container", None
                    )
                    if container is not None:
                        try:
                            if container.getParent() is overlay:
                                style_seekbar(
                                    seek_bar,
                                    bool(
                                        getattr(
                                            owner,
                                            "_native_seek_dragging",
                                            False,
                                        )
                                    ),
                                )
                                owner._set_native_time_text()
                                return
                        except Exception:
                            pass

                    old_parent = None
                    try:
                        old_parent = seek_bar.getParent()
                    except Exception:
                        old_parent = None
                    if old_parent is not None:
                        try:
                            old_parent.removeView(seek_bar)
                        except Exception:
                            pass
                        try:
                            if old_parent.getParent() is overlay:
                                overlay.removeView(old_parent)
                        except Exception:
                            pass

                    timeline = vpmod.FrameLayout(activity)
                    try:
                        timeline.setBackgroundColor(vpmod.Color.TRANSPARENT)
                        timeline.setClickable(False)
                        timeline.setFocusable(False)
                    except Exception:
                        pass

                    time_label = vpmod.TextView(activity)
                    time_label.setText(vpmod.String("0:00 / 0:00"))
                    time_label.setTextColor(
                        vpmod.Color.argb(238, 255, 255, 255)
                    )
                    time_label.setTextSize(12.0)
                    time_label.setGravity(
                        vpmod.Gravity.LEFT | vpmod.Gravity.CENTER_VERTICAL
                    )
                    try:
                        time_label.setShadowLayer(
                            3.0,
                            0.0,
                            1.0,
                            vpmod.Color.argb(210, 0, 0, 0),
                        )
                        time_label.setClickable(False)
                        time_label.setFocusable(False)
                    except Exception:
                        pass

                    label_params = vpmod.FrameLayoutLayoutParams(
                        vpmod.FrameLayoutLayoutParams.WRAP_CONTENT,
                        px(activity, 20),
                    )
                    label_params.gravity = (
                        vpmod.Gravity.LEFT | vpmod.Gravity.BOTTOM
                    )
                    label_params.leftMargin = px(activity, 10)
                    label_params.bottomMargin = px(activity, 15)
                    timeline.addView(time_label, label_params)

                    owner._native_seek_listener = YoutubeSeekListener(owner)
                    seek_bar.setOnSeekBarChangeListener(
                        owner._native_seek_listener
                    )
                    style_seekbar(seek_bar, False)

                    seek_params = vpmod.FrameLayoutLayoutParams(
                        vpmod.FrameLayoutLayoutParams.MATCH_PARENT,
                        px(activity, 28),
                    )
                    seek_params.gravity = vpmod.Gravity.BOTTOM
                    seek_params.leftMargin = 0
                    seek_params.rightMargin = 0
                    seek_params.bottomMargin = -px(activity, 8)
                    timeline.addView(seek_bar, seek_params)

                    timeline_params = vpmod.FrameLayoutLayoutParams(
                        vpmod.FrameLayoutLayoutParams.MATCH_PARENT,
                        px(activity, 38),
                    )
                    timeline_params.gravity = vpmod.Gravity.BOTTOM
                    timeline_params.bottomMargin = 0
                    overlay.addView(timeline, timeline_params)

                    owner._pymusic_timeline_container = timeline
                    owner._pymusic_timeline_time_label = time_label
                    owner._pymusic_timeline_current_ms = int(
                        getattr(owner, "_pymusic_timeline_current_ms", 0) or 0
                    )
                    owner._pymusic_timeline_total_ms = int(
                        getattr(owner, "_pymusic_timeline_total_ms", 0) or 0
                    )
                    owner._native_current_time = None
                    owner._native_total_time = None
                    owner._set_native_time_text()

                    try:
                        timeline.bringToFront()
                        overlay.requestLayout()
                        overlay.invalidate()
                    except Exception:
                        pass
                except Exception as exc:
                    try:
                        print("[YT-TIMELINE] apply failed:", exc)
                    except Exception:
                        pass

            old_ensure = cls._ensure_controls_overlay
            old_visible = cls.set_native_controls_visible

            def ensure_with_timeline(self, *args, **kwargs):
                result = old_ensure(self, *args, **kwargs)
                for delay in (0.0, 0.03, 0.10, 0.24):
                    Clock.schedule_once(
                        lambda _dt, owner=self: apply_timeline(owner), delay
                    )
                return result

            def visible_with_timeline(self, visible):
                result = old_visible(self, visible)
                if visible:
                    for delay in (0.0, 0.03, 0.10):
                        Clock.schedule_once(
                            lambda _dt, owner=self: apply_timeline(owner),
                            delay,
                        )
                return result

            cls._ensure_controls_overlay = ensure_with_timeline
            cls.set_native_controls_visible = visible_with_timeline
            cls._pymusic_youtube_timeline_v2 = True

            _PATCHED = True
            print("[YT-TIMELINE] YouTube-style native timeline v2 enabled")
            return True
        except Exception as exc:
            try:
                print("[YT-TIMELINE] install failed:", exc)
            except Exception:
                pass
            return False
