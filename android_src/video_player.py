from __future__ import annotations

from jnius import autoclass, PythonJavaClass, java_method
from android.runnable import run_on_ui_thread

from kivy.clock import Clock

import ytdlp_helpers as ydlh
try:
    import media_android as ma
except Exception:
    ma = None


def _log(msg: str):
    try:
        if ma is not None and hasattr(ma, "log"):
            ma.log(msg)
        else:
            print(msg)
    except Exception:
        pass

PythonActivity = autoclass("org.kivy.android.PythonActivity")
MediaPlayer = autoclass("android.media.MediaPlayer")
SurfaceViewClass = autoclass("android.view.SurfaceView")
Color = autoclass("android.graphics.Color")
ColorStateList = autoclass("android.content.res.ColorStateList")
FrameLayout = autoclass("android.widget.FrameLayout")
FrameLayoutLayoutParams = autoclass("android.widget.FrameLayout$LayoutParams")
LinearLayout = autoclass("android.widget.LinearLayout")
LinearLayoutLayoutParams = autoclass("android.widget.LinearLayout$LayoutParams")
TextView = autoclass("android.widget.TextView")
ImageButton = autoclass("android.widget.ImageButton")
SeekBar = autoclass("android.widget.SeekBar")
R_draw = autoclass("android.R$drawable")
Gravity = autoclass("android.view.Gravity")
Uri = autoclass("android.net.Uri")
HashMap = autoclass("java.util.HashMap")
WebView = autoclass("android.webkit.WebView")
WebViewClient = autoclass("android.webkit.WebViewClient")
WebChromeClient = autoclass("android.webkit.WebChromeClient")
View = autoclass("android.view.View")
GradientDrawable = autoclass("android.graphics.drawable.GradientDrawable")
ClipDrawable = autoclass("android.graphics.drawable.ClipDrawable")
LayerDrawable = autoclass("android.graphics.drawable.LayerDrawable")
R_id = autoclass("android.R$id")
String = autoclass("java.lang.String")


class AndroidVideoPlayer:

    def __init__(self):
        self.player = None
        self.surface_view = None
        self.screen_w_px = None
        self.screen_h_px = None
        self.pending_bounds: tuple[int, int, int, int] | None = None
        # лічильник викликів play - щоб не було старих "хвостів"
        self._play_gen = 0
        self._prepared = False
        self._surface_ready_to_show = False
        self._pending_start_pos_ms = None
        self._start_pos_provider = None
        self._start_paused = False
        self._on_prepared_cb = None
        self._prepared_listener = None
        self._video_size_listener = None
        self._error_listener = None
        self._tap_listener = None
        self._tap_callback = None
        self._frame_bounds: tuple[int, int, int, int] | None = None
        self._video_size: tuple[int, int] | None = None
        self._video_cache_enabled = False
        self.controls_overlay = None
        self._controls_touch_views = []
        self._controls_visible = False
        self._native_play_button = None
        self._native_playing = False
        self._native_seek_bar = None
        self._native_seek_listener = None
        self._native_seek_callback = None
        self._native_seek_dragging = False
        self._native_current_time = None
        self._native_total_time = None

    class _OnPreparedListener(PythonJavaClass):
        __javainterfaces__ = ['android/media/MediaPlayer$OnPreparedListener']
        __javacontext__ = 'app'

        def __init__(self, owner, gen):
            super().__init__()
            self._owner = owner
            self._gen = gen

        @java_method('(Landroid/media/MediaPlayer;)V')
        def onPrepared(self, mp):
            try:
                self._owner._on_prepared(mp, self._gen)
            except Exception:
                pass

    class _OnVideoSizeChangedListener(PythonJavaClass):
        __javainterfaces__ = ['android/media/MediaPlayer$OnVideoSizeChangedListener']
        __javacontext__ = 'app'

        def __init__(self, owner, gen):
            super().__init__()
            self._owner = owner
            self._gen = gen

        @java_method('(Landroid/media/MediaPlayer;II)V')
        def onVideoSizeChanged(self, mp, width, height):
            try:
                self._owner._on_video_size_changed(int(width), int(height), self._gen)
            except Exception:
                pass

    class _OnErrorListener(PythonJavaClass):
        __javainterfaces__ = ['android/media/MediaPlayer$OnErrorListener']
        __javacontext__ = 'app'

        def __init__(self, owner, gen):
            super().__init__()
            self._owner = owner
            self._gen = gen

        @java_method('(Landroid/media/MediaPlayer;II)Z')
        def onError(self, mp, what, extra):
            try:
                return self._owner._on_error(mp, what, extra, self._gen)
            except Exception:
                return True

    class _OnTouchListener(PythonJavaClass):
        __javainterfaces__ = ['android/view/View$OnTouchListener']
        __javacontext__ = 'app'

        def __init__(self, owner):
            super().__init__()
            self._owner = owner

        @java_method('(Landroid/view/View;Landroid/view/MotionEvent;)Z')
        def onTouch(self, view, event):
            try:
                action = int(event.getAction())
                if action != 1:  # MotionEvent.ACTION_UP
                    return True
                frame = getattr(self._owner, "_frame_bounds", None)
                if frame:
                    left, _top, frame_w, _frame_h = frame
                    width = float(frame_w or view.getWidth() or 1)
                    try:
                        x = float(event.getRawX()) - float(left)
                    except Exception:
                        x = float(event.getX() or 0)
                else:
                    width = float(view.getWidth() or 1)
                    x = float(event.getX() or 0)
                if x < width / 3.0:
                    zone = "left"
                elif x > (width * 2.0) / 3.0:
                    zone = "right"
                else:
                    zone = "center"
                cb = getattr(self._owner, "_tap_callback", None)
                if callable(cb):
                    cb(zone)
            except Exception:
                pass
            return True

    class _OnSeekBarChangeListener(PythonJavaClass):
        __javainterfaces__ = ['android/widget/SeekBar$OnSeekBarChangeListener']
        __javacontext__ = 'app'

        def __init__(self, owner):
            super().__init__()
            self._owner = owner

        @java_method('(Landroid/widget/SeekBar;IZ)V')
        def onProgressChanged(self, seek_bar, progress, from_user):
            if from_user:
                self._owner._set_native_time_text(current_ms=int(progress or 0))

        @java_method('(Landroid/widget/SeekBar;)V')
        def onStartTrackingTouch(self, seek_bar):
            self._owner._native_seek_dragging = True

        @java_method('(Landroid/widget/SeekBar;)V')
        def onStopTrackingTouch(self, seek_bar):
            self._owner._native_seek_dragging = False
            try:
                cb = self._owner._native_seek_callback
                if callable(cb):
                    cb(int(seek_bar.getProgress() or 0))
            except Exception:
                pass

    def set_tap_callback(self, callback):
        self._tap_callback = callback
        try:
            if self.surface_view is not None:
                self._bind_surface_tap()
        except Exception:
            pass

    def set_seek_callback(self, callback):
        self._native_seek_callback = callback

    @staticmethod
    def _format_time_ms(value_ms: int) -> str:
        total_seconds = max(0, int(value_ms or 0) // 1000)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def _set_native_time_text(self, current_ms=None, total_ms=None):
        try:
            if current_ms is not None and self._native_current_time is not None:
                self._native_current_time.setText(String(self._format_time_ms(int(current_ms or 0))))
            if total_ms is not None and self._native_total_time is not None:
                self._native_total_time.setText(String(self._format_time_ms(int(total_ms or 0))))
        except Exception:
            pass

    def _bind_surface_tap(self):
        try:
            if self.surface_view is None:
                return
            if self._tap_listener is None:
                self._tap_listener = self._OnTouchListener(self)
            self.surface_view.setClickable(True)
            self.surface_view.setOnTouchListener(self._tap_listener)
        except Exception as e:
            print("[VIDEO] bind tap err:", e)

    def _bind_controls_tap(self):
        try:
            if self._tap_listener is None:
                self._tap_listener = self._OnTouchListener(self)
            views = [self.controls_overlay] + list(getattr(self, "_controls_touch_views", []) or [])
            for view in views:
                if view is None:
                    continue
                view.setClickable(True)
                view.setOnTouchListener(self._tap_listener)
        except Exception as e:
            print("[VIDEO] bind controls tap err:", e)

    def _style_control_text(self, tv, text: str):
        try:
            tv.setText(text)
            tv.setTextColor(Color.WHITE)
            tv.setTextSize(22.0 if text != "▶/Ⅱ" else 20.0)
            tv.setGravity(Gravity.CENTER)
            tv.setClickable(True)
            bg = GradientDrawable()
            bg.setShape(GradientDrawable.RECTANGLE)
            bg.setColor(Color.argb(220, 0, 0, 0))
            bg.setStroke(2, Color.argb(235, 255, 255, 255))
            bg.setCornerRadius(44.0)
            tv.setBackground(bg)
            tv.setAlpha(1.0)
        except Exception:
            try:
                tv.setBackgroundColor(Color.argb(220, 0, 0, 0))
                tv.setTextColor(Color.WHITE)
                tv.setAlpha(1.0)
            except Exception:
                pass

    def _style_control_icon(self, btn, icon_res):
        try:
            btn.setImageResource(icon_res)
            btn.setColorFilter(Color.WHITE)
            btn.setPadding(20, 16, 20, 16)
            btn.setClickable(True)
            btn.setFocusable(False)
            btn.setBackgroundColor(Color.TRANSPARENT)
            btn.setAlpha(1.0)
        except Exception:
            try:
                btn.setBackgroundColor(Color.TRANSPARENT)
                btn.setColorFilter(Color.WHITE)
                btn.setAlpha(1.0)
            except Exception:
                pass

    def _style_youtube_seekbar(self, seek_bar):
        """Thin red YouTube-like native SeekBar for the SurfaceView overlay."""
        try:
            bg = GradientDrawable()
            bg.setShape(GradientDrawable.RECTANGLE)
            bg.setColor(Color.argb(95, 255, 255, 255))
            bg.setCornerRadius(4.0)
            try:
                bg.setSize(1, 4)
            except Exception:
                pass

            progress = GradientDrawable()
            progress.setShape(GradientDrawable.RECTANGLE)
            progress.setColor(Color.argb(255, 255, 0, 0))
            progress.setCornerRadius(4.0)
            try:
                progress.setSize(1, 4)
            except Exception:
                pass

            clip = ClipDrawable(progress, Gravity.LEFT, 1)  # ClipDrawable.HORIZONTAL == 1
            layers = LayerDrawable([bg, clip])
            try:
                layers.setId(0, R_id.background)
                layers.setId(1, R_id.progress)
            except Exception:
                pass
            seek_bar.setProgressDrawable(layers)

            thumb = GradientDrawable()
            thumb.setShape(GradientDrawable.OVAL)
            thumb.setColor(Color.argb(255, 255, 0, 0))
            try:
                thumb.setSize(16, 16)
            except Exception:
                pass
            seek_bar.setThumb(thumb)
            try:
                seek_bar.setThumbOffset(8)
                seek_bar.setMinHeight(18)
                seek_bar.setMaxHeight(18)
            except Exception:
                pass
            seek_bar.setPadding(4, 0, 4, 0)
        except Exception as e:
            # Fallback через tint, якщо конкретна прошивка не приймає LayerDrawable.
            try:
                seek_bar.setProgressTintList(ColorStateList.valueOf(Color.argb(255, 255, 0, 0)))
                seek_bar.setProgressBackgroundTintList(ColorStateList.valueOf(Color.argb(90, 255, 255, 255)))
                seek_bar.setThumbTintList(ColorStateList.valueOf(Color.argb(255, 255, 0, 0)))
            except Exception:
                pass

    @run_on_ui_thread
    def set_native_playing(self, playing: bool):
        self._native_playing = bool(playing)
        try:
            if self._native_play_button is not None:
                self._native_play_button.setImageResource(
                    R_draw.ic_media_pause if self._native_playing else R_draw.ic_media_play
                )
                self._native_play_button.setColorFilter(Color.WHITE)
                self._native_play_button.invalidate()
        except Exception:
            pass

    def _bring_controls_or_surface_front(self):
        try:
            ov = self.controls_overlay
            if ov is not None and self._controls_visible:
                ov.setAlpha(1.0)
                ov.setVisibility(0)
                ov.bringToFront()
                ov.requestLayout()
                ov.invalidate()
                return
        except Exception:
            pass
        try:
            if self.surface_view is not None:
                self.surface_view.bringToFront()
        except Exception:
            pass

    @run_on_ui_thread
    def _ensure_controls_overlay(self):
        try:
            if self.controls_overlay is not None:
                self._bind_controls_tap()
                return

            activity = PythonActivity.mActivity
            overlay = FrameLayout(activity)
            try:
                # Як у YouTube: контролам не потрібна суцільна темна плашка,
                # лише самі іконки + тонкий progress унизу.
                overlay.setBackgroundColor(Color.TRANSPARENT)
                overlay.setAlpha(1.0)
                overlay.setClickable(True)
                overlay.setFocusable(False)
            except Exception:
                pass

            bar = LinearLayout(activity)
            try:
                bar.setOrientation(LinearLayout.HORIZONTAL)
                bar.setGravity(Gravity.CENTER)
                bar.setClickable(False)
            except Exception:
                pass

            control_icons = (
                R_draw.ic_media_rew,
                R_draw.ic_media_pause if self._native_playing else R_draw.ic_media_play,
                R_draw.ic_media_ff,
            )
            for idx, icon_res in enumerate(control_icons):
                btn = ImageButton(activity)
                self._style_control_icon(btn, icon_res)
                # Extra horizontal space: on phones the native icon drawables can
                # look almost glued together if the button boxes are too small.
                lp = LinearLayoutLayoutParams(116 if idx == 1 else 104, 76)
                try:
                    lp.setMargins(28, 0, 28, 0)
                except Exception:
                    pass
                bar.addView(btn, lp)
                self._controls_touch_views.append(btn)
                if idx == 1:
                    self._native_play_button = btn

            bar_params = FrameLayoutLayoutParams(
                FrameLayoutLayoutParams.WRAP_CONTENT,
                FrameLayoutLayoutParams.WRAP_CONTENT,
            )
            bar_params.gravity = Gravity.CENTER
            bar_params.bottomMargin = 0
            overlay.addView(bar, bar_params)

            progress_row = LinearLayout(activity)
            progress_row.setOrientation(LinearLayout.HORIZONTAL)
            progress_row.setGravity(Gravity.CENTER_VERTICAL)
            progress_row.setPadding(10, 0, 10, 0)

            current_time = TextView(activity)
            current_time.setText(String("0:00"))
            current_time.setTextColor(Color.argb(210, 255, 255, 255))
            current_time.setTextSize(12.0)
            current_time.setGravity(Gravity.CENTER)
            self._native_current_time = current_time

            seek_bar = SeekBar(activity)
            seek_bar.setMax(1)
            seek_bar.setProgress(0)
            try:
                seek_bar.setSplitTrack(False)
            except Exception:
                pass
            self._style_youtube_seekbar(seek_bar)
            self._native_seek_listener = self._OnSeekBarChangeListener(self)
            seek_bar.setOnSeekBarChangeListener(self._native_seek_listener)
            self._native_seek_bar = seek_bar

            total_time = TextView(activity)
            total_time.setText(String("0:00"))
            total_time.setTextColor(Color.argb(150, 255, 255, 255))
            total_time.setTextSize(12.0)
            total_time.setGravity(Gravity.CENTER)
            self._native_total_time = total_time

            progress_row.addView(current_time, LinearLayoutLayoutParams(100, 42))
            progress_row.addView(seek_bar, LinearLayoutLayoutParams(0, 42, 1.0))
            progress_row.addView(total_time, LinearLayoutLayoutParams(100, 42))

            progress_params = FrameLayoutLayoutParams(
                FrameLayoutLayoutParams.MATCH_PARENT,
                36,
            )
            progress_params.gravity = Gravity.BOTTOM
            progress_params.bottomMargin = 2
            overlay.addView(progress_row, progress_params)
            self._controls_touch_views.append(bar)

            params = FrameLayoutLayoutParams(1, 1)
            params.gravity = Gravity.TOP | Gravity.LEFT
            activity.addContentView(overlay, params)
            overlay.setVisibility(4)
            self.controls_overlay = overlay
            self._bind_controls_tap()
            self._bring_controls_or_surface_front()
        except Exception as e:
            print("[VIDEO] controls overlay create err:", e)

    @run_on_ui_thread
    def create_surface(self):
        activity = PythonActivity.mActivity

        # REUSE EXISTING SURFACE
        if self.surface_view is not None:
            try:
                sv = self.surface_view
                if self._frame_bounds:
                    self._apply_surface_bounds()
                else:
                    sv.setVisibility(4)
                parent = sv.getParent()
                if parent is not None:
                    try:
                        parent.bringChildToFront(sv)
                    except Exception:
                        pass
                    try:
                        parent.requestLayout()
                    except Exception:
                        try:
                            sv.requestLayout()
                        except Exception:
                            pass
                    try:
                        parent.invalidate()
                    except Exception:
                        try:
                            sv.invalidate()
                        except Exception:
                            pass
            except Exception as e:
                print("[VIDEO] reuse surface_view err:", e)
            return

        # CREATE NEW SURFACE
        sv = SurfaceViewClass(activity)
        try:
            sv.setClickable(True)
            sv.setFocusable(False)
            sv.setFocusableInTouchMode(False)
            # SurfaceView може перекривати Kivy/native overlay. Тримаємо його не on-top,
            # а контролам даємо окремий native overlay поверх.
            sv.setZOrderOnTop(False)
            sv.setZOrderMediaOverlay(False)
        except Exception:
            pass

        try:
            metrics = activity.getResources().getDisplayMetrics()
            screen_w = int(metrics.widthPixels)
            screen_h = int(metrics.heightPixels)
        except Exception:
            screen_w = 1080
            screen_h = 1920

        self.screen_w_px = screen_w
        self.screen_h_px = screen_h

        params = FrameLayoutLayoutParams(1, 1)
        params.gravity = Gravity.CENTER
        sv.setLayoutParams(params)

        try:
            sv.setBackgroundColor(Color.TRANSPARENT)
        except Exception:
            pass

        try:
            activity.addContentView(sv, params)
            try:
                parent = sv.getParent()
                if parent is not None:
                    try:
                        parent.bringChildToFront(sv)
                    except Exception:
                        pass
                    try:
                        parent.requestLayout()
                    except Exception:
                        pass
                    try:
                        parent.invalidate()
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception as e:
            print("[VIDEO] addContentView error:", e)
            return

        sv.setVisibility(4)

        self.surface_view = sv
        self._bind_surface_tap()
        self._ensure_controls_overlay()
        try:
            if getattr(self, "pending_bounds", None):
                pending = self.pending_bounds
                self.pending_bounds = None
                self.set_bounds(*pending)
        except Exception:
            pass

        print("[VIDEO] SurfaceView created, screen_px =", self.screen_w_px, self.screen_h_px)

    @run_on_ui_thread
    def play(
        self,
        video_url: str,
        headers: dict | None = None,
        loop: bool = False,
        start_pos_ms: int | None = None,
        start_pos_provider=None,
        start_paused: bool = False,
        on_prepared=None,
    ):
        if not video_url:
            print("[VIDEO] empty url")
            return

        self.create_surface()
        self._surface_ready_to_show = False
        try:
            # Не ховаємо SurfaceView через INVISIBLE до onPrepared.
            # На частині Android-прошивок такий SurfaceView не показує відеоряд взагалі.
            # Якщо bounds уже є — тримаємо surface видимим як у старій робочій версії.
            if self.surface_view is not None and self._frame_bounds:
                self.surface_view.setVisibility(View.VISIBLE)
        except Exception:
            pass

        # нове покоління play
        self._play_gen += 1
        gen = self._play_gen

        if self.player is not None:
            try:
                self.player.stop()
            except Exception:
                pass
            try:
                self.player.release()
            except Exception:
                pass
            self.player = None

        self.player = MediaPlayer()
        self._prepared = False
        self._surface_ready_to_show = False
        self._pending_start_pos_ms = start_pos_ms
        self._start_pos_provider = start_pos_provider
        self._start_paused = start_paused
        self._on_prepared_cb = on_prepared
        self._video_size = None

        try:
            print("[VIDEO] MediaPlayer setDataSource", video_url)

            is_local = video_url.startswith("/") or video_url.startswith("file://")
            used_headers = headers or {}

            if is_local:
                if video_url.startswith("/"):
                    uri = Uri.parse("file://" + video_url)
                else:
                    uri = Uri.parse(video_url)
                self.player.setDataSource(PythonActivity.mActivity, uri)
            else:
                jmap = None
                if used_headers:
                    try:
                        jmap = ydlh.py_headers_to_javamap(used_headers, HashMap)
                    except Exception as e:
                        print("[VIDEO] py_headers_to_javamap err:", e)
                        jmap = None

                try:
                    if jmap is not None:
                        self.player.setDataSource(video_url, jmap)
                    else:
                        self.player.setDataSource(video_url)
                except Exception as e:
                    print("[VIDEO] setDataSource(url, headers) failed, fallback:", e)
                    self.player.setDataSource(video_url)

            self.player.setLooping(loop)
            self.player.setVolume(0.0, 0.0)
            try:
                self.player.setVideoScalingMode(1)  # VIDEO_SCALING_MODE_SCALE_TO_FIT
            except Exception:
                pass
            try:
                self._prepared_listener = self._OnPreparedListener(self, gen)
                self.player.setOnPreparedListener(self._prepared_listener)
            except Exception:
                self._prepared_listener = None
            try:
                self._video_size_listener = self._OnVideoSizeChangedListener(self, gen)
                self.player.setOnVideoSizeChangedListener(self._video_size_listener)
            except Exception:
                self._video_size_listener = None

            try:
                self._error_listener = self._OnErrorListener(self, gen)
                self.player.setOnErrorListener(self._error_listener)
            except Exception:
                self._error_listener = None


            Clock.schedule_once(lambda dt: self._attach_and_prepare_when_surface_ready(gen), 0.05)
        except Exception as e:
            print("[VIDEO] play() error (setDataSource):", e)
            try:
                if self.surface_view is not None:
                    self.surface_view.setVisibility(4)
            except Exception:
                pass

    def _on_prepared(self, mp, gen: int):
        if gen != self._play_gen:
            return
        if self.player is None or mp is None:
            return
        self._prepared = True
        pos_ms = None
        try:
            if callable(self._start_pos_provider):
                pos_ms = self._start_pos_provider()
        except Exception:
            pos_ms = None
        if pos_ms is None:
            pos_ms = self._pending_start_pos_ms
        try:
            if pos_ms is not None and int(pos_ms) > 0:
                mp.seekTo(int(pos_ms))
        except Exception:
            pass
        try:
            if not self._start_paused:
                mp.start()
        except Exception:
            pass
        try:
            self._surface_ready_to_show = True
            self._apply_surface_bounds()
        except Exception:
            pass
        try:
            if self.surface_view is not None:
                self.surface_view.setVisibility(View.VISIBLE)
        except Exception:
            pass
        try:

            if callable(self._on_prepared_cb):
                self._on_prepared_cb()
        except Exception:
            pass

    def _on_video_size_changed(self, width: int, height: int, gen: int):
        if gen != self._play_gen:
            return
        if width <= 0 or height <= 0:
            return
        self._video_size = (int(width), int(height))
        self._apply_surface_bounds()

    def _on_error(self, mp, what, extra, gen: int):
        if gen != self._play_gen:
            return True
        try:
            _log(f"[VIDEO] MediaPlayer error what={what} extra={extra}")
        except Exception:
            pass
        try:
            self.stop()
        except Exception:
            pass
        return True

    def _fit_rect_to_video(self, left: int, top: int, width: int, height: int) -> tuple[int, int, int, int]:
        vw, vh = self._video_size or (16, 9)
        if vw <= 0 or vh <= 0 or width <= 0 or height <= 0:
            return left, top, width, height

        frame_ratio = float(width) / float(height)
        video_ratio = float(vw) / float(vh)
        if video_ratio > frame_ratio:
            out_w = int(width)
            out_h = max(1, int(round(out_w / video_ratio)))
        else:
            out_h = int(height)
            out_w = max(1, int(round(out_h * video_ratio)))

        out_left = int(left + max(0, (width - out_w) / 2))
        out_top = int(top + max(0, (height - out_h) / 2))
        return out_left, out_top, out_w, out_h

    @run_on_ui_thread
    def _apply_surface_bounds(self) -> None:
        try:
            if self.surface_view is None or not self._frame_bounds:
                return

            left, top, width, height = self._frame_bounds
            if width <= 0 or height <= 0:
                print("[VIDEO] set_bounds skip, non positive size:", width, height)
                return

            left, top, width, height = self._fit_rect_to_video(left, top, width, height)
            sv = self.surface_view
            params = FrameLayoutLayoutParams(int(width), int(height))
            params.leftMargin = int(left)
            params.topMargin = int(top)
            sv.setLayoutParams(params)
            sv.setVisibility(View.VISIBLE)

            parent = sv.getParent()
            if parent is not None:
                try:
                    parent.bringChildToFront(sv)
                except Exception:
                    pass
                try:
                    parent.requestLayout()
                except Exception:
                    try:
                        sv.requestLayout()
                    except Exception:
                        pass
                try:
                    parent.invalidate()
                except Exception:
                    try:
                        sv.invalidate()
                    except Exception:
                        pass

            try:
                self._ensure_controls_overlay()
                ov = self.controls_overlay
                if ov is not None:
                    ov_params = FrameLayoutLayoutParams(int(width), int(height))
                    ov_params.leftMargin = int(left)
                    ov_params.topMargin = int(top)
                    ov.setLayoutParams(ov_params)
                    ov.setVisibility(0 if self._controls_visible else 4)
                    try:
                        ov.setAlpha(1.0)
                        ov.bringToFront()
                    except Exception:
                        pass
                    try:
                        ov.requestLayout()
                        ov.invalidate()
                    except Exception:
                        pass
            except Exception as e:
                print("[VIDEO] controls bounds err:", e)

            self._bring_controls_or_surface_front()
            print(f"[VIDEO] surface applied left={left}, top={top}, w={width}, h={height}, video_size={self._video_size}")
        except Exception as e:
            print(f"[VIDEO] apply bounds error: {e}")

    def _attach_and_prepare_when_surface_ready(self, gen: int):

        @run_on_ui_thread
        def _check(*_):
            if gen != self._play_gen:
                return
            if self.player is None:
                return
            if self.surface_view is None:
                Clock.schedule_once(lambda dt: _check(), 0.05)
                return

            if gen != self._play_gen:
                return

            try:
                holder = self.surface_view.getHolder()
                self.player.setDisplay(holder)

                try:
                    self.surface_view.setVisibility(View.VISIBLE)
                    parent = self.surface_view.getParent()
                    if parent is not None:
                        try:
                            parent.bringChildToFront(self.surface_view)
                        except Exception:
                            pass
                        try:
                            parent.requestLayout()
                        except Exception:
                            try:
                                self.surface_view.requestLayout()
                            except Exception:
                                pass
                        try:
                            parent.invalidate()
                        except Exception:
                            try:
                                self.surface_view.invalidate()
                            except Exception:
                                pass
                except Exception:
                    pass
                self._bring_controls_or_surface_front()
                print("[VIDEO] calling prepareAsync()")
                self.player.prepareAsync()
            except Exception as e:
                print("[VIDEO] play() error (attach/prepare):", e)
                try:
                    self.surface_view.setVisibility(4)
                except Exception:
                    pass

        Clock.schedule_once(lambda dt: _check(), 0)

    @run_on_ui_thread
    def stop(self):
        self._play_gen += 1
        self._prepared = False
        self._surface_ready_to_show = False
        try:
            if self.player is not None:
                try:
                    self.player.stop()
                except Exception:
                    pass
                try:
                    self.player.release()
                except Exception:
                    pass
                self.player = None
        except Exception:
            pass

        try:
            if self.surface_view is not None:
                self.surface_view.setVisibility(View.GONE)
                try:
                    self.surface_view.setLayoutParams(FrameLayoutLayoutParams(1, 1))
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self.controls_overlay is not None:
                self.controls_overlay.setVisibility(View.GONE)
                try:
                    self.controls_overlay.setLayoutParams(FrameLayoutLayoutParams(1, 1))
                except Exception:
                    pass
        except Exception:
            pass
        self._controls_visible = False

    @run_on_ui_thread
    def seek_to(self, ms: int):
        try:
            if self.player is None:
                return
            if self._prepared:
                self.player.seekTo(int(ms))
            else:
                self._pending_start_pos_ms = int(ms)
        except Exception:
            pass

    def get_current_position(self) -> int | None:
        try:
            if self.player is None or not self._prepared:
                return None
            return int(self.player.getCurrentPosition() or 0)
        except Exception:
            return None

    @run_on_ui_thread
    def set_bounds(self, left: int, top: int, width: int, height: int) -> None:
        try:
            if self.surface_view is None:
                self.pending_bounds = (left, top, width, height)
                print("[VIDEO] set_bounds stored pending:", self.pending_bounds)
                return

            if width <= 0 or height <= 0:
                print("[VIDEO] set_bounds skip, non positive size:", width, height)
                return

            self._frame_bounds = (int(left), int(top), int(width), int(height))
            self._apply_surface_bounds()
        except Exception as e:
            print(f"[VIDEO] set_bounds error: {e}")

    @run_on_ui_thread
    def set_native_controls_visible(self, visible: bool) -> None:
        self._controls_visible = bool(visible)
        try:
            self._ensure_controls_overlay()
            if self.controls_overlay is not None:
                self.controls_overlay.setVisibility(0 if visible else 4)
                self.controls_overlay.setAlpha(1.0)
                for view in list(getattr(self, "_controls_touch_views", []) or []):
                    try:
                        view.setAlpha(1.0)
                    except Exception:
                        pass
                self._bring_controls_or_surface_front()
        except Exception as e:
            print("[VIDEO] controls visible err:", e)

    @run_on_ui_thread
    def set_native_progress(self, position_ms: int, duration_ms: int) -> None:
        try:
            if self._native_seek_bar is None or self._native_seek_dragging:
                return
            duration_ms = max(1, int(duration_ms or 1))
            position_ms = max(0, min(int(position_ms or 0), duration_ms))
            self._native_seek_bar.setMax(duration_ms)
            self._native_seek_bar.setProgress(position_ms)
            self._set_native_time_text(current_ms=position_ms, total_ms=duration_ms)
        except Exception:
            pass

    @run_on_ui_thread
    def pause(self):
        try:
            if self.player and self.player.isPlaying():
                self.player.pause()
                print("[VIDEO] paused")
        except Exception:
            pass

    @run_on_ui_thread
    def resume(self):
        try:
            if self.player and not self.player.isPlaying():
                self.player.start()
                print("[VIDEO] resumed")
        except Exception:
            pass


class AndroidWebVideoPlayer:
    is_embed = True

    def __init__(self):
        self.webview = None
        self.screen_w_px = None
        self.screen_h_px = None
        self.pending_bounds: tuple[int, int, int, int] | None = None
        self._play_gen = 0
        self._current_video_id = None
        self._web_client = None
        self._use_full_page = False
        self._current_mode = "full"

    class _WebUiClient(PythonJavaClass):
        __javabase__ = 'android/webkit/WebViewClient'
        __javacontext__ = 'app'

        @java_method('(Landroid/webkit/WebView;Ljava/lang/String;)V')
        def onPageFinished(self, view, url):
            try:
                js = (
                    "try{"
                    "var css='html,body{margin:0!important;padding:0!important;width:100%!important;height:100%!important;"
                    "overflow:hidden!important;background:#000!important;}'"
                    "+'#player, ytm-player, .player-container{position:absolute!important;left:0;top:0;"
                    "width:100%!important;height:100%!important;background:#000!important;}'"
                    "+'video{width:100%!important;height:100%!important;object-fit:contain!important;background:#000!important;}';"
                    "var s=document.getElementById('pymusic-hide');"
                    "if(!s){s=document.createElement('style');s.id='pymusic-hide';document.documentElement.appendChild(s);}"
                    "s.textContent=css;"
                    "var v=document.querySelector('video');if(v){v.muted=true;v.volume=0;}"
                    "}catch(e){}"
                )
                if view is not None:
                    view.evaluateJavascript(js, None)
            except Exception:
                pass

    def _extract_video_id(self, url: str | None) -> str | None:
        if not url:
            return None
        try:
            u = str(url)
            if "youtu.be/" in u:
                part = u.split("youtu.be/", 1)[1]
                return part.split("?", 1)[0].split("&", 1)[0]
            if "watch?v=" in u:
                part = u.split("watch?v=", 1)[1]
                return part.split("&", 1)[0]
            if "/shorts/" in u:
                part = u.split("/shorts/", 1)[1]
                return part.split("?", 1)[0].split("&", 1)[0]
            if "/embed/" in u:
                part = u.split("/embed/", 1)[1]
                return part.split("?", 1)[0].split("&", 1)[0]
        except Exception:
            return None
        return None

    @run_on_ui_thread
    def create_surface(self):
        activity = PythonActivity.mActivity
        if self.webview is not None:
            try:
                self.webview.setVisibility(View.VISIBLE)
                parent = self.webview.getParent()
                if parent is not None:
                    try:
                        parent.bringChildToFront(self.webview)
                    except Exception:
                        pass
            except Exception:
                pass
            return

        wv = WebView(activity)
        try:
            metrics = activity.getResources().getDisplayMetrics()
            self.screen_w_px = int(metrics.widthPixels)
            self.screen_h_px = int(metrics.heightPixels)
        except Exception:
            self.screen_w_px = 1080
            self.screen_h_px = 1920

        params = FrameLayoutLayoutParams(
            FrameLayoutLayoutParams.MATCH_PARENT,
            FrameLayoutLayoutParams.MATCH_PARENT
        )
        params.gravity = Gravity.CENTER
        wv.setLayoutParams(params)
        try:
            wv.setBackgroundColor(Color.BLACK)
        except Exception:
            pass

        try:
            settings = wv.getSettings()
            settings.setJavaScriptEnabled(True)
            settings.setDomStorageEnabled(True)
            try:
                settings.setMediaPlaybackRequiresUserGesture(False)
            except Exception:
                pass
        except Exception:
            pass

        try:
            self._web_client = self._WebUiClient()
            wv.setWebViewClient(self._web_client)
        except Exception:
            pass
        try:
            wv.setWebChromeClient(WebChromeClient())
        except Exception:
            pass

        try:
            activity.addContentView(wv, params)
            parent = wv.getParent()
            if parent is not None:
                try:
                    parent.bringChildToFront(wv)
                except Exception:
                    pass
        except Exception:
            pass

        wv.setVisibility(View.VISIBLE)
        self.webview = wv
        try:
            if getattr(self, "pending_bounds", None):
                pending = self.pending_bounds
                self.pending_bounds = None
                self.set_bounds(*pending)
        except Exception:
            pass

    def _html(self, video_id: str, start_sec: int, allow_fallback: bool) -> str:
        return f"""<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no">
<style>
html,body,#player{{margin:0;width:100%;height:100%;background:#000;overflow:hidden;}}
</style>
</head>
<body>
<div id="player"></div>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
var player=null;
var currentVid='{video_id}';
var currentStart={start_sec};
var allowFallback={'true' if allow_fallback else 'false'};
var pymusicReady=false;
var pymusicStartTs=Date.now();
function fallbackUrl(vid,start){{
  var s = start||0;
  return 'https://m.youtube.com/watch?v=' + vid + '&playsinline=1&autoplay=1&start=' + s;
}}
function forceFallback(){{
  if(!allowFallback) return;
  try{{ window.location.replace(fallbackUrl(currentVid, currentStart)); }}catch(e){{ window.location.href=fallbackUrl(currentVid, currentStart); }}
}}
function handleError(e){{
  var code = e && e.data;
  if(code===101 || code===150 || code===152){{
    forceFallback();
  }}
}}
var _unmuted=false;
function tryUnmute(){{
  if(_unmuted || !player) return;
  try{{ player.unMute(); _unmuted=true; }}catch(e){{}}
}}
function onYouTubeIframeAPIReady() {{
  player = new YT.Player('player', {{
    videoId: '{video_id}',
    playerVars: {{
      'autoplay': 1,
      'controls': 1,
      'playsinline': 1,
      'mute': 1,
      'rel': 0,
      'modestbranding': 1,
      'iv_load_policy': 3,
      'fs': 0
    }},
    events: {{
      'onReady': function(e) {{
        pymusicReady=true;
        try{{ player.mute(); }}catch(e){{}}
        try{{ player.seekTo({start_sec}, true); }}catch(e){{}}
        try{{ player.playVideo(); }}catch(e){{}}
        try{{ setTimeout(tryUnmute, 1500); }}catch(e){{}}
        try{{ document.addEventListener('touchstart', tryUnmute, {{once:true, passive:true}}); }}catch(e){{}}
        try{{ document.addEventListener('click', tryUnmute, {{once:true}}); }}catch(e){{}}
        try{{
          var checks=0;
          var iv=setInterval(function(){{
            checks++;
            var st=-1;
            try{{ st=player.getPlayerState(); }}catch(e){{}}
            if(st===1||st===2||st===3){{ clearInterval(iv); return; }}
            if(checks>6){{ clearInterval(iv); forceFallback(); }}
          }}, 500);
        }}catch(e){{}}
      }},
      'onError': handleError
    }}
  }});
}}
setTimeout(function(){{
  if(!pymusicReady){{ forceFallback(); }}
}}, 3000);
window.pymusicLoad=function(vid,start){{
  currentVid = vid;
  currentStart = start||0;
  if(!player) {{
    window.location.href = fallbackUrl(currentVid, currentStart);
    return;
  }}
  try{{ player.loadVideoById(vid, currentStart); player.mute(); setTimeout(tryUnmute, 1500); }}catch(e){{}}
}};
window.pymusicSeek=function(sec){{ try{{ if(player) player.seekTo(sec,true); }}catch(e){{}} }};
window.pymusicPlay=function(){{ try{{ if(player) player.playVideo(); }}catch(e){{}} }};
window.pymusicPause=function(){{ try{{ if(player) player.pauseVideo(); }}catch(e){{}} }};
</script>
</body>
</html>
"""

    @run_on_ui_thread
    def play(self, video_url: str, start_pos_ms: int | None = None, start_pos_provider=None):
        vid = self._extract_video_id(video_url)
        if not vid:
            print("[VIDEO] embed: no video id")
            return

        self.create_surface()
        self._play_gen += 1
        gen = self._play_gen
        self._current_video_id = vid

        start_ms = None
        try:
            if callable(start_pos_provider):
                start_ms = int(start_pos_provider() or 0)
        except Exception:
            start_ms = None
        if start_ms is None:
            start_ms = int(start_pos_ms or 0)
        start_sec = max(0, int(start_ms / 1000))

        if self.webview is None:
            return
        try:
            self.webview.setVisibility(View.VISIBLE)
        except Exception:
            pass

        if self._use_full_page:
            self._current_mode = "full"
            url = f"https://m.youtube.com/watch?v={vid}&playsinline=1&autoplay=1&start={start_sec}"
            try:
                self.webview.loadUrl(url)
            except Exception:
                pass
        else:
            self._current_mode = "iframe"
            html = self._html(vid, start_sec, False)
            try:
                self.webview.loadDataWithBaseURL("https://www.youtube.com", html, "text/html", "utf-8", None)
            except Exception:
                try:
                    self.webview.loadData(html, "text/html", "utf-8")
                except Exception:
                    pass

    @run_on_ui_thread
    def seek_to(self, ms: int):
        if self.webview is None:
            return
        sec = max(0, int(ms / 1000))
        try:
            self.webview.evaluateJavascript(f"pymusicSeek({sec});", None)
        except Exception:
            pass
        try:
            self.webview.evaluateJavascript(
                f"var v=document.querySelector('video');if(v){{v.currentTime={sec};}}",
                None
            )
        except Exception:
            pass

    @run_on_ui_thread
    def pause(self):
        if self.webview is None:
            return
        try:
            self.webview.evaluateJavascript("pymusicPause();", None)
        except Exception:
            pass
        try:
            self.webview.evaluateJavascript(
                "var v=document.querySelector('video');if(v){v.pause();}",
                None
            )
        except Exception:
            pass

    @run_on_ui_thread
    def resume(self):
        if self.webview is None:
            return
        try:
            self.webview.evaluateJavascript("pymusicPlay();", None)
        except Exception:
            pass
        try:
            self.webview.evaluateJavascript(
                "var v=document.querySelector('video');if(v){v.play();}",
                None
            )
        except Exception:
            pass

    @run_on_ui_thread
    def stop(self):
        self._play_gen += 1
        if self.webview is None:
            return
        try:
            self.webview.loadUrl("about:blank")
        except Exception:
            pass
        try:
            self.webview.setVisibility(View.GONE)
        except Exception:
            pass

    @run_on_ui_thread
    def set_bounds(self, left: int, top: int, width: int, height: int) -> None:
        try:
            if self.webview is None:
                self.pending_bounds = (left, top, width, height)
                print("[VIDEO] set_bounds stored pending:", self.pending_bounds)
                return

            if width <= 0 or height <= 0:
                print("[VIDEO] set_bounds skip, non positive size:", width, height)
                return

            params = FrameLayoutLayoutParams(int(width), int(height))
            params.leftMargin = int(left)
            params.topMargin = int(top)
            self.webview.setLayoutParams(params)

            parent = self.webview.getParent()
            if parent is not None:
                try:
                    parent.bringChildToFront(self.webview)
                except Exception:
                    pass
                try:
                    parent.requestLayout()
                except Exception:
                    pass
                try:
                    parent.invalidate()
                except Exception:
                    pass
        except Exception as e:
            print(f"[VIDEO] set_bounds error: {e}")
