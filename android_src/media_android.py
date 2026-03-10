# media_android.py
# -*- coding: utf-8 -*-

import os
import threading, socket
from datetime import datetime

from jnius import autoclass, cast, PythonJavaClass, java_method
from android.runnable import run_on_ui_thread
from kivy.clock import Clock

# ===================== Android / Java classes =====================

# Core
PythonActivity      = autoclass('org.kivy.android.PythonActivity')
Context             = autoclass('android.content.Context')
Build_VERSION       = autoclass('android.os.Build$VERSION')
Handler             = autoclass('android.os.Handler')
Looper              = autoclass('android.os.Looper')

# Media
MediaPlayer              = autoclass('android.media.MediaPlayer')
MediaSession             = autoclass('android.media.session.MediaSession')
PlaybackState            = autoclass('android.media.session.PlaybackState')
PlaybackStateBuilder     = autoclass('android.media.session.PlaybackState$Builder')
MediaMetadata            = autoclass('android.media.MediaMetadata')
MediaMetadataBuilder     = autoclass('android.media.MediaMetadata$Builder')
AudioManager             = autoclass('android.media.AudioManager')
AudioAttributes          = autoclass('android.media.AudioAttributes')
AudioAttributesBuilder   = autoclass('android.media.AudioAttributes$Builder')
KeyEvent                 = autoclass('android.view.KeyEvent')

# URIs / Intents / PendingIntents
Uri                 = autoclass('android.net.Uri')
Intent              = autoclass('android.content.Intent')
PendingIntent       = autoclass('android.app.PendingIntent')

# Notifications (platform, БЕЗ AndroidX)
NotificationManager = autoclass('android.app.NotificationManager')
NotificationBuilder = autoclass('android.app.Notification$Builder')
try:
    NotificationChannel = autoclass('android.app.NotificationChannel')
except Exception:
    NotificationChannel = None
try:
    MediaStyle = autoclass('android.app.Notification$MediaStyle')
except Exception:
    MediaStyle = None
Notification        = autoclass('android.app.Notification')
NotificationActionBuilder = autoclass('android.app.Notification$Action$Builder')
Icon                = autoclass('android.graphics.drawable.Icon')
String              = autoclass('java.lang.String')

# System drawables
R_draw              = autoclass('android.R$drawable')

# Power / Network
PowerManager        = autoclass('android.os.PowerManager')
WifiManager         = autoclass('android.net.wifi.WifiManager')
ConnectivityManager = autoclass('android.net.ConnectivityManager')
NetworkCapabilities = autoclass('android.net.NetworkCapabilities')

# Misc
try:
    BitmapFactory   = autoclass('android.graphics.BitmapFactory')
except Exception:
    BitmapFactory = None

# Views / layout / surface
FrameLayout         = autoclass('android.widget.FrameLayout')
FrameLayoutLayoutParams = autoclass('android.widget.FrameLayout$LayoutParams')
VideoSurfaceView    = autoclass('android.view.SurfaceView')
View                = autoclass('android.view.View')
ViewGroup           = autoclass('android.view.ViewGroup')
ViewGroupLayoutParams = autoclass('android.view.ViewGroup$LayoutParams')
WebView             = autoclass('android.webkit.WebView')
WebViewClient        = autoclass('android.webkit.WebViewClient')
WebChromeClient      = autoclass('android.webkit.WebChromeClient')
CookieManager        = autoclass('android.webkit.CookieManager')
try:
    SDLActivity     = autoclass('org.libsdl.app.SDLActivity')
except Exception:
    SDLActivity     = None

# HashMap для headers
HashMap             = autoclass('java.util.HashMap')

# ===================== Logging =====================

try:
    _ctx = PythonActivity.mActivity
    _ext = _ctx.getExternalFilesDir(None)
    LOG_PATH = (_ext.getAbsolutePath() if _ext else _ctx.getFilesDir().getAbsolutePath()) + "/pymusic_diag.txt"
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
    if DEBUG_VERBOSE:
        log(msg)

# ===================== Globals / Context =====================

activity   = PythonActivity.mActivity
pm         = activity.getSystemService(Context.POWER_SERVICE)
wake_lock  = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, 'PyMusic::WakeLock')
wifi       = activity.getSystemService(Context.WIFI_SERVICE)
wifi_lock  = wifi.createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "PyMusicWifiLock")

android_player = None   # основний плеєр для аудіо
is_playing     = False
_mp_prepared   = False
_mp_volume     = 1.0

def _is_prepared():
    return bool(android_player) and bool(_mp_prepared)

# ===================== Video overlay (SurfaceView) =====================

_video_container = None
_video_surface   = None
_video_shown     = False

# ===================== WebView overlay (YouTube UI) =====================

_webview_container = None
_webview = None
_webview_client = None
_webview_shown = False
_webview_owner = None
_web_url_handler = None
_mode_handler = None


def _set_sdl_surface_visible(show: bool):
    try:
        if SDLActivity is None:
            return
        surface = getattr(SDLActivity, "mSurface", None)
        if surface is None:
            return
        surface.setVisibility(View.VISIBLE if show else View.GONE)
    except Exception as e:
        log(f"[WEB] set SDL surface err: {e}")


def _set_surface_views_visible(show: bool):
    try:
        act = PythonActivity.mActivity
        root = act.getWindow().getDecorView()
    except Exception as e:
        log(f"[WEB] decor view err: {e}")
        return

    def _walk(v):
        try:
            name = str(v.getClass().getName())
            if "Surface" in name:
                v.setVisibility(View.VISIBLE if show else View.GONE)
        except Exception:
            pass
        try:
            count = v.getChildCount()
        except Exception:
            return
        for i in range(int(count)):
            try:
                _walk(v.getChildAt(i))
            except Exception:
                pass

    _walk(root)


def _dp(px: int) -> int:
    try:
        metrics = activity.getResources().getDisplayMetrics()
        return int(px * float(metrics.density))
    except Exception:
        return int(px)


def _dispatch_mode(mode: str):
    try:
        try:
            print(f"[WEB] mode {mode}")
        except Exception:
            pass
        log(f"[WEB] mode {mode}")
        if mode != "web":
            try:
                webview_hide()
            except Exception:
                pass
        cb = _mode_handler
        if callable(cb):
            Clock.schedule_once(lambda dt: cb(mode), 0)
            return
        try:
            from kivy.app import App
            app = App.get_running_app()
            root = getattr(app, "root", None)
            if root and hasattr(root, "set_screen"):
                Clock.schedule_once(lambda dt: root.set_screen(mode), 0)
                return
        except Exception as e:
            log(f"[WEB] mode fallback err: {e}")
        log("[WEB] mode ignored (no handler)")
    except Exception as e:
        log(f"[WEB] mode dispatch err: {e}")


def _is_youtube_watch_url(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    return (
        "youtube.com/watch" in u
        or "m.youtube.com/watch" in u
        or "music.youtube.com/watch" in u
        or "youtu.be/" in u
        or "youtube.com/shorts/" in u
        or "m.youtube.com/shorts/" in u
        or "youtube.com/live/" in u
        or "m.youtube.com/live/" in u
    )


def _dispatch_webview_video(url: str):
    try:
        try:
            print(f"[WEB] dispatch url {url}")
        except Exception:
            pass
        cb = _web_url_handler
        if cb is None and _webview_owner is not None:
            cb = getattr(_webview_owner, "_webview_play", None)
        if callable(cb):
            Clock.schedule_once(lambda dt: cb(url), 0)
        else:
            try:
                print("[WEB] dispatch skipped (no handler)")
            except Exception:
                pass
    except Exception as e:
        log(f"[WEB] dispatch err: {e}")


class _PyWebViewClient(PythonJavaClass):
    __javabase__ = 'android/webkit/WebViewClient'
    __javacontext__ = 'app'

    @java_method('(Landroid/webkit/WebView;Ljava/lang/String;)Z')
    def shouldOverrideUrlLoading(self, view, url):
        try:
            u = str(url) if url is not None else ""
            if _is_youtube_watch_url(u):
                log(f"[WEB] intercept {u}")
                _dispatch_webview_video(u)
                return True
        except Exception as e:
            log(f"[WEB] shouldOverrideUrlLoading err: {e}")
        return False


@run_on_ui_thread
def webview_create():
    global _webview_container, _webview, _webview_client
    try:
        if _webview_container is not None and _webview is not None:
            return
        act = PythonActivity.mActivity
        ctx = cast('android.content.Context', act)
        _webview_container = FrameLayout(ctx)
        lp = ViewGroupLayoutParams(ViewGroupLayoutParams.MATCH_PARENT,
                                   ViewGroupLayoutParams.MATCH_PARENT)
        _webview_container.setLayoutParams(lp)
        _webview_container.setVisibility(View.GONE)
        try:
            _webview_container.setBackgroundColor(0xFFFFFFFF)
        except Exception:
            pass

        _webview = WebView(ctx)
        web_lp = FrameLayoutLayoutParams(ViewGroupLayoutParams.MATCH_PARENT,
                                         ViewGroupLayoutParams.MATCH_PARENT)
        web_lp.topMargin = 0
        _webview.setLayoutParams(web_lp)

        settings = _webview.getSettings()
        settings.setJavaScriptEnabled(True)
        settings.setDomStorageEnabled(True)
        try:
            _webview.setLayerType(View.LAYER_TYPE_HARDWARE, None)
        except Exception:
            pass
        try:
            settings.setMediaPlaybackRequiresUserGesture(False)
        except Exception:
            pass
        try:
            settings.setJavaScriptCanOpenWindowsAutomatically(True)
        except Exception:
            pass
        try:
            settings.setUseWideViewPort(True)
            settings.setLoadWithOverviewMode(True)
        except Exception:
            pass

        try:
            cookies = CookieManager.getInstance()
            cookies.setAcceptCookie(True)
            if Build_VERSION.SDK_INT >= 21:
                cookies.setAcceptThirdPartyCookies(_webview, True)
        except Exception:
            pass

        _ensure_action_constants()
        try:
            java_pkg = os.environ.get("MEDIAKEY_JAVA_PACKAGE") or _ACTION_PREFIX
            JavaWvCb = autoclass(f"{java_pkg}.WebViewClientBridge")
            _webview_client = JavaWvCb(act, _webview)
            _webview.setWebViewClient(_webview_client)
            log("[WEB] WebViewClientBridge set")
        except Exception as e:
            log(f"[WEB] WebViewClientBridge err: {e}")
            _webview_client = None
        try:
            _webview.setWebChromeClient(WebChromeClient())
        except Exception:
            pass

        _webview_container.addView(_webview)
        act.addContentView(_webview_container, lp)
        try:
            _webview_container.bringToFront()
            _webview.bringToFront()
        except Exception:
            pass
        log("[WEB] WebView created")
    except Exception as e:
        log(f"[WEB] create err: {e}")


@run_on_ui_thread
def webview_show(url: str | None = None):
    global _webview_shown
    try:
        webview_create()
        if _webview_container is None or _webview is None:
            return
        _webview_container.setVisibility(View.VISIBLE)
        _webview_shown = True
        try:
            _webview_container.bringToFront()
            _webview.bringToFront()
        except Exception:
            pass
        _set_sdl_surface_visible(False)
        _set_surface_views_visible(False)
        if url:
            _webview.loadUrl(url)
        elif _webview.getUrl() is None:
            _webview.loadUrl("https://m.youtube.com/")
        log("[WEB] WebView shown")
    except Exception as e:
        log(f"[WEB] show err: {e}")


@run_on_ui_thread
def webview_hide():
    global _webview_shown
    try:
        if _webview_container is None:
            return
        _webview_container.setVisibility(View.GONE)
        _webview_shown = False
        _set_sdl_surface_visible(True)
        _set_surface_views_visible(True)
        log("[WEB] WebView hidden")
    except Exception as e:
        log(f"[WEB] hide err: {e}")


@run_on_ui_thread
def webview_load(url: str):
    try:
        if _webview is None:
            webview_create()
        if _webview is None:
            return
        _webview.loadUrl(url)
    except Exception as e:
        log(f"[WEB] load err: {e}")


def bind_webview_action_router(owner):
    global _webview_owner
    _webview_owner = owner
    global _web_url_handler
    try:
        _web_url_handler = getattr(owner, "_webview_play", None)
    except Exception:
        _web_url_handler = None


def bind_mode_router(handler):
    global _mode_handler
    _mode_handler = handler


# окремий MediaPlayer тільки для відео (без звуку)
video_player          = None
_video_prepared_video = False

# ===================== AudioAttributes for "music" =====================

_audio_attrs = None


def _ensure_audio_attrs():
    """USAGE_MEDIA + CONTENT_TYPE_MUSIC used for player and MediaSession local playback."""
    global _audio_attrs
    if _audio_attrs is None:
        _audio_attrs = (AudioAttributesBuilder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build())
    return _audio_attrs

# ===================== Network check =====================

def is_network_available():
    try:
        cm = activity.getSystemService(Context.CONNECTIVITY_SERVICE)
        net = cm.getActiveNetwork()
        if net is None:
            return False
        caps = cm.getNetworkCapabilities(net)
        if caps is None:
            return False
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

# ===================== MediaPlayer listeners =====================

class OnCompletionListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnCompletionListener']
    __javacontext__ = 'app'

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @java_method('(Landroid/media/MediaPlayer;)V')
    def onCompletion(self, mp):
        threading.Thread(target=self.callback, daemon=True).start()


class OnPreparedListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnPreparedListener']
    __javacontext__ = 'app'

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @java_method('(Landroid/media/MediaPlayer;)V')
    def onPrepared(self, mp):
        threading.Thread(target=lambda: self.callback(mp), daemon=True).start()


class OnErrorListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnErrorListener']
    __javacontext__ = 'app'

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @java_method('(Landroid/media/MediaPlayer;II)Z')
    def onError(self, mp, what, extra):
        threading.Thread(target=lambda: self.callback(what, extra), daemon=True).start()
        return True


class OnInfoListener(PythonJavaClass):
    __javainterfaces__ = ['android/media/MediaPlayer$OnInfoListener']
    __javacontext__ = 'app'

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    @java_method('(Landroid/media/MediaPlayer;II)Z')
    def onInfo(self, mp, what, extra):
        threading.Thread(target=lambda: self.callback(what, extra), daemon=True).start()
        return False

# ===================== Wake/WiFi locks =====================

def acquire_wake_lock():
    try:
        if not wake_lock.isHeld():
            wake_lock.acquire()
        if not wifi_lock.isHeld():
            wifi_lock.acquire()
        log("[LOCK] acquired")
    except Exception as e:
        log(f"[LOCK] acquire error: {e}")


def release_wake_lock():
    try:
        if wake_lock.isHeld():
            wake_lock.release()
        if wifi_lock.isHeld():
            wifi_lock.release()
        log("[LOCK] released")
    except Exception as e:
        log(f"[LOCK] release error: {e}")

# ===================== MediaPlayer wrappers (audio) =====================

@run_on_ui_thread
def _mp_create_set_source_and_prepare_async(audio_url, headers, on_prepared, on_completed, on_error, on_info):
    global android_player, _mp_prepared, is_playing, _mp_volume
    try:
        _mp_prepared = False
        android_player = MediaPlayer()

        # set AudioAttributes to mark this as "music" playback
        try:
            android_player.setAudioAttributes(_ensure_audio_attrs())
        except Exception as e:
            log(f"[MP] setAudioAttributes err: {e}")
        try:
            android_player.setVolume(float(_mp_volume), float(_mp_volume))
        except Exception:
            pass

        def _wrap_on_prepared(mp):
            global _mp_prepared
            _mp_prepared = True
            threading.Thread(target=lambda: on_prepared(mp), daemon=True).start()

        _py_on_prepared = OnPreparedListener(_wrap_on_prepared)
        _py_on_completed = OnCompletionListener(on_completed) if on_completed else None
        _py_on_error = OnErrorListener(on_error)
        _py_on_info = OnInfoListener(on_info)

        android_player._py_on_prepared = _py_on_prepared
        android_player._py_on_completed = _py_on_completed
        android_player._py_on_error = _py_on_error
        android_player._py_on_info = _py_on_info

        android_player.setOnPreparedListener(_py_on_prepared)
        if _py_on_completed:
            android_player.setOnCompletionListener(_py_on_completed)
        android_player.setOnErrorListener(_py_on_error)
        android_player.setOnInfoListener(_py_on_info)

        ctx = cast('android.content.Context', PythonActivity.mActivity)
        if headers:
            android_player.setDataSource(ctx, Uri.parse(audio_url), headers)
        else:
            android_player.setDataSource(ctx, Uri.parse(audio_url))
        android_player.prepareAsync()
        log("[media_android] MediaPlayer preparing async")
    except Exception as e:
        log(f"[MP] prepareAsync error: {e}")


@run_on_ui_thread
def _mp_pause():
    global android_player, is_playing
    if android_player and android_player.isPlaying():
        android_player.pause()
        is_playing = False
        vlog("[MP] pause()")


@run_on_ui_thread
def _mp_start():
    global android_player, is_playing, _mp_prepared
    if android_player and _mp_prepared:
        try:
            android_player.start()
            is_playing = True
            vlog("[MP] start()")
        except Exception as e:
            log(f"[MP] start err: {e}")
    else:
        log("[MP] start ignored (not prepared)")


@run_on_ui_thread
def _mp_set_volume(vol: float):
    global android_player, _mp_volume
    try:
        _mp_volume = float(vol)
        if android_player:
            android_player.setVolume(_mp_volume, _mp_volume)
    except Exception as e:
        log(f"[MP] setVolume err: {e}")


@run_on_ui_thread
def _mp_seek_to(ms):
    global android_player, _mp_prepared
    if android_player and _mp_prepared:
        try:
            android_player.seekTo(int(ms))
            vlog(f"[MP] seekTo {ms}ms")
        except Exception as e:
            log(f"[MP] seek err: {e}")
    else:
        log("[MP] seek ignored (not prepared)")


@run_on_ui_thread
def _mp_reset_release():
    global android_player, is_playing, _mp_prepared
    try:
        if android_player:
            android_player.reset()
            android_player.release()
            log("[media_android] MediaPlayer released")
    except Exception as e:
        log(f"[MP] release err: {e}")
    android_player = None
    is_playing = False
    _mp_prepared = False

# ===================== Notifications / MediaStyle =====================

_ACTION_PREFIX = None
ACTION_PREV = None
ACTION_PLAY = None
ACTION_PAUSE = None
ACTION_TOGGLE = None
ACTION_NEXT = None
ACTION_WEB_URL = None
ACTION_WEB_MODE = None


def _ensure_action_constants():
    global _ACTION_PREFIX, ACTION_PREV, ACTION_PLAY, ACTION_PAUSE, ACTION_TOGGLE, ACTION_NEXT, ACTION_WEB_URL, ACTION_WEB_MODE
    if _ACTION_PREFIX:
        return
    try:
        _ACTION_PREFIX = os.environ.get("MEDIAKEY_ACTION_PREFIX")
    except Exception:
        _ACTION_PREFIX = None
    if not _ACTION_PREFIX:
        try:
            _ACTION_PREFIX = PythonActivity.mActivity.getPackageName()
        except Exception:
            _ACTION_PREFIX = "org.koteuka404.pymusic"
    ACTION_PREV = f"{_ACTION_PREFIX}.PREV"
    ACTION_PLAY = f"{_ACTION_PREFIX}.PLAY"
    ACTION_PAUSE = f"{_ACTION_PREFIX}.PAUSE"
    ACTION_TOGGLE = f"{_ACTION_PREFIX}.TOGGLE"
    ACTION_NEXT = f"{_ACTION_PREFIX}.NEXT"
    ACTION_WEB_URL = f"{_ACTION_PREFIX}.WEB_URL"
    ACTION_WEB_MODE = f"{_ACTION_PREFIX}.WEB_MODE"

NOTIF_CHANNEL_ID   = "pymusic_player"
NOTIF_CHANNEL_NAME = "PyMusic Player"
NOTIF_ID           = 4242


def _load_bitmap(path: str):
    if not path or not BitmapFactory:
        return None
    try:
        return BitmapFactory.decodeFile(path)
    except Exception:
        return None


def _safe_small_icon():
    try:
        app_icon = PythonActivity.mActivity.getApplicationInfo().icon
        if app_icon and app_icon != 0:
            return app_icon
    except Exception:
        pass
    try:
        return R_draw.ic_media_play
    except Exception:
        return 17301540  # fallback int


def _charseq(text: str):
    try:
        return cast('java.lang.CharSequence', String(str(text)))
    except Exception:
        return str(text)


@run_on_ui_thread
def create_notification_channel():
    try:
        if NotificationChannel is None:
            return
        nm = cast('android.app.NotificationManager',
                  activity.getSystemService(Context.NOTIFICATION_SERVICE))
        if nm.getNotificationChannel(NOTIF_CHANNEL_ID) is None:
            ch = NotificationChannel(
                NOTIF_CHANNEL_ID, NOTIF_CHANNEL_NAME,
                NotificationManager.IMPORTANCE_LOW
            )
            ch.enableLights(False)
            ch.enableVibration(False)
            nm.createNotificationChannel(ch)
        log("[NOTIF] channel ready")
    except Exception as e:
        log(f"[NOTIF] channel err: {e}")


@run_on_ui_thread
def create_or_update_media_notification(*,
    title: str = "Playing",
    subtitle: str = "",
    is_playing: bool = True,
    session_token=None,
    large_icon_path: str | None = None
):
    """
    Показуємо три кнопки (prev, play/pause, next) у compact view.
    """
    try:
        _ensure_action_constants()
        act = PythonActivity.mActivity

        # Builder
        if NotificationChannel is not None:
            b = NotificationBuilder(act, NOTIF_CHANNEL_ID)
        else:
            b = NotificationBuilder(act)

        # Content
        b.setContentTitle(title or "Playing")
        b.setContentText(subtitle or "")
        b.setSmallIcon(_safe_small_icon())
        b.setOnlyAlertOnce(True)
        b.setVisibility(Notification.VISIBILITY_PUBLIC)
        b.setShowWhen(False)
        try:
            b.setCategory(Notification.CATEGORY_TRANSPORT)
        except Exception:
            pass
        if hasattr(b, "setOngoing"):
            b.setOngoing(is_playing)

        # Large icon (арт)
        bmp = _load_bitmap(large_icon_path)
        if bmp is not None:
            try:
                b.setLargeIcon(bmp)
            except Exception:
                pass

        # Tap -> open app
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

        # Actions PendingIntent helper (route via Activity -> on_new_intent)
        def _pi(action: str, req: int):
            it = Intent(act, act.getClass())
            it.setAction(action)
            try:
                it.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP)
            except Exception:
                pass
            return PendingIntent.getActivity(act, req, it, flags)

        # Actions (use Notification.Action to avoid addAction signature mismatch)
        actions_added = 0
        def _add_action(icon, title, pi):
            nonlocal actions_added
            try:
                ab = NotificationActionBuilder(int(icon), _charseq(title), pi)
                action = ab.build()
                b.addAction(action)
                actions_added += 1
            except Exception as e:
                log(f"[NOTIF] addAction err: {e}")
                try:
                    icon_obj = Icon.createWithResource(act, int(icon))
                    ab = NotificationActionBuilder(icon_obj, _charseq(title), pi)
                    action = ab.build()
                    b.addAction(action)
                    actions_added += 1
                except Exception as e2:
                    log(f"[NOTIF] addAction icon err: {e2}")
                    try:
                        b.addAction(int(icon), _charseq(title), pi)
                        actions_added += 1
                    except Exception as e3:
                        log(f"[NOTIF] addAction fallback err: {e3}")
        _add_action(R_draw.ic_media_previous, "Prev", _pi(ACTION_PREV, 203))
        if is_playing:
            _add_action(R_draw.ic_media_pause, "Pause", _pi(ACTION_PAUSE, 201))
        else:
            _add_action(R_draw.ic_media_play, "Play", _pi(ACTION_PLAY, 202))
        _add_action(R_draw.ic_media_next, "Next", _pi(ACTION_NEXT, 204))

        # MediaStyle з compact-кнопками і токеном сесії
        try:
            if MediaStyle is not None:
                style = MediaStyle()
                if session_token is not None:
                    try:
                        style.setMediaSession(session_token)
                    except Exception as e:
                        log(f"[NOTIF] platform setMediaSession err: {e}")
                if actions_added > 0:
                    idx = list(range(min(3, actions_added)))
                    try:
                        style.setShowActionsInCompactView(*idx)
                    except Exception as e:
                        log(f"[NOTIF] platform compact idx err: {e}")
                b.setStyle(style)
        except Exception as e:
            log(f"[NOTIF] style err: {e}")

        nm = cast('android.app.NotificationManager',
                  act.getSystemService(Context.NOTIFICATION_SERVICE))
        nm.notify(NOTIF_ID, b.build())
        log(f"[NOTIF] updated (actions={actions_added})")
    except Exception as e:
        log(f"[NOTIF] update err: {e}")


@run_on_ui_thread
def cancel_media_notification():
    try:
        nm = cast('android.app.NotificationManager',
                  activity.getSystemService(Context.NOTIFICATION_SERVICE))
        nm.cancel(NOTIF_ID)
        log("[MEDIA] Notification canceled")
    except Exception as e:
        log(f"[NOTIF] cancel err: {e}")

# ===================== MediaSession metadata/state =====================

_media_session_ref = None


def register_media_session(ms):
    global _media_session_ref
    _media_session_ref = ms
    log("[media_android] MediaSession registered")


def _get_session():
    return _media_session_ref


@run_on_ui_thread
def set_media_metadata(title: str = "", artist: str = "", album: str = "",
                       duration_ms: int | None = None, art_path: str | None = None,
                       art_uri: str | None = None):
    try:
        ms = _get_session()
        if ms is None:
            return
        b = MediaMetadataBuilder()
        if title:
            b.putString(MediaMetadata.METADATA_KEY_TITLE, title)
        if artist:
            b.putString(MediaMetadata.METADATA_KEY_ARTIST, artist)
        if album:
            b.putString(MediaMetadata.METADATA_KEY_ALBUM, album)
        if duration_ms is not None:
            b.putLong(MediaMetadata.METADATA_KEY_DURATION, int(duration_ms))

        bmp = _load_bitmap(art_path) if art_path else None
        if bmp is not None:
            b.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, bmp)
            b.putBitmap(MediaMetadata.METADATA_KEY_ART, bmp)
            b.putBitmap(MediaMetadata.METADATA_KEY_DISPLAY_ICON, bmp)

        if art_uri:
            try:
                b.putString(MediaMetadata.METADATA_KEY_ALBUM_ART_URI, art_uri)
                b.putString(MediaMetadata.METADATA_KEY_ART_URI, art_uri)
                b.putString(MediaMetadata.METADATA_KEY_DISPLAY_ICON_URI, art_uri)
            except Exception:
                pass

        ms.setMetadata(b.build())
        vlog("[MS] metadata set (with art)")
    except Exception as e:
        log(f"[MS] set metadata err: {e}")

# --- MediaSession callback (системні медіакнопки / QS) ---

_ms_cb_owner = None
_ms_cb_instance = None


def _handle_media_button_intent(intent) -> bool:
    try:
        if intent is None:
            return False
        action = intent.getAction()
        if action is None or action != Intent.ACTION_MEDIA_BUTTON:
            return False

        event = intent.getParcelableExtra(Intent.EXTRA_KEY_EVENT)
        if event is None:
            return False

        # підтримуємо і DOWN, і UP (деякі гарнітури шлють лише ACTION_UP)
        if event.getAction() not in (KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP):
            return False

        key_code = event.getKeyCode()
        try:
            log(f"[MS-CB] media button intent action={action} key={key_code} ev_action={event.getAction()}")
        except Exception:
            pass
        owner = _ms_cb_owner
        if owner is None:
            return False

        handled = False

        if key_code in (KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE,
                        KeyEvent.KEYCODE_HEADSETHOOK):
            cb = getattr(owner, "_ms_toggle", None)
            if callable(cb):
                cb()
                handled = True
            else:
                global is_playing
                if is_playing and callable(getattr(owner, "_ms_pause", None)):
                    owner._ms_pause()
                    handled = True
                elif (not is_playing) and callable(getattr(owner, "_ms_play", None)):
                    owner._ms_play()
                    handled = True

        elif key_code == KeyEvent.KEYCODE_MEDIA_PLAY:
            cb = getattr(owner, "_ms_play", None)
            if callable(cb):
                cb()
                handled = True
        elif key_code == KeyEvent.KEYCODE_MEDIA_PAUSE:
            cb = getattr(owner, "_ms_pause", None)
            if callable(cb):
                cb()
                handled = True
        elif key_code == KeyEvent.KEYCODE_MEDIA_NEXT:
            cb = getattr(owner, "_ms_next", None)
            if callable(cb):
                cb()
                handled = True
        elif key_code == KeyEvent.KEYCODE_MEDIA_PREVIOUS:
            cb = getattr(owner, "_ms_prev", None)
            if callable(cb):
                cb()
                handled = True

        if handled:
            log(f"[MS-CB] media button key={key_code}")
        return handled
    except Exception as e:
        log(f"[MS-CB] media button err: {e}")
        return False


class _MediaSessionCallback(PythonJavaClass):
    __javaclass__ = 'android/media/session/MediaSession$Callback'
    __javacontext__ = 'app'
    __javainterfaces__ = []

    def __init__(self):
        super().__init__()

    @java_method('()V')
    def onPlay(self):
        try:
            log("[MS-CB] onPlay")
            if _ms_cb_owner:
                cb = getattr(_ms_cb_owner, "_ms_play", None)
                if callable(cb):
                    cb()
        except Exception as e:
            log(f"[MS-CB] onPlay err: {e}")

    @java_method('()V')
    def onPause(self):
        try:
            log("[MS-CB] onPause")
            if _ms_cb_owner:
                cb = getattr(_ms_cb_owner, "_ms_pause", None)
                if callable(cb):
                    cb()
        except Exception as e:
            log(f"[MS-CB] onPause err: {e}")

    @java_method('()V')
    def onSkipToNext(self):
        try:
            log("[MS-CB] onSkipToNext")
            if _ms_cb_owner:
                cb = getattr(_ms_cb_owner, "_ms_next", None)
                if callable(cb):
                    cb()
        except Exception as e:
            log(f"[MS-CB] onNext err: {e}")

    @java_method('()V')
    def onSkipToPrevious(self):
        try:
            log("[MS-CB] onSkipToPrevious")
            if _ms_cb_owner:
                cb = getattr(_ms_cb_owner, "_ms_prev", None)
                if callable(cb):
                    cb()
        except Exception as e:
            log(f"[MS-CB] onPrev err: {e}")

    @java_method('(J)V')
    def onSeekTo(self, posMs):
        try:
            log(f"[MS-CB] onSeekTo {posMs}")
            if _ms_cb_owner:
                cb = getattr(_ms_cb_owner, "seek", None)
                if callable(cb):
                    cb(int(posMs / 1000))
        except Exception as e:
            log(f"[MS-CB] onSeek err: {e}")

    # головне додавання - апаратні/гарнітурні медіа-кнопки
    @java_method('(Landroid/content/Intent;)Z')
    def onMediaButtonEvent(self, intent):
        return _handle_media_button_intent(intent)


@run_on_ui_thread
def set_media_session_callback(owner):
    """Прив'язуємо MediaSession.Callback до поточного екрана-плеєра."""
    try:
        global _ms_cb_owner, _ms_cb_instance
        _ensure_action_constants()
        ms = _get_session()
        if ms is None:
            return
        _ms_cb_owner = owner
        try:
            java_pkg = os.environ.get("MEDIAKEY_JAVA_PACKAGE") or _ACTION_PREFIX
            JavaMsCb = autoclass(f"{java_pkg}.MediaSessionCallback")
            _ms_cb_instance = JavaMsCb(PythonActivity.mActivity)
            try:
                handler = Handler(Looper.getMainLooper())
                ms.setCallback(_ms_cb_instance, handler)
            except Exception:
                ms.setCallback(_ms_cb_instance)
            log("[MS] java callback set")
        except Exception as e:
            log(f"[MS] java callback err: {e}")
            _ms_cb_instance = None
        try:
            act = PythonActivity.mActivity
            flags = PendingIntent.FLAG_UPDATE_CURRENT
            if Build_VERSION.SDK_INT >= 23:
                flags |= PendingIntent.FLAG_IMMUTABLE
            it = Intent(Intent.ACTION_MEDIA_BUTTON)
            it.setPackage(act.getPackageName())
            pi = PendingIntent.getBroadcast(act, 205, it, flags)
            ms.setMediaButtonReceiver(pi)
            log("[MS] media button receiver set")
        except Exception as e:
            log(f"[MS] set media button receiver err: {e}")
        try:
            java_pkg = os.environ.get("MEDIAKEY_JAVA_PACKAGE") or _ACTION_PREFIX
            JavaMb = autoclass(f"{java_pkg}.MediaButtonReceiver")
            JavaMb.register(PythonActivity.mActivity)
            log("[MS] media button receiver registered")
        except Exception as e:
            log(f"[MS] media button register err: {e}")
        log("[MS] callback set")
    except Exception as e:
        log(f"[MS] set callback err: {e}")


@run_on_ui_thread
def update_media_session_state(is_playing: bool, position_ms: int = 0,
                               duration_ms: int | None = None, can_seek: bool = True):
    try:
        ms = _get_session()
        if ms is None:
            return
        actions = (PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE |
                   PlaybackState.ACTION_PLAY_PAUSE | PlaybackState.ACTION_SKIP_TO_NEXT |
                   PlaybackState.ACTION_SKIP_TO_PREVIOUS)
        if can_seek:
            actions |= PlaybackState.ACTION_SEEK_TO

        state_code = PlaybackState.STATE_PLAYING if is_playing else PlaybackState.STATE_PAUSED
        pb = PlaybackStateBuilder()
        pb.setActions(actions)
        speed = 1.0 if is_playing else 0.0
        pb.setState(state_code, int(max(0, position_ms or 0)), float(speed))
        ms.setPlaybackState(pb.build())
        vlog("[MS] playback state set")
    except Exception as e:
        log(f"[MS] update state err: {e}")


@run_on_ui_thread
def configure_session_audio():
    """Прив'язуємо MediaSession до локального аудіо (USAGE_MEDIA/CONTENT_TYPE_MUSIC)."""
    try:
        ms = _get_session()
        if ms is None:
            return
        attrs = _ensure_audio_attrs()
        ms.setPlaybackToLocal(attrs)
        log("[MS] setPlaybackToLocal(USAGE_MEDIA, CONTENT_TYPE_MUSIC)")
    except Exception as e:
        log(f"[MS] configure_session_audio err: {e}")

# ===================== Notification action router =====================

from android import activity as _py_activity

_action_owner = None
_action_receiver = None
_action_receiver_registered = False
_intent_router_bound = False


def _route_action_intent(intent):
    try:
        _ensure_action_constants()
        if intent is None:
            return
        act = str(intent.getAction() or "")
        log(f"[NOTIF] action {act}")
        if act == ACTION_WEB_URL:
            try:
                url = intent.getStringExtra("url")
            except Exception:
                url = None
            if url:
                try:
                    print(f"[WEB] action url {url}")
                except Exception:
                    pass
                try:
                    webview_hide()
                except Exception:
                    pass
                _dispatch_webview_video(str(url))
            return
        if act == ACTION_WEB_MODE:
            try:
                mode = intent.getStringExtra("mode")
            except Exception:
                mode = None
            if mode:
                _dispatch_mode(str(mode))
            return
        if _action_owner is None:
            return
        if act == ACTION_NEXT:
            cb = getattr(_action_owner, "_ms_next", None)
            if callable(cb):
                cb()
        elif act == ACTION_PREV:
            cb = getattr(_action_owner, "_ms_prev", None)
            if callable(cb):
                cb()
        elif act == ACTION_TOGGLE:
            cb = getattr(_action_owner, "_ms_toggle", None)
            if callable(cb):
                cb()
        elif act == ACTION_PLAY:
            cb = getattr(_action_owner, "_ms_play", None)
            if callable(cb):
                cb()
        elif act == ACTION_PAUSE:
            cb = getattr(_action_owner, "_ms_pause", None)
            if callable(cb):
                cb()
        elif act == Intent.ACTION_MEDIA_BUTTON:
            _handle_media_button_intent(intent)
    except Exception as e:
        log(f"[NOTIF] action route err: {e}")


def bind_notification_action_router(owner):
    """Доставка PendingIntent дій у _ms_* через BroadcastReceiver / on_new_intent."""
    global _action_owner, _action_receiver, _action_receiver_registered
    _action_owner = owner
    try:
        _py_activity.bind(on_new_intent=_on_new_intent)
    except Exception as e:
        log(f"[NOTIF] bind router err: {e}")


def bind_intent_router():
    global _intent_router_bound
    if _intent_router_bound:
        return
    try:
        _py_activity.bind(on_new_intent=_on_new_intent)
        _intent_router_bound = True
    except Exception as e:
        log(f"[NOTIF] bind intent router err: {e}")


def unbind_notification_action_router():
    try:
        _py_activity.unbind(on_new_intent=_on_new_intent)
        log("[NOTIF] action router unbound")
    except Exception:
        pass


def _on_new_intent(intent):
    _route_action_intent(intent)

# ===================== Video overlay (SurfaceView + окремий MediaPlayer) =====================

@run_on_ui_thread
def video_overlay_create():
    """Створює повноекранний SurfaceView поверх Kivy (один раз за сесію)."""
    global _video_container, _video_surface, _video_shown
    try:
        if _video_container is not None and _video_surface is not None:
            return
        act = PythonActivity.mActivity
        ctx = cast('android.content.Context', act)

        _video_container = FrameLayout(ctx)
        lp = ViewGroupLayoutParams(ViewGroupLayoutParams.MATCH_PARENT,
                                   ViewGroupLayoutParams.MATCH_PARENT)
        _video_container.setLayoutParams(lp)
        _video_container.setClickable(False)

        _video_surface = VideoSurfaceView(ctx)
        _video_surface.setLayoutParams(lp)
        _video_surface.setZOrderOnTop(True)
        _video_surface.setVisibility(View.GONE)

        _video_container.addView(_video_surface)
        act.addContentView(_video_container, lp)
        _video_shown = False
        log("[VIDEO] overlay created")
    except Exception as e:
        log(f"[VIDEO] overlay create err: {e}")


@run_on_ui_thread
def video_overlay_show(media_url, headers=None):
    """
    Показати SurfaceView і запустити ОКРЕМИЙ MediaPlayer для відео.
    Аудіо йде через android_player, тут звук глушимо (setVolume(0,0)).
    """
    global _video_surface, _video_shown, video_player, _video_prepared_video
    try:
        if _video_surface is None:
            video_overlay_create()
        if _video_surface is None:
            log("[VIDEO] no surface, cannot show")
            return

        # якщо вже є відео-плеєр - скидаємо
        try:
            if video_player is not None:
                video_player.reset()
                video_player.release()
                log("[VIDEO] old video_player released")
        except Exception as e:
            log(f"[VIDEO] old video_player release err: {e}")

        _video_prepared_video = False

        # готуємо новий MediaPlayer для відео
        vp = MediaPlayer()
        try:
            vp.setAudioAttributes(_ensure_audio_attrs())
        except Exception as e:
            log(f"[VIDEO] setAudioAttributes err: {e}")

        # показати SurfaceView
        _video_surface.setVisibility(View.VISIBLE)
        _video_shown = True

        holder = _video_surface.getHolder()
        try:
            vp.setDisplay(holder)
        except Exception as e:
            log(f"[VIDEO] setDisplay err: {e}")

        # звук вимикаємо - аудіо вже йде з android_player
        try:
            vp.setVolume(0.0, 0.0)
        except Exception:
            pass

        ctx = cast('android.content.Context', PythonActivity.mActivity)
        try:
            if headers is not None:
                vp.setDataSource(ctx, Uri.parse(media_url), headers)
            else:
                vp.setDataSource(ctx, Uri.parse(media_url))
        except Exception as e:
            log(f"[VIDEO] setDataSource err: {e}")
            try:
                vp.reset()
                vp.release()
            except Exception:
                pass
            _video_shown = False
            _video_surface.setVisibility(View.GONE)
            return

        def _on_prepared(mp):
            global _video_prepared_video
            _video_prepared_video = True
            # якщо основний плеєр грає - стартуємо відео й приблизно синхронимось по позиції
            try:
                playing = bool(android_player and android_player.isPlaying())
                pos_ms = android_player.getCurrentPosition() if android_player else 0
            except Exception:
                playing = False
                pos_ms = 0
            try:
                if pos_ms and pos_ms > 0:
                    mp.seekTo(int(pos_ms))
                if playing:
                    mp.start()
                log(f"[VIDEO] prepared, seek={pos_ms}ms, playing={playing}")
            except Exception as e:
                log(f"[VIDEO] on_prepared err: {e}")

        def _on_completed(mp):
            log("[VIDEO] completed")

        def _on_error(what, extra):
            log(f"[VIDEO] error what={what} extra={extra}")

        def _on_info(what, extra):
            vlog(f"[VIDEO] info what={what} extra={extra}")

        vp.setOnPreparedListener(OnPreparedListener(_on_prepared))
        vp.setOnCompletionListener(OnCompletionListener(_on_completed))
        vp.setOnErrorListener(OnErrorListener(_on_error))
        vp.setOnInfoListener(OnInfoListener(_on_info))

        vp.prepareAsync()
        video_player = vp
        log("[VIDEO] player prepareAsync started")
    except Exception as e:
        log(f"[VIDEO] overlay show err: {e}")


@run_on_ui_thread
def video_overlay_hide():
    """Сховати overlay і звільнити відео-плеєр."""
    global _video_surface, _video_shown, video_player, _video_prepared_video
    try:
        if _video_surface is not None:
            _video_surface.setVisibility(View.GONE)
        _video_shown = False
    except Exception as e:
        log(f"[VIDEO] hide err: {e}")

    try:
        if video_player is not None:
            video_player.reset()
            video_player.release()
            log("[VIDEO] video_player released")
    except Exception as e:
        log(f"[VIDEO] video_player release err: {e}")
    video_player = None
    _video_prepared_video = False


def video_overlay_is_shown():
    return bool(_video_shown)


@run_on_ui_thread
def video_overlay_set_playing(playing: bool):
    """Синхронізуємо play/pause відео з основним аудіо-плеєром."""
    global video_player, _video_prepared_video
    try:
        if not (video_player and _video_prepared_video):
            return
        if playing and not video_player.isPlaying():
            video_player.start()
            vlog("[VIDEO] start() by set_playing True")
        elif not playing and video_player.isPlaying():
            video_player.pause()
            vlog("[VIDEO] pause() by set_playing False")
    except Exception as e:
        log(f"[VIDEO] set_playing err: {e}")


@run_on_ui_thread
def video_overlay_seek(seconds: float):
    """Підтягнути відео до потрібної позиції (секунди). Викликається з AudioPlayerScreen.seek()."""
    global video_player, _video_prepared_video
    try:
        if not (video_player and _video_prepared_video):
            return
        ms = int(max(0, seconds) * 1000)
        video_player.seekTo(ms)
        vlog(f"[VIDEO] seekTo {ms}ms")
    except Exception as e:
        log(f"[VIDEO] seek err: {e}")
