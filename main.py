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

# ================= NOTIFICATION / ANDROID UTILS =================

def _sdk_int():
    return autoclass('android.os.Build$VERSION').SDK_INT

def _activity():
    return autoclass('org.kivy.android.PythonActivity').mActivity

def _pm():
    return _activity().getPackageManager()

def _pkg():
    return _activity().getPackageName()

def _uid():
    return _activity().getApplicationInfo().uid

def _notif_manager():
    Context = autoclass('android.content.Context')
    return _activity().getSystemService(Context.NOTIFICATION_SERVICE)

def _notif_perm_granted():
    """Runtime POST_NOTIFICATIONS (Android 13+)."""
    if _sdk_int() < 33:
        return True
    PM = autoclass('android.content.pm.PackageManager')
    return _activity().checkSelfPermission("android.permission.POST_NOTIFICATIONS") == PM.PERMISSION_GRANTED

def _notif_enabled_in_system():
    """Загальний системний тумблер (на MIUI часто викл.)."""
    try:
        return bool(_notif_manager().areNotificationsEnabled())
    except Exception:
        return True  # якщо API нема — вважаємо, що ок

def notifications_ready():
    return _notif_perm_granted() and _notif_enabled_in_system()

# ---------- ОДИН ВИКЛИК ДЛЯ ВСІХ RUNTIME-ПРАВ ----------
@run_on_ui_thread
def request_all_runtime_permissions_auto():
    """
    Попросити всі небезпечні (runtime) дозволи ОДНИМ системним діалогом.
    Normal-права (INTERNET/WAKE_LOCK/FOREGROUND_SERVICE...) у runtime не просяться.
    """
    PM = autoclass('android.content.pm.PackageManager')
    act = _activity()
    sdk = _sdk_int()

    perms = set()
    # те, що реально потрібне застосунку:
    perms.add("android.permission.RECORD_AUDIO")

    if sdk >= 33:
        # Android 13+: заміна READ/WRITE_EXTERNAL_STORAGE
        perms.add("android.permission.READ_MEDIA_AUDIO")
        # додай за потреби:
        # perms.add("android.permission.READ_MEDIA_VIDEO")
        # perms.add("android.permission.READ_MEDIA_IMAGES")
        perms.add("android.permission.POST_NOTIFICATIONS")
    else:
        # Android 12 та нижче
        perms.add("android.permission.READ_EXTERNAL_STORAGE")
        perms.add("android.permission.WRITE_EXTERNAL_STORAGE")

    to_request = [p for p in perms if act.checkSelfPermission(p) != PM.PERMISSION_GRANTED]
    if to_request:
        print("[PERMS] requesting runtime in one dialog:", to_request)
        try:
            act.requestPermissions(to_request, 777)
        except Exception as e:
            print("[PERMS] requestPermissions failed:", e)
    else:
        print("[PERMS] all runtime already granted")

# ---------- відкривач налаштувань сповіщень (робастний) ----------
@run_on_ui_thread
def open_app_notification_settings_robust():
    """
    Прагматичний відкривач налаштувань сповіщень:
      1) AOSP APP_NOTIFICATION_SETTINGS (O+ і L–N),
      2) Application details (універсально),
      3) MIUI SecurityCenter (два відомих варіанти),
      4) Загальні системні списки (останній шанс).
    Вибирає перший, що реально резолвиться.
    """
    Intent   = autoclass('android.content.Intent')
    Settings = autoclass('android.provider.Settings')
    Uri      = autoclass('android.net.Uri')
    act = _activity(); pm = _pm()
    pkg = _pkg(); uid = _uid()

    candidates = []

    # --- AOSP O+ (API 26+) ---
    if _sdk_int() >= 26:
        it = Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
        try:
            EXTRA_APP_PACKAGE = getattr(Settings, 'EXTRA_APP_PACKAGE')
            it.putExtra(EXTRA_APP_PACKAGE, pkg)
        except Exception:
            pass
        it.putExtra("android.provider.extra.APP_PACKAGE", pkg)
        it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        candidates.append(it)

    # --- AOSP L–N (API 21–25) ---
    if 21 <= _sdk_int() < 26:
        it = Intent("android.settings.APP_NOTIFICATION_SETTINGS")
        it.putExtra("app_package", pkg)
        it.putExtra("app_uid", uid)
        it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        candidates.append(it)

    # --- Деталі застосунку (майже завжди є) ---
    it = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
    it.setData(Uri.parse("package:" + pkg))
    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    candidates.append(it)

    # --- MIUI специфіка 1 ---
    it = Intent("miui.intent.action.APP_PERM_EDITOR")
    it.setClassName("com.miui.securitycenter",
                    "com.miui.permcenter.permissions.PermissionsEditorActivity")
    it.putExtra("extra_pkgname", pkg)
    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    candidates.append(it)

    # --- MIUI специфіка 2 ---
    it = Intent("miui.intent.action.APP_PERM_EDITOR")
    it.setClassName("com.miui.securitycenter",
                    "com.miui.permcenter.settings.AppPermissionsEditorActivity")
    it.putExtra("extra_pkgname", pkg)
    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    candidates.append(it)

    # --- Загальні екрани ---
    it = Intent(Settings.ACTION_MANAGE_APPLICATIONS_SETTINGS)
    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    candidates.append(it)

    it = Intent(Settings.ACTION_APPLICATION_SETTINGS)
    it.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    candidates.append(it)

    for idx, candidate in enumerate(candidates):
        try:
            if pm.resolveActivity(candidate, 0) is not None:
                print(f"[NOTIF] open settings candidate #{idx} -> OK")
                act.startActivity(candidate)
                return True
        except Exception as e:
            print(f"[NOTIF] candidate #{idx} failed:", e)

    print("[NOTIF] no settings intent could be resolved")
    return False

def send_test_heads_up_notification():
    """Коротке heads-up після увімкнення сповіщень (канал IMPORTANCE_HIGH)."""
    if not notifications_ready():
        print("[NOTIF] test skipped: not ready"); return
    act = _activity()
    NotificationManager = autoclass('android.app.NotificationManager')
    NotificationChannel = autoclass('android.app.NotificationChannel')
    NotificationBuilder = autoclass('android.app.Notification$Builder')
    channel_id, channel_name = "pymusic_alerts", "PyMusic Alerts"
    importance = NotificationManager.IMPORTANCE_HIGH
    nm = _notif_manager()
    if nm.getNotificationChannel(channel_id) is None:
        ch = NotificationChannel(channel_id, channel_name, importance)
        ch.enableVibration(True)
        nm.createNotificationChannel(ch)
    b = NotificationBuilder(act, channel_id)
    b.setContentTitle("PyMusic")
    b.setContentText("Сповіщення увімкнено ✅")
    b.setSmallIcon(act.getApplicationInfo().icon)
    b.setAutoCancel(True)
    nm.notify(1001, b.build())

# ========== ЖОРСТКИЙ ГЕЙТ (force settings loop) ==========

class NotificationForceSettingsGate:
    """
    Автоматично і наполегливо веде юзера в екран дозволів:
      • Показує діалог без закриття поза ним,
      • Питає runtime-дозвіл,
      • Відкриває екран налаштувань,
      • Щосекунди перевіряє; кожні 4с знову відкриває налаштування,
      • Завершує, коли notifications_ready() == True.
    """
    def __init__(self):
        self.dialog = None
        self._tries = 0

    def start(self):
        if notifications_ready():
            print("[NOTIF-GATE] already ok"); send_test_heads_up_notification(); return
        self._show_dialog()
        request_all_runtime_permissions_auto()  # на випадок, якщо ще не просили
        open_app_notification_settings_robust()
        Clock.schedule_once(self._poll, 1.0)

    def _poll(self, dt):
        ok = notifications_ready()
        print(f"[NOTIF-GATE] ready? {ok}")
        if ok:
            self._dismiss(); send_test_heads_up_notification(); return
        self._tries += 1
        if self._tries % 4 == 0:
            open_app_notification_settings_robust()
        Clock.schedule_once(self._poll, 1.0)

    def _dismiss(self):
        if self.dialog:
            try: self.dialog.dismiss()
            except Exception: pass
            self.dialog = None

    def _on_exit(self, *a):
        App.get_running_app().stop()

    def _on_open_settings(self, *a):
        open_app_notification_settings_robust()

    def _show_dialog(self):
        if self.dialog: return
        content = MDBoxLayout(orientation="vertical", adaptive_height=True,
                              spacing="8dp", padding=("0dp","4dp","0dp","0dp"))
        lbl = MDLabel(
            text=("PyMusic потребує сповіщень, щоб працювати у фоні та показувати плеєр.\n"
                  "Ми відкрили екран налаштувань — увімкни сповіщення для застосунку."),
            halign="left", theme_text_color="Primary"
        )
        # не даємо тексту накладатися
        lbl.bind(width=lambda *_: setattr(lbl, "text_size", (lbl.width, None)))
        content.add_widget(lbl)

        self.dialog = MDDialog(
            title="Увімкнути сповіщення",
            type="custom",
            content_cls=content,
            auto_dismiss=False,
            buttons=[
                MDFlatButton(text="ВИЙТИ", on_release=self._on_exit),
                MDRaisedButton(text="НАЛАШТУВАННЯ", on_release=self._on_open_settings),
            ],
        )
        self.dialog.open()

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
        # один системний діалог на увесь набір runtime-дозволів
        request_all_runtime_permissions_auto()
        # Жорсткий гейт сповіщень (особливо для MIUI)
        Clock.schedule_once(lambda dt: NotificationForceSettingsGate().start(), 0.5)

if __name__ == "__main__":
    YoutubeSearchApp().run()
