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
        threading.Thread(
            target=self._fetch_playlist_thread,
            args=(playlist_url, playlist_title, start_video_id, start_after, fallback_url),
            daemon=True,
        ).start()

    def _fetch_playlist_thread(
        self,
        playlist_url,
        playlist_title,
        start_video_id=None,
        start_after=False,
        fallback_url=None,
    ):
        try:
            from yt_dlp import YoutubeDL
            import re
            opts = {'quiet': True, 'extract_flat': 'in_playlist', 'skip_download': True}
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(playlist_url, download=False)
                extracted_title = (
                    info.get("title")
                    or info.get("playlist_title")
                    or playlist_title
                    or "Черга"
                )
                entries = info.get('entries') or []
                tracks = []
                track_ids = []
                for e in entries:
                    if not isinstance(e, dict):
                        continue
                    url = e.get("url") or e.get("id") or ""
                    try:
                        u = str(url or "")
                        if u.startswith("//"):
                            url = f"https:{u}"
                        elif u.startswith("/watch") or u.startswith("/shorts/") or u.startswith("/live/"):
                            url = f"https://www.youtube.com{u}"
                        elif u.startswith("watch?"):
                            url = f"https://www.youtube.com/{u}"
                        elif u.startswith("youtube.com/") or u.startswith("www.youtube.com/") or u.startswith("youtu.be/"):
                            url = f"https://{u}"
                    except Exception:
                        pass
                    title = e.get("title") or e.get("fulltitle") or ""
                    channel = e.get("uploader") or e.get("channel") or ""
                    if not url:
                        continue
                    vid = str(e.get("id") or "")
                    if not vid and isinstance(url, str) and url.startswith("http"):
                        try:
                            from urllib.parse import urlparse, parse_qs
                            parsed = urlparse(url)
                            q = parse_qs(parsed.query or "")
                            vid = (q.get("v") or [None])[0] or ""
                            if not vid and "youtu.be" in (parsed.netloc or ""):
                                vid = (parsed.path or "").lstrip("/")
                        except Exception:
                            vid = ""
                    if vid and not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
                        try:
                            from urllib.parse import urlparse, parse_qs
                            p2 = urlparse(vid)
                            q2 = parse_qs(p2.query or "")
                            vv = (q2.get("v") or [""])[0]
                            if vv:
                                vid = vv
                            elif "youtu.be" in (p2.netloc or ""):
                                vid = (p2.path or "").lstrip("/")
                        except Exception:
                            pass
                    m = re.search(r"([A-Za-z0-9_-]{11})", str(vid or ""))
                    vid = m.group(1) if m else ""
                    if not vid:
                        try:
                            turl = str(e.get("thumbnail") or "")
                            m2 = re.search(r"/vi/([A-Za-z0-9_-]{11})/", turl)
                            if m2:
                                vid = m2.group(1)
                        except Exception:
                            pass
                    thumb = e.get("thumbnail") or ""
                    if isinstance(thumb, str) and thumb.startswith("//"):
                        thumb = f"https:{thumb}"
                    if not thumb:
                        try:
                            thumbs = e.get("thumbnails") or []
                            if isinstance(thumbs, list) and thumbs:
                                cand = thumbs[-1]
                                if isinstance(cand, dict):
                                    thumb = cand.get("url") or ""
                                    if isinstance(thumb, str) and thumb.startswith("//"):
                                        thumb = f"https:{thumb}"
                        except Exception:
                            thumb = thumb or ""
                    if (not thumb) and vid:
                        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
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
                    track_ids.append(str(vid or url or ""))
                    try:
                        print(f"[PLAYLIST] row vid={vid!r} title={title!r} thumb={(thumb or '')!r}")
                    except Exception:
                        pass
                    tracks.append((url, title, channel, thumb, duration, vid))
        except Exception as e:
            print(f"[PLAYLIST] extract err: {e}")
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
        Clock.schedule_once(
            lambda dt: self._open_playlist_on_ui(
                tracks,
                extracted_title,
                start_index,
                playlist_url,
            )
        )

    def _open_playlist_on_ui(self, tracks, playlist_title, start_index=0, playlist_url=None):
        audio_screen = self.manager.get_screen("audio")
        audio_screen.play_playlist(
            tracks,
            playlist_title,
            start_index=start_index,
            playlist_url=playlist_url,
        )
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
            # якщо це watch з playlist - відкриваємо плейлист і стартуємо з поточного відео
            try:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(url).query)
                playlist_id = (q.get("list") or [None])[0]
                video_id = (q.get("v") or [None])[0]
            except Exception:
                playlist_id = None
                video_id = None

            started = False
            if playlist_id:
                try:
                    search_screen = self.manager.get_screen("search")
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


class RootLayout(MDBoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)
        self._active_tab = "web"

        self.sm = ScreenManager()
        self.sm.add_widget(YoutubeWebScreen(name="web"))
        self.sm.add_widget(YoutubeSearchScreen(name="search"))
        self.sm.add_widget(AudioPlayerScreen(name="audio"))
        self.add_widget(self.sm)
        self._build_bottom_bar()
        try:
            ma.bind_mode_router(self.set_screen)
        except Exception:
            pass

        self.sm.current = "web"

    def _build_bottom_bar(self):
        bar = MDBoxLayout(
            size_hint_y=None,
            height=dp(52),
            padding=(dp(12), dp(6), dp(12), dp(6)),
            spacing=dp(8),
            md_bg_color=(0, 0, 0, 1),
        )
        btn_web = MDRoundFlatButton(
            text="Web",
            theme_text_color="Custom",
            text_color=(1, 1, 1, 1),
            line_color=(1, 1, 1, 0.35),
            on_release=lambda _btn: self.set_screen("search"),
        )
        btn_yt = MDRoundFlatButton(
            text="YT",
            theme_text_color="Custom",
            text_color=(1, 1, 1, 1),
            line_color=(1, 1, 1, 0.35),
            on_release=lambda _btn: self.set_screen("web"),
        )
        for btn in (btn_web, btn_yt):
            btn.size_hint_x = None
            btn.width = dp(88)
        bar.add_widget(Widget())
        bar.add_widget(btn_web)
        bar.add_widget(Widget())
        bar.add_widget(btn_yt)
        bar.add_widget(Widget())
        self.add_widget(bar)

    def set_screen(self, name: str):
        if name in ("web", "search"):
            self._active_tab = name
        if name != "web":
            try:
                ma.webview_hide()
            except Exception:
                pass
        self.sm.current = name

    def open_audio(self):
        try:
            ma.webview_hide()
        except Exception:
            pass
        self.sm.current = "audio"

    def go_to_active_tab(self):
        self.set_screen(self._active_tab)

    def handle_app_pause(self):
        try:
            if self.sm.current == "audio":
                screen = self.sm.get_screen("audio")
                if hasattr(screen, "handle_app_pause"):
                    screen.handle_app_pause()
        except Exception:
            pass

    def handle_app_resume(self):
        try:
            if self.sm.current == "audio":
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
            root = getattr(self, "root", None)
            if root and hasattr(root, "handle_app_resume"):
                root.handle_app_resume()
        except Exception:
            pass

if __name__ == "__main__":
    YoutubeSearchApp().run()
