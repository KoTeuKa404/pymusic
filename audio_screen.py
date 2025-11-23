from jnius import autoclass, cast
from android.runnable import run_on_ui_thread

from kivy.uix.screenmanager import Screen
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.cache import Cache

import threading
import time
import os
import urllib.request
import tempfile

import media_android as ma
import ytdlp_helpers as ydlh
from headset_listener import headset_router


# ==================== AndroidVideoPlayer (SurfaceView + MediaPlayer) ====================

PythonActivity = autoclass("org.kivy.android.PythonActivity")
MediaPlayer = autoclass("android.media.MediaPlayer")
SurfaceViewClass = autoclass("android.view.SurfaceView")
R_id = autoclass("android.R$id")
Color = autoclass("android.graphics.Color")
FrameLayoutLayoutParams = autoclass("android.widget.FrameLayout$LayoutParams")
Gravity = autoclass("android.view.Gravity")


class AndroidVideoPlayer:

    def __init__(self):
        self.player = None
        self.surface_view = None
        self.screen_w_px = None
        self.screen_h_px = None
        self.pending_bounds = None


    @run_on_ui_thread
    def create_surface(self):
        activity = PythonActivity.mActivity

        if self.surface_view is not None:
            try:
                parent = self.surface_view.getParent()
                if parent is not None:
                    try:
                        parent.bringChildToFront(self.surface_view)
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
                print("[VIDEO] reuse surface_view err:", e)
            return

        try:
            root_view = activity.findViewById(R_id.content)
        except Exception as e:
            print("[VIDEO] findViewById error:", e)
            root_view = None

        root = None
        if root_view is not None:
            try:
                root = cast("android.view.ViewGroup", root_view)
            except Exception as e:
                print("[VIDEO] cast to ViewGroup failed:", e)
                root = None

        if root is None:
            try:
                decor = activity.getWindow().getDecorView()
                root = cast("android.view.ViewGroup", decor)
            except Exception as e:
                print("[VIDEO] fallback cast failed:", e)
                root = None

        if root is None:
            print("[VIDEO] no ViewGroup root - не можу створити SurfaceView")
            return

        sv = SurfaceViewClass(activity)

        try:
            metrics = activity.getResources().getDisplayMetrics()
            screen_w = metrics.widthPixels
            screen_h = metrics.heightPixels
        except Exception:
            screen_w = 1080
            screen_h = 1920

        self.screen_w_px = int(screen_w)
        self.screen_h_px = int(screen_h)

        video_h = int(screen_w * 9 / 16)

        params = FrameLayoutLayoutParams(
            FrameLayoutLayoutParams.MATCH_PARENT,
            video_h
        )
        params.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL
        sv.setLayoutParams(params)

        try:
            sv.setBackgroundColor(Color.BLACK)
        except Exception:
            pass

        try:
            sv.setZOrderOnTop(True)
            sv.setZOrderMediaOverlay(True)
        except Exception:
            pass

        try:
            root.addView(sv)
            try:
                root.bringChildToFront(sv)
            except Exception:
                pass
            try:
                root.requestLayout()
            except Exception:
                try:
                    sv.requestLayout()
                except Exception:
                    pass
            try:
                root.invalidate()
            except Exception:
                try:
                    sv.invalidate()
                except Exception:
                    pass
        except Exception as e:
            print("[VIDEO] addView error (root is not ViewGroup?):", e)
            return

        sv.setVisibility(4)

        self.surface_view = sv
        try:
            if getattr(self, "pending_bounds", None):
                pending = self.pending_bounds
                self.pending_bounds = None
                self.set_bounds(*pending)
        except Exception:
            pass

        print("[VIDEO] SurfaceView created, screen_px =", self.screen_w_px, self.screen_h_px)


    @run_on_ui_thread
    def play(self, video_url: str, loop: bool = False):
        if not video_url:
            print("[VIDEO] empty url")
            return

        self.create_surface()

        if self.player is not None:
            try:
                self.player.stop()
                self.player.release()
            except Exception:
                pass
            self.player = None

        # Створюємо новий плеєр і ставимо URL
        self.player = MediaPlayer()
        try:
            print("[VIDEO] MediaPlayer setDataSource", video_url)
            self.player.setDataSource(video_url)
            self.player.setLooping(loop)
            self.player.setVolume(0.0, 0.0)
            self._attach_and_prepare_when_surface_ready()
        except Exception as e:
            print("[VIDEO] play() error (setDataSource):", e)
            try:
                if self.surface_view is not None:
                    self.surface_view.setVisibility(4)
            except Exception:
                pass


    def _attach_and_prepare_when_surface_ready(self):


        from kivy.clock import Clock

        @run_on_ui_thread
        def _check(*_):
            if self.player is None:
                return

            if self.surface_view is None:
                try:
                    print("[VIDEO] _check: surface_view is None, retry...")
                except Exception:
                    pass
                Clock.schedule_once(_check, 0.05)
                return

            try:
                holder = self.surface_view.getHolder()
                surface = holder.getSurface()
            except Exception as e:
                try:
                    print("[VIDEO] _check: holder/surface error:", e)
                except Exception:
                    pass
                Clock.schedule_once(_check, 0.05)
                return

            try:
                if surface is None or not surface.isValid():
                    Clock.schedule_once(_check, 0.05)
                    return
            except Exception:
                Clock.schedule_once(_check, 0.05)
                return

            try:
                self.player.setSurface(surface)

                try:
                    self.surface_view.setVisibility(0)  # VISIBLE
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

                try:
                    print("[VIDEO] calling prepare()")
                except Exception:
                    pass

                self.player.prepare()

                try:
                    print("[VIDEO] prepared, start()")
                except Exception:
                    pass

                self.player.start()
            except Exception as e:
                try:
                    print("[VIDEO] play() error (attach/prepare):", e)
                except Exception:
                    pass
                try:
                    self.surface_view.setVisibility(4)  # INVISIBLE
                except Exception:
                    pass

        Clock.schedule_once(_check, 0)

    @run_on_ui_thread
    def stop(self):
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
                self.surface_view.setVisibility(4)
        except Exception:
            pass


    @run_on_ui_thread
    def set_bounds(self, left: int, top: int, width: int, height: int) -> None:

        try:
            if self.surface_view is None:
                self.pending_bounds = (left, top, width, height)
                print("[VIDEO] set_bounds stored pending:", self.pending_bounds)
                return

            sv = self.surface_view

            if width <= 0 or height <= 0:
                print("[VIDEO] set_bounds skip, non positive size:", width, height)
                return

            params = FrameLayoutLayoutParams(int(width), int(height))
            params.leftMargin = int(left)
            params.topMargin = int(top)
            sv.setLayoutParams(params)

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

            print(f"[VIDEO] set_bounds applied left={left}, top={top}, w={width}, h={height}")
        except Exception as e:
            try:
                print(f"[VIDEO] set_bounds error: {e}")
            except Exception:
                pass


# ==================== AudioPlayerScreen ====================


class AudioPlayerScreen(Screen):
    """Аудіо через Android MediaPlayer, відео через AndroidVideoPlayer (SurfaceView)."""

    _ms_next = None
    _ms_prev = None
    _ms_toggle = None
    _ms_play = None
    _ms_pause = None

    def __init__(self, **kw):
        super().__init__(**kw)
        self.repeat = False
        self._playback_desired = False
        self._user_paused = False
        self._update_ev = None
        self._refresh_ev = None

        self._title = ""
        self._channel = ""
        self._thumb = ""
        self._stream_url = None
        self._expire_ts = None
        self._headers = {}
        self._last_video_url = None

        self._media_session = None
        self._art_path = None

        self._url_cache = {}
        self._URL_CACHE_MAX = 50

        self.playlist = []
        self.playlist_idx = 0

        self._last_click = 0.0
        self._last_media_ts = 0.0
        self._bind_uid = None

        self._load_gen = 0
        self._bg_endguard_fired_gen = -1

        self._video_player: AndroidVideoPlayer | None = None

    # ==================== lifecycle ====================

    def _ensure_video_player(self) -> bool:
        if self._video_player is not None:
            return True
        try:
            self._video_player = AndroidVideoPlayer()
            print("[VIDEO] AndroidVideoPlayer created")
            return True
        except Exception as e:
            ma.log(f"[VIDEO] init err: {e}")
            self._video_player = None
            return False

    def on_kv_post(self, base_widget):
        super().on_kv_post(base_widget)
        try:
            thumb = self.ids.audio_thumbnail
            thumb.bind(pos=self._align_video_to_thumb, size=self._align_video_to_thumb)
        except Exception as e:
            print("[VIDEO] on_kv_post bind err:", e)

    def on_pre_enter(self):
        try:
            ma.request_post_notifications_permission()
        except Exception:
            pass
        ma.create_notification_channel()

        # MediaSession
        try:
            if not self._media_session:
                self._media_session = ma.MediaSession(ma.PythonActivity.mActivity, "PyMusicSession")
                try:
                    flags = (ma.MediaSession.FLAG_HANDLES_MEDIA_BUTTONS |
                            ma.MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS)
                    self._media_session.setFlags(flags)
                except Exception:
                    pass
            self._media_session.setActive(True)
            ma.register_media_session(self._media_session)
            ma.configure_session_audio()
            ma.set_media_session_callback(self)
        except Exception:
            pass

        self._ms_next = self._act_next
        self._ms_prev = self._act_prev
        self._ms_toggle = lambda *a: self.toggle_play_pause()
        self._ms_play = lambda *a: (self._user_paused and self.toggle_play_pause())
        self._ms_pause = lambda *a: (ma.android_player and ma.android_player.isPlaying() and self.toggle_play_pause())
        ma.bind_notification_action_router(self)

        self._bind_keys()
        self._bind_headset()
        self._sync_ui_loaded()

        self._ensure_video_player()

        Clock.schedule_once(self._align_video_to_thumb, 0.3)

    def on_pre_leave(self):
        self._unbind_keys()
        self._unbind_headset()
        try:
            ma.unbind_notification_action_router()
        except Exception:
            pass
        try:
            if self._media_session:
                self._media_session.setActive(False)
        except Exception:
            pass
        try:
            ma.unbind_notification_action_router()
        except Exception:
            pass

        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass


    def _align_video_to_thumb(self, *args):
        """
        Виставляє SurfaceView так, щоб він співпадав з зоною прев'ю audio_thumbnail.
        """
        try:
            if not self._video_player:
                return
            thumb = self.ids.get("audio_thumbnail")
            if not thumb:
                return

            win_w, win_h = Window.size

            if win_w <= 0 or win_h <= 0:
                return

            wx, wy = thumb.to_window(thumb.x, thumb.y, relative=False)
            ww = thumb.width
            wh = thumb.height

            activity = PythonActivity.mActivity
            try:
                metrics = activity.getResources().getDisplayMetrics()
                screen_w = int(metrics.widthPixels)
                screen_h = int(metrics.heightPixels)
            except Exception:
                screen_w = self._video_player.screen_w_px or 1080
                screen_h = self._video_player.screen_h_px or 1920

            left_px = int(wx / float(win_w) * screen_w)
            bottom_px = int(wy / float(win_h) * screen_h)
            width_px = int(ww / float(win_w) * screen_w)
            height_px = int(wh / float(win_h) * screen_h)
            top_px = int(screen_h - bottom_px - height_px)

            print("[VIDEO] align thumb win:",
                "win_size=", win_w, win_h,
                "thumb=", wx, wy, ww, wh,
                "screen=", screen_w, screen_h,
                "bounds_px=", left_px, top_px, width_px, height_px)

            self._video_player.set_bounds(left_px, top_px, width_px, height_px)
        except Exception as e:
            print("[VIDEO] align err:", e)

    # ==================== headset ====================

    def _bind_headset(self):
        try:
            headset_router.set_callbacks(
                on_play=self._act_play,
                on_pause=self._act_pause,
                on_toggle=self._act_toggle,
                on_next=self._act_next,
                on_prev=self._act_prev,
            )
            headset_router.set_active(True)
        except Exception as e:
            ma.log(f"[HEADSET] bind err: {e}")

    def _unbind_headset(self):
        try:
            headset_router.set_active(False)
        except Exception:
            pass

    # ==================== keys ====================

    def _bind_keys(self):
        def _on_key_down(window, keycode, scancode, text, modifiers):
            code = keycode[0] if isinstance(keycode, (tuple, list)) else keycode
            if code in (24, 25) and (time.time() - getattr(self, "_last_media_ts", 0.0) < 0.7):
                return True
            return False

        self._bind_uid = Window.bind(on_key_down=_on_key_down)

    def _unbind_keys(self):
        try:
            Window.unbind_uid("on_key_down", self._bind_uid)
        except Exception:
            pass

    # ==================== public API ====================

    def play_audio(self, video_url: str, title: str = "", channel: str = "",
                   duration_or_thumb=None, thumb: str | None = None, *, clear_playlist=True):
        _clear = bool(clear_playlist)
        self._last_video_url = video_url

        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass

        if isinstance(duration_or_thumb, str) and duration_or_thumb.startswith("http") and thumb is None:
            thumb = duration_or_thumb

        if _clear:
            self.playlist[:] = []
            self.playlist_idx = 0

        self._playback_desired = True
        self._user_paused = False

        self._title = title or ""
        self._channel = channel or ""
        self._thumb = thumb or ""
        self._sync_ui_loading()

        self._load_gen += 1
        my_gen = self._load_gen
        self._bg_endguard_fired_gen = -1

        if self._thumb:
            self._download_art_async(self._thumb)

        try:
            ma.set_media_metadata(title=self._title, artist=self._channel, art_uri=self._thumb or "")
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing",
                subtitle=self._channel or "YouTube",
                is_playing=True,
                session_token=token,
                large_icon_path=self._art_path
            )
        except Exception:
            pass

        fast = self._url_cache.get(video_url)
        if fast and fast.get("audio_url"):
            if not fast.get("expire_ts") or (int(time.time()) + 120) < int(fast["expire_ts"]):
                self._stream_url = fast["audio_url"]
                self._headers = dict(fast.get("headers") or {})
                self._expire_ts = fast.get("expire_ts")
                threading.Thread(target=lambda: self._start_from_known_stream(my_gen), daemon=True).start()
                return
            else:
                self._url_cache.pop(video_url, None)

        threading.Thread(target=lambda: self._extract_and_start_gen(video_url, my_gen), daemon=True).start()

        Clock.schedule_once(self._align_video_to_thumb, 0.5)

    def play_playlist(self, tracks, maybe2=None, *, start_index=0, clear_playlist=True):
        if isinstance(maybe2, bool):
            _clear = maybe2
            _start = start_index
        elif isinstance(maybe2, int):
            _clear = clear_playlist
            _start = maybe2
        else:
            _clear = clear_playlist
            _start = start_index

        norm = []
        for item in tracks or []:
            if isinstance(item, (list, tuple)) and item:
                u = str(item[0] or "")
                t = str(item[1]) if len(item) > 1 and item[1] is not None else ""
                c = str(item[2]) if len(item) > 2 and item[2] is not None else ""
            elif isinstance(item, dict):
                u = str(item.get("url") or item.get("id") or item.get("video_id") or "")
                t = str(item.get("title") or "")
                c = str(item.get("channel") or "")
            else:
                u = str(item or "")
                t = ""
                c = ""
            if u and not u.startswith("http"):
                u = f"https://www.youtube.com/watch?v={u}"
            norm.append((u, t, c))

        if _clear:
            self.playlist = norm
        else:
            self.playlist.extend(norm)

        self.playlist_idx = _start if 0 <= _start < len(self.playlist) else 0

        if self.playlist:
            u, t, c = self.playlist[self.playlist_idx]
            self.play_audio(u, t, c, clear_playlist=False)

    # ==================== helpers ====================

    def _download_art_async(self, url: str):
        if not url or not url.startswith(("http://", "https://")):
            return

        def _job():
            try:
                fn = os.path.join(tempfile.gettempdir(), "pymusic_art.jpg")
                urllib.request.urlretrieve(url, fn)
                self._art_path = fn
                try:
                    ma.set_media_metadata(
                        title=self._title, artist=self._channel,
                        art_path=self._art_path, art_uri=self._thumb
                    )
                except Exception:
                    pass
                try:
                    token = self._media_session.getSessionToken() if self._media_session else None
                    ma.create_or_update_media_notification(
                        title=self._title or "Playing",
                        subtitle=self._channel or "YouTube",
                        is_playing=bool(ma.android_player and ma.android_player.isPlaying()),
                        session_token=token,
                        large_icon_path=self._art_path
                    )
                except Exception:
                    pass
            except Exception:
                pass

        threading.Thread(target=_job, daemon=True).start()

    def _is_current_gen(self, gen: int) -> bool:
        return gen == self._load_gen

    def _pre_start_cleanup(self):
        try:
            if self._update_ev:
                Clock.unschedule(self._tick)
                self._update_ev = None
            if self._refresh_ev:
                self._refresh_ev.cancel()
                self._refresh_ev = None
        except Exception:
            pass
        try:
            ma._mp_reset_release()
            ma.release_wake_lock()
        except Exception:
            pass

    # ==================== fast path start ====================

    def _start_from_known_stream(self, gen: int | None = None):
        gen = self._load_gen if gen is None else gen
        if not self._is_current_gen(gen):
            return

        self._pre_start_cleanup()

        try:
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing audio",
                subtitle=self._channel or "YouTube",
                is_playing=True,
                session_token=token,
                large_icon_path=self._art_path
            )
        except Exception:
            pass

        try:
            jheaders = ydlh.py_headers_to_javamap(self._headers, ma.HashMap) if self._headers else None
        except Exception:
            jheaders = None

        def _on_prepared(mp):
            if not self._is_current_gen(gen) or not self._playback_desired:
                return
            try:
                ma.acquire_wake_lock()
                self._request_af()
                ma._mp_start()
                try:
                    dur = ma.android_player.getDuration() if ma.android_player else 0
                    ma.set_media_metadata(
                        title=self._title, artist=self._channel,
                        duration_ms=int(dur or 0),
                        art_path=self._art_path, art_uri=self._thumb
                    )
                    ma.update_media_session_state(True, position_ms=0, duration_ms=int(dur or 0), can_seek=True)
                    token2 = self._media_session.getSessionToken() if self._media_session else None
                    ma.create_or_update_media_notification(
                        title=self._title or "Playing",
                        subtitle=self._channel or "YouTube",
                        is_playing=True,
                        session_token=token2,
                        large_icon_path=self._art_path
                    )
                except Exception:
                    pass

                Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
                self._schedule_progress()
                self._schedule_expiry()

                self._auto_video_for_current(gen)
            except Exception as e:
                ma.log(f"on_prepared(err fast): {e}")

        def _on_complete():
            if not self._is_current_gen(gen):
                return
            try:
                if self._video_player:
                    self._video_player.stop()
            except Exception:
                pass

            if self.repeat:
                self._restart_same()
                return
            if self.playlist and len(self.playlist) > 1:
                self._act_next()
                return
            self._playback_desired = False
            self._user_paused = True
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)

        def _on_error(what, extra):
            if not self._is_current_gen(gen):
                return True
            if self._last_video_url:
                self._extract_and_start_gen(self._last_video_url, gen)
            else:
                Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
            return True

        if hasattr(ma, "_mp_create_set_source_and_prepare_async"):
            ma._mp_create_set_source_and_prepare_async(
                self._stream_url, jheaders,
                _on_prepared, _on_complete,
                lambda w, e: _on_error(w, e),
                lambda w, e: None
            )
        else:
            ma._mp_create(self._stream_url, _on_prepared, _on_complete, lambda w, e: _on_error(w, e))

    def _auto_video_for_current(self, gen: int):
        if not self._is_current_gen(gen) or not self._playback_desired:
            return
        if not self._last_video_url:
            return
        if not self._ensure_video_player():
            return

        def _job():
            try:
                info_v = ydlh.extract_video_info(self._last_video_url)
                vurl = info_v.get("video_url") or info_v.get("url")
                print("[VIDEO] got vurl:", vurl)
                if not vurl:
                    return
                if not self._is_current_gen(gen) or not self._playback_desired:
                    return
                if self._video_player:
                    Clock.schedule_once(self._align_video_to_thumb, 0)
                    self._video_player.play(vurl, loop=False)
            except Exception as e:
                try:
                    print("[VIDEO] auto fail:", e)
                except Exception:
                    pass

        threading.Thread(target=_job, daemon=True).start()

    # ==================== extract & start ====================

    def _extract_and_start_gen(self, video_url: str, gen: int | None = None):

        if gen is None:
            gen = self._load_gen

        if not self._is_current_gen(gen):
            return

        self._extract_and_start(video_url, gen)

    def _extract_and_start(self, video_url: str, gen: int):

        if not self._is_current_gen(gen):
            return

        self._pre_start_cleanup()

        try:
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing audio",
                subtitle=self._channel or "YouTube",
                is_playing=True,
                session_token=token,
                large_icon_path=self._art_path
            )
        except Exception:
            pass

        try:
            info = ydlh.extract_audio_info(video_url)
            if not self._is_current_gen(gen):
                return

            self._stream_url = info["audio_url"]
            self._expire_ts = info.get("expire_ts")
            self._headers = info.get("http_headers", {})

            if not self._thumb:
                self._thumb = info.get("thumb") or ""
                Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)
                if self._thumb:
                    self._download_art_async(self._thumb)

            try:
                ma.set_media_metadata(
                    title=self._title, artist=self._channel, art_uri=self._thumb
                )
            except Exception:
                pass

            self._put_cache(video_url, self._stream_url, self._headers, self._expire_ts)

        except Exception as e:
            ma.log(f"extract fail: {e}")
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
            return

        if self._is_current_gen(gen):
            self._start_from_known_stream(gen)


    def _put_cache(self, url, audio_url, headers, expire_ts):
        try:
            if len(self._url_cache) >= self._URL_CACHE_MAX:
                old_key = next(iter(self._url_cache.keys()))
                self._url_cache.pop(old_key, None)
            self._url_cache[url] = {
                "audio_url": audio_url,
                "headers": dict(headers or {}),
                "expire_ts": expire_ts,
                "ts_put": time.time(),
            }
        except Exception:
            pass

    def _restart_same(self):
        if not self._playback_desired or not self._last_video_url:
            return
        try:
            ma._mp_reset_release()
        except Exception:
            pass
        fast = self._url_cache.get(self._last_video_url)
        if fast and fast.get("audio_url"):
            self._stream_url = fast["audio_url"]
            self._headers = dict(fast.get("headers") or {})
            self._expire_ts = fast.get("expire_ts")
            self._start_from_known_stream(self._load_gen)
        else:
            self._extract_and_start_gen(self._last_video_url, self._load_gen)

    def _request_af(self):
        try:
            am = ma.activity.getSystemService(ma.Context.AUDIO_SERVICE)
            if ma.Build_VERSION.SDK_INT >= 26:
                aa = (ma.AudioAttributesBuilder()
                    .setUsage(ma.AudioManager.USAGE_MEDIA)
                    .setContentType(ma.AudioManager.CONTENT_TYPE_MUSIC)
                    .build())
                afr = ma.AudioFocusRequestBuilder(ma.AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(aa).build()
                am.requestAudioFocus(afr)
            else:
                am.requestAudioFocus(None, ma.AudioManager.STREAM_MUSIC, ma.AudioManager.AUDIOFOCUS_GAIN)
        except Exception:
            pass

    # ==================== UI wiring ====================

    def _sync_ui_loaded(self):
        try:
            self.ids.audio_title.text = self._title or ""
            self.ids.audio_thumbnail.source = self._thumb or ""
            self.ids.current_time_label.text = "0:00"
            self.ids.total_time_label.text = "0:00"
            self.ids.progress_slider.value = 0
            self.ids.progress_slider.max = 1
            self.ids.repeat_btn.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
        except Exception:
            pass

    def _sync_ui_loading(self):
        Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)

    def _sync_thumb_now(self):
        try:
            if self._thumb:
                try:
                    Cache.remove("kv.image", self.ids.audio_thumbnail.source)
                except Exception:
                    pass
                src = self._thumb
                if src.startswith("http"):
                    sep = "&" if "?" in src else "?"
                    src = f"{src}{sep}ts={int(time.time())}"
                self.ids.audio_thumbnail.source = src
        except Exception:
            pass

    def _ui_set_playing(self, playing: bool):
        try:
            btn = self.ids.play_pause_btn
            if hasattr(btn, "icon") and btn.icon:
                btn.icon = "ico/icopausebutton.png" if playing else "ico/icoplaybutton.png"
            elif hasattr(btn, "source"):
                btn.source = "ico/icopausebutton.png" if playing else "ico/icoplaybutton.png"
            else:
                btn.text = "⏸ Pause" if playing else "▶ Play"
        except Exception:
            pass

        try:
            pos = ma.android_player.getCurrentPosition() if (ma.android_player and ma._is_prepared()) else 0
            dur = ma.android_player.getDuration() if (ma.android_player and ma._is_prepared()) else None
            ma.update_media_session_state(
                bool(playing),
                position_ms=int(pos or 0),
                duration_ms=(int(dur) if dur else None),
                can_seek=True
            )
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing audio",
                subtitle=self._channel or "YouTube",
                is_playing=bool(playing),
                session_token=token,
                large_icon_path=self._art_path
            )
        except Exception:
            pass

    # ==================== progress / expiry ====================

    def _schedule_progress(self):
        if self._update_ev:
            Clock.unschedule(self._tick)
        self._update_ev = Clock.schedule_interval(self._tick, 0.5)

    def _tick(self, dt):
        if not ma.android_player:
            return
        try:
            pos = ma.android_player.getCurrentPosition() or 0
            dur = ma.android_player.getDuration() or 1
            self.ids.current_time_label.text = self._fmt_ms(pos)
            self.ids.total_time_label.text = self._fmt_ms(dur)
            self.ids.progress_slider.max = int(dur / 1000)
            self.ids.progress_slider.value = int(pos / 1000)
        except Exception:
            try:
                pos = ma.android_player.getCurrentPosition() or 0
                dur = ma.android_player.getDuration() or 1
            except Exception:
                return

        try:
            if (dur > 0 and (dur - pos) <= 1500 and
                self._playback_desired and not self._user_paused and
                    self._bg_endguard_fired_gen != self._load_gen):
                self._bg_endguard_fired_gen = self._load_gen
                if self.repeat:
                    self._restart_same()
                elif self.playlist and len(self.playlist) > 1:
                    self._act_next()
                else:
                    self._playback_desired = False
                    self._ui_set_playing(False)
                return
        except Exception:
            pass

        try:
            ma.update_media_session_state(
                bool(ma.android_player and ma.android_player.isPlaying()),
                position_ms=int(pos or 0),
                duration_ms=int(dur or 0),
                can_seek=True
            )
        except Exception:
            pass

    def _schedule_expiry(self):
        if not self._expire_ts:
            return
        now = int(time.time())
        dt = max(5, self._expire_ts - now - 60)
        self._refresh_ev = Clock.schedule_once(
            lambda _: self._extract_and_start_gen(self._last_video_url, self._load_gen),
            dt
        )

    # ==================== buttons ====================

    def toggle_play_pause(self, *a):
        if not self._debounce():
            return
        if ma.android_player and ma.android_player.isPlaying():
            self._user_paused = True
            self._playback_desired = False
            ma._mp_pause()
            self._ui_set_playing(False)
        else:
            self._user_paused = False
            self._playback_desired = True
            try:
                ma._mp_start()
                self._ui_set_playing(True)
            except Exception:
                if self._last_video_url:
                    self._extract_and_start_gen(self._last_video_url, self._load_gen)

    def toggle_repeat(self, *a):
        self.repeat = not self.repeat
        try:
            self.ids.repeat_btn.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
        except Exception:
            pass

    def seek(self, value):
        try:
            ma._mp_seek_to(int(value * 1000))
        except Exception:
            pass

    # ==================== navigation ====================

    def go_back(self, *a):
        self.stop_audio()
        try:
            self.manager.current = "search"
        except Exception:
            pass

    # ==================== media actions ====================

    def _act_next(self, *a):
        if not self._debounce(0.15):
            return
        if not self.playlist:
            return
        self.playlist_idx = (self.playlist_idx + 1) % len(self.playlist)
        u, t, c = self.playlist[self.playlist_idx]
        self.play_audio(u, t, c, clear_playlist=False)

    def _act_prev(self, *a):
        if not self._debounce(0.15):
            return
        if not self.playlist:
            return
        try:
            cur = ma.android_player.getCurrentPosition() if ma.android_player else 0
        except Exception:
            cur = 0
        if cur and cur > 5000:
            try:
                ma._mp_seek_to(0)
                return
            except Exception:
                pass
        self.playlist_idx = (self.playlist_idx - 1) % len(self.playlist)
        u, t, c = self.playlist[self.playlist_idx]
        self.play_audio(u, t, c, clear_playlist=False)

    def _act_play(self, *a):
        self._last_media_ts = time.time()
        if not (ma.android_player and ma.android_player.isPlaying()):
            self.toggle_play_pause()

    def _act_pause(self, *a):
        self._last_media_ts = time.time()
        if ma.android_player and ma.android_player.isPlaying():
            self.toggle_play_pause()

    def _act_toggle(self, *a):
        self._last_media_ts = time.time()
        self.toggle_play_pause()

    # ==================== stop / cleanup ====================

    def stop_audio(self, *a):
        self._playback_desired = False
        try:
            if self._update_ev:
                Clock.unschedule(self._tick)
                self._update_ev = None
            if self._refresh_ev:
                self._refresh_ev.cancel()
                self._refresh_ev = None
        except Exception:
            pass
        try:
            ma._mp_reset_release()
            ma.release_wake_lock()
        except Exception:
            pass

        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass

        try:
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Stopped",
                subtitle=self._channel or "",
                is_playing=False,
                session_token=token,
                large_icon_path=self._art_path
            )
        except Exception:
            pass

        self._ui_set_playing(False)

    # ==================== utils ====================

    def _fmt_ms(self, ms):
        s = int(ms / 1000)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _debounce(self, delay: float = 0.25) -> bool:
        now = time.time()
        if now - self._last_click < delay:
            return False
        self._last_click = now
        return True
