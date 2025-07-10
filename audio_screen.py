import threading
from yt_dlp import YoutubeDL
import yt_dlp.cache

from kivy.app import App
from kivy.clock import Clock
from kivy.uix.screenmanager import Screen

from jnius import (
    autoclass, cast,
    PythonJavaClass, java_method
)
from android.runnable import run_on_ui_thread

# Вимикаємо кеш yt_dlp
yt_dlp.cache.store  = lambda *a, **k: None
yt_dlp.cache.load   = lambda *a, **k: None
yt_dlp.cache.remove = lambda *a, **k: None

# Android Java класи
MediaPlayer       = autoclass('android.media.MediaPlayer')
PythonActivity    = autoclass('org.kivy.android.PythonActivity')
Uri               = autoclass('android.net.Uri')

VideoView         = autoclass('android.widget.VideoView')
MediaController   = autoclass('android.widget.MediaController')
LayoutParams      = autoclass('android.view.ViewGroup$LayoutParams')
R_id              = autoclass('android.R$id')

PowerManager      = autoclass('android.os.PowerManager')
activity          = PythonActivity.mActivity
pm                = activity.getSystemService(PowerManager)
wake_lock         = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, 'PyMusic::WakeLock')

android_player = None
is_playing     = False

def acquire_wake_lock():
    try:
        if not wake_lock.isHeld():
            wake_lock.acquire()
    except Exception as e:
        print(f"[WAKE_LOCK] Error acquiring: {e}")

def release_wake_lock():
    try:
        if wake_lock.isHeld():
            wake_lock.release()
    except Exception as e:
        print(f"[WAKE_LOCK] Error releasing: {e}")

# === VideoView Controller (чорна панель) ===

@run_on_ui_thread
def show_native_video(url):
    activity = PythonActivity.mActivity
    if not activity:
        return

    vv = VideoView(activity)
    mc = MediaController(activity)
    mc.setAnchorView(vv)
    vv.setMediaController(mc)
    vv.setVideoURI(Uri.parse(url))

    decor   = activity.getWindow().getDecorView()
    content = cast('android.view.ViewGroup', decor.findViewById(R_id.content))

    lp = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
    setattr(activity, '_video_view', vv)
    content.addView(vv, lp)
    vv.start()

@run_on_ui_thread
def hide_native_video():
    activity = PythonActivity.mActivity
    vv       = getattr(activity, '_video_view', None)
    if vv:
        decor   = activity.getWindow().getDecorView()
        content = cast('android.view.ViewGroup', decor.findViewById(R_id.content))
        content.removeView(vv)
        delattr(activity, '_video_view')

# === Audio MediaPlayer ===

class PreparedListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnPreparedListener']
    @java_method('(Landroid/media/MediaPlayer;)V')
    def onPrepared(self, mp):
        mp.start()
        global is_playing
        is_playing = True
        Clock.schedule_once(lambda dt: App.get_running_app()
                                         .root.get_screen("audio")
                                         ._init_slider(0), 0)

@run_on_ui_thread
def play_audio_android(url):
    global android_player
    ctx = cast('android.content.Context', PythonActivity.mActivity)
    uri = Uri.parse(url)
    android_player = MediaPlayer()
    android_player.setOnPreparedListener(PreparedListener())
    android_player.setDataSource(ctx, uri)
    android_player.prepareAsync()

@run_on_ui_thread
def pause_audio_android():
    global android_player, is_playing
    if android_player and android_player.isPlaying():
        android_player.pause()
        is_playing = False

@run_on_ui_thread
def resume_audio_android():
    global android_player, is_playing
    if android_player:
        android_player.start()
        is_playing = True

@run_on_ui_thread
def stop_audio_android():
    global android_player, is_playing
    if android_player:
        android_player.stop()
        android_player.release()
        android_player = None
    is_playing = False

# === YDL Logger ===

class YDLLogger:
    def debug(self, msg):   pass
    def warning(self, msg): pass
    def error(self, msg):   print(f"[YDL ERROR] {msg}")

# === AudioPlayerScreen ===

class AudioPlayerScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.repeat = False
        self._update_ev = None
        self._last_url = None  # для перемикання між режимами

    def on_pre_leave(self):
        self.stop_audio()
        hide_native_video()

    def play_audio(self, video_url, title, channel, duration):
        acquire_wake_lock()
        self._last_url = video_url  # зберігаємо останній url
        self.stop_audio()
        hide_native_video()
        self.ids.audio_title.text        = title
        self.ids.current_time_label.text = "0:00"
        self.ids.total_time_label.text   = "0:00"
        self.ids.progress_slider.value   = 0
        self.ids.progress_slider.max     = 1
        self.ids.audio_thumbnail.source  = ""
        threading.Thread(
            target=self._fetch_and_play_audio,
            args=(video_url,),
            daemon=True
        ).start()

    def _fetch_and_play_audio(self, video_url):
        opts = {
            'quiet': True,
            'format': 'bestaudio[abr<=64]/bestaudio',
            'noplaylist': True,
            'skip_download': True,
            'nocheckcertificate': True,
            'logger': YDLLogger(),
        }
        try:
            with YoutubeDL(opts) as ydl:
                info      = ydl.extract_info(video_url, download=False)
                audio_url = info['url']
                thumb     = info.get('thumbnail', '')
                Clock.schedule_once(lambda dt: self._update_thumbnail(thumb), 0)
                Clock.schedule_once(lambda dt: play_audio_android(audio_url), 0)
        except Exception as e:
            print(f"[ERROR] Не вдалося отримати URL потоку: {e}")

    def play_video(self):
        """Відтворити відео поверх (VideoView + MediaController)"""
        release_wake_lock()  # не треба wake lock для відео
        self.stop_audio()
        threading.Thread(
            target=self._fetch_and_play_video,
            args=(self._last_url,),
            daemon=True
        ).start()

    def _fetch_and_play_video(self, video_url):
        opts = {
            'quiet': True,
            'format': 'best[ext=mp4]',
            'noplaylist': True,
            'skip_download': True,
            'nocheckcertificate': True,
            'logger': YDLLogger(),
        }
        try:
            with YoutubeDL(opts) as ydl:
                info      = ydl.extract_info(video_url, download=False)
                video_url = info['url']
                Clock.schedule_once(lambda dt: show_native_video(video_url), 0)
        except Exception as e:
            print(f"[ERROR] Не вдалося отримати відео-потік: {e}")

    def _update_thumbnail(self, url):
        if url:
            img = self.ids.audio_thumbnail
            img.source = url
            img.reload()

    def _init_slider(self, dt):
        global android_player
        if android_player:
            dur_s = android_player.getDuration() / 1000.0
            self.ids.progress_slider.max = dur_s
            m, s = divmod(int(dur_s), 60)
            self.ids.total_time_label.text = f"{m}:{s:02}"
            self._update_ev = Clock.schedule_interval(self._update_progress, 0.5)

    def _update_progress(self, dt):
        global android_player, is_playing
        if not android_player:
            return False
        pos_s = android_player.getCurrentPosition() / 1000.0
        if self.repeat and pos_s >= self.ids.progress_slider.max - .1:
            android_player.seekTo(0)
            android_player.start()
            is_playing = True
        self.ids.progress_slider.value = pos_s
        m, s = divmod(int(pos_s), 60)
        self.ids.current_time_label.text = f"{m}:{s:02}"
        return True

    def toggle_play_pause(self, *args):
        global is_playing
        if android_player:
            if is_playing:
                pause_audio_android()
                self.ids.play_pause_btn.text = "▶ Play"
            else:
                resume_audio_android()
                self.ids.play_pause_btn.text = "⏸ Pause"

    def toggle_repeat(self, *args):
        btn = self.ids.repeat_btn
        self.repeat = not self.repeat
        btn.background_color = (0,0.6,0,1) if self.repeat else (0.3,0.3,0.3,1)

    def seek(self, value):
        global android_player
        if android_player:
            android_player.seekTo(int(value * 1000))

    def stop_audio(self, *args):
        stop_audio_android()
        release_wake_lock()
        if self._update_ev:
            Clock.unschedule(self._update_progress)
            self._update_ev = None
        self.ids.play_pause_btn.text = "▶ Play"

    def go_back(self, *args):
        self.stop_audio()
        hide_native_video()
        self.manager.current = "search"

    def play_playlist(self, tracks, playlist_title):
        self.playlist_tracks = tracks
        self.playlist_index = 0
        self.ids.audio_title.text = f"[PL] {playlist_title}"
        self._play_current_track()

    def _play_current_track(self):
        if not hasattr(self, "playlist_tracks"): return
        url, title, channel = self.playlist_tracks[self.playlist_index]
        self.play_audio(url, title, channel, "")

    def _play_next_track(self, *args):
        if hasattr(self, "playlist_tracks") and self.playlist_index + 1 < len(self.playlist_tracks):
            self.playlist_index += 1
            self._play_current_track()