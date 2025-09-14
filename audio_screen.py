# audio_screen.py
import threading
import time
import socket
import urllib.parse as urlparse
import yt_dlp.cache

from yt_dlp import YoutubeDL

from kivy.clock import Clock
from kivy.uix.screenmanager import Screen
from kivy.core.window import Window

from jnius import (autoclass, cast, PythonJavaClass, java_method)
from android.runnable import run_on_ui_thread
from android import activity as android_activity  # ловимо onNewIntent / pause / resume

from stream_recovery import StreamRecovery

# ===== yt_dlp cache OFF =====
yt_dlp.cache.store  = lambda *a, **k: None
yt_dlp.cache.load   = lambda *a, **k: None
yt_dlp.cache.remove = lambda *a, **k: None

# ===== Android classes =====
MediaPlayer         = autoclass('android.media.MediaPlayer')
PythonActivity      = autoclass('org.kivy.android.PythonActivity')
Uri                 = autoclass('android.net.Uri')
VideoView           = autoclass('android.widget.VideoView')
MediaController     = autoclass('android.widget.MediaController')
LayoutParams        = autoclass('android.view.ViewGroup$LayoutParams')
R_id                = autoclass('android.R$id')
PowerManager        = autoclass('android.os.PowerManager')
WifiManager         = autoclass('android.net.wifi.WifiManager')
Context             = autoclass('android.content.Context')
ConnectivityManager = autoclass('android.net.ConnectivityManager')
NetworkCapabilities = autoclass('android.net.NetworkCapabilities')
HashMap             = autoclass('java.util.HashMap')
KeyEvent            = autoclass('android.view.KeyEvent')
InputDevice         = autoclass('android.view.InputDevice')

# MediaSession + PlaybackState
MediaSession        = autoclass('android.media.session.MediaSession')
PlaybackState       = autoclass('android.media.session.PlaybackState')
PlaybackStateBuilder= autoclass('android.media.session.PlaybackState$Builder')
Intent              = autoclass('android.content.Intent')

# Audio Focus
AudioManager             = autoclass('android.media.AudioManager')
AudioAttributesBuilder   = autoclass('android.media.AudioAttributes$Builder')
AudioFocusRequestBuilder = autoclass('android.media.AudioFocusRequest$Builder')
Build_VERSION            = autoclass('android.os.Build$VERSION')

# Notifications
NotificationManager  = autoclass('android.app.NotificationManager')
NotificationBuilder  = autoclass('android.app.Notification$Builder')
PendingIntent        = autoclass('android.app.PendingIntent')
try:
    NotificationChannel = autoclass('android.app.NotificationChannel')  # API 26+
except Exception:
    NotificationChannel = None
try:
    MediaStyle = autoclass('android.app.Notification$MediaStyle')       # API 21+
except Exception:
    MediaStyle = None

# ===== simple file logger =====
from datetime import datetime
try:
    _ctx = PythonActivity.mActivity
    _ext = _ctx.getExternalFilesDir(None)
    if _ext is not None:
        LOG_PATH = _ext.getAbsolutePath() + "/pymusic_diag.txt"
    else:
        LOG_PATH = _ctx.getFilesDir().getAbsolutePath() + "/pymusic_diag.txt"
except Exception:
    LOG_PATH = "/data/local/tmp/pymusic_diag.txt"

DEBUG_VERBOSE = True
def log(msg: str):
    try:
        ts = datetime.now().strftime("%H:%M:%S")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except Exception:
        pass

def vlog(msg: str):
    if DEBUG_VERBOSE: log(msg)

def _is_foreground():
    try:
        return bool(PythonActivity.mActivity.hasWindowFocus())
    except Exception:
        return None

# ===== Context + Wake/WiFi locks =====
activity   = PythonActivity.mActivity
pm         = activity.getSystemService(Context.POWER_SERVICE)
wake_lock  = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, 'PyMusic::WakeLock')
wifi       = activity.getSystemService(Context.WIFI_SERVICE)
wifi_lock  = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "PyMusicWifiLock")

android_player = None
is_playing     = False

# ===== MediaPlayer listeners =====
class OnCompletionListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnCompletionListener']
    def __init__(self, callback): super().__init__(); self.callback = callback
    @java_method('(Landroid/media/MediaPlayer;)V')
    def onCompletion(self, mp):
        threading.Thread(target=self.callback, daemon=True).start()

class OnPreparedListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnPreparedListener']
    def __init__(self, callback): super().__init__(); self.callback = callback
    @java_method('(Landroid/media/MediaPlayer;)V')
    def onPrepared(self, mp):
        threading.Thread(target=lambda: self.callback(mp), daemon=True).start()

class OnErrorListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnErrorListener']
    def __init__(self, callback): super().__init__(); self.callback = callback
    @java_method('(Landroid/media/MediaPlayer;II)Z')
    def onError(self, mp, what, extra):
        threading.Thread(target=lambda: self.callback(what, extra), daemon=True).start()
        return True

class OnInfoListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnInfoListener']
    def __init__(self, callback): super().__init__(); self.callback = callback
    @java_method('(Landroid/media/MediaPlayer;II)Z')
    def onInfo(self, mp, what, extra):
        threading.Thread(target=lambda: self.callback(what, extra), daemon=True).start()
        return False

# ===== Headset key support (foreground) =====
class OnKeyListener(PythonJavaClass):
    __javainterfaces__ = ['android/view/View$OnKeyListener']
    def __init__(self, owner): super().__init__(); self.owner = owner
    @java_method('(Landroid/view/View;ILandroid/view/KeyEvent;)Z')
    def onKey(self, v, keyCode, event):
        try:
            if event.getAction() != KeyEvent.ACTION_DOWN:
                return False
            # джерело події (спроба відрізнити гарнітуру)
            try:
                dev = InputDevice.getDevice(event.getDeviceId())
                is_headset = bool(dev) and ("bluetooth" in str(dev.getName()).lower() or "headset" in str(dev.getName()).lower())
            except Exception:
                is_headset = False

            if keyCode in (
                KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                KeyEvent.KEYCODE_HEADSETHOOK,
                KeyEvent.KEYCODE_MEDIA_PLAY,
                KeyEvent.KEYCODE_MEDIA_PAUSE,
                KeyEvent.KEYCODE_MEDIA_STOP,
                KeyEvent.KEYCODE_MEDIA_PREVIOUS,
                KeyEvent.KEYCODE_MEDIA_NEXT,
            ):
                self.owner._last_media_intent_ts = time.time()
                Clock.schedule_once(lambda dt: self.owner._ms_toggle() if keyCode in (
                    KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                    KeyEvent.KEYCODE_HEADSETHOOK,
                    KeyEvent.KEYCODE_MEDIA_PLAY,
                    KeyEvent.KEYCODE_MEDIA_PAUSE
                ) else (self.owner._ms_next() if keyCode == KeyEvent.KEYCODE_MEDIA_NEXT else self.owner._ms_prev()), 0)
                return True

            # пробуємо «з’їсти» гучність від гарнітури (у фореграунді)
            if keyCode in (KeyEvent.KEYCODE_VOLUME_UP, KeyEvent.KEYCODE_VOLUME_DOWN):
                if is_headset or (time.time() - getattr(self.owner, "_last_media_intent_ts", 0.0) < 0.80):
                    log(f"[KEY] swallow VOLUME (headset/after-media) keyCode={keyCode}")
                    return True
        except Exception:
            pass
        return False

@run_on_ui_thread
def _attach_key_listeners(owner):
    try:
        act = PythonActivity.mActivity
        win = act.getWindow()
        decor = win.getDecorView()
        content = cast('android.view.ViewGroup', decor.findViewById(R_id.content))
        owner._key_listener = OnKeyListener(owner)
        for view in (decor, content):
            view.setFocusableInTouchMode(True)
            view.setFocusable(True)
            view.requestFocus()
            view.setOnKeyListener(owner._key_listener)
    except Exception:
        pass

@run_on_ui_thread
def _detach_key_listeners(owner):
    try:
        act = PythonActivity.mActivity
        win = act.getWindow()
        decor = win.getDecorView()
        content = cast('android.view.ViewGroup', decor.findViewById(R_id.content))
        for view in (decor, content):
            view.setOnKeyListener(None)
        owner._key_listener = None
    except Exception:
        pass

def _bind_window_keys(owner):
    def _on_kivy_key_down(window, keycode, scancode, text, modifiers):
        try:
            num = keycode[0] if isinstance(keycode, (tuple, list)) else int(keycode)
            name = keycode[1] if isinstance(keycode, (tuple, list)) and len(keycode) > 1 else str(keycode)
            media_names = ("playpause","media_play_pause","headsethook","media_play","media_pause","media_stop","media_previous","media_next")
            if num in (79, 85, 86, 87, 88) or str(name).lower() in media_names:
                owner._last_media_intent_ts = time.time()
                if num == 87 or name == "media_next":
                    owner._ms_next()
                elif num == 88 or name == "media_previous":
                    owner._ms_prev()
                else:
                    owner._ms_toggle()
                return True
            # ковтач VOLUME одразу після медіа-події
            if num in (24, 25) and time.time() - getattr(owner, "_last_media_intent_ts", 0.0) < 0.80:
                log(f"[KEY] swallow volume {num}")
                return True
        except Exception:
            pass
        return False
    owner._win_key_uid = Window.bind(on_key_down=_on_kivy_key_down)

def _unbind_window_keys(owner):
    try:
        if getattr(owner, "_win_key_uid", None):
            Window.unbind_uid('on_key_down', owner._win_key_uid)
            owner._win_key_uid = None
    except Exception:
        pass

# ===== AudioFocus listener =====
class AFChangeListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/AudioManager$OnAudioFocusChangeListener']
    def __init__(self, owner): super().__init__(); self.owner = owner
    @java_method('(I)V')
    def onAudioFocusChange(self, focusChange):
        try:
            if focusChange == AudioManager.AUDIOFOCUS_GAIN:
                if android_player and not android_player.isPlaying() and not self.owner._user_paused:
                    _mp_start()
                    Clock.schedule_once(lambda dt: self.owner._ui_set_playing(True), 0)
                    self.owner._push_ms_state()
            elif focusChange in (AudioManager.AUDIOFOCUS_LOSS, AudioManager.AUDIOFOCUS_LOSS_TRANSIENT):
                if android_player and android_player.isPlaying():
                    _mp_pause()
                    self.owner._user_paused = True
                    self.owner.recovery.set_user_paused(True)
                    Clock.schedule_once(lambda dt: self.owner._ui_set_playing(False), 0)
                    self.owner._push_ms_state()
                if focusChange == AudioManager.AUDIOFOCUS_LOSS:
                    self.owner._has_af = False
        except Exception:
            pass

# ===== Wake/WiFi helpers =====
def acquire_wake_lock():
    try:
        if not wake_lock.isHeld(): wake_lock.acquire()
        if not wifi_lock.isHeld():  wifi_lock.acquire()
        log("[LOCK] acquired")
    except Exception as e:
        log(f"[LOCK] acquire error: {e}")

def release_wake_lock():
    try:
        if wake_lock.isHeld(): wake_lock.release()
        if wifi_lock.isHeld():  wifi_lock.release()
        log("[LOCK] released")
    except Exception as e:
        log(f"[LOCK] release error: {e}")

# ===== Network quick check =====
def _is_network_available():
    try:
        cm = activity.getSystemService(Context.CONNECTIVITY_SERVICE)
        net = cm.getActiveNetwork()
        if net is None: return False
        caps = cm.getNetworkCapabilities(net)
        if caps is None: return False
        if caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED):
            return True
    except Exception:
        pass
    try:
        sock = socket.create_connection(("1.1.1.1", 53), timeout=2.0)
        sock.close()
        return True
    except Exception:
        return False

# ===== Video overlay (optional) =====
@run_on_ui_thread
def show_native_video(url):
    act = PythonActivity.mActivity
    if not act: return
    vv = VideoView(act); mc = MediaController(act); mc.setAnchorView(vv); vv.setMediaController(mc)
    vv.setVideoURI(Uri.parse(url))
    decor = act.getWindow().getDecorView()
    content = cast('android.view.ViewGroup', decor.findViewById(R_id.content))
    lp = LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
    setattr(act, '_video_view', vv); content.addView(vv, lp); vv.start()

@run_on_ui_thread
def hide_native_video():
    act = PythonActivity.mActivity
    vv  = getattr(act, '_video_view', None)
    if vv:
        decor = act.getWindow().getDecorView()
        content = cast('android.view.ViewGroup', decor.findViewById(R_id.content))
        content.removeView(vv); delattr(act, '_video_view')

# ===== Java headers map =====
def _py_headers_to_javamap(headers: dict):
    m = HashMap()
    if headers:
        for k, v in headers.items():
            if k and v: m.put(str(k), str(v))
    if not m.containsKey("User-Agent"):
        m.put("User-Agent", "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36")
    if not m.containsKey("Referer"):
        m.put("Referer", "https://www.youtube.com")
    return m

# ===== MediaPlayer wrappers (UI thread) =====
@run_on_ui_thread
def _mp_create_set_source_and_prepare_async(audio_url, headers, on_prepared, on_completed, on_error, on_info):
    global android_player
    try:
        android_player = MediaPlayer()
        android_player.setOnPreparedListener(OnPreparedListener(on_prepared))
        if on_completed:
            android_player.setOnCompletionListener(OnCompletionListener(on_completed))
        android_player.setOnErrorListener(OnErrorListener(on_error))
        android_player.setOnInfoListener(OnInfoListener(on_info))
        ctx = cast('android.content.Context', PythonActivity.mActivity)
        if headers:
            android_player.setDataSource(ctx, Uri.parse(audio_url), _py_headers_to_javamap(headers))
        else:
            android_player.setDataSource(ctx, Uri.parse(audio_url))
        android_player.prepareAsync()
    except Exception as e:
        log(f"[MP] prepareAsync error: {e}")

@run_on_ui_thread
def _mp_pause():
    global android_player, is_playing
    if android_player and android_player.isPlaying():
        android_player.pause(); is_playing = False; vlog("[MP] pause()")

@run_on_ui_thread
def _mp_start():
    global android_player, is_playing
    if android_player:
        android_player.start(); is_playing = True; vlog("[MP] start()")

@run_on_ui_thread
def _mp_seek_to(ms):
    global android_player
    if android_player:
        android_player.seekTo(int(ms)); vlog(f"[MP] seekTo {ms}ms")

@run_on_ui_thread
def _mp_reset_release():
    global android_player, is_playing
    try:
        if android_player:
            android_player.reset()
            android_player.release()
            vlog("[MP] reset+release")
    except Exception as e:
        log(f"[MP] release err: {e}")
    android_player = None; is_playing = False

# ===== yt-dlp helpers =====
class YDLLogger:
    def debug(self, msg):   print(f"[YDL] {msg}") if "Downloading webpage" in msg else None
    def warning(self, msg): print(f"[YDL WARN] {msg}")
    def error(self, msg):   print(f"[YDL ERROR] {msg}")

def _parse_expire_ts(url):
    try:
        q = urlparse.urlparse(url).query
        params = urlparse.parse_qs(q)
        if 'expire' in params:
            return int(params['expire'][0])
    except Exception:
        pass
    return None

def _extract_audio_info(video_url):
    base_opts = {
        'quiet': True,
        'format': (
            'bestaudio[acodec^=opus]/bestaudio[ext=webm]/'
            'bestaudio[ext=m4a]/bestaudio/best'
        ),
        'noplaylist': True,
        'skip_download': True,
        'nocheckcertificate': True,
        'logger': YDLLogger(),
        'extractor_args': {'youtube': {'player_client': ['android']}},
    }
    try:
        with YoutubeDL(base_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
    except Exception:
        fallback = dict(base_opts); fallback['format'] = 'bestaudio/best'
        with YoutubeDL(fallback) as ydl:
            info = ydl.extract_info(video_url, download=False)

    audio_url = info.get('url')
    if not audio_url:
        for f in info.get('formats', []):
            if f.get('vcodec') == 'none' and f.get('acodec') != 'none' and f.get('url'):
                audio_url = f['url']; break
    if not audio_url:
        raise RuntimeError("No audio format URL resolved")

    headers = info.get('http_headers') or {}
    if not headers:
        for f in info.get('formats', []):
            if f.get('url') == audio_url and f.get('http_headers'):
                headers = f['http_headers']; break

    thumb = info.get('thumbnail', '')
    expire_ts = _parse_expire_ts(audio_url)
    return {'audio_url': audio_url, 'thumb': thumb, 'expire_ts': expire_ts, 'http_headers': headers or {}}

# ===== MediaSession Callback (background media buttons) =====
class MSCallback(PythonJavaClass):
    __javainterfaces__ = ['android/media/session/MediaSession$Callback']
    def __init__(self, owner): super().__init__(); self.owner = owner

    @java_method('()V')
    def onPlay(self):
        Clock.schedule_once(lambda dt: self.owner._ms_play(), 0)

    @java_method('()V')
    def onPause(self):
        Clock.schedule_once(lambda dt: self.owner._ms_pause(), 0)

    @java_method('(Landroid/content/Intent;)Z')
    def onMediaButtonEvent(self, intent):
        try:
            ke = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT)
            if not ke or ke.getAction() != KeyEvent.ACTION_DOWN:
                return False
            code = ke.getKeyCode()
            self.owner._last_media_intent_ts = time.time()
            log(f"[MS] media button code={code}")
            if code in (KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                        KeyEvent.KEYCODE_HEADSETHOOK,
                        KeyEvent.KEYCODE_MEDIA_PLAY,
                        KeyEvent.KEYCODE_MEDIA_PAUSE):
                Clock.schedule_once(lambda dt: self.owner._ms_toggle(), 0)
                return True
            if code == KeyEvent.KEYCODE_MEDIA_NEXT:
                Clock.schedule_once(lambda dt: self.owner._ms_next(), 0); return True
            if code == KeyEvent.KEYCODE_MEDIA_PREVIOUS:
                Clock.schedule_once(lambda dt: self.owner._ms_prev(), 0); return True
        except Exception:
            pass
        return False

# ===== Screen =====
class AudioPlayerScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.repeat = False
        self._update_ev = None
        self._refresh_ev = None
        self._watchdog_ev = None

        self._last_video_url = None
        self._current_stream_url = None
        self._current_expire_ts = None
        self._current_headers = {}

        self._retry_cnt = 0
        self._last_pos_ms = 0
        self._stall_started_ts = None

        # playlist state
        self.playlist_tracks = []  # list[(url, title, channel)]
        self.playlist_index = 0

        self._key_listener = None
        self._win_key_uid = None

        self._user_paused = False

        # метадані для UI
        self._current_title = ""
        self._current_channel = ""
        self._current_thumb = ""
        self._need_ui_sync = False

        # MediaSession
        self._media_session = None
        self._ms_cb = None

        # Audio Focus
        self._audio_manager = activity.getSystemService(Context.AUDIO_SERVICE)
        self._af_listener   = AFChangeListener(self)
        self._af_request    = None
        self._has_af        = False

        # Notification
        self._notif_manager = activity.getSystemService(Context.NOTIFICATION_SERVICE)
        self._notif_id      = 4242

        try:
            PythonActivity.mActivity.setVolumeControlStream(AudioManager.STREAM_MUSIC)
        except Exception:
            pass

        # debounce & ковтач гучності
        self._last_action_ts = 0.0
        self._last_media_intent_ts = 0.0
        self._vol_key_uid = None

        # відновлення стріму
        self.recovery = StreamRecovery(
            on_resume=self._resume_from_recovery,
            on_pause=self._pause_from_recovery,
            on_refresh=self._refresh_stream_and_resume,
            get_player_state=self._get_player_state
        )

        # ловимо onNewIntent + життєвий цикл
        try:
            android_activity.bind(on_new_intent=self._on_new_intent)
            android_activity.bind(on_pause=self._on_android_pause)
            android_activity.bind(on_resume=self._on_android_resume)
        except Exception as e:
            log(f"[INTENT] bind err: {e}")

        # створюємо MediaSession одразу
        self._ensure_media_session()

    # ——— helpers ———
    def _debounce(self, min_dt: float = 0.30) -> bool:
        now = time.time()
        if now - getattr(self, "_last_action_ts", 0) < min_dt:
            return False
        self._last_action_ts = now
        return True

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

    # ——— recovery → player ———
    def _resume_from_recovery(self):
        if self._user_paused: return
        if android_player:
            self._request_audio_focus()
            self._set_ms_active(True)
            _mp_start()
            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
            self._push_ms_state()

    def _pause_from_recovery(self):
        if android_player and android_player.isPlaying():
            _mp_pause()
            self._user_paused = True
            self.recovery.set_user_paused(True)
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
            self._push_ms_state()

    def _get_player_state(self):
        try:
            if not android_player: return None
            pos = android_player.getCurrentPosition() or 0
            dur = android_player.getDuration() or 1
            playing = android_player.isPlaying()
            return pos, dur, playing
        except Exception:
            return None

    # === android lifecycle hooks ===
    def _on_android_pause(self, *a):
        log("[LIFE] on_pause")
        self._push_ms_state()
        return True

    def _on_android_resume(self, *a):
        log("[LIFE] on_resume")
        self._push_ms_state()

    # === lifecycle of screen ===
    def on_pre_enter(self):
        _bind_window_keys(self)
        _attach_key_listeners(self)
        self._set_ms_active(True)
        self._push_ms_state()
        self.recovery.start()
        self._maybe_sync_ui()
        self._bind_volume_swallow()

    def on_pre_leave(self):
        hide_native_video()
        _unbind_window_keys(self)
        _detach_key_listeners(self)
        # НЕ вимикаємо сесію — хай живе в фоні
        self._set_ms_active(True)
        self._push_ms_state()
        self._unbind_volume_swallow()

    # ====== MEDIASESSION helpers ======
    def _ensure_media_session(self):
        if self._media_session: return
        try:
            ctx = cast('android.content.Context', PythonActivity.mActivity)
            ms = MediaSession(ctx, "PyMusicSession")
            ms.setFlags(
                MediaSession.FLAG_HANDLES_MEDIA_BUTTONS |
                MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS
            )

            # Тап по нотифікації -> відкрити активність
            intent_open = Intent(ctx, PythonActivity)
            intent_open.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP)
            flags = PendingIntent.FLAG_UPDATE_CURRENT
            try:
                if Build_VERSION.SDK_INT >= 23:
                    flags |= PendingIntent.FLAG_IMMUTABLE
            except Exception:
                pass
            open_pi = PendingIntent.getActivity(ctx, 0, intent_open, flags)
            try:
                ms.setSessionActivity(open_pi)
            except Exception:
                pass

            self._ms_cb = MSCallback(self)
            ms.setCallback(self._ms_cb)

            self._media_session = ms
            log("[MS] created & callback set")
        except Exception as e:
            log(f"[MS] create err: {e}")

    def _set_ms_active(self, active: bool):
        try:
            if self._media_session:
                self._media_session.setActive(bool(active))
        except Exception:
            pass

    def _push_ms_state(self):
        try:
            if not self._media_session: return
            actions = (PlaybackState.ACTION_PLAY |
                       PlaybackState.ACTION_PAUSE |
                       PlaybackState.ACTION_PLAY_PAUSE |
                       PlaybackState.ACTION_STOP |
                       PlaybackState.ACTION_SKIP_TO_NEXT |
                       PlaybackState.ACTION_SKIP_TO_PREVIOUS |
                       PlaybackState.ACTION_SEEK_TO)
            b = PlaybackStateBuilder()
            b.setActions(actions)
            pos = 0
            playing = False
            try:
                if android_player:
                    pos = android_player.getCurrentPosition() or 0
                    playing = android_player.isPlaying()
            except Exception:
                pass
            state = PlaybackState.STATE_PLAYING if playing else PlaybackState.STATE_PAUSED
            b.setState(state, pos, 1.0)
            self._media_session.setPlaybackState(b.build())
            self._update_player_notification(playing)
        except Exception as e:
            log(f"[MS] push state err: {e}")

    # ловимо інтенти від системи та нотифікації
    def _on_new_intent(self, intent):
        try:
            if intent is None:
                return
            act = intent.getAction()
            log(f"[INTENT] action={act}")

            # --- Системні кнопки ---
            if act == Intent.ACTION_MEDIA_BUTTON:
                ke = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT)
                if not ke or ke.getAction() != KeyEvent.ACTION_DOWN:
                    return
                if ke.getRepeatCount() > 0:
                    return

                self._last_media_intent_ts = time.time()
                if not self._debounce():
                    return

                code = ke.getKeyCode()
                log(f"[INTENT] keyCode={code}")

                if code in (KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                            KeyEvent.KEYCODE_HEADSETHOOK,
                            KeyEvent.KEYCODE_MEDIA_PLAY,
                            KeyEvent.KEYCODE_MEDIA_PAUSE):
                    self._ms_toggle(); return
                if code == KeyEvent.KEYCODE_MEDIA_NEXT:
                    self._ms_next(); return
                if code == KeyEvent.KEYCODE_MEDIA_PREVIOUS:
                    self._ms_prev(); return
                return

            # --- Наші дії з нотифікації ---
            if act and act.startswith("org.koteuka404.pymusic."):
                self._last_media_intent_ts = time.time()
                if not self._debounce():
                    return
                log(f"[INTENT] notif={act}")
                if act == "org.koteuka404.pymusic.PAUSE":
                    self._ms_pause(); return
                if act == "org.koteuka404.pymusic.PLAY":
                    self._ms_play(); return
                if act == "org.koteuka404.pymusic.TOGGLE":
                    self._ms_toggle(); return
                if act == "org.koteuka404.pymusic.NEXT":
                    self._ms_next(); return
                if act == "org.koteuka404.pymusic.PREV":
                    self._ms_prev(); return
        except Exception as e:
            log(f"[INTENT] on_new_intent err: {e}")

    # — ковтач VOLUME_UP/DOWN після медіакнопки —
    def _bind_volume_swallow(self):
        def _on_kivy_key_down(window, keycode, scancode, text, modifiers):
            try:
                code = keycode[0] if isinstance(keycode, (tuple, list)) else int(keycode)
            except Exception:
                return False
            if code in (24, 25):  # VOLUME_UP / VOLUME_DOWN
                if time.time() - getattr(self, "_last_media_intent_ts", 0.0) < 0.80:
                    log(f"[KEY] swallow volume {code}")
                    return True
            return False
        self._vol_key_uid = Window.bind(on_key_down=_on_kivy_key_down)

    def _unbind_volume_swallow(self):
        try:
            if getattr(self, "_vol_key_uid", None):
                Window.unbind_uid('on_key_down', self._vol_key_uid)
                self._vol_key_uid = None
        except Exception:
            pass

    # ===== колбеки, що викликаємо з інтентів/кнопок =====
    def _ms_play(self):
        if android_player and not android_player.isPlaying():
            self._user_paused = False
            self.recovery.set_user_paused(False)
            self._request_audio_focus()
            self._set_ms_active(True)
            _mp_start()
            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
        self._push_ms_state()

    def _ms_pause(self):
        if android_player and android_player.isPlaying():
            self._user_paused = True
            self.recovery.set_user_paused(True)
            _mp_pause()
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
        self._push_ms_state()

    def _ms_toggle(self):
        if not self._debounce():
            return
        if android_player and android_player.isPlaying():
            self._user_paused = True
            self.recovery.set_user_paused(True)
            _mp_pause()
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
        else:
            self._user_paused = False
            self.recovery.set_user_paused(False)
            self._request_audio_focus()
            self._set_ms_active(True)
            _mp_start()
            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
        self._push_ms_state()

    def _ms_next(self):
        if self.playlist_tracks:
            self._play_next_in_playlist_adaptive()
        self._push_ms_state()

    def _ms_prev(self):
        if self.playlist_tracks:
            try:
                pos = android_player.getCurrentPosition() if android_player else 0
            except Exception:
                pos = 0
            if pos and pos > 5000:
                _mp_seek_to(0)
            else:
                self._play_prev_in_playlist()
        self._push_ms_state()

    # ====== AUDIO FOCUS helpers ======
    def _request_audio_focus(self):
        try:
            if self._has_af:
                return True
            if Build_VERSION.SDK_INT >= 26:
                aa  = AudioAttributesBuilder()\
                        .setUsage(AudioManager.USAGE_MEDIA)\
                        .setContentType(AudioManager.CONTENT_TYPE_MUSIC)\
                        .build()
                afr = AudioFocusRequestBuilder(AudioManager.AUDIOFOCUS_GAIN)\
                        .setAudioAttributes(aa)\
                        .setOnAudioFocusChangeListener(self._af_listener)\
                        .build()
                res = self._audio_manager.requestAudioFocus(afr)
                self._af_request = afr
            else:
                res = self._audio_manager.requestAudioFocus(
                    self._af_listener,
                    AudioManager.STREAM_MUSIC,
                    AudioManager.AUDIOFOCUS_GAIN
                )
            self._has_af = (res == AudioManager.AUDIOFOCUS_REQUEST_GRANTED)
            return self._has_af
        except Exception as e:
            log(f"[AF] request err: {e}")
            return False

    def _abandon_audio_focus(self):
        try:
            if not self._has_af:
                return
            if Build_VERSION.SDK_INT >= 26 and self._af_request is not None:
                self._audio_manager.abandonAudioFocusRequest(self._af_request)
                self._af_request = None
            else:
                self._audio_manager.abandonAudioFocus(self._af_listener)
            self._has_af = False
        except Exception as e:
            log(f"[AF] abandon err: {e}")

    # ====== Now Playing notification ======
    def _ensure_player_channel(self):
        try:
            if NotificationChannel is None:
                return
            nm = self._notif_manager
            if nm.getNotificationChannel("pymusic_player") is None:
                ch = NotificationChannel("pymusic_player", "PyMusic Player",
                                         NotificationManager.IMPORTANCE_LOW)
                nm.createNotificationChannel(ch)
        except Exception as e:
            log(f"[NOTIF] channel err: {e}")

    def _update_player_notification(self, is_playing: bool):
        try:
            act = PythonActivity.mActivity
            if NotificationChannel is not None:
                self._ensure_player_channel()
                b = NotificationBuilder(act, "pymusic_player")
            else:
                b = NotificationBuilder(act)

            title = self._current_title or "Playing audio"
            text  = self._current_channel or "YouTube"
            b.setContentTitle(title)
            b.setContentText(text)
            b.setSmallIcon(act.getApplicationInfo().icon)
            b.setOngoing(bool(is_playing))

            # tap -> bring app to front
            intent_open = Intent(act, PythonActivity)
            intent_open.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP)
            flags = PendingIntent.FLAG_UPDATE_CURRENT
            try:
                if Build_VERSION.SDK_INT >= 23:
                    flags |= PendingIntent.FLAG_IMMUTABLE
            except Exception:
                pass
            pi_open = PendingIntent.getActivity(act, 0, intent_open, flags)
            b.setContentIntent(pi_open)

            # actions (Prev / Play-Pause / Next)
            def _pi(action, req):
                it = Intent()
                it.setClass(act, PythonActivity)
                it.setAction(action)
                return PendingIntent.getActivity(act, req, it, flags)

            try:
                draw = autoclass('android.R$drawable')
                b.addAction(draw.ic_media_previous, "Prev", _pi("org.koteuka404.pymusic.PREV", 203))
                if is_playing:
                    b.addAction(draw.ic_media_pause, "Pause", _pi("org.koteuka404.pymusic.PAUSE", 201))
                else:
                    b.addAction(draw.ic_media_play, "Play", _pi("org.koteuka404.pymusic.PLAY", 202))
                b.addAction(draw.ic_media_next, "Next", _pi("org.koteuka404.pymusic.NEXT", 204))
            except Exception as e:
                log(f"[NOTIF] addAction err: {e}")

            # MediaStyle
            if MediaStyle is not None and self._media_session:
                style = MediaStyle()
                style.setMediaSession(self._media_session.getSessionToken())
                b.setStyle(style)

            self._notif_manager.notify(self._notif_id, b.build())
        except Exception as e:
            log(f"[NOTIF] player update err: {e}")

    def _cancel_player_notification(self):
        try:
            self._notif_manager.cancel(self._notif_id)
        except Exception:
            pass

    # ====== PLAYLIST API ======
    def play_playlist(self, tracks, playlist_title=""):
        try:
            norm = []
            for u, t, c in tracks:
                u = u or ""
                if not str(u).startswith("http"):
                    u = f"https://www.youtube.com/watch?v={u}"
                norm.append((u, t, c))
            self.playlist_tracks = norm
            self.playlist_index = 0
            if not self.playlist_tracks:
                return
            self._play_current_index()
        except Exception as e:
            log(f"play_playlist err: {e}")

    def _after_track_switch(self):
        self._user_paused = False
        self.recovery.set_user_paused(False)
        self._push_ms_state()
        try:
            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
        except Exception:
            pass

    def _play_current_index(self):
        if not self.playlist_tracks:
            return
        i = max(0, min(self.playlist_index, len(self.playlist_tracks)-1))
        url, title, channel = self.playlist_tracks[i]
        self.play_audio(url, title, channel, "")
        self._after_track_switch()

    def _play_next_in_playlist(self):
        if not self.playlist_tracks:
            return
        self.playlist_index += 1
        if self.playlist_index >= len(self.playlist_tracks):
            self.playlist_index = 0
        self._play_current_index()

    def _play_prev_in_playlist(self):
        if not self.playlist_tracks:
            return
        self.playlist_index -= 1
        if self.playlist_index < 0:
            self.playlist_index = len(self.playlist_tracks) - 1
        self._play_current_index()

    # === Public ===
    def play_audio(self, video_url, title, channel, duration):
        acquire_wake_lock()
        self._last_video_url = video_url
        self._retry_cnt = 0
        self.stop_audio()
        hide_native_video()

        self._user_paused = False
        self.recovery.set_user_paused(False)

        # кеш метаданих
        self._current_title = title or ""
        self._current_channel = channel or ""
        self._current_thumb = ""
        self._need_ui_sync = True

        # швидкий ресет UI
        def _ui_init(dt):
            self.ids.audio_title.text        = self._current_title
            self.ids.current_time_label.text = "0:00"
            self.ids.total_time_label.text   = "0:00"
            self.ids.progress_slider.value   = 0
            self.ids.progress_slider.max     = 1
            self.ids.audio_thumbnail.source  = ""
            self.ids.repeat_btn.source = "ico/repeat_active.png" if self.repeat else "ico/icorepeat.png"
            self._ui_set_playing(True)
        Clock.schedule_once(_ui_init, 0)

        threading.Thread(target=self._start_stream_from_video_url, args=(video_url,), daemon=True).start()

    def play_video(self):
        release_wake_lock()
        self.stop_audio()
        threading.Thread(target=self._fetch_and_play_video, args=(self._last_video_url,), daemon=True).start()

    def toggle_play_pause(self, *args):
        self._ms_toggle()

    def toggle_repeat(self, *args):
        btn = self.ids.repeat_btn
        self.repeat = not self.repeat
        Clock.schedule_once(lambda dt: setattr(btn, "source", "ico/repeat_active.png" if self.repeat else "ico/icorepeat.png"), 0)
        log(f"toggle_repeat -> {self.repeat}")

    def seek(self, value):
        if android_player:
            _mp_seek_to(int(value * 1000))
            self._push_ms_state()
            vlog(f"seek -> {value}s")

    def stop_audio(self, *args):
        self._cancel_events()
        _mp_reset_release()
        release_wake_lock()
        self._abandon_audio_focus()
        Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
        self._push_ms_state()
        self._cancel_player_notification()
        # спеціально НЕ деактивуємо сесію — щоб кнопки працювали з фону

    def go_back(self, *args):
        self.stop_audio(); hide_native_video(); self.manager.current = "search"

    # === Internal ===
    def _cancel_events(self):
        if self._update_ev:
            Clock.unschedule(self._update_progress); self._update_ev = None
        if self._refresh_ev:
            try: self._refresh_ev.cancel()
            except: pass
            self._refresh_ev = None
        if self._watchdog_ev:
            try: self._watchdog_ev.cancel()
            except: pass
            self._watchdog_ev = None
        vlog("_cancel_events()")

    # ----- Stream bootstrap -----
    def _start_stream_from_video_url(self, video_url):
        try:
            info = _extract_audio_info(video_url)
            self._current_stream_url = info['audio_url']
            self._current_expire_ts  = info.get('expire_ts')
            self._current_headers    = info.get('http_headers', {})
            thumb = info.get('thumb', '')

            self._current_thumb = thumb or ""
            self._need_ui_sync = True
            self._maybe_sync_ui()

            def on_prepared_fg(mp):
                try:
                    _ = mp.getDuration()
                except Exception:
                    pass
                self._request_audio_focus()
                self._set_ms_active(True)
                _mp_start()
                Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
                self._schedule_progress_updates()
                self._schedule_expire_refresh()
                self._start_watchdog()
                self._push_ms_state()

            def on_error(what, extra):
                self._retry_cnt += 1
                if self._retry_cnt <= 3:
                    self._restart_same_url_adaptive()
                else:
                    Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
                    self._push_ms_state()

            _mp_create_set_source_and_prepare_async(
                self._current_stream_url,
                self._current_headers,
                on_prepared_fg,
                self._on_completion,
                on_error,
                lambda w,e: None
            )
        except Exception as e:
            log(f"start_stream failed: {e}")
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
            self._push_ms_state()

    # ----- COMPLETION -----
    def _on_completion(self):
        self._user_paused = False
        self.recovery.set_user_paused(False)

        if self.repeat:
            self._restart_same_url_adaptive()
        elif self.playlist_tracks:
            self._play_next_in_playlist_adaptive()
        else:
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
        self._push_ms_state()

    def _restart_same_url_adaptive(self):
        if _is_foreground():
            self._restart_same_url()
        else:
            self._restart_same_url_headless()

    def _play_next_in_playlist_adaptive(self):
        if _is_foreground():
            self._play_next_in_playlist()
        else:
            self._play_next_in_playlist_headless()

    # ----- BG/headless variants -----
    def _restart_same_url_headless(self):
        _mp_reset_release()

        def on_prepared_bg(mp):
            self._request_audio_focus()
            self._set_ms_active(True)
            _mp_start()
            self._push_ms_state()

        _mp_create_set_source_and_prepare_async(
            self._current_stream_url,
            self._current_headers,
            on_prepared_bg,
            self._on_completion,
            lambda w,e: None,
            lambda w,e: None
        )

    def _play_next_in_playlist_headless(self):
        if not self.playlist_tracks:
            return
        self.playlist_index += 1
        if self.playlist_index >= len(self.playlist_tracks):
            self.playlist_index = 0
        url, title, channel = self.playlist_tracks[self.playlist_index]

        try:
            info = _extract_audio_info(url)
            self._last_video_url     = url
            self._current_stream_url = info['audio_url']
            self._current_expire_ts  = info.get('expire_ts')
            self._current_headers    = info.get('http_headers', {})
            self._current_title      = title or ""
            self._current_channel    = channel or ""
            self._current_thumb      = info.get('thumb', '') or ""
            self._need_ui_sync       = True
        except Exception as e:
            log(f"bg extract next failed: {e}")
            return

        _mp_reset_release()

        def on_prepared_bg(mp):
            self._request_audio_focus()
            self._set_ms_active(True)
            _mp_start()
            self._user_paused = False
            self.recovery.set_user_paused(False)
            self._push_ms_state()

        _mp_create_set_source_and_prepare_async(
            self._current_stream_url,
            self._current_headers,
            on_prepared_bg,
            self._on_completion,
            lambda w,e: None,
            lambda w,e: None
        )

    # ----- Foreground restart -----
    def _restart_same_url(self):
        _mp_reset_release()
        self._cancel_events()
        self._stall_started_ts = None

        def on_prepared(mp):
            self._request_audio_focus()
            self._set_ms_active(True)
            _mp_start()
            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
            self._schedule_progress_updates()
            self._schedule_expire_refresh()
            self._start_watchdog()
            self._push_ms_state()

        _mp_create_set_source_and_prepare_async(
            self._current_stream_url,
            self._current_headers,
            on_prepared,
            self._on_completion,
            lambda w,e: None,
            lambda w,e: None
        )

    # ----- UI helpers -----
    def _schedule_progress_updates(self):
        if self._update_ev: Clock.unschedule(self._update_progress)
        self._update_ev = Clock.schedule_interval(self._update_progress, 0.5)

    def _maybe_sync_ui(self):
        if not self._need_ui_sync or not _is_foreground():
            return
        try:
            self.ids.audio_title.text = self._current_title
            if self._current_thumb:
                self.ids.audio_thumbnail.source = self._current_thumb
            self.ids.repeat_btn.source = "ico/repeat_active.png" if self.repeat else "ico/icorepeat.png"
            self._need_ui_sync = False
        except Exception as e:
            log(f"sync_ui err: {e}")

    def _update_progress(self, dt):
        self._maybe_sync_ui()
        try:
            if not android_player: return
            pos = android_player.getCurrentPosition()
            dur = android_player.getDuration() or 1
            if pos is None or dur is None: return
            self.ids.current_time_label.text = self._fmt_ms(pos)
            self.ids.total_time_label.text   = self._fmt_ms(dur)
            self.ids.progress_slider.max     = int(dur/1000)
            self.ids.progress_slider.value   = int(pos/1000)
        except Exception as e:
            vlog(f"progress err: {e}")

    # ----- Expire refresh -----
    def _schedule_expire_refresh(self):
        if not self._current_expire_ts: return
        now = int(time.time())
        delta = max(5, self._current_expire_ts - now - 60)
        self._refresh_ev = Clock.schedule_once(lambda dt: self._refresh_stream_and_resume(), delta)

    def _refresh_stream_and_resume(self):
        try:
            info = _extract_audio_info(self._last_video_url)
            self._current_stream_url = info['audio_url']
            self._current_expire_ts  = info.get('expire_ts')
            self._current_headers    = info.get('http_headers', {})
            self._restart_same_url_adaptive()
        except Exception as e:
            log(f"refresh failed: {e}")

    # ----- Player stall watchdog -----
    def _start_watchdog(self):
        if self._watchdog_ev:
            try: self._watchdog_ev.cancel()
            except: pass
        self._last_pos_ms = -1
        self._stall_started_ts = None
        self._watchdog_ev = Clock.schedule_interval(self._watchdog_tick, 2.0)

    def _watchdog_tick(self, dt):
        if not android_player: return
        try:
            net_ok = _is_network_available()
            pos = android_player.getCurrentPosition() or 0
            playing = android_player.isPlaying()

            if not net_ok:
                if self._stall_started_ts is None:
                    self._stall_started_ts = time.time()
                return

            if playing:
                if self._last_pos_ms == pos:
                    if self._stall_started_ts is None:
                        self._stall_started_ts = time.time()
                    elif time.time() - self._stall_started_ts >= 10 and not self._user_paused:
                        self._stall_started_ts = None
                        self._refresh_stream_and_resume()
                else:
                    self._stall_started_ts = None
            else:
                if (not self._user_paused):
                    dur = android_player.getDuration() or 1
                    if 0 < pos < (dur-1500):
                        self._request_audio_focus()
                        self._set_ms_active(True)
                        _mp_start()
            self._last_pos_ms = pos
        except Exception as e:
            log(f"[WD] tick err: {e}")

    # ----- Utils -----
    @staticmethod
    def _fmt_ms(ms):
        s = int(ms/1000); m, s = divmod(s, 60); h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    # ----- Optional: play video -----
    def _fetch_and_play_video(self, url):
        try:
            opts = {'quiet': True, 'format': 'best', 'skip_download': True, 'noplaylist': True, 'logger': YDLLogger()}
            with YoutubeDL(opts) as ydl: info = ydl.extract_info(url, download=False)
            vurl = info.get('url'); show_native_video(vurl)
        except Exception as e:
            log(f"[VIDEO] play err: {e}")
