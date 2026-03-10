from __future__ import annotations

from kivy.uix.screenmanager import Screen
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.cache import Cache
from kivy.resources import resource_find
from kivy.app import App

import threading
import time
import os
import hashlib
import urllib.request
import urllib.parse
import tempfile
import ssl

import media_android as ma
import ytdlp_helpers as ydlh
from headset_listener import headset_router
from recent_utils import load_recent, update_recent_cache, update_recent_art

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
    def _normalize_item(item) -> dict:
        """
        Приводить будь-який формат елемента до dict:
        {"url", "title", "channel", "thumb"}
        """
        url = ""
        title = ""
        channel = ""
        thumb = ""

        if isinstance(item, (list, tuple)) and item:
            url = str(item[0] or "")
            if len(item) > 1 and item[1] is not None:
                title = str(item[1])
            if len(item) > 2 and item[2] is not None:
                channel = str(item[2])
            if len(item) > 3 and item[3] is not None:
                thumb = str(item[3])
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
        else:
            url = str(item or "")

        if url and not url.startswith("http"):
            # вважаємо, що це просто videoId
            url = f"https://www.youtube.com/watch?v={url}"

        return {
            "url": url,
            "title": title,
            "channel": channel,
            "thumb": thumb,
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

    def __init__(self, **kw):
        super().__init__(**kw)
        # Ensure media callbacks are always callable (QS/headset/notification).
        self._ms_next = self._act_next
        self._ms_prev = self._act_prev
        self._ms_toggle = self._act_toggle
        self._ms_play = self._act_play
        self._ms_pause = self._act_pause
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
        self._video_active = False
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1

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

    def on_pre_enter(self):
        try:
            ma.request_post_notifications_permission()
        except Exception:
            pass
        ma.create_notification_channel()
        try:
            ma.webview_hide()
        except Exception:
            pass

        # MediaSession
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
        except Exception:
            pass

        self._ms_next = self._act_next
        self._ms_prev = self._act_prev
        self._ms_toggle = self._act_toggle
        self._ms_play = self._act_play
        self._ms_pause = self._act_pause
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
        except Exception:
            pass

    def _align_video_to_thumb(self, *args): # TODO переробити це забрати обмеження фіксації рамки відео щоб воно було таке як є навіть якщо там чорні рамки
        """
        Виставляє SurfaceView так, щоб він співпадав з зоною прев'ю audio_thumbnail,
        але залишав смужку знизу під слайдер (як у YouTube).
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

            # повна висота області превʼю в px
            full_height_px = int(wh * ky)

            # без додаткового резерву - відео має займати весь блок превʼю
            height_px = full_height_px
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

            # ВАЖЛИВО:
            # AndroidVideoPlayer всередині вже займається тим,
            # щоб відео не розтягувалось, а зберігало пропорції з чорними смугами.
            self._video_player.set_bounds(left_px, top_px, width_px, height_px)
        except Exception as e:
            print("[VIDEO] align err:", e)

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
                is_playing = bool(ma.android_player.isPlaying())

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
    def play_audio(
        self,
        video_url: str,
        title: str = "",
        channel: str = "",
        duration_or_thumb=None,
        thumb: str | None = None,
        *,
        clear_playlist=True,
    ):
        _clear = bool(clear_playlist)
        if video_url and video_url != self._last_video_url:
            try:
                self._pre_start_cleanup()
            except Exception:
                pass
        self._last_video_url = video_url
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1
        self._video_active = False

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

        self._playback_desired = True
        self._user_paused = False

        self._title = title or ""
        self._channel = channel or ""
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

    def play_playlist(self, tracks, maybe2=None, *, start_index=0, clear_playlist=True):
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
                urllib.request.urlretrieve(url, art_path)
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
            if self._video_enabled:
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
            if self.playlist and len(self.playlist) > 1:
                self._act_next()
                return
            self._playback_desired = False
            self._user_paused = True
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)

        def _on_error(what, extra):
            print(f"[AUDIO] MediaPlayer error: what={what}, extra={extra}")

            if not self._is_current_gen(gen):
                return True

            # збережемо поточну позицію перед перезапуском
            try:
                if ma.android_player:
                    self._resume_pos_ms = ma.android_player.getCurrentPosition() or 0
            except Exception:
                pass

            # типові помилки нестабільного інтернету
            NETWORK_ERRORS = (-1004, -110, 1)

            if what in NETWORK_ERRORS or extra in NETWORK_ERRORS:
                print("[AUDIO] Network stream failed, re-extracting fresh URL...")
                if self._last_video_url:
                    if self._try_start_cached_audio(self._last_video_url, gen):
                        return True
                    Clock.schedule_once(
                        lambda dt: threading.Thread(
                            target=lambda: self._extract_and_start_gen(self._last_video_url, gen),
                            daemon=True,
                        ).start(),
                        0.2,
                    )
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
            Clock.schedule_once(lambda dt: self._set_video_mode(True), 0)
            try:
                self._video_player.play(self._last_video_url, start_pos_provider=self._audio_pos_ms)
            except Exception as e:
                try:
                    print("[VIDEO] embed play err:", e)
                except Exception:
                    pass
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
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
                if not self._is_current_gen(gen) or not self._playback_desired:
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

                if not self._is_current_gen(gen) or not self._playback_desired:
                    return

                if not self._video_enabled:
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
                        self._video_player.play(
                            vurl_final,
                            headers=(vheaders_final or {}),
                            loop=False,
                            start_pos_provider=self._audio_pos_ms,
                        )
            except Exception as e:
                try:
                    print("[VIDEO] auto fail:", e)
                except Exception:
                    pass
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

        threading.Thread(target=_job, daemon=True).start()
    def _start_synced_audio_and_video(self, gen: int, vurl: str, vheaders: dict):
        """Старт аудіо і відео максимально одночасно."""
        if not self._is_current_gen(gen) or not self._playback_desired:
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
                self._video_player.play(
                    vurl,
                    headers=(vheaders or {}),
                    loop=False,
                    start_pos_provider=self._audio_pos_ms,
                 
                )
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
        except Exception as e:
            ma.log(f"audio-only start err: {e}")

    # ==================== extract & start ====================
# TODO в нас дуже багато подібних екстаріктів і gen оптимізуй їх
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
            # кнопка відео
            vid_btn = self.ids.get("vid")
            if vid_btn:
                vid_btn.icon = "ico/icovid_on.png" if self._video_enabled else "ico/icovid_off.png"
        except Exception:
            pass

        self._set_video_mode(False)
        
# TODO ввідео має бути ПІД слайдером а не над
    def _set_video_mode(self, video_on: bool):
        """
        video_on = True  -> відео поверх, мініатюра прозора
        video_on = False -> тільки мініатюра, без відео-ряду
        """
        self._video_active = bool(video_on)
        try:
            if not video_on:
                ma._mp_set_volume(1.0)
        except Exception:
            pass
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
        self._start_buffer_watchdog()

    def _recover_stream(self, reason: str, pos: int):
        now = time.time()
        if (now - self._last_stream_recover_ts) < 3.0:
            return
        self._last_stream_recover_ts = now
        self._resume_pos_ms = int(pos or 0)
        print(f"[AUDIO] recover stream ({reason})")
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

        if self._bad_dur_hits >= 4 and self._playback_desired and not self._user_paused:
            self._recover_stream("duration=0", pos)
            self._bad_dur_hits = 0

        try:
            if (dur > 0 and (dur - pos) <= 1500 and
                    self._playback_desired and not self._user_paused and
                    self._bg_endguard_fired_gen != self._load_gen):
                self._bg_endguard_fired_gen = self._load_gen
                # кінець треку - резюму не потрібно
                self._resume_pos_ms = 0
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

        # embed video sync disabled to avoid audio glitches when video audio is active

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
            # збережемо позицію для ручної паузи
            try:
                self._resume_pos_ms = ma.android_player.getCurrentPosition() or 0
            except Exception:
                pass
            # пауза аудіо
            ma._mp_pause()
            # пауза відео
            try:
                if self._video_player:
                    self._video_player.pause()
            except Exception:
                pass
            self._ui_set_playing(False)
        else:
            self._user_paused = False
            self._playback_desired = True
            try:
                ma._mp_start()
                try:
                    if self._video_player and self._video_enabled:
                        self._video_player.resume()
                except Exception:
                    pass
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

    def _act_next(self, *a):
        if not self._debounce(0.15):
            return
        if not self.playlist:
            return
        track = self.playlist.next()
        if not track:
            return
        self.play_audio(
            track["url"],
            track["title"],
            track["channel"],
            track.get("thumb") or "",
            clear_playlist=False,
        )

    def _act_prev(self, *a):
        if not self._debounce(0.15):
            return
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
        )

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

    def _debounce(self, delay: float = 0.25) -> bool:
        now = time.time()
        if now - self._last_click < delay:
            return False
        self._last_click = now
        return True
