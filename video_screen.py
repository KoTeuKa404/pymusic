from kivymd.uix.screen import MDScreen
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDRaisedButton
from kivy.uix.video import Video
from kivy.graphics import Color, Rectangle
from kivy.core.window import Window
import os


class VideoPlayerScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        with self.canvas.before:
            Color(1, 1, 1, 1)
            self.bg_rect = Rectangle(size=self.size, pos=self.pos)
        self.bind(size=self.update_bg_rect, pos=self.update_bg_rect)

        video_height = Window.width * 9 // 16
        self.video_player = Video(size_hint=(1, None), height=video_height)
        self.video_player.options = {'ffmpeg': 'ffpyplayer'}
        self.video_player.allow_stretch = True
        self.video_player.keep_ratio = True

        self.video_title = MDLabel(
            text="Now Playing...",
            halign="center",
            theme_text_color="Primary",
            font_style="H6",
            size_hint_y=None,
            height=40
        )

        self.video_info = MDLabel(
            text="Channel • Duration",
            halign="center",
            theme_text_color="Secondary",
            size_hint_y=None,
            height=30
        )

        self.controls = MDBoxLayout(
            orientation="horizontal",
            spacing=10,
            padding=[20, 10],
            size_hint_y=None,
            height=60,
            pos_hint={"center_x": 0.5}
        )

        back_button = MDRaisedButton(
            text="⏪ Back",
            md_bg_color=(0.5, 0, 0, 1),
            text_color=(1, 1, 1, 1)
        )
        back_button.bind(on_press=self.go_back)

        play_button = MDRaisedButton(
            text="▶ Play",
            md_bg_color=(0, 0.6, 0, 1),
            text_color=(1, 1, 1, 1)
        )
        play_button.bind(on_press=self.play_video)

        pause_button = MDRaisedButton(
            text="⏸ Pause",
            md_bg_color=(0.3, 0.3, 0.3, 1),
            text_color=(1, 1, 1, 1)
        )
        pause_button.bind(on_press=self.pause_video)

        self.controls.add_widget(back_button)
        self.controls.add_widget(play_button)
        self.controls.add_widget(pause_button)

        content_box = MDBoxLayout(
            orientation="vertical",
            spacing=10,
            padding=[20, 20, 20, 10],
            size_hint=(1, None),
            height=video_height + 90  
        )
        content_box.add_widget(self.video_player)
        content_box.add_widget(self.video_title)
        content_box.add_widget(self.video_info)

        full_layout = MDBoxLayout(
            orientation="vertical",
            spacing=10,
            padding=0,
            size_hint=(1, 1)
        )
        full_layout.add_widget(content_box)
        full_layout.add_widget(self.controls)

        self.add_widget(full_layout)

    def update_bg_rect(self, *args):
        self.bg_rect.size = self.size
        self.bg_rect.pos = self.pos

    def play(self, video_url, title, channel, duration):
        self.video_title.text = title
        self.video_info.text = f"{channel} • {duration}"

        temp_video = "temp_video.mp4"
        if os.path.exists(temp_video):
            os.remove(temp_video)

        print("⬇️ Завантаження відео...")
        os.system(f"yt-dlp -f best[ext=mp4] -o {temp_video} {video_url}")
        print("✅ Відео завантажено!")

        self.video_player.source = temp_video
        self.video_player.state = "play"

    def play_video(self, instance):
        self.video_player.state = "play"

    def pause_video(self, instance):
        self.video_player.state = "pause"

    def go_back(self, instance):
        self.video_player.state = "stop"
        self.manager.current = "search"
