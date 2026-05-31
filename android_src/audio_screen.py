from __future__ import annotations

from kivy.uix.screenmanager import Screen
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.cache import Cache
from kivy.resources import resource_find
from kivy.app import App
from kivy.metrics import dp
from kivy.factory import Factory
from kivymd.uix.list import OneLineListItem, TwoLineListItem

import threading
import time
import os
import re
import hashlib
import urllib.request
import urllib.parse
import tempfile
import ssl

import media_android as ma
import ytdlp_helpers as ydlh
from headset_listener import headset_router
from recent_utils import (
    load_recent,
    save_recent,
    update_recent_cache,
    update_recent_art,
    is_favorite,
    upsert_favorite,
    remove_favorite,
    update_favorite_cache,
)

# новий імпорт замість локального класу
from video_player import AndroidVideoPlayer


# ==================== PLAYLIST MODEL ====================

class Playlist:
    """
    Нормальний плейлист:
      - зберігає треки у вигляді dict:
        {"url", "title", "channel", "thumb"}
      - вміє нормалізувати різні формати ззовні (tuple, dict, просто id)
      - інкапсулює поточний індекс і переходи next/prev
    """

    def __init__(self) -> None:
        self.tracks: list[dict] = []
        self.index: int = 0
        self.name: str = ""

    def __len__(self) -> int:
        return len(self.tracks)

    def __bool__(self) -> bool:
        return bool(self.tracks)

    def clear(self) -> None:
        self.tracks.clear()
        self.index = 0
        self.name = ""

    @staticmethod
    def _normalize_img_url(url: str) -> str:
        u = str(url or "").strip()
        if u.startswith("//"):
            return f"https:{u}"
        return u

    @staticmethod
    def _video_id_from_url(url: str) -> str:
        try:
            from urllib.parse import urlparse, parse_qs
            u = str(url or "")
            p = urlparse(u)
            q = parse_qs(p.query or "")
            vid = (q.get("v") or [""])[0]
            if vid:
                return vid
            if "youtu.be" in (p.netloc or ""):
                return (p.path or "").lstrip("/")
        except Exception:
            pass
        return ""

    @staticmethod
    def _normalize_video_id(value: str) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        # Якщо це вже чистий youtube id.
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
            return s
        # Якщо прилетів URL або сміття - пробуємо витягнути id.
        vid = Playlist._video_id_from_url(s)
        if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return vid
        m = re.search(r"(?:v=|/vi/|youtu\.be/)([A-Za-z0-9_-]{11})", s)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _video_id_from_thumb_url(thumb_url: str) -> str:
        s = str(thumb_url or "")
        m = re.search(r"/vi/([A-Za-z0-9_-]{11})/", s)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _normalize_video_url(url: str) -> str:
        u = str(url or "").strip()
        if not u:
            return ""
        if u.startswith("//"):
            return f"https:{u}"
        if u.startswith("/watch") or u.startswith("/shorts/") or u.startswith("/live/"):
            return f"https://www.youtube.com{u}"
        if u.startswith("watch?"):
            return f"https://www.youtube.com/{u}"
        if u.startswith("youtu.be/") or u.startswith("www.youtube.com/") or u.startswith("youtube.com/"):
            return f"https://{u}"
        if u.startswith("http://") or u.startswith("https://"):
            return u
        # Інакше вважаємо, що це youtube id.
        return f"https://www.youtube.com/watch?v={u}"

    @staticmethod
    def _normalize_item(item) -> dict:
        """
        Приводить будь-який формат елемента до dict:
        {"url", "title", "channel", "thumb", "duration"}
        """
        url = ""
        title = ""
        channel = ""
        thumb = ""
        duration = ""
        video_id = ""

        if isinstance(item, (list, tuple)) and item:
            url = str(item[0] or "")
            if len(item) > 1 and item[1] is not None:
                title = str(item[1])
            if len(item) > 2 and item[2] is not None:
                channel = str(item[2])
            if len(item) > 3 and item[3] is not None:
                thumb = str(item[3])
            if len(item) > 4 and item[4] is not None:
                duration = str(item[4])
            if len(item) > 5 and item[5] is not None:
                video_id = str(item[5])
        elif isinstance(item, dict):
            url = str(
                item.get("url")
                or item.get("id")
                or item.get("video_id")
                or ""
            )
            title = str(item.get("title") or "")
            channel = str(item.get("channel") or item.get("uploader") or "")
            thumb = str(item.get("thumb") or item.get("thumbnail") or "")
            duration = str(item.get("duration") or item.get("duration_string") or "")
            video_id = str(item.get("video_id") or "")
        else:
            url = str(item or "")

        url = Playlist._normalize_video_url(url)
        thumb = Playlist._normalize_img_url(thumb)
        video_id = Playlist._normalize_video_id(video_id)
        if not video_id:
            video_id = Playlist._normalize_video_id(url)
        if not video_id:
            video_id = Playlist._video_id_from_thumb_url(thumb)
        if not thumb and video_id:
            thumb = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

        return {
            "url": url,
            "title": title,
            "channel": channel,
            "thumb": thumb,
            "duration": duration,
            "video_id": video_id,
        }

    def set_tracks(
        self,
        tracks,
        name: str = "",
        start_index: int = 0,
        extend: bool = False,
    ) -> None:
        """
        Оновити плейлист:
          - tracks: iterable елементів у будь-якому форматі
          - name: назва плейлиста (для майбутнього UI, якщо треба)
          - start_index: з якого елемента починати
          - extend: якщо True - дописати до існуючого, якщо False - замінити
        """
        norm = [self._normalize_item(x) for x in (tracks or [])]

        if extend and self.tracks:
            self.tracks.extend(norm)
        else:
            self.tracks = norm

        if self.tracks:
            self.index = start_index if 0 <= start_index < len(self.tracks) else 0
        else:
            self.index = 0

        if name:
            self.name = name

    def current(self) -> dict | None:
        if not self.tracks:
            return None
        return self.tracks[self.index]

    def next(self) -> dict | None:
        if not self.tracks:
            return None
        self.index = (self.index + 1) % len(self.tracks)
        return self.current()

    def prev(self) -> dict | None:
        if not self.tracks:
            return None
        self.index = (self.index - 1) % len(self.tracks)
        return self.current()


class AudioPlayerScreen(Screen):
    """Аудіо через Android MediaPlayer, відео через AndroidVideoPlayer (SurfaceView)."""

    _ms_next = None
    _ms_prev = None
    _ms_toggle = None
    _ms_play = None
    _ms_pause = None
    _ms_repeat = None

    def __init__(self, **kw):
        super().__init__(**kw)
        # Ensure media callbacks are always callable (QS/headset/notification).
        self._ms_next = self._act_next
        self._ms_prev = self._act_prev
        self._ms_toggle = self._act_toggle
        self._ms_play = self._act_play
        self._ms_pause = self._act_pause
        self._ms_repeat = self._act_repeat
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

        # тепер повноцінний обʼєкт замість "сирого" списку + індекс
        self.playlist = Playlist()

        self._last_click = 0.0
        self._last_media_ts = 0.0
        self._bind_uid = None

        self._load_gen = 0
        self._bg_endguard_fired_gen = -1

        self._video_player: AndroidVideoPlayer | None = None

        # 🔹 нові поля
        self._buffer_watchdog_ev = None
        self._buffer_watchdog_ts = 0.0
        self._last_watch_pos = None

        self._resume_pos_ms = 0      # позиція для резюму після падіння/паузи
        self._video_enabled = True   # відео вмикається автоматично
        self._bg_tick_ev = Clock.schedule_interval(self._background_tick, 1.0)
        self._audio_cache_inflight = set()
        self._last_good_dur_ms = 0
        self._bad_dur_hits = 0
        self._last_stream_recover_ts = 0.0
        self._is_scrubbing = False
        self._last_video_sync_ts = 0.0
        self._bg_stall_since = 0.0
        self._video_active = False
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1
        self._playlist_url = None
        self._playlist_refreshing = False
        self._playlist_last_refresh_ts = 0.0
        self._auto_skip = True
        self._views_text = ""
        self._related_items = []
        self._channel_thumb = ""
        self._channel_thumb_local = ""
        self._meta_inflight = False
        self._meta_last_url = None
        self._favorite = False
        self._video_controls_visible = False
        self._video_controls_ev = None
        self._last_bg_resume_ts = 0.0
        self._app_in_background = False
        self._prefetch_inflight = set()
        self._stream_error_hits = 0
        self._last_stream_error_ts = 0.0
        self._prefer_compat_audio = False
        self._recover_loop_url = ""
        self._recover_loop_hits = 0
        self._recover_loop_ts = 0.0
        self._playlist_ui_sig = None
        self._playlist_ui_last_ts = 0.0
        self._playlist_thumb_inflight = set()
        self._playlist_thumb_waiters = {}
        self._playlist_thumb_sem = threading.BoundedSemaphore(4)
        self._fav_cache_inflight = set()

    # ==================== lifecycle ====================

    def _ensure_video_player(self) -> bool:
        if self._video_player is not None:
            return True
        try:
            self._video_player = AndroidVideoPlayer()
            self._video_player.set_tap_callback(
                lambda zone="center": Clock.schedule_once(lambda dt: self._handle_video_surface_tap(zone), 0)
            )
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
        try:
            slider = self.ids.progress_slider
            slider.bind(on_touch_down=lambda inst, touch: self._set_scrubbing(True) if inst.collide_point(*touch.pos) else None)
            slider.bind(on_touch_up=lambda inst, touch: self._set_scrubbing(False))
        except Exception:
            pass

    def _set_scrubbing(self, active: bool):
        self._is_scrubbing = bool(active)

    def _audio_pos_ms(self) -> int:
        try:
            if ma.android_player:
                return int(ma.android_player.getCurrentPosition() or 0)
        except Exception:
            pass
        return 0

    def _is_screen_active(self) -> bool:
        try:
            if self.manager is not None and self.manager.current != self.name:
                return False
        except Exception:
            pass
        try:
            app = App.get_running_app()
            root = getattr(app, "root", None)
            sm = getattr(root, "sm", None)
            if sm is not None and sm.current != self.name:
                return False
        except Exception:
            pass
        return True

    def on_pre_enter(self):
        self._app_in_background = False
        try:
            ma.request_post_notifications_permission()
        except Exception:
            pass
        ma.create_notification_channel()
        try:
            ma.webview_hide()
        except Exception:
            pass

        self._ensure_media_session()

        self._ms_next = self._act_next
        self._ms_prev = self._act_prev
        self._ms_toggle = self._act_toggle
        self._ms_play = self._act_play
        self._ms_pause = self._act_pause
        self._ms_repeat = self._act_repeat
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
            if self._media_session:
                is_playing = False
                try:
                    is_playing = bool(ma.android_player and ma.android_player.isPlaying())
                except Exception:
                    is_playing = False
                if not (is_playing or self._playback_desired):
                    self._media_session.setActive(False)
        except Exception:
            pass

        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass
        Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

    def handle_app_pause(self):
        self._app_in_background = True
        self._last_bg_resume_ts = time.time()
        try:
            self._video_was_active = bool(self._video_active)
            if self._video_active:
                self._video_resume_url = self._last_video_url
                self._video_resume_gen = int(self._load_gen)
                self._set_video_mode(False)
            else:
                self._video_resume_url = None
                self._video_resume_gen = -1
        except Exception:
            pass

    def handle_app_resume(self):
        self._app_in_background = False
        self._last_bg_resume_ts = time.time()
        self._last_watch_pos = None
        self._buffer_watchdog_ts = time.time()
        try:
            if (
                self._video_was_active
                and self._video_resume_url
                and self._video_resume_url == self._last_video_url
                and int(self._video_resume_gen) == int(self._load_gen)
            ):
                self._video_was_active = False
                if self._last_video_url and self._video_enabled:
                    threading.Thread(
                        target=lambda: self._auto_video_for_current(self._load_gen, sync_start=False),
                        daemon=True,
                    ).start()
            else:
                self._video_was_active = False
            if (
                self._playback_desired
                and not self._user_paused
                and self._video_enabled
                and self._last_video_url
                and not self._video_active
            ):
                threading.Thread(
                    target=lambda: self._auto_video_for_current(self._load_gen, sync_start=False),
                    daemon=True,
                ).start()
        except Exception:
            pass

    def _align_video_to_thumb(self, *args):
        """
        Виставляє SurfaceView у межах прев'ю. Сам SurfaceView потім
        зменшується під реальний aspect-ratio відео в AndroidVideoPlayer.
        """
        try:
            if not self._video_player:
                return

            win_w, win_h = Window.size
            if win_w <= 0 or win_h <= 0:
                return

            # ТЕПЕР: через media_android, а не через локальний PythonActivity
            activity = ma.PythonActivity.mActivity
            try:
                metrics = activity.getResources().getDisplayMetrics()
                screen_w = int(metrics.widthPixels)
                screen_h = int(metrics.heightPixels)
            except Exception:
                screen_w = self._video_player.screen_w_px or 1080
                screen_h = self._video_player.screen_h_px or 1920

            # коефіцієнти переходу з координат Kivy у px екрана Android
            kx = screen_w / float(win_w)
            ky = screen_h / float(win_h)

            thumb = self.ids.get("audio_thumbnail")
            if not thumb:
                return

            # позиція превʼю у вікні Kivy (0,0 - знизу зліва)
            wx, wy = thumb.to_window(thumb.x, thumb.y, relative=False)
            ww = thumb.width
            wh = thumb.height

            full_height_px = int(wh * ky)

            height_px = max(0, full_height_px)
            if height_px < int(40 * ky):  # мінімальна висота відео
                height_px = int(40 * ky)

            width_px = int(ww * kx)

            # горизонтально - по центру екрана
            left_px = max(0, int((screen_w - width_px) / 2))

            # верхня межа превʼю в координатах вікна Kivy:
            top_win = wy + wh
            # відстань від верху екрана Android
            top_px = int((win_h - top_win) * ky)

            # кламп по екрану
            if width_px > screen_w:
                width_px = screen_w
            if height_px > screen_h:
                height_px = screen_h
            if top_px < 0:
                top_px = 0
            if top_px + height_px > screen_h:
                top_px = max(0, screen_h - height_px)

            print(
                "[VIDEO] align thumb win:",
                "win_size=",
                win_w,
                win_h,
                "thumb=",
                wx,
                wy,
                ww,
                wh,
                "screen=",
                screen_w,
                screen_h,
                "bounds_px=",
                left_px,
                top_px,
                width_px,
                height_px,
            )

            self._video_player.set_bounds(left_px, top_px, width_px, height_px)
        except Exception as e:
            print("[VIDEO] align err:", e)

    def toggle_video_controls(self):
        self._set_video_controls_visible(not self._video_controls_visible)

    def _handle_video_surface_tap(self, zone: str = "center"):
        zone = str(zone or "center")
        if not self._video_controls_visible:
            self._set_video_controls_visible(True)
            return
        if zone == "left":
            self.video_seek(-10)
        elif zone == "right":
            self.video_seek(10)
        else:
            self.video_toggle_play()
        self._set_video_controls_visible(True)

    def _set_video_controls_visible(self, visible: bool, auto_hide: bool = True):
        self._video_controls_visible = bool(visible)
        try:
            controls = self.ids.get("video_controls")
            if controls:
                controls.opacity = 1 if visible else 0
                controls.disabled = not visible
        except Exception:
            pass
        try:
            bar = self.ids.get("progress_bar")
            if bar:
                bar.opacity = 1 if visible else 0
                bar.disabled = not visible
        except Exception:
            pass
        try:
            if self._video_player and hasattr(self._video_player, "set_native_controls_visible"):
                self._video_player.set_native_controls_visible(bool(visible))
        except Exception:
            pass
        try:
            if self._video_controls_ev:
                self._video_controls_ev.cancel()
                self._video_controls_ev = None
        except Exception:
            pass
        try:
            # Перераховуємо bounds оверлею, щоб не перекривати сенсорну зону контролів.
            Clock.schedule_once(self._align_video_to_thumb, 0)
        except Exception:
            pass
        if visible and auto_hide:
            self._video_controls_ev = Clock.schedule_once(self._hide_video_controls, 5.0)

    def _hide_video_controls(self, dt):
        self._set_video_controls_visible(False, auto_hide=False)

    def video_toggle_play(self):
        self.toggle_play_pause()

    def video_seek(self, delta_sec: int):
        try:
            pos = ma.android_player.getCurrentPosition() if ma.android_player else 0
        except Exception:
            pos = 0
        target = max(0, int(pos + (delta_sec * 1000)))
        try:
            ma._mp_seek_to(target)
            self._resume_pos_ms = target
            if self._video_player and self._video_enabled:
                self._video_player.seek_to(target)
        except Exception:
            pass
        self._set_video_controls_visible(True)

    # ==================== headset ====================
    # TODO дуже багато біндів я думаю їх можна якось оптимізувати
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

    def _background_tick(self, dt):
        # працює навіть у фоні
        try:
            if ma.android_player and ma._is_prepared():
                pos = ma.android_player.getCurrentPosition() or 0
                dur = ma.android_player.getDuration() or 0
                if dur > 0:
                    self._last_good_dur_ms = int(dur)
                elif self._last_good_dur_ms > 0:
                    dur = int(self._last_good_dur_ms)
                is_playing = bool(ma.android_player.isPlaying())
                if self._maybe_handle_track_end(pos, dur):
                    return
                if self._playback_desired and not self._user_paused and not is_playing:
                    now = time.time()
                    # якщо ще не кінець треку — намагаємось м'яко відновити playback у фоні
                    if dur <= 0 or pos < max(0, dur - 2500):
                        if (now - self._last_bg_resume_ts) > 2.0:
                            self._last_bg_resume_ts = now
                            try:
                                ma._mp_start()
                                is_playing = bool(ma.android_player.isPlaying())
                            except Exception:
                                pass
                    if dur <= 0 and pos <= 0:
                        if self._bg_stall_since <= 0:
                            self._bg_stall_since = now
                        elif (now - self._bg_stall_since) > 5.0:
                            self._bg_stall_since = now
                            self._recover_stream("bg_stall", self._resume_pos_ms)
                    else:
                        self._bg_stall_since = 0.0
                else:
                    self._bg_stall_since = 0.0

                ma.update_media_session_state(
                    is_playing,
                    position_ms=int(pos),
                    duration_ms=int(dur),
                    can_seek=True
                )

                token = self._media_session.getSessionToken() if self._media_session else None
                ma.create_or_update_media_notification(
                    title=self._title or "Playing",
                    subtitle=self._channel or "YouTube",
                    is_playing=is_playing,
                    session_token=token,
                    large_icon_path=self._art_path
                )
        except Exception as e:
            print("[BG TICK] err:", e)

    def _maybe_handle_track_end(self, pos: int, dur: int) -> bool:
        try:
            if not (
                dur > 0
                and (dur - pos) <= 1500
                and self._playback_desired
                and not self._user_paused
                and self._bg_endguard_fired_gen != self._load_gen
            ):
                return False
            self._bg_endguard_fired_gen = self._load_gen
            self._resume_pos_ms = 0
            if self.repeat:
                self._restart_same()
                return True
            if self._auto_skip and self._advance_to_next_track():
                return True
            self._playback_desired = False
            self._ui_set_playing(False)
            return True
        except Exception:
            return False

    def _bind_keys(self):
        try:
            from jnius import autoclass
            KeyEvent = autoclass("android.view.KeyEvent")
            media_toggle = {
                int(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE),
                int(KeyEvent.KEYCODE_HEADSETHOOK),
            }
            media_play = int(KeyEvent.KEYCODE_MEDIA_PLAY)
            media_pause = int(KeyEvent.KEYCODE_MEDIA_PAUSE)
            media_next = int(KeyEvent.KEYCODE_MEDIA_NEXT)
            media_prev = int(KeyEvent.KEYCODE_MEDIA_PREVIOUS)
        except Exception:
            media_toggle = {79, 85}
            media_play = 126
            media_pause = 127
            media_next = 87
            media_prev = 88

        def _on_key_down(window, keycode, scancode, text, modifiers):
            code = keycode[0] if isinstance(keycode, (tuple, list)) else keycode
            if code in media_toggle:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down toggle code={code}")
                except Exception:
                    pass
                self._act_toggle()
                return True
            if code == media_play:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down play code={code}")
                except Exception:
                    pass
                self._act_play()
                return True
            if code == media_pause:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down pause code={code}")
                except Exception:
                    pass
                self._act_pause()
                return True
            if code == media_next:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down next code={code}")
                except Exception:
                    pass
                self._act_next()
                return True
            if code == media_prev:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down prev code={code}")
                except Exception:
                    pass
                self._act_prev()
                return True
            if code in (24, 25) and (time.time() - getattr(self, "_last_media_ts", 0.0) < 0.7):
                return True
            return False

        self._bind_uid = Window.bind(on_key_down=_on_key_down)

    def _unbind_keys(self):
        try:
            Window.unbind_uid("on_key_down", self._bind_uid)
        except Exception:
            pass

    # ==================== public API (audio) ====================
# TODO створи клас для відео з всіма полями тайлтлу і тд щоб їх отримувати тут
    def _remember_recent(self, url: str, title: str, channel: str, thumb: str):
        try:
            if not url:
                return
            recent = load_recent()
            entry = {
                "url": url,
                "title": title or "",
                "channel": channel or "",
                "thumb": thumb or "",
            }
            recent = [r for r in recent if r.get("url") != url]
            recent.insert(0, entry)
            save_recent(recent)
        except Exception:
            pass

    def play_audio(
        self,
        video_url: str,
        title: str = "",
        channel: str = "",
        duration_or_thumb=None,
        thumb: str | None = None,
        *,
        clear_playlist=True,
        hard_reset: bool = False,
    ):
        self._ensure_media_session()
        _clear = bool(clear_playlist)
        if hard_reset:
            try:
                self._hard_transition_reset()
            except Exception:
                pass
        elif video_url and video_url != self._last_video_url:
            try:
                self._pre_start_cleanup()
            except Exception:
                pass
        if _clear:
            self._playlist_url = None
        self._last_video_url = video_url
        self._remember_recent(video_url, title, channel, str(thumb or ""))
        self._favorite = bool(is_favorite(video_url))
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1
        self._video_active = False
        self._stream_error_hits = 0
        self._last_stream_error_ts = 0.0
        self._prefer_compat_audio = False
        self._views_text = ""
        self._related_items = []
        self._render_similar_ui()

        # 🔹 новий трек - не відновлюємось з позиції попереднього
        self._resume_pos_ms = 0

        # зупиняємо відео, якщо було
        try:
            if self._video_player:
                self._video_player.stop()
            self._set_video_mode(False)
        except Exception:
            pass

        # якщо duration_or_thumb - це URL мініатюри
        if isinstance(duration_or_thumb, str) and duration_or_thumb.startswith("http") and thumb is None:
            thumb = duration_or_thumb

        if _clear:
            self.playlist.clear()
            self._render_playlist_ui(force=True)
            self._related_items = []
            self._views_text = ""
            self._channel_thumb = ""
            self._channel_thumb_local = ""
            self._render_similar_ui()

        self._playback_desired = True
        self._user_paused = False

        self._title = title or ""
        self._channel = channel or ""
        self._channel_thumb = ""
        self._channel_thumb_local = ""
        thumb_url = thumb or ""
        self._thumb = thumb_url
        if self._last_video_url:
            cached_art = self._find_cached_art(self._last_video_url)
            if cached_art:
                self._art_path = cached_art
                self._thumb = cached_art
        self._sync_ui_loading()
        Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)

        # оновлюємо покоління завантаження
        self._load_gen += 1
        my_gen = self._load_gen
        self._bg_endguard_fired_gen = -1

        if thumb_url and thumb_url.startswith(("http://", "https://")):
            self._download_art_async(thumb_url)
        elif self._thumb and self._thumb.startswith(("http://", "https://")):
            self._download_art_async(self._thumb)

        # оновлення метаданих та нотифікації
        try:
            ma.set_media_metadata(
                title=self._title,
                artist=self._channel,
                art_uri=self._thumb or "",
            )
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing",
                subtitle=self._channel or "YouTube",
                is_playing=True,
                session_token=token,
                large_icon_path=self._art_path,
            )
        except Exception:
            pass

        # пробуємо локальний кеш (офлайн)
        if self._try_start_cached_audio(video_url, my_gen):
            Clock.schedule_once(self._align_video_to_thumb, 0.5)
            self._ensure_metadata_async(video_url)
            return

        # пробуємо взяти URL з кешу
        fast = self._url_cache.get(video_url)
        if fast and fast.get("audio_url"):
            # перевірка на протухання URL
            if not fast.get("expire_ts") or (int(time.time()) + 120) < int(fast["expire_ts"]):
                self._stream_url = fast["audio_url"]
                self._headers = dict(fast["headers"] or {})
                self._expire_ts = fast.get("expire_ts")
                threading.Thread(
                    target=lambda: self._start_from_known_stream(my_gen),
                    daemon=True,
                ).start()
                Clock.schedule_once(self._align_video_to_thumb, 0.5)
                self._ensure_metadata_async(video_url)
                return
            else:
                # видаляємо старий запис
                self._url_cache.pop(video_url, None)

        # якщо в кеші нема або протух - витягуємо свіжий URL через yt_dlp
        threading.Thread(
            target=lambda: self._extract_and_start_gen(video_url, my_gen),
            daemon=True,
        ).start()

        # підлаштувати рамку відео під превʼю (навіть якщо відео зараз вимкнене)
        Clock.schedule_once(self._align_video_to_thumb, 0.5)
        self._ensure_metadata_async(video_url)

    def play_playlist(
        self,
        tracks,
        maybe2=None,
        *,
        start_index=0,
        clear_playlist=True,
        playlist_url=None,
    ):
        """
        Старт плейлиста:
          play_playlist(tracks, playlist_title)
          play_playlist(tracks, start_index=3)
          play_playlist(tracks, False, start_index=3)  # не чистити, а дописати
        """
        playlist_name = ""

        if isinstance(maybe2, bool):
            _clear = maybe2
            _start = start_index
        elif isinstance(maybe2, int):
            _clear = clear_playlist
            _start = maybe2
        elif isinstance(maybe2, str):
            _clear = clear_playlist
            _start = start_index
            playlist_name = maybe2
        else:
            _clear = clear_playlist
            _start = start_index

        self.playlist.set_tracks(
            tracks,
            name=playlist_name,
            start_index=_start,
            extend=not _clear,
        )
        if playlist_url:
            self._playlist_url = playlist_url
        self._render_playlist_ui(force=True)

        current = self.playlist.current()
        if current:
            self.play_audio(
                current["url"],
                current["title"],
                current["channel"],
                current.get("thumb") or "",
                clear_playlist=False,
            )

    # ==================== helpers ====================

    def _download_art_async(self, url: str): 
        if not url or not url.startswith(("http://", "https://")):
            return

        def _job():
            try:
                art_path = None
                if self._last_video_url:
                    art_path = self._art_cache_path(self._last_video_url)
                if not art_path:
                    art_path = os.path.join(tempfile.gettempdir(), "pymusic_art.jpg")
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
                        "Referer": "https://www.youtube.com",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Connection": "keep-alive",
                    },
                )
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=12, context=ctx) as resp, open(art_path, "wb") as f:
                    f.write(resp.read())
                self._art_path = art_path
                if self._last_video_url:
                    update_recent_art(self._last_video_url, art_path)
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

    def _channel_avatar_cache_path(self, channel_thumb_url: str) -> str:
        key = self._cache_key(channel_thumb_url or self._channel or "channel")
        return os.path.join(self._art_cache_dir(), f"ch_{key}.jpg")

    def _download_channel_avatar_async(self, url: str):
        if not url or not url.startswith(("http://", "https://")):
            return

        def _job():
            try:
                path = self._channel_avatar_cache_path(url)
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
                        "Referer": "https://www.youtube.com",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept": "image/jpeg,image/png,image/*;q=0.9,*/*;q=0.8",
                        "Connection": "keep-alive",
                    },
                )
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp, open(path, "wb") as f:
                    f.write(resp.read())
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    self._channel_thumb_local = path
                    try:
                        ma.log(f"[META] avatar saved: {path}")
                    except Exception:
                        pass
                    Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)
            except Exception as e:
                try:
                    ma.log(f"[META] avatar download fail: {e}")
                except Exception:
                    pass

        threading.Thread(target=_job, daemon=True).start()

    def _playlist_thumb_cache_path(self, thumb_url: str) -> str:
        key = self._cache_key(f"pl:{thumb_url}")
        return os.path.join(self._art_cache_dir(), f"pl_{key}.jpg")

    def _set_playlist_thumb(self, img_widget, thumb_url: str):
        src = Playlist._normalize_img_url(str(thumb_url or ""))
        if not src.startswith(("http://", "https://")):
            try:
                img_widget.source = ""
            except Exception:
                pass
            return

        path = self._playlist_thumb_cache_path(src)
        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                img_widget.source = path
                try:
                    img_widget.reload()
                except Exception:
                    pass
                return
        except Exception:
            pass

        try:
            img_widget.source = ""
        except Exception:
            pass

        try:
            self._playlist_thumb_waiters.setdefault(path, []).append(img_widget)
            if path in self._playlist_thumb_inflight:
                return
            self._playlist_thumb_inflight.add(path)
        except Exception:
            pass

        def _apply(_dt=None):
            waiters = []
            try:
                waiters = self._playlist_thumb_waiters.pop(path, [])
            except Exception:
                waiters = []
            for widget in waiters:
                try:
                    widget.source = path
                    try:
                        widget.reload()
                    except Exception:
                        pass
                except Exception:
                    pass

        def _job():
            try:
                with self._playlist_thumb_sem:
                    tmp = f"{path}.tmp"
                    req = urllib.request.Request(
                        src,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
                            "Referer": "https://www.youtube.com",
                            "Accept-Language": "en-US,en;q=0.9",
                            "Accept": "image/jpeg,image/png,image/*;q=0.9,*/*;q=0.8",
                            "Connection": "keep-alive",
                        },
                    )
                    ctx = ssl._create_unverified_context()
                    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp, open(tmp, "wb") as f:
                        f.write(resp.read())
                    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                        os.replace(tmp, path)
                    else:
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
                if os.path.exists(path) and os.path.getsize(path) > 0:
                    Clock.schedule_once(_apply, 0)
            except Exception:
                pass
            finally:
                try:
                    self._playlist_thumb_inflight.discard(path)
                except Exception:
                    pass

        threading.Thread(target=_job, daemon=True).start()

    def _ensure_media_session(self):
        try:
            if not self._media_session:
                self._media_session = ma.MediaSession(
                    ma.PythonActivity.mActivity,
                    "PyMusicSession",
                )
                try:
                    flags = (
                        ma.MediaSession.FLAG_HANDLES_MEDIA_BUTTONS
                        | ma.MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS
                    )
                    self._media_session.setFlags(flags)
                except Exception:
                    pass
            self._media_session.setActive(True)
            ma.register_media_session(self._media_session)
            ma.configure_session_audio()
            ma.set_media_session_callback(self)
            ma.bind_notification_action_router(self)
        except Exception:
            pass

    def _render_playlist_ui(self, force: bool = False):
        try:
            lst = self.ids.get("playlist_list")
            scroll = self.ids.get("playlist_scroll")
            header = self.ids.get("playlist_header")
            if not lst:
                return
            sig = None
            try:
                if self.playlist and self.playlist.tracks:
                    sig = tuple(
                        (
                            str(t.get("url") or ""),
                            str(t.get("video_id") or ""),
                        )
                        for t in self.playlist.tracks
                    )
            except Exception:
                sig = None
            # Не перебудовуємо однаковий плейлист повторно, це збиває прев'ю.
            if (not force) and sig is not None and sig == self._playlist_ui_sig:
                return
            self._playlist_ui_sig = sig
            self._playlist_ui_last_ts = time.time()
            lst.clear_widgets()
            if not self.playlist or not self.playlist.tracks:
                if header:
                    header.height = 0
                    header.opacity = 0
                if scroll:
                    scroll.height = 0
                    scroll.opacity = 0
                return
            if header:
                header.height = dp(28)
                header.opacity = 1
                header.text = self.playlist.name or "Черга"
            if scroll:
                scroll.height = dp(240)
                scroll.opacity = 1
            for idx, item in enumerate(self.playlist.tracks):
                title = (item.get("title") or "").strip()
                channel = (item.get("channel") or "").strip()
                if not title:
                    title = item.get("url") or "Track"
                row = Factory.PlaylistTrackItem()
                row.ids.pt_title.text = f"{idx + 1}. {title}"
                row.ids.pt_channel.text = channel
                thumb = str(item.get("thumb") or "")
                vid = Playlist._normalize_video_id(str(item.get("video_id") or ""))
                if not vid:
                    vid = Playlist._normalize_video_id(str(item.get("url") or ""))
                if not vid:
                    vid = Playlist._video_id_from_thumb_url(thumb)
                # Для плейлиста беремо легший static thumb без sqp/rs.
                if vid:
                    thumb = f"https://img.youtube.com/vi/{vid}/mqdefault.jpg"
                if thumb:
                    self._set_playlist_thumb(row.ids.pt_thumb, thumb)
                row.ids.pt_duration.text = str(item.get("duration") or "")
                row.bind(on_release=lambda _inst, i=idx: self._play_from_playlist_index(i))
                lst.add_widget(row)
        except Exception:
            pass

    def _render_similar_ui(self):
        try:
            header = self.ids.get("similar_header")
            scroll = self.ids.get("similar_scroll")
            lst = self.ids.get("similar_list")
            if not lst:
                return
            lst.clear_widgets()
            if not self._related_items:
                if header:
                    header.height = 0
                    header.opacity = 0
                if scroll:
                    scroll.height = 0
                    scroll.opacity = 0
                return
            if header:
                header.height = dp(28)
                header.opacity = 1
            if scroll:
                scroll.height = dp(220)
                scroll.opacity = 1
            for idx, item in enumerate(self._related_items):
                title = (item.get("title") or "").strip()
                channel = (item.get("channel") or "").strip()
                if not title:
                    title = item.get("url") or "Video"
                row = Factory.SimilarItem()
                row.ids.similar_title.text = title
                row.ids.similar_channel.text = channel
                thumb = item.get("thumb") or ""
                if thumb:
                    row.ids.similar_thumb.source = thumb
                row.bind(on_release=lambda _inst, i=idx: self._play_from_related_index(i))
                lst.add_widget(row)
        except Exception:
            pass

    def _ensure_metadata_async(self, video_url: str):
        if not video_url:
            return
        if self._meta_inflight and self._meta_last_url == video_url:
            return
        self._meta_inflight = True
        self._meta_last_url = video_url

        def _job():
            try:
                info = ydlh.extract_audio_info(video_url)
                if video_url != self._last_video_url:
                    return
                self._apply_info_metadata(info)
            except Exception:
                pass
            finally:
                self._meta_inflight = False

        threading.Thread(target=_job, daemon=True).start()

    def _apply_info_metadata(self, info: dict):
        updated = False
        title = info.get("title") or ""
        channel = info.get("channel") or info.get("uploader") or ""
        try:
            ma.log(f"[META] info title={title!r} channel={channel!r} cthumb={(info.get('channel_thumb') or '')!r}")
        except Exception:
            pass
        if title and (not self._title or self._title.lower() == "playing"):
            self._title = title
            updated = True
        if channel and (not self._channel or self._channel.lower() in {"youtube", "unknown"}):
            self._channel = channel
            updated = True
        cthumb = info.get("channel_thumb") or info.get("uploader_thumb") or ""
        if isinstance(cthumb, str) and cthumb.startswith("//"):
            cthumb = f"https:{cthumb}"
        if cthumb:
            self._channel_thumb = cthumb
            self._download_channel_avatar_async(cthumb)
            updated = True
        vc = info.get("view_count")
        if vc:
            self._views_text = self._fmt_views(vc)
            updated = True
        related = info.get("related_videos") or []
        if related:
            self._related_items = self._normalize_related(related)
            Clock.schedule_once(lambda dt: self._render_similar_ui(), 0)
        if updated:
            Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)

    def _play_from_playlist_index(self, idx: int):
        if not self.playlist or not self.playlist.tracks:
            return
        if idx < 0 or idx >= len(self.playlist.tracks):
            return
        self.playlist.index = idx
        track = self.playlist.current()
        if not track:
            return
        self.play_audio(
            track["url"],
            track.get("title") or "",
            track.get("channel") or "",
            track.get("thumb") or "",
            clear_playlist=False,
            hard_reset=True,
        )

    def _play_from_related_index(self, idx: int):
        if not self._related_items:
            return
        if idx < 0 or idx >= len(self._related_items):
            return
        item = self._related_items[idx]
        self.play_audio(
            item.get("url") or "",
            item.get("title") or "",
            item.get("channel") or "",
            item.get("thumb") or "",
            clear_playlist=True,
        )

    def _art_cache_dir(self) -> str:
        base_dir = None
        try:
            app = App.get_running_app()
            base_dir = getattr(app, "user_data_dir", None)
        except Exception:
            base_dir = None
        if not base_dir:
            base_dir = tempfile.gettempdir()
        cache_dir = os.path.join(base_dir, "pymusic_art_cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass
        return cache_dir

    def _art_cache_path(self, video_url: str) -> str:
        key = self._cache_key(video_url)
        return os.path.join(self._art_cache_dir(), f"{key}.jpg")

    def _find_cached_art(self, video_url: str) -> str | None:
        if not video_url:
            return None
        try:
            recent = load_recent()
            for r in recent:
                if r.get("url") == video_url:
                    p = str(r.get("art_path") or "")
                    if p and os.path.exists(p) and os.path.getsize(p) > 0:
                        return p
        except Exception:
            pass
        try:
            path = self._art_cache_path(video_url)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
        except Exception:
            pass
        return None

    def _is_current_gen(self, gen: int) -> bool:
        return gen == self._load_gen

    def _stop_buffer_watchdog(self):
        try:
            if self._buffer_watchdog_ev:
                self._buffer_watchdog_ev.cancel()
                self._buffer_watchdog_ev = None
        except Exception:
            pass

    def _start_buffer_watchdog(self):
        try:
            self._buffer_watchdog_ts = time.time()
            self._last_watch_pos = None
            self._stop_buffer_watchdog()
            self._buffer_watchdog_ev = Clock.schedule_interval(self._buffer_watchdog, 3.0)
        except Exception:
            pass
# TODO буфер ламає відтворення відео(через repeat) і ламає роботу з плейлистами
    def _buffer_watchdog(self, dt):
        """
        Якщо позиція програвання не змінюється деякий час при активному відтворенні -
        швидше за все потік завис через проблеми з інтернетом, тоді запускаємо
        мʼякий рестарт із повторним витягуванням URL.
        """
        if self._app_in_background or (time.time() - float(self._last_bg_resume_ts or 0.0)) < 6.0:
            return
        if not self._playback_desired or self._user_paused:
            return
        if not ma.android_player:
            return
        try:
            pos = ma.android_player.getCurrentPosition() or 0
        except Exception:
            return

        now = time.time()
        last_pos = self._last_watch_pos if self._last_watch_pos is not None else -1

        if pos == last_pos:
            # якщо більше 8 секунд стоїмо на одному місці - робимо restart
            if now - self._buffer_watchdog_ts > 8.0:
                print("[AUDIO] Watchdog: stream stuck, restarting...")
                self._buffer_watchdog_ts = now
                # збережемо позицію для відновлення
                try:
                    self._resume_pos_ms = pos
                except Exception:
                    self._resume_pos_ms = 0
                if self._last_video_url:
                    threading.Thread(
                        target=lambda: self._extract_and_start_gen(self._last_video_url, self._load_gen),
                        daemon=True,
                    ).start()
        else:
            self._buffer_watchdog_ts = now

        self._last_watch_pos = pos

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
        self._stop_buffer_watchdog()
        try:
            ma._mp_reset_release()
            ma.release_wake_lock()
        except Exception:
            pass

    def _hard_transition_reset(self):
        self._playback_desired = False
        self._user_paused = False
        self._bg_stall_since = 0.0
        self._bad_dur_hits = 0
        self._resume_pos_ms = 0
        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass
        try:
            self._set_video_mode(False)
        except Exception:
            pass
        self._pre_start_cleanup()

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
            self._stream_error_hits = 0
            self._last_stream_error_ts = 0.0
            # не стартуємо відразу, лише базові метадані
            try:
                ma.set_media_metadata(
                    title=self._title,
                    artist=self._channel,
                    art_path=self._art_path,
                    art_uri=self._thumb,
                )
            except Exception:
                pass

            # або стартуємо з відео, або тільки аудіо, залежно від кнопки vid
            if self._video_enabled and not self._app_in_background:
                # швидкий старт аудіо, відео підвантажуємо паралельно
                threading.Thread(
                    target=lambda: self._start_audio_only_after_prepared(gen),
                    daemon=True,
                ).start()
                threading.Thread(
                    target=lambda: self._auto_video_for_current(gen, sync_start=False),
                    daemon=True,
                ).start()
            else:
                threading.Thread(
                    target=lambda: self._start_audio_only_after_prepared(gen),
                    daemon=True,
                ).start()

        def _on_complete():
            if not self._is_current_gen(gen):
                return
            try:
                if self._video_player:
                    self._video_player.stop()
            except Exception:
                pass

            # нормальне завершення - не треба резюму
            self._resume_pos_ms = 0

            if self.repeat:
                self._restart_same()
                return
            if self._auto_skip:
                self._queue_auto_next()
                return
            self._playback_desired = False
            self._user_paused = True
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)

        def _on_error(what, extra):
            print(f"[AUDIO] MediaPlayer error: what={what}, extra={extra}")

            if not self._is_current_gen(gen):
                return True

            now = time.time()
            if (now - self._last_stream_error_ts) > 5.0:
                self._stream_error_hits = 0
            self._stream_error_hits += 1
            self._last_stream_error_ts = now

            # збережемо поточну позицію перед перезапуском
            try:
                if ma.android_player:
                    self._resume_pos_ms = ma.android_player.getCurrentPosition() or 0
            except Exception:
                pass

            # типові помилки нестабільного інтернету
            NETWORK_ERRORS = (-1004, -110, 1)

            if what in NETWORK_ERRORS or extra in NETWORK_ERRORS:
                # Частий кейс проблемних треків: Unknown error(1,-2147483648).
                # Після кількох помилок форсимо сумісний AAC/M4A профіль.
                if self._stream_error_hits >= 3 and not self._prefer_compat_audio:
                    self._prefer_compat_audio = True
                    print("[AUDIO] switch to compat audio profile (m4a/aac)")
                if self._stream_error_hits >= 8:
                    print("[AUDIO] too many stream errors, skip/stop track")
                    if self._auto_skip:
                        self._queue_auto_next()
                    else:
                        self._playback_desired = False
                        self._user_paused = True
                        Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0.1)
                    return True
                print("[AUDIO] Network stream failed, re-extracting fresh URL...")
                if self._last_video_url:
                    if self._try_start_cached_audio(self._last_video_url, gen):
                        return True
                    self._recover_stream("media_error", self._resume_pos_ms)
                return True

            # якщо інша помилка - мʼякий рестарт з того ж потоку
            print("[AUDIO] Unknown error, soft restart...")
            Clock.schedule_once(lambda dt: self._start_from_known_stream(gen), 0.2)
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

    def _audio_cache_dir(self) -> str:
        base_dir = None
        try:
            app = App.get_running_app()
            base_dir = getattr(app, "user_data_dir", None)
        except Exception:
            base_dir = None
        if not base_dir:
            base_dir = tempfile.gettempdir()
        cache_dir = os.path.join(base_dir, "pymusic_audio_cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass
        return cache_dir

    def _cache_key(self, video_url: str) -> str:
        return hashlib.md5((video_url or "").encode("utf-8")).hexdigest()

    def _guess_audio_ext(self, audio_url: str) -> str:
        try:
            path = urllib.parse.urlparse(audio_url).path or ""
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            if ext in {"m4a", "webm", "mp4", "mp3", "aac", "ogg"}:
                return ext
        except Exception:
            pass
        return "audio"

    def _audio_cache_path(self, video_url: str, ext: str | None = None) -> str:
        ext = ext or "audio"
        key = self._cache_key(video_url)
        return os.path.join(self._audio_cache_dir(), f"{key}.{ext}")

    def _find_cached_audio(self, video_url: str) -> str | None:
        if not video_url:
            return None
        try:
            recent = load_recent()
            for r in recent:
                if r.get("url") == video_url:
                    p = str(r.get("cache_path") or "")
                    if p and os.path.exists(p) and os.path.getsize(p) > 0:
                        return p
        except Exception:
            pass
        try:
            cache_dir = self._audio_cache_dir()
            key = self._cache_key(video_url)
            for name in os.listdir(cache_dir):
                if name == key or name.startswith(f"{key}."):
                    path = os.path.join(cache_dir, name)
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        return path
        except Exception:
            pass
        return None

    def _download_audio_locally(self, video_url: str, audio_url: str, headers: dict | None = None) -> str | None:
        if not audio_url or audio_url.startswith("file://") or "m3u8" in audio_url:
            return None

        ext = self._guess_audio_ext(audio_url)
        path = self._audio_cache_path(video_url, ext)

        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
        except Exception:
            pass

        headers = headers or {}
        req_headers = {
            "User-Agent": headers.get("User-Agent") or "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
            "Referer": headers.get("Referer") or "https://www.youtube.com",
            "Accept-Language": headers.get("Accept-Language") or "en-US,en;q=0.9",
            "Connection": headers.get("Connection") or "keep-alive",
        }

        tmp_path = f"{path}.part"
        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(audio_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp, open(tmp_path, "wb") as f:
                while True:
                    chunk = resp.read(512 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp_path, path)
            update_recent_cache(video_url, path)
            update_favorite_cache(video_url, path)
            return path
        except Exception as e:
            try:
                print("[AUDIO] cache download fail:", e)
            except Exception:
                pass
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return None

    def _cache_audio_async(self, video_url: str, audio_url: str, headers: dict | None = None):
        if not video_url or not audio_url:
            return
        key = self._cache_key(video_url)
        if key in self._audio_cache_inflight:
            return
        if self._find_cached_audio(video_url):
            return

        self._audio_cache_inflight.add(key)

        def _job():
            try:
                self._download_audio_locally(video_url, audio_url, headers)
            finally:
                self._audio_cache_inflight.discard(key)

        threading.Thread(target=_job, daemon=True).start()

    def _try_start_cached_audio(self, video_url: str, gen: int | None = None) -> bool:
        cached = self._find_cached_audio(video_url)
        if not cached:
            return False
        self._stream_url = cached if cached.startswith("file://") else f"file://{cached}"
        self._headers = {}
        self._expire_ts = None
        threading.Thread(
            target=lambda: self._start_from_known_stream(gen),
            daemon=True,
        ).start()
        return True
# TODO треба скинути всю роботу з відео в video_player.py тут тільки виклики базові і все
    def _download_video_locally(self, video_url: str, headers: dict | None = None) -> str | None:
        """
        Скачує відеофайл у тимчасову теку та повертає шлях або None при помилці.
        (Залишено як резервний варіант.)
        """
        if not video_url or video_url.startswith("file://") or "m3u8" in video_url:
            return None

        headers = headers or {}
        cache_dir = os.path.join(tempfile.gettempdir(), "pymusic_video_cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass

        safe_name = hashlib.md5(video_url.encode("utf-8")).hexdigest() + ".mp4"
        path = os.path.join(cache_dir, safe_name)

        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
        except Exception:
            pass

        req_headers = {
            "User-Agent": headers.get("User-Agent") or "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
            "Referer": headers.get("Referer") or "https://www.youtube.com",
            "Accept-Language": headers.get("Accept-Language") or "en-US,en;q=0.9",
            "Connection": headers.get("Connection") or "keep-alive",
        }

        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(video_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp, open(path, "wb") as f:
                while True:
                    chunk = resp.read(512 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            return path if os.path.exists(path) and os.path.getsize(path) > 0 else None
        except Exception as e:
            try:
                print("[VIDEO] download fail:", e)
            except Exception:
                pass
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            return None

    def _auto_video_for_current(self, gen: int, sync_start: bool = False):
        """Автоматичний показ YouTube-відео для поточного треку."""
        if self._app_in_background or not self._is_screen_active():
            return
        if not self._is_current_gen(gen) or not self._playback_desired:
            return
        if not self._last_video_url:
            return
        if not self._ensure_video_player():
            return
        if getattr(self._video_player, "is_embed", False):
            if not self._video_enabled:
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
                if sync_start:
                    self._start_audio_only_after_prepared(gen)
                return
            if not self._is_screen_active():
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
                return
            Clock.schedule_once(lambda dt: self._set_video_mode(True), 0)
            Clock.schedule_once(lambda dt: self._play_embed_if_screen_active(), 0)
            if sync_start:
                self._start_audio_only_after_prepared(gen)
            return

        # якщо користувач вимкнув відео - не тягнемо потік
        if not self._video_enabled:
            Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
            if sync_start:
                self._start_audio_only_after_prepared(gen)
            return

        def _job():
            try:
                if self._app_in_background or not self._is_screen_active():
                    return
                info_v = ydlh.safe_extract_video_info(self._last_video_url)
                if not info_v:
                    print("[VIDEO] no video info from yt_dlp")
                    Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
                    if sync_start:
                        self._start_audio_only_after_prepared(gen)
                    return

                vurl = info_v.get("video_url") or info_v.get("url")
                vheaders = info_v.get("http_headers") or {}
                print("[VIDEO] got vurl:", vurl)
                if not vurl:
                    Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
                    if sync_start:
                        self._start_audio_only_after_prepared(gen)
                    return
                if not self._is_current_gen(gen) or not self._playback_desired or not self._is_screen_active():
                    return

                vurl_final = vurl
                vheaders_final = vheaders

                if self._video_player and getattr(self._video_player, "_video_cache_enabled", False):
                    if vurl.startswith("http") and "m3u8" not in vurl:
                        local_path = self._download_video_locally(vurl, headers=vheaders)
                        if local_path:
                            print("[VIDEO] using buffered local file:", local_path)
                            vurl_final = local_path
                            vheaders_final = {}
                        else:
                            print("[VIDEO] buffering failed, streaming remote url")

                if not self._is_current_gen(gen) or not self._playback_desired or not self._is_screen_active():
                    return

                if not self._video_enabled or not self._is_screen_active():
                    Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
                    if sync_start:
                        self._start_audio_only_after_prepared(gen)
                    return

                # відео є -> ховаємо мініатюру
                Clock.schedule_once(lambda dt: self._set_video_mode(True), 0)

                if sync_start:
                    self._start_synced_audio_and_video(gen, vurl_final, vheaders_final or {})
                else:
                    if self._video_player:
                        Clock.schedule_once(self._align_video_to_thumb, 0)
                        Clock.schedule_once(
                            lambda dt: self._play_video_if_screen_active(vurl_final, vheaders_final or {}),
                            0,
                        )
            except Exception as e:
                try:
                    print("[VIDEO] auto fail:", e)
                except Exception:
                    pass
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

        threading.Thread(target=_job, daemon=True).start()

    def _play_embed_if_screen_active(self):
        if not self._is_screen_active() or self._app_in_background or not self._video_enabled:
            self._set_video_mode(False)
            return
        try:
            if self._video_player:
                self._video_player.play(self._last_video_url, start_pos_provider=self._audio_pos_ms)
        except Exception as e:
            try:
                print("[VIDEO] embed play err:", e)
            except Exception:
                pass
            self._set_video_mode(False)

    def _play_video_if_screen_active(self, vurl: str, vheaders: dict):
        if not self._is_screen_active() or self._app_in_background or not self._video_enabled:
            self._set_video_mode(False)
            return
        if self._video_player:
            self._video_player.play(
                vurl,
                headers=(vheaders or {}),
                loop=False,
                start_pos_provider=self._audio_pos_ms,
            )

    def _start_synced_audio_and_video(self, gen: int, vurl: str, vheaders: dict):
        """Старт аудіо і відео максимально одночасно."""
        if not self._is_current_gen(gen) or not self._playback_desired or not self._is_screen_active():
            return

        if not self._ensure_video_player():
            self._start_audio_only_after_prepared(gen)
            return

        try:
            ma.acquire_wake_lock()
            self._request_af()
        except Exception:
            pass

        try:
            Clock.schedule_once(self._align_video_to_thumb, 0)
        except Exception:
            pass

        try:
            if self._video_player:
                self._play_video_if_screen_active(vurl, vheaders or {})
        except Exception as e:
            try:
                print("[VIDEO] synced start error:", e)
            except Exception:
                pass

        try:
            dur = ma.android_player.getDuration() if ma.android_player else 0

            # якщо ми відновлюємось - стрибнемо в останню позицію (не дуже близько до кінця)
            try:
                if self._resume_pos_ms and dur and self._resume_pos_ms < (dur - 5000):
                    ma._mp_seek_to(int(self._resume_pos_ms))
                    print(f"[AUDIO] resume from {self._resume_pos_ms} ms")
            except Exception:
                pass

            ma._mp_start()

            try:
                ma.set_media_metadata(
                    title=self._title,
                    artist=self._channel,
                    duration_ms=int(dur or 0),
                    art_path=self._art_path,
                    art_uri=self._thumb,
                )
            except Exception:
                pass

            try:
                ma.update_media_session_state(
                    True,
                    position_ms=0,
                    duration_ms=int(dur or 0),
                    can_seek=True,
                )
                token2 = self._media_session.getSessionToken() if self._media_session else None
                ma.create_or_update_media_notification(
                    title=self._title or "Playing",
                    subtitle=self._channel or "YouTube",
                    is_playing=True,
                    session_token=token2,
                    large_icon_path=self._art_path,
                )
            except Exception:
                pass

            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
            self._schedule_progress()
            self._schedule_expiry()
            self._prefetch_next_track_audio()
        except Exception as e:
            ma.log(f"sync start err: {e}")

    def _start_audio_only_after_prepared(self, gen: int):
        """Фолбек: якщо відео не вдалось або вимкнене - стартуємо тільки аудіо."""
        if not self._is_current_gen(gen) or not self._playback_desired:
            return
        try:
            ma.acquire_wake_lock()
            self._request_af()

            dur = ma.android_player.getDuration() if ma.android_player else 0

            # відновлення з останньої позиції, якщо не майже кінець
            try:
                if self._resume_pos_ms and dur and self._resume_pos_ms < (dur - 5000):
                    ma._mp_seek_to(int(self._resume_pos_ms))
                    print(f"[AUDIO] resume (audio-only) from {self._resume_pos_ms} ms")
            except Exception:
                pass

            ma._mp_start()
            try:
                if not (self._video_enabled and getattr(self._video_player, "is_embed", False)):
                    Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
            except Exception:
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

            try:
                ma.set_media_metadata(
                    title=self._title,
                    artist=self._channel,
                    duration_ms=int(dur or 0),
                    art_path=self._art_path,
                    art_uri=self._thumb,
                )
            except Exception:
                pass

            try:
                ma.update_media_session_state(
                    True,
                    position_ms=0,
                    duration_ms=int(dur or 0),
                    can_seek=True,
                )
                token2 = self._media_session.getSessionToken() if self._media_session else None
                ma.create_or_update_media_notification(
                    title=self._title or "Playing",
                    subtitle=self._channel or "YouTube",
                    is_playing=True,
                    session_token=token2,
                    large_icon_path=self._art_path,
                )
            except Exception:
                pass

            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
            self._schedule_progress()
            self._schedule_expiry()
            self._prefetch_next_track_audio()
        except Exception as e:
            ma.log(f"audio-only start err: {e}")

    def _prefetch_next_track_audio(self):
        if not self.playlist or len(self.playlist) < 2:
            return
        try:
            next_idx = (int(self.playlist.index) + 1) % len(self.playlist.tracks)
            nxt = self.playlist.tracks[next_idx]
            next_url = str(nxt.get("url") or "")
        except Exception:
            return
        if not next_url or next_url == self._last_video_url:
            return
        if next_url in self._prefetch_inflight:
            return
        fast = self._url_cache.get(next_url)
        if fast and fast.get("audio_url"):
            return

        self._prefetch_inflight.add(next_url)

        def _job():
            try:
                info = ydlh.extract_audio_info(next_url)
                aurl = info.get("audio_url") or ""
                headers = info.get("http_headers") or {}
                exp = info.get("expire_ts")
                if aurl:
                    self._put_cache(next_url, aurl, headers, exp)
                    self._cache_audio_async(next_url, aurl, headers)
            except Exception:
                pass
            finally:
                self._prefetch_inflight.discard(next_url)

        threading.Thread(target=_job, daemon=True).start()

    # ==================== extract & start ====================
# TODO в нас дуже багато подібних екстаріктів і gen оптимізуй їх
    def _extract_and_start_gen(self, video_url: str, gen: int | None = None, *, prefer_compat: bool | None = None):

        if gen is None:
            gen = self._load_gen

        if not self._is_current_gen(gen):
            return

        self._extract_and_start(video_url, gen, prefer_compat=prefer_compat)

    def _extract_and_start(self, video_url: str, gen: int, *, prefer_compat: bool | None = None):

        if not self._is_current_gen(gen):
            return

        use_compat = self._prefer_compat_audio if prefer_compat is None else bool(prefer_compat)

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
            info = ydlh.extract_audio_info(video_url, prefer_compat=use_compat)
            if not self._is_current_gen(gen):
                return

            self._stream_url = info["audio_url"]
            self._expire_ts = info.get("expire_ts")
            self._headers = info.get("http_headers", {})

            updated_meta = False
            if not self._title:
                t = info.get("title") or ""
                if t:
                    self._title = t
                    updated_meta = True
            if not self._channel:
                ch = info.get("channel") or info.get("uploader") or ""
                if ch:
                    self._channel = ch
                    updated_meta = True
            cthumb = info.get("channel_thumb") or info.get("uploader_thumb") or ""
            if isinstance(cthumb, str) and cthumb.startswith("//"):
                cthumb = f"https:{cthumb}"
            if cthumb:
                self._channel_thumb = cthumb
                self._download_channel_avatar_async(cthumb)
            vc = info.get("view_count")
            if vc:
                self._views_text = self._fmt_views(vc)
            else:
                self._views_text = ""
            related = info.get("related_videos") or []
            self._related_items = self._normalize_related(related)
            Clock.schedule_once(lambda dt: self._render_similar_ui(), 0)

            if not self._thumb:
                self._thumb = info.get("thumb") or ""
                Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)
                if self._thumb:
                    self._download_art_async(self._thumb)
                elif self._last_video_url:
                    cached_art = self._find_cached_art(self._last_video_url)
                    if cached_art:
                        self._art_path = cached_art
                        self._thumb = cached_art
                        Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)

            try:
                try:
                    ma.log(f"[META] extract title={self._title!r} channel={self._channel!r} cthumb={self._channel_thumb!r}")
                except Exception:
                    pass
                ma.set_media_metadata(
                    title=self._title, artist=self._channel, art_uri=self._thumb
                )
            except Exception:
                pass

            if updated_meta:
                Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)

            self._put_cache(video_url, self._stream_url, self._headers, self._expire_ts)
            self._cache_audio_async(video_url, self._stream_url, self._headers)

        except Exception as e:
            ma.log(f"extract fail: {e}")
            if self._last_video_url and self._try_start_cached_audio(self._last_video_url, gen):
                return
            try:
                if not ma.is_network_available():
                    Clock.schedule_once(lambda dt: self.go_back(), 0)
            except Exception:
                pass
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
        # при повному рестарті (repeat) починаємо з 0
        self._resume_pos_ms = 0
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
                      .setUsage(ma.AudioAttributes.USAGE_MEDIA)
                      .setContentType(ma.AudioAttributes.CONTENT_TYPE_MUSIC)
                      .build())
                afr = ma.AudioFocusRequestBuilder(ma.AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(aa).build()
                am.requestAudioFocus(afr)
            else:
                am.requestAudioFocus(None, ma.AudioManager.STREAM_MUSIC, ma.AudioManager.AUDIOFOCUS_GAIN)
        except Exception:
            pass

    def _try_playlist_refresh_next(self) -> bool:
        if self._playlist_refreshing:
            return False
        now = time.time()
        if (now - self._playlist_last_refresh_ts) < 3.0:
            return False

        playlist_url = self._playlist_url
        start_video_id = None

        try:
            from urllib.parse import urlparse, parse_qs

            def _extract_vid(u: str | None) -> str | None:
                if not u:
                    return None
                parsed = urlparse(u)
                q = parse_qs(parsed.query or "")
                vid = (q.get("v") or [None])[0]
                if vid:
                    return vid
                if "youtu.be" in (parsed.netloc or ""):
                    return (parsed.path or "").lstrip("/") or None
                return None

            if not playlist_url and self._last_video_url:
                parsed = urlparse(self._last_video_url)
                q = parse_qs(parsed.query or "")
                playlist_id = (q.get("list") or [None])[0]
                if playlist_id:
                    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                start_video_id = _extract_vid(self._last_video_url)
            else:
                start_video_id = _extract_vid(self._last_video_url)
        except Exception:
            playlist_url = playlist_url or None
            start_video_id = start_video_id or None

        if not playlist_url or not start_video_id:
            return False

        self._playlist_refreshing = True
        self._playlist_last_refresh_ts = now
        Clock.schedule_once(lambda dt: setattr(self, "_playlist_refreshing", False), 2.0)

        try:
            search_screen = self.manager.get_screen("search")
            search_screen.open_playlist(
                playlist_url,
                self.playlist.name or "Черга",
                start_video_id=start_video_id,
                start_after=True,
                fallback_url=self._last_video_url,
            )
            return True
        except Exception:
            return False

    # ==================== UI wiring ====================

    def _sync_ui_loaded(self):
        try:
            self.ids.audio_title.text = self._title or ""
            views_lbl = self.ids.get("audio_views")
            if views_lbl:
                views_lbl.text = self._views_text or ""
            ch_lbl = self.ids.get("audio_channel")
            if ch_lbl:
                ch_lbl.text = self._channel or ""
            self.ids.audio_thumbnail.source = self._thumb or ""
            self.ids.current_time_label.text = "0:00"
            self.ids.total_time_label.text = "0:00"
            self.ids.progress_slider.value = 0
            self.ids.progress_slider.max = 1
            self.ids.repeat_btn.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
            inline_repeat = self.ids.get("repeat_inline_btn")
            if inline_repeat:
                inline_repeat.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
            auto_btn = self.ids.get("autoskip_btn")
            if auto_btn:
                auto_btn.source = "ico/icoauto_on.png" if self._auto_skip else "ico/icoauto_off.png"
            fav_btn = self.ids.get("favorite_btn")
            if fav_btn:
                fav_btn.source = "ico/icofavorite_active.png" if self._favorite else "ico/icofavorite.png"
            # кнопка відео
            vid_btn = self.ids.get("vid")
            if vid_btn:
                vid_btn.icon = "ico/icovid_on.png" if self._video_enabled else "ico/icovid_off.png"
            avatar = self.ids.get("channel_avatar")
            if avatar:
                src = self._channel_thumb_local or self._channel_thumb or ""
                if src:
                    if src.startswith("http"):
                        sep = "&" if "?" in src else "?"
                        src = f"{src}{sep}ts={int(time.time())}"
                    avatar.source = src
                    avatar.opacity = 1
                    try:
                        avatar.reload()
                    except Exception:
                        pass
                else:
                    avatar.source = ""
                    avatar.opacity = 0
                    try:
                        avatar.reload()
                    except Exception:
                        pass
        except Exception:
            pass

        # Оновлення аватара окремо, щоб не губилось через помилки в інших id.
        try:
            avatar = self.ids.get("channel_avatar")
            if avatar:
                src = self._channel_thumb_local or self._channel_thumb or ""
                if src:
                    avatar.source = src
                    avatar.opacity = 1
                    try:
                        avatar.reload()
                    except Exception:
                        pass
                else:
                    avatar.source = ""
                    avatar.opacity = 0
        except Exception:
            pass

        # Не вимикаємо відео при кожному UI refresh.
        # Інакше метадані/оновлення заголовка глушать відеоряд.
        try:
            thumb = self.ids.get("audio_thumbnail")
            if thumb:
                thumb.opacity = 0.0 if self._video_active else 1.0
        except Exception:
            pass
        
# TODO ввідео має бути ПІД слайдером а не над
    def _set_video_mode(self, video_on: bool):
        """
        video_on = True  -> відео поверх, мініатюра прозора
        video_on = False -> тільки мініатюра, без відео-ряду
        """
        if video_on and not self._is_screen_active():
            video_on = False
        self._video_active = bool(video_on)
        try:
            if not video_on:
                ma._mp_set_volume(1.0)
        except Exception:
            pass
        if not video_on:
            self._set_video_controls_visible(False, auto_hide=False)
        try:
            thumb = self.ids.get("audio_thumbnail")
            if thumb:
                thumb.opacity = 0.0 if video_on else 1.0
        except Exception:
            pass
        if not video_on:
            try:
                if self._video_player:
                    self._video_player.stop()
            except Exception:
                pass

    def hide_video_overlay_fast(self):
        self._video_active = False
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1
        try:
            self._set_video_controls_visible(False, auto_hide=False)
        except Exception:
            pass
        try:
            thumb = self.ids.get("audio_thumbnail")
            if thumb:
                thumb.opacity = 1.0
        except Exception:
            pass
        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass

    def _normalize_related(self, items):
        out = []
        for e in items or []:
            if not isinstance(e, dict):
                continue
            url = e.get("url") or e.get("id") or ""
            if url and not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            title = e.get("title") or ""
            channel = e.get("uploader") or e.get("channel") or ""
            thumb = e.get("thumbnail") or ""
            if not url:
                continue
            out.append(
                {
                    "url": url,
                    "title": title,
                    "channel": channel,
                    "thumb": thumb,
                }
            )
        return out

    def _build_default_channel_thumb(self) -> str:
        return ""

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
            vbtn = self.ids.get("video_play_btn")
            if vbtn:
                vbtn.icon = "pause" if playing else "play"
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
        self._start_buffer_watchdog()

    def _recover_stream(self, reason: str, pos: int):
        now = time.time()
        if (now - self._last_stream_recover_ts) < 3.0:
            return
        self._last_stream_recover_ts = now
        self._resume_pos_ms = int(pos or 0)
        print(f"[AUDIO] recover stream ({reason})")

        cur_url = str(self._last_video_url or "")
        if cur_url and cur_url == self._recover_loop_url and (now - self._recover_loop_ts) < 90.0:
            self._recover_loop_hits += 1
        else:
            self._recover_loop_url = cur_url
            self._recover_loop_hits = 1
        self._recover_loop_ts = now

        # Захист від нескінченного циклу ресетів одного й того ж треку.
        if self._recover_loop_hits >= 4:
            print("[AUDIO] recover loop detected, skipping track")
            self._recover_loop_hits = 0
            if self._auto_skip and self._advance_to_next_track():
                return
            self._playback_desired = False
            self._user_paused = True
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
            return

        if self._last_video_url and self._try_start_cached_audio(self._last_video_url, self._load_gen):
            return
        if self._last_video_url:
            threading.Thread(
                target=lambda: self._extract_and_start_gen(self._last_video_url, self._load_gen),
                daemon=True,
            ).start()

    def _tick(self, dt):
        if not ma.android_player:
            return
        try:
            pos = ma.android_player.getCurrentPosition() or 0
            dur = ma.android_player.getDuration() or 0
            if dur > 0:
                self._last_good_dur_ms = dur
                self._bad_dur_hits = 0
            else:
                self._bad_dur_hits += 1
                if self._last_good_dur_ms > 0:
                    dur = self._last_good_dur_ms
            self.ids.current_time_label.text = self._fmt_ms(pos)
            self.ids.total_time_label.text = self._fmt_ms(dur or 0)
            self.ids.progress_slider.max = int((dur or 1) / 1000)
            if not self._is_scrubbing:
                self.ids.progress_slider.value = int(pos / 1000)
        except Exception:
            try:
                pos = ma.android_player.getCurrentPosition() or 0
                dur = ma.android_player.getDuration() or 0
            except Exception:
                return

        if (
            self._bad_dur_hits >= 4
            and self._playback_desired
            and not self._user_paused
            and not self._app_in_background
            and (time.time() - float(self._last_bg_resume_ts or 0.0)) >= 6.0
        ):
            self._recover_stream("duration=0", pos)
            self._bad_dur_hits = 0

        try:
            if self._maybe_handle_track_end(pos, dur):
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

        # Не робимо частий hard seek відео до аудіо: Android MediaPlayer після
        # seekTo() флашить декодер, що виглядає як ресет відеоряду.
        try:
            if (
                self._video_enabled
                and self._video_active
                and self._video_player
                and self._playback_desired
                and not self._user_paused
                and not self._is_scrubbing
                and not self._app_in_background
                and ma.android_player
                and ma._is_prepared()
                and ma.android_player.isPlaying()
            ):
                now = time.time()
                video_pos = None
                try:
                    video_pos = self._video_player.get_current_position()
                except Exception:
                    video_pos = None
                drift = abs(int(pos or 0) - int(video_pos or 0)) if video_pos is not None else 0
                if drift >= 5000 and (now - float(self._last_video_sync_ts or 0.0)) >= 15.0:
                    print(f"[VIDEO] drift resync audio={int(pos or 0)} video={int(video_pos or 0)} drift={drift}")
                    self._video_player.seek_to(int(pos or 0))
                    self._last_video_sync_ts = now
        except Exception:
            pass

    def _schedule_expiry(self):
        if not self._expire_ts:
            return
        now = int(time.time())
        # Не перезапускати надто часто, інакше при короткому TTL отримуємо цикли ресетів.
        dt = max(30, self._expire_ts - now - 60)
        self._refresh_ev = Clock.schedule_once(
            lambda _: self._extract_and_start_gen(self._last_video_url, self._load_gen),
            dt
        )

    # ==================== buttons ====================

    def toggle_play_pause(self, *a):
        if not self._debounce():
            return
        if ma.android_player and ma.android_player.isPlaying():
            self._pause_playback()
        else:
            self._resume_playback()

    def toggle_repeat(self, *a):
        self.repeat = not self.repeat
        try:
            self.ids.repeat_btn.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
            inline_repeat = self.ids.get("repeat_inline_btn")
            if inline_repeat:
                inline_repeat.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
        except Exception:
            pass

    def toggle_autoskip(self, *a):
        self._auto_skip = not self._auto_skip
        try:
            btn = self.ids.get("autoskip_btn")
            if btn:
                btn.source = "ico/icoauto_on.png" if self._auto_skip else "ico/icoauto_off.png"
        except Exception:
            pass

    def toggle_favorite(self, *a):
        url = str(self._last_video_url or "")
        if not url:
            return
        self._favorite = not self._favorite
        try:
            btn = self.ids.get("favorite_btn")
            if btn:
                btn.source = "ico/icofavorite_active.png" if self._favorite else "ico/icofavorite.png"
        except Exception:
            pass
        if self._favorite:
            item = {
                "url": url,
                "title": self._title or "",
                "channel": self._channel or "",
                "thumb": self._thumb or "",
                "art_path": self._art_path or "",
                "added_at": int(time.time()),
            }
            try:
                cached = self._find_cached_audio(url)
                if cached:
                    item["cache_path"] = cached
            except Exception:
                pass
            upsert_favorite(item)
            self._cache_current_favorite_async(url)
        else:
            try:
                cached = self._find_cached_audio(url)
                if cached and os.path.exists(cached):
                    os.remove(cached)
            except Exception:
                pass
            update_recent_cache(url, None)
            update_favorite_cache(url, None)
            remove_favorite(url)

    def _cache_current_favorite_async(self, url: str):
        if not url or url in self._fav_cache_inflight:
            return
        self._fav_cache_inflight.add(url)

        def _job():
            try:
                cached = self._find_cached_audio(url)
                if cached:
                    update_favorite_cache(url, cached)
                    return
                stream_url = str(self._stream_url or "")
                headers = dict(self._headers or {})
                if not stream_url or stream_url.startswith("file://"):
                    try:
                        info = ydlh.extract_audio_info(url, prefer_compat=self._prefer_compat_audio)
                        stream_url = str(info.get("audio_url") or "")
                        headers = dict(info.get("http_headers") or headers)
                    except Exception:
                        stream_url = ""
                if stream_url:
                    path = self._download_audio_locally(url, stream_url, headers)
                    if path:
                        update_favorite_cache(url, path)
            finally:
                self._fav_cache_inflight.discard(url)

        threading.Thread(target=_job, daemon=True).start()

    def seek(self, value):
        try:
            ms = int(value * 1000)
            ma._mp_seek_to(ms)
            self._resume_pos_ms = ms
            try:
                if self._video_player and self._video_enabled:
                    self._video_player.seek_to(ms)
            except Exception:
                pass
        except Exception:
            pass

    def vid(self, *a):
        """Обробка кнопки MDIconButton id: vid"""
        if not self._debounce(0.2):
            return
        self._video_enabled = not self._video_enabled
        try:
            vid_btn = self.ids.get("vid")
            if vid_btn:
                vid_btn.icon = "ico/icovid_on.png" if self._video_enabled else "ico/icovid_off.png"
        except Exception:
            pass

        if self._video_enabled:
            # вмикаємо відео поверх поточного аудіо
            if not self._last_video_url:
                return
            if not self._ensure_video_player():
                return
            Clock.schedule_once(self._align_video_to_thumb, 0)
            threading.Thread(
                target=lambda: self._auto_video_for_current(self._load_gen, sync_start=False),
                daemon=True,
            ).start()
        else:
            # вимикаємо відео, залишаємо тільки аудіо
            try:
                if self._video_player:
                    self._video_player.stop()
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

    # ==================== navigation ====================

    def go_back(self, *a):
        self.stop_audio()
        try:
            from kivy.app import App
            app = App.get_running_app()
            root = getattr(app, "root", None)
            if root and hasattr(root, "go_to_active_tab"):
                root.go_to_active_tab()
            else:
                self.manager.current = "web"
        except Exception:
            pass

    # ==================== media actions ====================

    def _advance_to_next_track(self) -> bool:
        if self.playlist and len(self.playlist) > 1:
            track = self.playlist.next()
            if not track:
                return False
            self.play_audio(
                track["url"],
                track["title"],
                track["channel"],
                track.get("thumb") or "",
                clear_playlist=False,
                hard_reset=True,
            )
            return True
        return self._try_playlist_refresh_next()

    def _queue_auto_next(self) -> bool:
        def _run(_dt):
            if not self._advance_to_next_track():
                self._playback_desired = False
                self._user_paused = True
                self._ui_set_playing(False)
        if self._app_in_background:
            _run(0)
        else:
            Clock.schedule_once(_run, 0)
        return True

    def _act_next(self, *a):
        self._advance_to_next_track()

    def _act_prev(self, *a):
        if not self.playlist:
            return
        try:
            cur = ma.android_player.getCurrentPosition() if ma.android_player else 0
        except Exception:
            cur = 0
        # як і раніше: якщо >5 секунд - просто перемотуємо на початок
        if cur and cur > 5000:
            try:
                ma._mp_seek_to(0)
                self._resume_pos_ms = 0
                return
            except Exception:
                pass

        track = self.playlist.prev()
        if not track:
            return
        self.play_audio(
            track["url"],
            track["title"],
            track["channel"],
            track.get("thumb") or "",
            clear_playlist=False,
            hard_reset=True,
        )

    def _act_play(self, *a):
        self._last_media_ts = time.time()
        if not (ma.android_player and ma.android_player.isPlaying()):
            self._resume_playback()

    def _act_pause(self, *a):
        self._last_media_ts = time.time()
        if ma.android_player and ma.android_player.isPlaying():
            self._pause_playback()

    def _act_toggle(self, *a):
        self._last_media_ts = time.time()
        if ma.android_player and ma.android_player.isPlaying():
            self._pause_playback()
        else:
            self._resume_playback()

    def _act_repeat(self, *a):
        self._last_media_ts = time.time()
        self.toggle_repeat()

    def _pause_playback(self):
        self._user_paused = True
        self._playback_desired = False
        try:
            self._resume_pos_ms = ma.android_player.getCurrentPosition() or 0
        except Exception:
            pass
        try:
            ma._mp_pause()
        except Exception:
            pass
        try:
            if self._video_player:
                self._video_player.pause()
        except Exception:
            pass
        self._ui_set_playing(False)

    def _resume_playback(self):
        self._user_paused = False
        self._playback_desired = True
        try:
            ma._mp_start()
            try:
                if self._video_player and self._video_enabled:
                    self._video_player.resume()
            except Exception:
                pass
            try:
                Clock.schedule_once(lambda dt: self._force_video_resync(), 0.35)
            except Exception:
                pass
            self._ui_set_playing(True)
        except Exception:
            if self._last_video_url:
                self._extract_and_start_gen(self._last_video_url, self._load_gen)

    def _force_video_resync(self):
        try:
            if (
                self._app_in_background
                or not self._video_enabled
                or not self._video_active
                or not self._video_player
                or not self._playback_desired
                or self._user_paused
            ):
                return
            if not (ma.android_player and ma._is_prepared() and ma.android_player.isPlaying()):
                return
            pos = self._audio_pos_ms()
            video_pos = None
            try:
                video_pos = self._video_player.get_current_position()
            except Exception:
                video_pos = None
            if video_pos is not None and abs(int(pos or 0) - int(video_pos or 0)) < 1500:
                return
            if pos >= 0:
                self._video_player.seek_to(int(pos))
                self._last_video_sync_ts = time.time()
        except Exception:
            pass

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
        self._stop_buffer_watchdog()
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

        self._resume_pos_ms = 0
        self._ui_set_playing(False)
        Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

    # ==================== utils ====================

    def _fmt_ms(self, ms):
        s = int(ms / 1000)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _fmt_views(self, count):
        try:
            n = int(count)
        except Exception:
            return ""
        if n >= 1_000_000_000:
            text = f"{n / 1_000_000_000:.1f} млрд переглядів"
        elif n >= 1_000_000:
            text = f"{n / 1_000_000:.1f} млн переглядів"
        elif n >= 1_000:
            text = f"{n / 1_000:.1f} тис. переглядів"
        else:
            text = f"{n} переглядів"
        return text.replace(".", ",")

    def _debounce(self, delay: float = 0.25) -> bool:
        now = time.time()
        if now - self._last_click < delay:
            return False
        self._last_click = now
        return True
