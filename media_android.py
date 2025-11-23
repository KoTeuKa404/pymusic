# media_android.py
# -*- coding: utf-8 -*-

import threading, socket
from datetime import datetime

from jnius import autoclass, cast, PythonJavaClass, java_method
from android.runnable import run_on_ui_thread

# ===================== Android / Java classes =====================

# Core
PythonActivity      = autoclass('org.kivy.android.PythonActivity')
Context             = autoclass('android.content.Context')
Build_VERSION       = autoclass('android.os.Build$VERSION')

# Media
MediaPlayer              = autoclass('android.media.MediaPlayer')
MediaSession             = autoclass('android.media.session.MediaSession')
PlaybackState            = autoclass('android.media.session.PlaybackState')
PlaybackStateBuilder     = autoclass('android.media.session.PlaybackState$Builder')
MediaMetadata            = autoclass('android.media.MediaMetadata')
MediaMetadataBuilder     = autoclass('android.media.MediaMetadata$Builder')
AudioManager             = autoclass('android.media.AudioManager')
AudioAttributesBuilder   = autoclass('android.media.AudioAttributes$Builder')

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
VideoSurfaceView    = autoclass('android.view.SurfaceView')
View                = autoclass('android.view.View')
ViewGroupLayoutParams = autoclass('android.view.ViewGroup$LayoutParams')

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

def _is_prepared():
    return bool(android_player) and bool(_mp_prepared)

# ===================== Video overlay (SurfaceView) =====================

_video_container = None
_video_surface   = None
_video_shown     = False

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
                        .setUsage(AudioManager.USAGE_MEDIA)
                        .setContentType(AudioManager.CONTENT_TYPE_MUSIC)
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
    global android_player, _mp_prepared, is_playing
    try:
        _mp_prepared = False
        android_player = MediaPlayer()

        # set AudioAttributes to mark this as "music" playback
        try:
            android_player.setAudioAttributes(_ensure_audio_attrs())
        except Exception as e:
            log(f"[MP] setAudioAttributes err: {e}")

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

ACTION_PREV   = "org.koteuka404.pymusic.PREV"
ACTION_PLAY   = "org.koteuka404.pymusic.PLAY"
ACTION_PAUSE  = "org.koteuka404.pymusic.PAUSE"
ACTION_TOGGLE = "org.koteuka404.pymusic.TOGGLE"
ACTION_NEXT   = "org.koteuka404.pymusic.NEXT"

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

        # Actions PendingIntent helper
        def _pi(action: str, req: int):
            it = Intent(act, PythonActivity)
            it.setAction(action)
            it.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP)
            return PendingIntent.getActivity(act, req, it, flags)

        # Actions
        actions_added = 0
        try:
            b.addAction(R_draw.ic_media_previous, "Prev", _pi(ACTION_PREV, 203))
            actions_added += 1
            if is_playing:
                b.addAction(R_draw.ic_media_pause, "Pause", _pi(ACTION_PAUSE, 201))
                actions_added += 1
            else:
                b.addAction(R_draw.ic_media_play, "Play", _pi(ACTION_PLAY, 202))
                actions_added += 1
            b.addAction(R_draw.ic_media_next, "Next", _pi(ACTION_NEXT, 204))
            actions_added += 1
        except Exception as e:
            log(f"[NOTIF] addAction err: {e}")

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


class _MediaSessionCallback(PythonJavaClass):
    __javainterfaces__ = ['android/media/session/MediaSession$Callback']
    __javacontext__ = 'app'

    def __init__(self):
        super().__init__()

    @java_method('()V')
    def onPlay(self):
        try:
            if _ms_cb_owner and hasattr(_ms_cb_owner, "_ms_play"):
                _ms_cb_owner._ms_play()
        except Exception as e:
            log(f"[MS-CB] onPlay err: {e}")

    @java_method('()V')
    def onPause(self):
        try:
            if _ms_cb_owner and hasattr(_ms_cb_owner, "_ms_pause"):
                _ms_cb_owner._ms_pause()
        except Exception as e:
            log(f"[MS-CB] onPause err: {e}")

    @java_method('()V')
    def onSkipToNext(self):
        try:
            if _ms_cb_owner and hasattr(_ms_cb_owner, "_ms_next"):
                _ms_cb_owner._ms_next()
        except Exception as e:
            log(f"[MS-CB] onNext err: {e}")

    @java_method('()V')
    def onSkipToPrevious(self):
        try:
            if _ms_cb_owner and hasattr(_ms_cb_owner, "_ms_prev"):
                _ms_cb_owner._ms_prev()
        except Exception as e:
            log(f"[MS-CB] onPrev err: {e}")

    @java_method('(J)V')
    def onSeekTo(self, posMs):
        try:
            if _ms_cb_owner and hasattr(_ms_cb_owner, "seek"):
                _ms_cb_owner.seek(int(posMs / 1000))
        except Exception as e:
            log(f"[MS-CB] onSeek err: {e}")


@run_on_ui_thread
def set_media_session_callback(owner):
    """Прив'язуємо MediaSession.Callback до поточного екрана-плеєра."""
    try:
        global _ms_cb_owner
        ms = _get_session()
        if ms is None:
            return
        _ms_cb_owner = owner
        ms.setCallback(_MediaSessionCallback())
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


def bind_notification_action_router(owner):
    """Доставка PendingIntent дій у _ms_* через on_new_intent."""
    global _action_owner
    _action_owner = owner
    try:
        _py_activity.bind(on_new_intent=_on_new_intent)
        log("[NOTIF] action router bound")
    except Exception as e:
        log(f"[NOTIF] bind router err: {e}")


def unbind_notification_action_router():
    try:
        _py_activity.unbind(on_new_intent=_on_new_intent)
        log("[NOTIF] action router unbound")
    except Exception:
        pass


def _on_new_intent(intent):
    try:
        if _action_owner is None or intent is None:
            return
        act = str(intent.getAction() or "")
        log(f"[NOTIF] on_new_intent {act}")
        if act == ACTION_NEXT and hasattr(_action_owner, "_ms_next"):
            _action_owner._ms_next()
        elif act == ACTION_PREV and hasattr(_action_owner, "_ms_prev"):
            _action_owner._ms_prev()
        elif act == ACTION_TOGGLE and hasattr(_action_owner, "_ms_toggle"):
            _action_owner._ms_toggle()
        elif act == ACTION_PLAY and hasattr(_action_owner, "_ms_play"):
            _action_owner._ms_play()
        elif act == ACTION_PAUSE and hasattr(_action_owner, "_ms_pause"):
            _action_owner._ms_pause()
    except Exception as e:
        log(f"[NOTIF] intent err: {e}")

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
