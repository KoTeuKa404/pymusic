from kivymd.app import MDApp
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.button import MDRaisedButton
from kivymd.uix.textfield import MDTextField
from kivymd.uix.label import MDLabel
from kivymd.uix.card import MDCard
from kivymd.uix.screen import MDScreen
from kivymd.uix.scrollview import MDScrollView
from kivymd.uix.gridlayout import MDGridLayout
from kivy.uix.image import AsyncImage
from kivy.uix.screenmanager import ScreenManager
from video_screen import VideoPlayerScreen
from youtube_search import fetch_youtube_videos
from audio_screen import AudioPlayerScreen


class YoutubeSearchScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        layout = MDBoxLayout(orientation="vertical", spacing=10, padding=10)

        self.search_input = MDTextField(
            hint_text="Enter song name",
            size_hint_y=None,
            height=60,
            mode="rectangle"
        )
        layout.add_widget(self.search_input)

        search_button = MDRaisedButton(
            text="Search",
            pos_hint={"center_x": 0.5},
            md_bg_color=(0, 0.5, 1, 1),
            text_color=(1, 1, 1, 1),
            size_hint=(1, None),
            height=50
        )
        search_button.bind(on_press=self.perform_search)
        layout.add_widget(search_button)

        self.results_scroll = MDScrollView()
        self.results_grid = MDGridLayout(cols=1, adaptive_height=True, spacing=10, padding=10)
        self.results_scroll.add_widget(self.results_grid)
        layout.add_widget(self.results_scroll)

        self.add_widget(layout)

    def perform_search(self, instance):
        query = self.search_input.text.strip()
        if not query:
            return

        self.results_grid.clear_widgets()
        results = fetch_youtube_videos(query)

        if not results:
            self.results_grid.add_widget(MDLabel(text="No results found", halign="center"))
            return

        for url, title, channel, thumbnail, duration in results:
            card = MDCard(orientation="horizontal", size_hint=(1, None), height=150, padding=10)

            thumbnail_img = AsyncImage(source=thumbnail, size_hint=(None, 1), width=180)
            card.add_widget(thumbnail_img)

            card_box = MDBoxLayout(orientation="vertical", spacing=5, padding=5)

            card_box.add_widget(MDLabel(
                text=f"[b]{title}[/b]",
                markup=True,
                theme_text_color="Primary",
                size_hint_y=None,
                height=40
            ))

            card_box.add_widget(MDLabel(
                text=f"{channel} • {duration}",
                theme_text_color="Secondary",
                size_hint_y=None,
                height=30
            ))

            btn_box = MDBoxLayout(orientation="horizontal", spacing=10, size_hint_y=None, height=40)

            play_video = MDRaisedButton(
                text="▶ Video",
                md_bg_color=(0.2, 0.2, 1, 1),
                text_color=(1, 1, 1, 1),
                size_hint=(None, None),
                size=(100, 40)
            )
            play_video.bind(on_press=lambda inst, u=url, t=title, c=channel, d=duration: self.play_video(u, t, c, d))

            btn_box.add_widget(play_video)

            card_box.add_widget(btn_box)
            card.add_widget(card_box)

            self.results_grid.add_widget(card)


    def play_video(self, video_url, title, channel, duration):
        self.manager.get_screen("video").play(video_url, title, channel, duration)
        self.manager.current = "video"
        
    def play_audio(self, video_url, title, channel, duration):
        self.manager.get_screen("audio").play_audio(video_url, title, channel, duration)
        self.manager.current = "audio"


class YoutubeSearchApp(MDApp):
    def build(self):
        self.theme_cls.theme_style = "Light"
        self.theme_cls.primary_palette = "Blue"

        sm = ScreenManager()
        sm.add_widget(YoutubeSearchScreen(name="search"))
        sm.add_widget(VideoPlayerScreen(name="video"))
        sm.add_widget(AudioPlayerScreen(name="audio"))

        return sm


if __name__ == "__main__":
    YoutubeSearchApp().run()
