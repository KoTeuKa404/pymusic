"""Player UI patch for YouTube like statistics.

Adds a compact like counter to the right of the favorite button, shows the
like/dislike percentage underneath, and makes the channel name bold.

The data comes from Return YouTube Dislike's public HTTPS API because YouTube
no longer exposes public dislike counts. Failures are isolated from playback:
requests run off the Kivy UI thread, use certifi-backed TLS verification,
are cached, and can retry after transient network failures.
"""
from __future__ import annotations

import re
import threading
import time
import urllib.parse
from typing import Any

import certifi
import requests
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivymd.uix.label import MDIcon


_PATCHED = False
_CACHE_TTL_SECONDS = 30 * 60
_REQUEST_TIMEOUT_SECONDS = 6
_RETRY_AFTER_SECONDS = 15
_VOTES_ENDPOINT = "https://returnyoutubedislikeapi.com/votes"
_stats_cache: dict[str, tuple[float, int, int]] = {}
_cache_lock = threading.RLock()


def _video_id_from_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text

    try:
        parsed = urllib.parse.urlparse(text)
        query = urllib.parse.parse_qs(parsed.query or "")
        candidate = str((query.get("v") or [""])[0] or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate

        host = (parsed.netloc or "").lower()
        if "youtu.be" in host:
            candidate = (parsed.path or "").strip("/").split("/")[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate

        match = re.search(
            r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{11})(?:[/?#]|$)",
            parsed.path or "",
        )
        if match:
            return match.group(1)
    except Exception:
        pass

    match = re.search(
        r"(?:[?&]v=|youtu\.be/|/(?:shorts|live|embed)/)"
        r"([A-Za-z0-9_-]{11})(?:[&/?#]|$)",
        text,
    )
    return match.group(1) if match else ""


def _to_non_negative_int(value: Any) -> int:
    try:
        number = int(float(value))
    except Exception:
        return 0
    return max(0, number)


def _format_compact_count(value: int) -> str:
    try:
        number = max(0, int(value))
    except Exception:
        return ""

    units = (
        (1_000_000_000, "млрд"),
        (1_000_000, "млн"),
        (1_000, "тис."),
    )
    for divider, suffix in units:
        if number >= divider:
            compact = number / float(divider)
            if compact >= 100:
                text = f"{compact:.0f}"
            else:
                text = f"{compact:.1f}"
            text = text.rstrip("0").rstrip(".").replace(".", ",")
            return f"{text} {suffix}"
    return f"{number:,}".replace(",", " ")


def _format_ratio(likes: int, dislikes: int) -> str:
    total = max(0, int(likes)) + max(0, int(dislikes))
    if total <= 0:
        return ""
    like_pct = 100.0 * max(0, int(likes)) / total
    dislike_pct = max(0.0, 100.0 - like_pct)
    left = f"{like_pct:.1f}".replace(".", ",")
    right = f"{dislike_pct:.1f}".replace(".", ",")
    return f"{left}% / {right}%"


def _log(message: str) -> None:
    try:
        import media_android as ma

        ma.log(message)
    except Exception:
        try:
            print(message)
        except Exception:
            pass


def _read_cached(video_id: str) -> tuple[int, int] | None:
    now = time.monotonic()
    with _cache_lock:
        cached = _stats_cache.get(video_id)
        if not cached:
            return None
        ts, likes, dislikes = cached
        if now - ts > _CACHE_TTL_SECONDS:
            _stats_cache.pop(video_id, None)
            return None
        return int(likes), int(dislikes)


def _write_cached(video_id: str, likes: int, dislikes: int) -> None:
    with _cache_lock:
        _stats_cache[video_id] = (
            time.monotonic(),
            max(0, int(likes)),
            max(0, int(dislikes)),
        )
        if len(_stats_cache) > 128:
            oldest = sorted(_stats_cache.items(), key=lambda item: item[1][0])[:32]
            for key, _value in oldest:
                _stats_cache.pop(key, None)


def _fetch_votes(video_id: str) -> tuple[int, int] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return None

    cached = _read_cached(video_id)
    if cached is not None:
        return cached

    try:
        response = requests.get(
            _VOTES_ENDPOINT,
            params={"videoId": video_id},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "User-Agent": "PyMusic/1.0 (Android)",
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
            verify=certifi.where(),
        )
        if response.status_code == 429:
            _log(f"[LIKES-UI] RYD rate limited video={video_id}")
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        _log(f"[LIKES-UI] RYD request failed video={video_id}: {exc}")
        return None

    if not isinstance(payload, dict):
        _log(f"[LIKES-UI] invalid RYD response video={video_id}")
        return None

    likes = _to_non_negative_int(payload.get("likes"))
    dislikes = _to_non_negative_int(payload.get("dislikes"))
    if likes <= 0 and dislikes <= 0:
        _log(f"[LIKES-UI] empty RYD votes video={video_id}")
        return None

    _write_cached(video_id, likes, dislikes)
    return likes, dislikes


def _make_stats_widget(owner) -> BoxLayout:
    holder = BoxLayout(
        orientation="vertical",
        size_hint=(None, None),
        size=(dp(94), dp(38)),
        spacing=0,
        padding=(0, dp(1), 0, 0),
    )

    top = BoxLayout(
        orientation="horizontal",
        size_hint=(1, None),
        height=dp(22),
        spacing=dp(2),
        padding=(0, 0, 0, 0),
    )

    icon_holder = AnchorLayout(
        size_hint=(None, None),
        size=(dp(22), dp(22)),
        anchor_x="center",
        anchor_y="center",
    )
    icon = MDIcon(
        icon="thumb-up-outline",
        size_hint=(None, None),
        size=(dp(18), dp(18)),
        font_size="17sp",
        theme_text_color="Custom",
        text_color=(0.15, 0.15, 0.15, 1),
        halign="center",
        valign="middle",
    )
    icon_holder.add_widget(icon)

    count_label = Label(
        text="",
        size_hint=(1, None),
        height=dp(22),
        font_size="13sp",
        color=(0.15, 0.15, 0.15, 1),
        halign="left",
        valign="middle",
        shorten=True,
        shorten_from="right",
        text_size=(dp(70), dp(22)),
    )

    ratio_label = Label(
        text="",
        size_hint=(1, None),
        height=dp(14),
        font_size="8sp",
        color=(0.48, 0.48, 0.48, 1),
        halign="left",
        valign="top",
        text_size=(dp(94), dp(14)),
    )

    top.add_widget(icon_holder)
    top.add_widget(count_label)
    holder.add_widget(top)
    holder.add_widget(ratio_label)

    holder._pymusic_like_stats = True
    holder._pymusic_count_label = count_label
    holder._pymusic_ratio_label = ratio_label
    owner._likes_holder = holder
    owner._likes_count_label = count_label
    owner._likes_ratio_label = ratio_label
    return holder


def _ensure_stats_widget(owner) -> None:
    try:
        channel = owner.ids.get("audio_channel")
        if channel is not None:
            channel.bold = True
    except Exception:
        pass

    try:
        existing = getattr(owner, "_likes_holder", None)
        if existing is not None and getattr(existing, "parent", None) is not None:
            return

        favorite = owner.ids.get("favorite_btn")
        parent = getattr(favorite, "parent", None) if favorite is not None else None
        if parent is None:
            return

        for child in list(getattr(parent, "children", []) or []):
            if bool(getattr(child, "_pymusic_like_stats", False)):
                owner._likes_holder = child
                owner._likes_count_label = getattr(child, "_pymusic_count_label", None)
                owner._likes_ratio_label = getattr(child, "_pymusic_ratio_label", None)
                return

        try:
            parent.spacing = dp(8)
        except Exception:
            pass

        widget = _make_stats_widget(owner)
        parent.add_widget(widget)
    except Exception as exc:
        _log(f"[LIKES-UI] widget creation failed: {exc}")


def _set_stats_ui(owner, likes: int | None, dislikes: int | None) -> None:
    _ensure_stats_widget(owner)
    try:
        count_label = getattr(owner, "_likes_count_label", None)
        ratio_label = getattr(owner, "_likes_ratio_label", None)
        if count_label is None or ratio_label is None:
            return

        if likes is None:
            count_label.text = ""
            ratio_label.text = ""
            return

        safe_likes = max(0, int(likes))
        safe_dislikes = max(0, int(dislikes or 0))
        count_label.text = _format_compact_count(safe_likes)
        ratio_label.text = _format_ratio(safe_likes, safe_dislikes)
    except Exception as exc:
        _log(f"[LIKES-UI] render failed: {exc}")


def _ensure_likes_async(owner, video_url: str) -> None:
    video_id = _video_id_from_url(video_url)
    if not video_id:
        Clock.schedule_once(lambda _dt: _set_stats_ui(owner, None, None), 0)
        return

    current_video_id = str(getattr(owner, "_likes_video_id", "") or "")
    if current_video_id == video_id:
        failed_at = float(getattr(owner, "_likes_failed_at", 0.0) or 0.0)
        if failed_at <= 0.0:
            return
        if time.monotonic() - failed_at < _RETRY_AFTER_SECONDS:
            return

    owner._likes_video_id = video_id
    owner._likes_failed_at = 0.0
    owner._likes_request_token = int(getattr(owner, "_likes_request_token", 0)) + 1
    token = int(owner._likes_request_token)

    Clock.schedule_once(lambda _dt: _set_stats_ui(owner, None, None), 0)

    def worker() -> None:
        result = _fetch_votes(video_id)
        if token != int(getattr(owner, "_likes_request_token", -1)):
            return
        if video_id != _video_id_from_url(str(getattr(owner, "_last_video_url", "") or "")):
            return

        if result is None:
            owner._likes_failed_at = time.monotonic()
            Clock.schedule_once(lambda _dt: _set_stats_ui(owner, None, None), 0)
            return

        owner._likes_failed_at = 0.0
        likes, dislikes = result
        Clock.schedule_once(
            lambda _dt, l=likes, d=dislikes: _set_stats_ui(owner, l, d),
            0,
        )

    threading.Thread(
        target=worker,
        name=f"pymusic-like-stats-{video_id}",
        daemon=True,
    ).start()


def install_likes_ui_patch() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        import audio_screen

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if screen_cls is None:
            return False
        if bool(getattr(screen_cls, "_pymusic_likes_ui_v3", False)):
            _PATCHED = True
            return True

        old_init = screen_cls.__init__
        old_on_kv_post = getattr(screen_cls, "on_kv_post", None)
        old_ensure_metadata = screen_cls._ensure_metadata_async
        old_sync_loaded = screen_cls._sync_ui_loaded
        old_sync_loading = screen_cls._sync_ui_loading

        def init_with_likes(self, *args, **kwargs):
            old_init(self, *args, **kwargs)
            self._likes_video_id = ""
            self._likes_failed_at = 0.0
            self._likes_request_token = 0
            self._likes_holder = None
            self._likes_count_label = None
            self._likes_ratio_label = None

        def on_kv_post_with_likes(self, base_widget):
            if callable(old_on_kv_post):
                old_on_kv_post(self, base_widget)
            Clock.schedule_once(lambda _dt: _ensure_stats_widget(self), 0)

        def ensure_metadata_with_likes(self, video_url: str):
            result = old_ensure_metadata(self, video_url)
            _ensure_likes_async(self, video_url)
            return result

        def sync_loaded_with_likes(self, *args, **kwargs):
            result = old_sync_loaded(self, *args, **kwargs)
            _ensure_stats_widget(self)
            current = str(getattr(self, "_last_video_url", "") or "")
            if current:
                _ensure_likes_async(self, current)
            return result

        def sync_loading_with_likes(self, *args, **kwargs):
            result = old_sync_loading(self, *args, **kwargs)
            _ensure_stats_widget(self)
            current = str(getattr(self, "_last_video_url", "") or "")
            current_id = _video_id_from_url(current)
            if current_id and current_id != str(getattr(self, "_likes_video_id", "") or ""):
                self._likes_video_id = ""
                self._likes_failed_at = 0.0
                self._likes_request_token = int(getattr(self, "_likes_request_token", 0)) + 1
                Clock.schedule_once(lambda _dt: _set_stats_ui(self, None, None), 0)
            return result

        screen_cls.__init__ = init_with_likes
        screen_cls.on_kv_post = on_kv_post_with_likes
        screen_cls._ensure_metadata_async = ensure_metadata_with_likes
        screen_cls._sync_ui_loaded = sync_loaded_with_likes
        screen_cls._sync_ui_loading = sync_loading_with_likes
        screen_cls._pymusic_likes_ui_v3 = True

        _PATCHED = True
        print("[LIKES-UI] like counter + ratio patch v3 enabled")
        return True
    except Exception as exc:
        print("[LIKES-UI] patch install failed:", exc)
        return False
