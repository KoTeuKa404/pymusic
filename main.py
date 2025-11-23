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
from youtube_search import fetch_youtube_results
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
from kivymd.uix.button    import MDRaisedButton, MDFlatButton
from kivymd.uix.label     import MDLabel
from kivymd.uix.dialog    import MDDialog
from kivy.uix.image       import AsyncImage

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

class YoutubeSearchScreen(MDScreen):
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
        if not from_chip:
            history = [q for q in load_search_history() if q != query]
            history.insert(0, query); save_search_history(history)
        threading.Thread(target=self._fetch_results_thread, args=(query,), daemon=True).start()

    def _fetch_results_thread(self, query):
        yt_video_regex = r"(?:v=|be/)([A-Za-z0-9_-]{11})"
        yt_playlist_regex = r"(?:list=)([A-Za-z0-9_-]+)"
        video_id = playlist_id = None
        if "youtube.com" in query or "youtu.be" in query:
            vm = re.search(yt_video_regex, query); pm = re.search(yt_playlist_regex, query)
            if pm: playlist_id = pm.group(1)
            if vm: video_id    = vm.group(1)
            if playlist_id:
                Clock.schedule_once(lambda dt: self.open_playlist(f"https://www.youtube.com/playlist?list={playlist_id}", f"Playlist {playlist_id}")); return
            elif video_id:
                Clock.schedule_once(lambda dt: self.play_audio(f"https://www.youtube.com/watch?v={video_id}", f"Video {video_id}", "", "")); return
        videos, playlists = fetch_youtube_results(query)
        Clock.schedule_once(lambda dt: self._show_results_on_ui(videos, playlists))

    def _show_results_on_ui(self, videos, playlists):
        grid = self.ids.results_grid; grid.clear_widgets()
        if not videos and not playlists:
            grid.add_widget(MDLabel(text="No results found", halign="center")); return
        for url, title, channel, thumb, count in playlists:
            card = MDCard(orientation="horizontal", size_hint_y=None, height="150dp", padding="8dp")
            card.add_widget(AsyncImage(source=thumb, size_hint=(None, 1), width="180dp"))
            box = MDBoxLayout(orientation="vertical", spacing="4dp", padding="4dp")
            box.add_widget(MDLabel(text=f"[b]{title}[/b]", markup=True, theme_text_color="Primary", size_hint_y=None, height="40dp"))
            box.add_widget(MDLabel(text=f"{channel} • {count} tracks", theme_text_color="Secondary", size_hint_y=None, height="30dp"))
            btn_box = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="40dp")
            open_btn = MDRaisedButton(text="▶ Playlist", size_hint=(None, None), size=("100dp","40dp"))
            open_btn.bind(on_press=lambda inst, u=url, t=title: self.open_playlist(u, t))
            btn_box.add_widget(open_btn); box.add_widget(btn_box)
            card.add_widget(box); grid.add_widget(card)
        for url, title, channel, thumb, dur in videos:
            card = MDCard(orientation="horizontal", size_hint_y=None, height="150dp", padding="8dp")
            card.add_widget(AsyncImage(source=thumb, size_hint=(None, 1), width="180dp"))
            box = MDBoxLayout(orientation="vertical", spacing="4dp", padding="4dp")
            box.add_widget(MDLabel(text=f"[b]{title}[/b]", markup=True, theme_text_color="Primary", size_hint_y=None, height="40dp"))
            box.add_widget(MDLabel(text=f"{channel} • {dur}", theme_text_color="Secondary", size_hint_y=None, height="30dp"))
            btn_box = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="40dp")
            play_btn = MDRaisedButton(text="♫ Audio", size_hint=(None, None), size=("100dp","40dp"))
            play_btn.bind(on_press=partial(self.play_audio, url, title, channel, dur, thumb))
            btn_box.add_widget(play_btn); box.add_widget(btn_box)
            card.add_widget(box); grid.add_widget(card)

    def open_playlist(self, playlist_url, playlist_title):
        threading.Thread(target=self._fetch_playlist_thread, args=(playlist_url, playlist_title), daemon=True).start()

    def _fetch_playlist_thread(self, playlist_url, playlist_title):
        from yt_dlp import YoutubeDL
        opts = {'quiet': True, 'extract_flat': True, 'skip_download': True}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
            entries = info['entries']
            tracks = [(e['url'], e['title'], e.get('uploader', '')) for e in entries]
        Clock.schedule_once(lambda dt: self._open_playlist_on_ui(tracks, playlist_title))

    def _open_playlist_on_ui(self, tracks, playlist_title):
        audio_screen = self.manager.get_screen("audio")
        audio_screen.play_playlist(tracks, playlist_title)
        self.manager.current = "audio"

    def play_audio(self, url, title, channel, duration, thumb="", *args, **kwargs):
        from recent_utils import load_recent, save_recent
        recent = load_recent()
        entry = {"url": url, "title": title, "channel": channel, "thumb": thumb}
        recent = [r for r in recent if r["url"] != url]
        recent.insert(0, entry); save_recent(recent)
        screen = self.manager.get_screen("audio")
        screen.play_audio(url, title, channel, duration)
        self.manager.current = "audio"

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
        sm = ScreenManager()
        sm.add_widget(YoutubeSearchScreen(name="search"))
        sm.add_widget(AudioPlayerScreen(name="audio"))
        return sm

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

if __name__ == "__main__":
    YoutubeSearchApp().run()
