# main.py

import os
# Use SDL2 audio provider
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
from youtube_search import fetch_youtube_videos
from audio_screen import AudioPlayerScreen
from kivymd.uix.screen import MDScreen

# Завантажуємо KV-розмітку
Builder.load_file("youtube_gui.kv")

class YoutubeSearchScreen(MDScreen):
    def perform_search(self):
        query = self.ids.search_input.text.strip()
        grid  = self.ids.results_grid
        grid.clear_widgets()
        if not query:
            return
        videos = fetch_youtube_videos(query)
        if not videos:
            from kivymd.uix.label import MDLabel
            grid.add_widget(MDLabel(text="No results found", halign="center"))
            return

        from kivymd.uix.card import MDCard
        from kivymd.uix.boxlayout import MDBoxLayout
        from kivymd.uix.button    import MDRaisedButton
        from kivymd.uix.label     import MDLabel
        from kivy.uix.image       import AsyncImage

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


class YoutubeSearchApp(MDApp):
    def build(self):
        self.theme_cls.theme_style   = "Light"
        self.theme_cls.primary_palette = "Blue"
        sm = ScreenManager()
        sm.add_widget(YoutubeSearchScreen(name="search"))
        sm.add_widget(AudioPlayerScreen(name="audio"))
        return sm

if __name__ == "__main__":
    YoutubeSearchApp().run()
