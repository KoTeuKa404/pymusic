from kivymd.uix.screen import MDScreen
from kivymd.uix.boxlayout import MDBoxLayout
from kivymd.uix.label import MDLabel
from kivymd.uix.button import MDRaisedButton
from kivy.graphics import Color, Rectangle
from ffpyplayer.player import MediaPlayer
import os


class AudioPlayerScreen(MDScreen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        with self.canvas.before:
            Color(1, 1, 1, 1)
            self.bg_rect = Rectangle(size=self.size, pos=self.pos)
        self.bind(size=self.update_bg_rect, pos=self.update_bg_rect)

        self.audio_player = None

        self.song_title = MDLabel(
            text="Now Playing...",
            halign="center",
            theme_text_color="Primary",
            font_style="H6",
            size_hint_y=None,
            height=40
        )

        self.song_info = MDLabel(
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

        back_btn = MDRaisedButton(text="⏪ Back", md_bg_color=(1, 0, 0, 1), text_color=(1, 1, 1, 1))
        back_btn.bind(on_press=self.go_back)

        play_btn = MDRaisedButton(text="▶ Play", md_bg_color=(0, 0.6, 0, 1), text_color=(1, 1, 1, 1))
        play_btn.bind(on_press=self.resume_audio)

        pause_btn = MDRaisedButton(text="⏸ Pause", md_bg_color=(0.3, 0.3, 0.3, 1), text_color=(1, 1, 1, 1))
        pause_btn.bind(on_press=self.pause_audio)

        self.controls.add_widget(back_btn)
        self.controls.add_widget(play_btn)
        self.controls.add_widget(pause_btn)

        layout = MDBoxLayout(orientation="vertical", spacing=10, padding=20)
        layout.add_widget(self.song_title)
        layout.add_widget(self.song_info)
        layout.add_widget(self.controls)

        self.add_widget(layout)

    def update_bg_rect(self, *args):
        self.bg_rect.size = self.size
        self.bg_rect.pos = self.pos

    def play_audio(self, video_url, title, channel, duration):
        self.song_title.text = title
        self.song_info.text = f"{channel} • {duration}"

        audio_file = "temp_audio.mp3"
        if os.path.exists(audio_file):
            os.remove(audio_file)

        print("⬇️ Завантаження аудіо...")
        os.system(f"yt-dlp -x --audio-format mp3 -o {audio_file} {video_url}")
        print("✅ Аудіо завантажено")

        if self.audio_player:
            self.audio_player.close_player()

        self.audio_player = MediaPlayer(audio_file)
        self.audio_player.set_pause(False)

    def resume_audio(self, instance):
        if self.audio_player:
            self.audio_player.set_pause(False)

    def pause_audio(self, instance):
        if self.audio_player:
            self.audio_player.set_pause(True)

    def go_back(self, instance):
        if self.audio_player:
            self.audio_player.toggle_pause()
            self.audio_player.close_player()
            self.audio_player = None
        self.manager.current = "search"
