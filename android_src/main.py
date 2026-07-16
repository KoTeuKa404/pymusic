import os
os.environ["KIVY_AUDIO"] = "sdl2"

import re
import threading

# ---- kill yt_dlp cache ----
try:
    import yt_dlp.cache as ytcache
    ytcache.store = ytcache.load = ytcache.remove = (lambda *a, **k: None)
except Exception as e:
    print("❌ yt_dlp.cache monkey patch failed:", e)

from kivymd.app import MDApp
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager
from youtube_search import fetch_youtube_results, fetch_youtube_continuation
from audio_screen import AudioPlayerScreen
from kivymd.uix.screen import MDScreen
from jnius import autoclass
from functools import partial
from kivy.clock import Clock
from android.runnable import run_on_ui_thread  # UI calls
from kivy.app import App

import media_android as ma  # <<< ДОДАНО

from recent_utils import load_recent, save_recent
from search_utils import load_search_history, save_search_history
from kivymd.uix.chip import MDChip
from kivymd.uix.card import MDCard
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button    import MDRaisedButton, MDFlatButton, MDRoundFlatButton
from kivymd.uix.label     import MDLabel
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.dialog    import MDDialog
from kivy.uix.image       import AsyncImage
from kivy.animation       import Animation
from kivy.uix.widget      import Widget
from kivy.uix.stencilview import StencilView
from kivy.uix.behaviors   import ButtonBehavior
from kivy.uix.label       import Label
from kivy.graphics        import Color, Line, RoundedRectangle
from kivy.metrics         import dp

Builder.load_file("youtube_gui.kv")

# ================= ANDROID UTILS =================

def _sdk_int():
    return autoclass('android.os.Build$VERSION').SDK_INT

def _activity():
    return autoclass('org.kivy.android.PythonActivity').mActivity

def _notif_manager():
    Context = autoclass('android.content.Context')
    return _activity().getSystemService(Context.NOTIFICATION_SERVICE)

def _notif_perm_granted():
    if _sdk_int() < 33:
        return True
    PM = autoclass('android.content.pm.PackageManager')
    return _activity().checkSelfPermission("android.permission.POST_NOTIFICATIONS") == PM.PERMISSION_GRANTED

def _notif_enabled_in_system():
    try:
        return bool(_notif_manager().areNotificationsEnabled())
    except Exception:
        return True

def notifications_ready():
    return _notif_perm_granted() and _notif_enabled_in_system()

# ---------- ОДИН ВИКЛИК ДЛЯ ВСІХ RUNTIME-ПРАВ ----------

_perm_once_guard = {"asked_post_notif": False, "asked_media_storage": False}

@run_on_ui_thread
def request_runtime_permissions_safely():
    PM = autoclass('android.content.pm.PackageManager')
    act = _activity()
    sdk = _sdk_int()

    # 1) POST_NOTIFICATIONS (Android 13+)
    if sdk >= 33 and not _perm_once_guard["asked_post_notif"]:
        _perm_once_guard["asked_post_notif"] = True
        if act.checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PM.PERMISSION_GRANTED:
            try:
                print("[PERMS] requesting POST_NOTIFICATIONS")
                act.requestPermissions(["android.permission.POST_NOTIFICATIONS"], 900)
            except Exception as e:
                print("[PERMS] POST_NOTIFICATIONS request failed:", e)

    # 2) Медійні/сторедж-права — одним батчем (один раз)
    if not _perm_once_guard["asked_media_storage"]:
        perms = set()
        if sdk >= 33:
            perms.add("android.permission.READ_MEDIA_AUDIO")
            # perms.add("android.permission.READ_MEDIA_VIDEO")
            # perms.add("android.permission.READ_MEDIA_IMAGES")
        else:
            perms.add("android.permission.READ_EXTERNAL_STORAGE")
            perms.add("android.permission.WRITE_EXTERNAL_STORAGE")

        to_request = [p for p in perms if act.checkSelfPermission(p) != PM.PERMISSION_GRANTED]
        if to_request:
            _perm_once_guard["asked_media_storage"] = True
            try:
                print("[PERMS] requesting media/storage:", to_request)
                act.requestPermissions(to_request, 901)
            except Exception as e:
                print("[PERMS] media/storage request failed:", e)
        else:
            print("[PERMS] media/storage already granted")

# =================== UI / SEARCH ===================

class MarqueeLabel(MDScrollView):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.do_scroll_y = False
        self.do_scroll_x = True
        self.bar_width = 0
        self._label = MDLabel(
            size_hint_x=None,
            size_hint_y=None,
            height=self.height,
            halign="left",
            valign="middle",
            theme_text_color=kwargs.get("theme_text_color", "Primary"),
        )
        self.add_widget(self._label)
        self._marquee_ev = None
        self._marquee_anim = None
        self.bind(size=self._refresh, pos=self._refresh)

    def set_text(self, text: str):
        self._label.text = text or ""
        self._refresh()

    def _refresh(self, *args):
        try:
            self._label.text_size = (None, None)
            self._label.texture_update()
            tw = int(self._label.texture_size[0] or 0)
            self._label.width = max(tw, int(self.width))
            self._label.height = int(self.height)
        except Exception:
            return

        self._stop_marquee()
        if tw <= int(self.width):
            self.scroll_x = 0
            return
        self._start_marquee()

    def _start_marquee(self):
        if self._marquee_anim:
            return
        gap = max(1, int(self._label.width - self.width))
        duration = max(6.0, gap / 30.0)

        def _loop(*_):
            self.scroll_x = 0
            self._marquee_anim = Animation(scroll_x=1.0, d=duration, t="linear")
            self._marquee_anim.bind(on_complete=lambda *_: self._schedule_next())
            self._marquee_anim.start(self)

        _loop()

    def _schedule_next(self):
        self._marquee_anim = None
        self._marquee_ev = Clock.schedule_once(lambda dt: self._start_marquee(), 1.0)

    def _stop_marquee(self):
        try:
            if self._marquee_ev:
                self._marquee_ev.cancel()
                self._marquee_ev = None
        except Exception:
            pass
        try:
            if self._marquee_anim:
                self._marquee_anim.cancel(self)
                self._marquee_anim = None
        except Exception:
            pass

class YoutubeSearchScreen(MDScreen):
    _scroll_bound = False
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._search_query = ""
        self._continuation = None
        self._ytcfg = {}
        self._loading_more = False
        self._scroll_bound = False

    def on_kv_post(self, base_widget):
        super().on_kv_post(base_widget)
        if not self._scroll_bound:
            try:
                self.ids.results_scroll.bind(scroll_y=self._on_results_scroll)
                self._scroll_bound = True
            except Exception:
                pass

    def on_pre_enter(self):
        self.show_recent_videos()
        self.ids.search_history_box.clear_widgets()

    def set_search_and_run(self, query):
        self.ids.search_input.text = query
        self.show_search_history()
        self.perform_search(from_chip=True)

    def show_search_history(self):
        box = self.ids.search_history_box
        box.clear_widgets()
        query = self.ids.search_input.text.strip()
        if not query or self.ids.results_grid.children:
            return
        history = [q for q in load_search_history() if q and query.lower() in q.lower()]
        for q in history:
            chip = MDChip(text=q, icon_left="magnify",
                          on_release=lambda inst, search=q: self.set_search_and_run(search))
            box.add_widget(chip)

    def show_recent_videos(self):
        grid = self.ids.results_grid
        grid.clear_widgets()
        recent = load_recent()
        if recent:
            grid.add_widget(MDLabel(text="Recently Watched", halign="left", font_style="Subtitle1"))
            for rec in recent:
                url, title, channel, thumb = rec["url"], rec["title"], rec["channel"], rec["thumb"]
                card = MDCard(orientation="horizontal", size_hint_y=None, height="120dp", padding="8dp")
                card.add_widget(AsyncImage(source=thumb, size_hint=(None, 1), width="110dp"))
                box = MDBoxLayout(orientation="vertical", spacing="2dp", padding="2dp")
                box.add_widget(MDLabel(text=title, theme_text_color="Primary", size_hint_y=None, height="36dp"))
                box.add_widget(MDLabel(text=channel, theme_text_color="Secondary", size_hint_y=None, height="26dp"))
                play_btn = MDRaisedButton(text="Play", size_hint=(None, None), size=("60dp","36dp"))
                play_btn.bind(on_press=partial(self.play_audio, url, title, channel, "", thumb))
                box.add_widget(play_btn)
                card.add_widget(box); grid.add_widget(card)

    def perform_search(self, from_chip=False):
        query = self.ids.search_input.text.strip()
        self.ids.results_grid.clear_widgets(); self.ids.search_history_box.clear_widgets()
        if not query: return
        self._search_query = query
        self._continuation = None
        self._ytcfg = {}
        self._loading_more = False
        if not from_chip:
            history = [q for q in load_search_history() if q != query]
            history.insert(0, query); save_search_history(history)
        threading.Thread(target=self._fetch_results_thread, args=(query,), daemon=True).start()

    def _fetch_results_thread(self, query):
        video_id = None
        playlist_id = None
        normalized_watch_url = None
        if "youtube.com" in query or "youtu.be" in query:
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(query.strip())
                host = (parsed.netloc or "").lower()
                q = parse_qs(parsed.query or "")
                playlist_id = (q.get("list") or [None])[0]
                video_id = (q.get("v") or [None])[0]
                is_browse_album = (
                    "/browse/" in (parsed.path or "")
                    and ("youtube.com" in host)
                )
                if not video_id and "youtu.be" in host:
                    video_id = (parsed.path or "").strip("/").split("/")[0] or None
                if video_id:
                    normalized_watch_url = f"https://www.youtube.com/watch?v={video_id}"
                    if playlist_id:
                        normalized_watch_url = f"{normalized_watch_url}&list={playlist_id}"
                if playlist_id:
                    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                    Clock.schedule_once(
                        lambda dt: self.open_playlist(
                            playlist_url,
                            "Черга",
                            start_video_id=video_id,
                            fallback_url=normalized_watch_url or query.strip(),
                        )
                    )
                    return
                if is_browse_album:
                    Clock.schedule_once(
                        lambda dt: self.open_playlist(
                            query.strip(),
                            "Альбом",
                            start_video_id=video_id,
                            fallback_url=query.strip(),
                        )
                    )
                    return
                if video_id:
                    Clock.schedule_once(
                        lambda dt: self.play_audio(normalized_watch_url, f"Video {video_id}", "", "")
                    )
                    return
            except Exception:
                pass
        videos, playlists, cont, cfg = fetch_youtube_results(query)
        Clock.schedule_once(lambda dt: self._show_results_on_ui(videos, playlists, cont, cfg))

    def _render_results(self, grid, videos, playlists, *, add_headers: bool):
        if add_headers and playlists:
            grid.add_widget(MDLabel(text="Збірки", halign="left", font_style="Subtitle1"))
        for url, title, channel, thumb, count in playlists:
            card = MDCard(orientation="horizontal", size_hint_y=None, height="150dp", padding="8dp")
            card.add_widget(AsyncImage(source=thumb, size_hint=(None, 1), width="180dp"))
            box = MDBoxLayout(orientation="vertical", spacing="4dp", padding="4dp")
            title_w = MarqueeLabel(size_hint_y=None, height="40dp")
            title_w._label.markup = True
            title_w.set_text(f"[b]{title}[/b]")
            box.add_widget(title_w)
            box.add_widget(MDLabel(text=f"{channel} • {count} треків", theme_text_color="Secondary", size_hint_y=None, height="30dp"))
            btn_box = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="40dp")
            open_btn = MDRaisedButton(text="▶ Відкрити", size_hint=(None, None), size=("100dp","40dp"))
            open_btn.bind(on_press=lambda inst, u=url, t=title: self.open_playlist(u, t))
            btn_box.add_widget(open_btn); box.add_widget(btn_box)
            card.add_widget(box); grid.add_widget(card)
        if add_headers and videos:
            grid.add_widget(MDLabel(text="Відео", halign="left", font_style="Subtitle1"))
        for url, title, channel, thumb, dur in videos:
            card = MDCard(orientation="horizontal", size_hint_y=None, height="150dp", padding="8dp")
            card.add_widget(AsyncImage(source=thumb, size_hint=(None, 1), width="180dp"))
            box = MDBoxLayout(orientation="vertical", spacing="4dp", padding="4dp")
            title_w = MarqueeLabel(size_hint_y=None, height="40dp")
            title_w._label.markup = True
            title_w.set_text(f"[b]{title}[/b]")
            box.add_widget(title_w)
            box.add_widget(MDLabel(text=f"{channel} • {dur}", theme_text_color="Secondary", size_hint_y=None, height="30dp"))
            btn_box = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="40dp")
            play_btn = MDRaisedButton(text="♫ Audio", size_hint=(None, None), size=("100dp","40dp"))
            play_btn.bind(on_press=partial(self.play_audio, url, title, channel, dur, thumb))
            btn_box.add_widget(play_btn); box.add_widget(btn_box)
            card.add_widget(box); grid.add_widget(card)

    def _show_results_on_ui(self, videos, playlists, continuation=None, cfg=None):
        grid = self.ids.results_grid; grid.clear_widgets()
        self._continuation = continuation
        self._ytcfg = cfg or {}
        self._loading_more = False
        if not videos and not playlists:
            grid.add_widget(MDLabel(text="No results found", halign="center")); return
        self._render_results(grid, videos, playlists, add_headers=True)

    def _append_results_on_ui(self, videos, playlists, continuation=None):
        grid = self.ids.results_grid
        self._continuation = continuation
        self._loading_more = False
        if not videos and not playlists:
            return
        add_headers = len(grid.children) == 0
        self._render_results(grid, videos, playlists, add_headers=add_headers)

    def _on_results_scroll(self, scrollview, value):
        if value > 0.05:
            return
        if self._loading_more or not self._continuation or not self._search_query:
            return
        self._loading_more = True
        threading.Thread(target=self._fetch_more_thread, daemon=True).start()

    def _fetch_more_thread(self):
        videos, playlists, cont = fetch_youtube_continuation(self._continuation, self._ytcfg or {})
        Clock.schedule_once(lambda dt: self._append_results_on_ui(videos, playlists, cont))

    def open_playlist(
        self,
        playlist_url,
        playlist_title,
        start_video_id=None,
        start_after=False,
        fallback_url=None,
    ):
        # Для великих плейлистів не чекаємо, поки yt-dlp витягне всі 200+ треків.
        # Якщо посилання містить поточне відео, запускаємо його одразу, а чергу
        # підтягуємо у фоні й підставляємо в плеєр без повторного рестарту треку.
        started_fast = False
        fast_url = str(fallback_url or "").strip()
        if not fast_url and start_video_id:
            fast_url = f"https://www.youtube.com/watch?v={start_video_id}"
        if fast_url:
            started_fast = True
            Clock.schedule_once(lambda dt, u=fast_url: self._open_single_on_ui(u), 0)

        threading.Thread(
            target=self._fetch_playlist_thread,
            args=(playlist_url, playlist_title, start_video_id, start_after, fallback_url, started_fast),
            daemon=True,
        ).start()

    def _fetch_playlist_thread(
        self,
        playlist_url,
        playlist_title,
        start_video_id=None,
        start_after=False,
        fallback_url=None,
        already_started=False,
    ):
        try:
            from yt_dlp import YoutubeDL
            from ytdlp_helpers import YDLLogger
            import re
            from urllib.parse import urlparse, parse_qs

            def _playlist_id_from_url(u: str) -> str:
                try:
                    q = parse_qs(urlparse(str(u or "")).query or "")
                    return str((q.get("list") or [""])[0] or "")
                except Exception:
                    return ""

            def _extract_vid_from_any(u: str) -> str:
                raw = str(u or "").strip()
                if not raw:
                    return ""
                if re.fullmatch(r"[A-Za-z0-9_-]{11}", raw):
                    return raw
                try:
                    parsed = urlparse(raw)
                    q = parse_qs(parsed.query or "")
                    v = str((q.get("v") or [""])[0] or "")
                    if v:
                        return v
                    host = (parsed.netloc or "").lower()
                    if "youtu.be" in host:
                        return str((parsed.path or "").strip("/").split("/", 1)[0] or "")
                    for prefix in ("/shorts/", "/live/", "/embed/"):
                        if prefix in (parsed.path or ""):
                            return (parsed.path.split(prefix, 1)[1] or "").split("/", 1)[0].split("?", 1)[0]
                except Exception:
                    pass
                for pattern in (
                    r"/vi/([A-Za-z0-9_-]{11})/",
                    r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})",
                ):
                    m = re.search(pattern, raw)
                    if m:
                        return m.group(1)
                return ""

            def _extract_mix_info(playlist_id: str, seed_video_id: str | None):
                if not playlist_id.startswith("RD"):
                    return None
                seed = str(seed_video_id or "").strip()
                if not seed:
                    seed = _extract_vid_from_any(fallback_url or "")
                if not seed:
                    seed = _extract_vid_from_any(playlist_url)
                if not seed:
                    return None
                mix_watch_url = f"https://www.youtube.com/watch?v={seed}&list={playlist_id}"

                candidates = [
                    {
                        "quiet": True,
                        "logger": YDLLogger(),
                        "skip_download": True,
                        "ignoreerrors": True,
                        "extract_flat": "in_playlist",
                        "playlistend": 250,
                    },
                    {
                        "quiet": True,
                        "logger": YDLLogger(),
                        "skip_download": True,
                        "ignoreerrors": True,
                        "extract_flat": False,
                        "playlistend": 250,
                    },
                    {
                        "quiet": True,
                        "logger": YDLLogger(),
                        "skip_download": True,
                        "ignoreerrors": True,
                        "playlist_items": "1-250",
                    },
                ]
                best = None
                best_len = 0
                for op in candidates:
                    try:
                        with YoutubeDL(op) as ydl_mix:
                            info_mix = ydl_mix.extract_info(mix_watch_url, download=False)
                        if not isinstance(info_mix, dict):
                            continue
                        en = info_mix.get("entries") or []
                        ln = len(en)
                        if ln > best_len:
                            best = info_mix
                            best_len = ln
                        if ln >= 2:
                            return info_mix
                    except Exception:
                        continue
                return best

            opts = {
                'quiet': True,
                'logger': YDLLogger(),
                'extract_flat': 'in_playlist',
                'skip_download': True,
                'ignoreerrors': True,
                'playlistend': 250,
            }
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
                entries = info.get('entries') or []
                playlist_id = _playlist_id_from_url(playlist_url)
                if len(entries) < 2:
                    try:
                        # Fallback для міксів/нестабільних playlist endpoint.
                        opts2 = {
                            'quiet': True,
                            'logger': YDLLogger(),
                            'skip_download': True,
                            'ignoreerrors': True,
                            'playlistend': 250,
                        }
                        with YoutubeDL(opts2) as ydl2:
                            info2 = ydl2.extract_info(playlist_url, download=False)
                        if isinstance(info2, dict):
                            info = info2
                            entries = info.get("entries") or []
                    except Exception:
                        pass
                if len(entries) < 2 and playlist_id.startswith("RD"):
                    try:
                        seed_guess = start_video_id
                        if not seed_guess and entries:
                            first_e = entries[0] if isinstance(entries[0], dict) else {}
                            seed_guess = (
                                _extract_vid_from_any(first_e.get("url") or "")
                                or _extract_vid_from_any(first_e.get("id") or "")
                            )
                        info_mix = _extract_mix_info(playlist_id, seed_guess)
                        if isinstance(info_mix, dict):
                            info = info_mix
                            entries = info.get("entries") or []
                    except Exception:
                        pass
                extracted_title = (
                    info.get("title")
                    or info.get("playlist_title")
                    or playlist_title
                    or "Черга"
                )
                entries = info.get('entries') or []
                tracks = []
                track_ids = []
                missing_vid = 0
                for e in entries:
                    if not isinstance(e, dict):
                        continue

                    # Спочатку беремо URL, а вже потім id. У деяких YouTube Music
                    # плейлистах поле id/thumbnail може бути не тим самим відео, через що
                    # превʼю рядків зʼїжджали на останній елемент.
                    url = e.get("webpage_url") or e.get("url") or e.get("id") or ""
                    vid = (
                        _extract_vid_from_any(e.get("webpage_url") or "")
                        or _extract_vid_from_any(e.get("url") or "")
                        or _extract_vid_from_any(url or "")
                        or _extract_vid_from_any(e.get("id") or "")
                        or _extract_vid_from_any(e.get("thumbnail") or "")
                    )

                    try:
                        u = str(url or "").strip()
                        if u.startswith("//"):
                            url = f"https:{u}"
                        elif u.startswith("/watch") or u.startswith("/shorts/") or u.startswith("/live/"):
                            url = f"https://www.youtube.com{u}"
                        elif u.startswith("watch?"):
                            url = f"https://www.youtube.com/{u}"
                        elif u.startswith("youtube.com/") or u.startswith("www.youtube.com/") or u.startswith("youtu.be/"):
                            url = f"https://{u}"
                        elif re.fullmatch(r"[A-Za-z0-9_-]{11}", u):
                            url = f"https://www.youtube.com/watch?v={u}"
                        elif not u and vid:
                            url = f"https://www.youtube.com/watch?v={vid}"
                    except Exception:
                        pass

                    if not vid:
                        vid = _extract_vid_from_any(url)
                    if not vid:
                        missing_vid += 1

                    if not url:
                        continue

                    title = e.get("title") or e.get("fulltitle") or ""
                    channel = e.get("uploader") or e.get("channel") or e.get("uploader_id") or ""

                    thumb = e.get("thumbnail") or ""
                    if isinstance(thumb, str) and thumb.startswith("//"):
                        thumb = f"https:{thumb}"
                    if not thumb:
                        try:
                            thumbs = e.get("thumbnails") or []
                            if isinstance(thumbs, list) and thumbs:
                                for cand in reversed(thumbs):
                                    if isinstance(cand, dict) and cand.get("url"):
                                        thumb = cand.get("url") or ""
                                        if isinstance(thumb, str) and thumb.startswith("//"):
                                            thumb = f"https:{thumb}"
                                        break
                        except Exception:
                            thumb = thumb or ""

                    if vid:
                        # Для списку краще стабільний static JPEG без webp/sqp/rs параметрів.
                        thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"

                    duration = e.get("duration_string") or ""
                    if not duration:
                        try:
                            d = int(e.get("duration") or 0)
                            if d > 0:
                                m, s = divmod(d, 60)
                                h, m = divmod(m, 60)
                                duration = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
                        except Exception:
                            duration = ""

                    key = str(vid or url or "")
                    if not key:
                        continue
                    track_ids.append(key)
                    tracks.append((url, title, channel, thumb, duration, vid))
                try:
                    try:
                        uniq_ids = len({str(v or "") for v in track_ids if str(v or "")})
                    except Exception:
                        uniq_ids = 0
                    print(
                        f"[PLAYLIST] extracted tracks={len(tracks)} missing_vid={missing_vid} uniq_vid={uniq_ids} title={extracted_title!r}"
                    )
                except Exception:
                    pass
        except Exception as e:
            print(f"[PLAYLIST] extract err: {e}")
            try:
                import traceback
                print("[PLAYLIST] extract traceback:\n" + traceback.format_exc())
            except Exception:
                pass
            if fallback_url:
                Clock.schedule_once(lambda dt: self._open_single_on_ui(fallback_url), 0)
            return
        if not tracks:
            if fallback_url:
                Clock.schedule_once(lambda dt: self._open_single_on_ui(fallback_url), 0)
            return
        start_index = 0
        if start_video_id:
            try:
                for idx, vid in enumerate(track_ids):
                    if vid == start_video_id:
                        start_index = idx + (1 if start_after else 0)
                        if start_index >= len(tracks):
                            start_index = 0
                        break
            except Exception:
                start_index = 0
        if already_started:
            try:
                audio_screen = self.manager.get_screen("audio")
                if getattr(audio_screen, "_app_in_background", False):
                    audio_screen.play_playlist(
                        tracks,
                        extracted_title,
                        start_index=start_index,
                        playlist_url=playlist_url,
                        start_playback=False,
                    )
                    print(
                        f"[PLAYLIST] installed in background tracks={len(tracks)} start={start_index} title={extracted_title!r}"
                    )
                    return
            except Exception as e:
                print(f"[PLAYLIST] background install err: {e}")
        Clock.schedule_once(
            lambda dt: self._open_playlist_on_ui(
                tracks,
                extracted_title,
                start_index,
                playlist_url,
                start_playback=not already_started,
            )
        )

    def _open_playlist_on_ui(self, tracks, playlist_title, start_index=0, playlist_url=None, start_playback=True):
        audio_screen = self.manager.get_screen("audio")
        audio_screen.play_playlist(
            tracks,
            playlist_title,
            start_index=start_index,
            playlist_url=playlist_url,
            start_playback=start_playback,
        )
        try:
            if getattr(audio_screen, "_app_in_background", False):
                return
        except Exception:
            pass
        try:
            app = App.get_running_app()
            root = getattr(app, "root", None)
            if root and hasattr(root, "open_audio"):
                root.open_audio()
            else:
                self.manager.current = "audio"
        except Exception:
            self.manager.current = "audio"

    def _open_single_on_ui(self, url):
        try:
            screen = self.manager.get_screen("audio")
            screen.play_audio(url)
            try:
                if getattr(screen, "_app_in_background", False):
                    return
            except Exception:
                pass
            app = App.get_running_app()
            root = getattr(app, "root", None)
            if root and hasattr(root, "open_audio"):
                root.open_audio()
            else:
                self.manager.current = "audio"
        except Exception:
            pass

    def play_audio(self, url, title, channel, duration, thumb="", *args, **kwargs):
        from recent_utils import load_recent, save_recent
        recent = load_recent()
        entry = {"url": url, "title": title, "channel": channel, "thumb": thumb}
        recent = [r for r in recent if r["url"] != url]
        recent.insert(0, entry); save_recent(recent)
        screen = self.manager.get_screen("audio")
        screen.play_audio(url, title, channel, duration, thumb=thumb)
        try:
            if getattr(screen, "_app_in_background", False):
                return
        except Exception:
            pass
        try:
            app = App.get_running_app()
            root = getattr(app, "root", None)
            if root and hasattr(root, "open_audio"):
                root.open_audio()
            else:
                self.manager.current = "audio"
        except Exception:
            self.manager.current = "audio"


class YoutubeWebScreen(MDScreen):
    def on_pre_enter(self):
        try:
            ma.bind_webview_action_router(self)
            ma.bind_intent_router()
            # Не скидаємо сторінку при поверненні — WebView зберігає попередній стан.
            ma.webview_show()
        except Exception:
            pass

    def on_pre_leave(self):
        try:
            ma.webview_hide()
        except Exception:
            pass

    def _webview_play(self, url):
        try:
            if not url:
                return
            # якщо це watch/browse з playlist/album - відкриваємо чергу і стартуємо з поточного відео
            try:
                from urllib.parse import urlparse, parse_qs
                parsed = urlparse(url)
                q = parse_qs(parsed.query)
                playlist_id = (q.get("list") or [None])[0]
                video_id = (q.get("v") or [None])[0]
                if not video_id and "youtu.be" in (parsed.netloc or ""):
                    video_id = (parsed.path or "").strip("/").split("/")[0] or None
                is_browse_album = (
                    "/browse/" in (parsed.path or "")
                    and ("music.youtube.com" in (parsed.netloc or "") or "youtube.com" in (parsed.netloc or ""))
                )
            except Exception:
                playlist_id = None
                video_id = None
                is_browse_album = False

            started = False
            if playlist_id:
                try:
                    search_screen = self.manager.get_screen("search")
                    # Для RD mix надійніше йти через watch+list (seed video),
                    # інакше YouTube інколи повертає лише 1 елемент.
                    if str(playlist_id).startswith("RD") and video_id:
                        playlist_url = f"https://www.youtube.com/watch?v={video_id}&list={playlist_id}"
                    else:
                        playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                    search_screen.open_playlist(
                        playlist_url,
                        "Черга",
                        start_video_id=video_id,
                        fallback_url=url,
                    )
                    started = True
                except Exception:
                    started = False
            elif is_browse_album:
                try:
                    search_screen = self.manager.get_screen("search")
                    search_screen.open_playlist(
                        url,
                        "Альбом",
                        start_video_id=video_id,
                        fallback_url=url,
                    )
                    started = True
                except Exception:
                    started = False
            if not started:
                screen = self.manager.get_screen("audio")
                screen.play_audio(url)
            try:
                app = App.get_running_app()
                root = getattr(app, "root", None)
                if root and hasattr(root, "open_audio"):
                    root.open_audio()
                else:
                    self.manager.current = "audio"
            except Exception:
                self.manager.current = "audio"
        except Exception:
            pass



class BottomNavButton(ButtonBehavior, Label):
    """Однакові нижні кнопки для Kivy-екрана і WebView-injected панелі."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint = (None, None)
        self.size = (dp(84), dp(34))
        self.font_size = "14sp"
        self.color = (1, 1, 1, 1)
        self.halign = "center"
        self.valign = "middle"
        self.bind(pos=self._redraw, size=self._redraw, state=self._redraw)
        self._redraw()

    def _redraw(self, *args):
        try:
            self.text_size = self.size
            self.canvas.before.clear()
            with self.canvas.before:
                if self.state == "down":
                    Color(1, 1, 1, 0.10)
                    RoundedRectangle(
                        pos=self.pos,
                        size=self.size,
                        radius=[dp(18), dp(18), dp(18), dp(18)],
                    )
                Color(1, 1, 1, 0.35)
                Line(
                    rounded_rectangle=(self.x, self.y, self.width, self.height, dp(18)),
                    width=dp(1),
                )
        except Exception:
            pass

class RootLayout(MDBoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        self._active_tab = "web"

        self.sm = ScreenManager()

        # Якщо інтернету немає, не стартуємо одразу YT WebView.
        # Інакше Android показує сторінку net::ERR_TIMED_OUT на m.youtube.com.
        start_with_web = True
        try:
            start_with_web = bool(ma.is_network_available())
        except Exception:
            start_with_web = True

        if start_with_web:
            self.sm.add_widget(YoutubeWebScreen(name="web"))
            self.sm.add_widget(YoutubeSearchScreen(name="search"))
            self._active_tab = "web"
        else:
            self.sm.add_widget(YoutubeSearchScreen(name="search"))
            self.sm.add_widget(YoutubeWebScreen(name="web"))
            self._active_tab = "search"

        self.sm.add_widget(AudioPlayerScreen(name="audio"))
        self.add_widget(self.sm)
        self._build_bottom_bar()
        try:
            ma.bind_mode_router(self.set_screen)
        except Exception:
            pass

        try:
            self.set_screen("web" if start_with_web else "search")
        except Exception:
            self.sm.current = "search"

    def _build_bottom_bar(self):
        bar = MDBoxLayout(
            size_hint_y=None,
            height=dp(56),
            padding=(dp(10), 0, dp(10), 0),
            spacing=0,
            md_bg_color=(0, 0, 0, 1),
        )
        center = MDBoxLayout(
            size_hint=(None, 1),
            width=dp(184),
            spacing=dp(16),
            padding=(0, dp(11), 0, dp(11)),
        )
        btn_web = BottomNavButton(text="Web")
        btn_web.bind(on_release=lambda _btn: self.set_screen("search"))
        btn_yt = BottomNavButton(text="YT")
        btn_yt.bind(on_release=lambda _btn: self.set_screen("web"))

        bar.add_widget(Widget())
        center.add_widget(btn_web)
        center.add_widget(btn_yt)
        bar.add_widget(center)
        bar.add_widget(Widget())
        self.add_widget(bar)

    def set_screen(self, name: str):
        # Offline fallback applies only on app startup / WebView load errors.
        # Manual YT button must still be allowed; otherwise a false network check
        # keeps the app stuck on the Web/search screen forever.
        if name != "audio":
            try:
                audio = self.sm.get_screen("audio")
                if hasattr(audio, "hide_video_overlay_fast"):
                    audio.hide_video_overlay_fast()
                    # Добиваємо race: інколи відкладений callback знову показує surface.
                    Clock.schedule_once(lambda dt: audio.hide_video_overlay_fast(), 0.15)
            except Exception:
                pass

        if name in ("web", "search"):
            self._active_tab = name
        if name != "web":
            try:
                ma.webview_hide()
            except Exception:
                pass
        self.sm.current = name
        try:
            ma.apply_light_system_bars()
        except Exception:
            pass

    def open_audio(self):
        try:
            ma.webview_hide()
        except Exception:
            pass
        self.sm.current = "audio"
        try:
            ma.apply_light_system_bars()
        except Exception:
            pass

    def go_to_active_tab(self):
        self.set_screen(self._active_tab)

    def handle_app_pause(self):
        try:
            screen = self.sm.get_screen("audio")
            if hasattr(screen, "handle_app_pause"):
                screen.handle_app_pause()
        except Exception:
            pass

    def handle_app_resume(self):
        try:
            screen = self.sm.get_screen("audio")
            if hasattr(screen, "handle_app_resume"):
                screen.handle_app_resume()
        except Exception:
            pass

# ---------- Diagnostics ----------
def _log_build_info():
    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    PackageManager = autoclass('android.content.pm.PackageManager')
    VERSION = autoclass('android.os.Build$VERSION')
    activity = PythonActivity.mActivity
    pm = activity.getPackageManager()
    pkg = activity.getPackageName()
    try:
        info = pm.getPackageInfo(pkg, PackageManager.GET_PERMISSIONS)
        requested = list(getattr(info, 'requestedPermissions', []) or [])
    except Exception as e:
        requested = []
        print("[BUILD] getPackageInfo err:", e)
    target = activity.getApplicationInfo().targetSdkVersion
    print(f"[BUILD] SDK_INT={VERSION.SDK_INT}, targetSdk={target}")
    print(f"[BUILD] requestedPermissions={requested}")

# ================= APP =================
class YoutubeSearchApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Light"
        self.theme_cls.primary_palette = "Blue"
        return RootLayout()

    def on_start(self):
        _log_build_info()
        # Канал і пермішени для нотифікацій до першого показу
        ma.create_notification_channel()
        try:
            ma.request_post_notifications_permission()
        except Exception:
            pass
        # інші runtime-права
        request_runtime_permissions_safely()
        try:
            ma.bind_intent_router()
        except Exception:
            pass
        try:
            ma.consume_current_activity_intent()
        except Exception:
            pass
        try:
            ma.apply_light_system_bars()
        except Exception:
            pass

    def on_pause(self):
        try:
            root = getattr(self, "root", None)
            if root and hasattr(root, "handle_app_pause"):
                root.handle_app_pause()
        except Exception:
            pass
        return True

    def on_resume(self):
        try:
            ma.apply_light_system_bars()
        except Exception:
            pass
        try:
            ma.consume_current_activity_intent()
        except Exception:
            pass
        try:
            root = getattr(self, "root", None)
            if root and hasattr(root, "handle_app_resume"):
                root.handle_app_resume()
        except Exception:
            pass

if __name__ == "__main__":
    YoutubeSearchApp().run()
