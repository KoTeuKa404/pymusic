import os
os.environ["KIVY_AUDIO"] = "sdl2"

# Disable yt_dlp cache completely (must be before import)
try:
    import yt_dlp.cache as ytcache
    ytcache.store = lambda *args, **kwargs: None
    ytcache.load  = lambda *args, **kwargs: None
    ytcache.remove= lambda *args, **kwargs: None
except Exception as e:
    print("❌ Monkey patching yt_dlp.cache failed:", e)

from kivymd.app import MDApp
from kivy.lang import Builder
from kivy.uix.screenmanager import ScreenManager
from youtube_search import fetch_youtube_results
from audio_screen import AudioPlayerScreen
from kivymd.uix.screen import MDScreen
from jnius import autoclass
import re

Builder.load_file("youtube_gui.kv")

class YoutubeSearchScreen(MDScreen):
    def perform_search(self):
        query = self.ids.search_input.text.strip()
        grid = self.ids.results_grid
        grid.clear_widgets()
        if not query:
            return

        yt_video_regex = r"(?:v=|be/)([A-Za-z0-9_-]{11})"
        yt_playlist_regex = r"(?:list=)([A-Za-z0-9_-]+)"
        video_id = None
        playlist_id = None

        if "youtube.com" in query or "youtu.be" in query:
            video_match = re.search(yt_video_regex, query)
            playlist_match = re.search(yt_playlist_regex, query)
            if playlist_match:
                playlist_id = playlist_match.group(1)
            if video_match:
                video_id = video_match.group(1)

            if playlist_id:
                playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                self.open_playlist(playlist_url, f"Playlist {playlist_id}")
                return
            elif video_id:
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                self.play_audio(video_url, f"Video {video_id}", "", "")
                return

        videos, playlists = fetch_youtube_results(query)
        if not videos and not playlists:
            from kivymd.uix.label import MDLabel
            grid.add_widget(MDLabel(text="No results found", halign="center"))
            return

        from kivymd.uix.card import MDCard
        from kivymd.uix.boxlayout import MDBoxLayout
        from kivymd.uix.button    import MDRaisedButton
        from kivymd.uix.label     import MDLabel
        from kivy.uix.image       import AsyncImage

        for url, title, channel, thumb, count in playlists:
            card = MDCard(orientation="horizontal", size_hint_y=None, height="150dp", padding="8dp")
            card.add_widget(AsyncImage(source=thumb, size_hint=(None, 1), width="180dp"))
            box = MDBoxLayout(orientation="vertical", spacing="4dp", padding="4dp")
            box.add_widget(MDLabel(text=f"[b]{title}[/b]", markup=True, theme_text_color="Primary", size_hint_y=None, height="40dp"))
            box.add_widget(MDLabel(text=f"{channel} • {count} tracks", theme_text_color="Secondary", size_hint_y=None, height="30dp"))
            btn_box = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="40dp")
            open_btn = MDRaisedButton(text="▶ Playlist", size_hint=(None, None), size=("100dp","40dp"))
            open_btn.bind(on_press=lambda inst, u=url, t=title: self.open_playlist(u, t))
            btn_box.add_widget(open_btn)
            box.add_widget(btn_box)
            card.add_widget(box)
            grid.add_widget(card)

        for url, title, channel, thumb, dur in videos:
            card = MDCard(orientation="horizontal", size_hint_y=None, height="150dp", padding="8dp")
            card.add_widget(AsyncImage(source=thumb, size_hint=(None, 1), width="180dp"))
            box = MDBoxLayout(orientation="vertical", spacing="4dp", padding="4dp")
            box.add_widget(MDLabel(text=f"[b]{title}[/b]", markup=True, theme_text_color="Primary", size_hint_y=None, height="40dp"))
            box.add_widget(MDLabel(text=f"{channel} • {dur}", theme_text_color="Secondary", size_hint_y=None, height="30dp"))
            btn_box = MDBoxLayout(orientation="horizontal", spacing="8dp", size_hint_y=None, height="40dp")
            play_btn = MDRaisedButton(text="♫ Audio", size_hint=(None, None), size=("100dp","40dp"))
            play_btn.bind(on_press=lambda inst, u=url, t=title, c=channel, d=dur: self.play_audio(u,t,c,d))
            btn_box.add_widget(play_btn)
            box.add_widget(btn_box)
            card.add_widget(box)
            grid.add_widget(card)

    def play_audio(self, url, title, channel, duration):
        screen = self.manager.get_screen("audio")
        screen.play_audio(url, title, channel, duration)
        self.manager.current = "audio"

    def open_playlist(self, playlist_url, playlist_title):
        from yt_dlp import YoutubeDL
        opts = {'quiet': True, 'extract_flat': True, 'skip_download': True}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
            entries = info['entries']
            tracks = [(e['url'], e['title'], e.get('uploader', '')) for e in entries]
        audio_screen = self.manager.get_screen("audio")
        audio_screen.play_playlist(tracks, playlist_title)
        self.manager.current = "audio"

def check_permission(activity, permission):
    PackageManager = autoclass('android.content.pm.PackageManager')
    result = activity.checkSelfPermission(permission)
    return result == PackageManager.PERMISSION_GRANTED

def request_all_permissions():
    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    activity = PythonActivity.mActivity

    permissions = [
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.WAKE_LOCK",
        "android.permission.MODIFY_AUDIO_SETTINGS",
        "android.permission.RECORD_AUDIO",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.WRITE_EXTERNAL_STORAGE",
        "android.permission.INTERNET"
    ]

    VERSION = autoclass('android.os.Build$VERSION')
    if VERSION.SDK_INT >= 33:
        permissions.append("android.permission.POST_NOTIFICATIONS")

    to_request = []
    for perm in permissions:
        if not check_permission(activity, perm):
            to_request.append(perm)

    if to_request:
        activity.requestPermissions(to_request, 1)
    else:
        print("Усі необхідні permissions вже видані.")

class YoutubeSearchApp(MDApp):
    def build(self):
        self.theme_cls.theme_style   = "Light"
        self.theme_cls.primary_palette = "Blue"
        sm = ScreenManager()
        sm.add_widget(YoutubeSearchScreen(name="search"))
        sm.add_widget(AudioPlayerScreen(name="audio"))
        return sm

if __name__ == "__main__":
    request_all_permissions()
    YoutubeSearchApp().run()
