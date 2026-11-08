from __future__ import annotations

from kivy.uix.screenmanager import Screen
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.cache import Cache
from kivy.core.image import Image as CoreImage
from kivy.resources import resource_find
from kivy.app import App
from kivy.metrics import dp
from kivy.factory import Factory
from kivy.uix.widget import Widget
from kivy.properties import ObjectProperty, StringProperty, BooleanProperty
from kivy.graphics import Color, Rectangle, RoundedRectangle
from kivy.uix.modalview import ModalView
from kivy.uix.scrollview import ScrollView
from kivy.uix.button import Button
from kivymd.uix.list import OneLineListItem, TwoLineListItem

import threading
import time
import os
import re
from datetime import date, datetime
import hashlib
import urllib.request
import urllib.parse
from urllib.parse import parse_qs, urlparse
import tempfile
import ssl

import media_android as ma
import ytdlp_helpers as ydlh
from headset_listener import headset_router
from recent_utils import (
    load_recent,
    save_recent,
    update_recent_cache,
    update_recent_art,
    is_favorite,
    upsert_favorite,
    remove_favorite,
    update_favorite_cache,
    load_favorites,
)

# новий імпорт замість локального класу
from video_player import AndroidVideoPlayer, _native_transport_bridge


# Integrated like statistics and channel-row layout.
# Previously provided by likes_ui_patch.py and likes_ui_tuning.py.
import certifi
import requests
from typing import Any
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivymd.uix.label import MDIcon

_player_likes_CACHE_TTL_SECONDS = 30 * 60
_player_likes_REQUEST_TIMEOUT_SECONDS = 6
_player_likes_RETRY_AFTER_SECONDS = 15
_player_likes_VOTES_ENDPOINT = "https://returnyoutubedislikeapi.com/votes"
_player_likes_stats_cache: dict[str, tuple[float, int, int]] = {}
_player_likes_cache_lock = threading.RLock()


def _player_likes_video_id_from_url(value: str) -> str:
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


def _player_likes_to_non_negative_int(value: Any) -> int:
    try:
        number = int(float(value))
    except Exception:
        return 0
    return max(0, number)


def _player_likes_format_compact_count(value: int) -> str:
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


def _player_likes_format_ratio(likes: int, dislikes: int) -> str:
    total = max(0, int(likes)) + max(0, int(dislikes))
    if total <= 0:
        return ""
    like_pct = 100.0 * max(0, int(likes)) / total
    dislike_pct = max(0.0, 100.0 - like_pct)
    left = f"{like_pct:.1f}".replace(".", ",")
    right = f"{dislike_pct:.1f}".replace(".", ",")
    return f"{left}% / {right}%"


def _player_likes_log(message: str) -> None:
    try:
        import media_android as ma

        ma.log(message)
    except Exception:
        try:
            print(message)
        except Exception:
            pass


def _player_likes_read_cached(video_id: str) -> tuple[int, int] | None:
    now = time.monotonic()
    with _player_likes_cache_lock:
        cached = _player_likes_stats_cache.get(video_id)
        if not cached:
            return None
        ts, likes, dislikes = cached
        if now - ts > _player_likes_CACHE_TTL_SECONDS:
            _player_likes_stats_cache.pop(video_id, None)
            return None
        return int(likes), int(dislikes)


def _player_likes_write_cached(video_id: str, likes: int, dislikes: int) -> None:
    with _player_likes_cache_lock:
        _player_likes_stats_cache[video_id] = (
            time.monotonic(),
            max(0, int(likes)),
            max(0, int(dislikes)),
        )
        if len(_player_likes_stats_cache) > 128:
            oldest = sorted(_player_likes_stats_cache.items(), key=lambda item: item[1][0])[:32]
            for key, _value in oldest:
                _player_likes_stats_cache.pop(key, None)


def _player_likes_fetch_votes(video_id: str) -> tuple[int, int] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return None

    cached = _player_likes_read_cached(video_id)
    if cached is not None:
        return cached

    try:
        response = requests.get(
            _player_likes_VOTES_ENDPOINT,
            params={"videoId": video_id},
            headers={
                "Accept": "application/json, text/plain, */*",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "User-Agent": "PyMusic/1.0 (Android)",
            },
            timeout=_player_likes_REQUEST_TIMEOUT_SECONDS,
            verify=certifi.where(),
        )
        if response.status_code == 429:
            _player_likes_log(f"[LIKES-UI] RYD rate limited video={video_id}")
            return None
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        _player_likes_log(f"[LIKES-UI] RYD request failed video={video_id}: {exc}")
        return None

    if not isinstance(payload, dict):
        _player_likes_log(f"[LIKES-UI] invalid RYD response video={video_id}")
        return None

    likes = _player_likes_to_non_negative_int(payload.get("likes"))
    dislikes = _player_likes_to_non_negative_int(payload.get("dislikes"))
    if likes <= 0 and dislikes <= 0:
        _player_likes_log(f"[LIKES-UI] empty RYD votes video={video_id}")
        return None

    _player_likes_write_cached(video_id, likes, dislikes)
    return likes, dislikes


def _player_likes_make_stats_widget(owner) -> FloatLayout:
    # Keep the like icon on the same visual baseline as the 28dp repeat/favorite
    # PNG buttons. Material font glyphs sit high inside their label box, so the
    # glyph itself is enlarged and its 28dp box is shifted down slightly.
    holder = FloatLayout(
        size_hint=(None, None),
        size=(dp(106), dp(46)),
        # BoxLayout otherwise places a fixed-height child at its lower edge.
        # Match the same center line as the avatar, name and PNG action icons.
        pos_hint={"center_y": 0.5},
    )

    icon = MDIcon(
        icon="thumb-up-outline",
        size_hint=(None, None),
        size=(dp(28), dp(28)),
        pos_hint={"x": 0.0, "center_y": 0.5},
        font_size="24sp",
        theme_text_color="Custom",
        text_color=(0.15, 0.15, 0.15, 1),
        halign="center",
        valign="middle",
        text_size=(dp(28), dp(28)),
    )

    # The count and like/dislike ratio are a compact two-line block
    # straddling the thumb's center (0.5): count above, ratio below.
    count_label = Label(
        text="",
        size_hint=(None, None),
        size=(dp(72), dp(18)),
        pos_hint={"x": 34.0 / 106.0, "center_y": 0.68},
        font_size="13sp",
        color=(0.15, 0.15, 0.15, 1),
        halign="left",
        valign="middle",
        shorten=True,
        shorten_from="right",
        text_size=(dp(72), dp(18)),
    )

    ratio_label = Label(
        text="",
        size_hint=(None, None),
        size=(dp(72), dp(14)),
        pos_hint={"x": 34.0 / 106.0, "center_y": 0.32},
        font_size="8sp",
        color=(0.48, 0.48, 0.48, 1),
        halign="left",
        valign="middle",
        text_size=(dp(72), dp(14)),
    )

    holder.add_widget(icon)
    holder.add_widget(count_label)
    holder.add_widget(ratio_label)

    holder._pymusic_like_stats = True
    holder._pymusic_count_label = count_label
    holder._pymusic_ratio_label = ratio_label
    owner._likes_holder = holder
    owner._likes_count_label = count_label
    owner._likes_ratio_label = ratio_label
    return holder


def _player_likes_ensure_stats_widget(owner) -> None:
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

        widget = _player_likes_make_stats_widget(owner)
        parent.add_widget(widget)
    except Exception as exc:
        _player_likes_log(f"[LIKES-UI] widget creation failed: {exc}")


def _player_likes_set_stats_ui(owner, likes: int | None, dislikes: int | None) -> None:
    _player_likes_ensure_stats_widget(owner)
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
        count_label.text = _player_likes_format_compact_count(safe_likes)
        ratio_label.text = _player_likes_format_ratio(safe_likes, safe_dislikes)
    except Exception as exc:
        _player_likes_log(f"[LIKES-UI] render failed: {exc}")


def _player_likes_ensure_likes_async(owner, video_url: str) -> None:
    video_id = _player_likes_video_id_from_url(video_url)
    if not video_id:
        Clock.schedule_once(lambda _dt: _player_likes_set_stats_ui(owner, None, None), 0)
        return

    current_video_id = str(getattr(owner, "_likes_video_id", "") or "")
    if current_video_id == video_id:
        failed_at = float(getattr(owner, "_likes_failed_at", 0.0) or 0.0)
        if failed_at <= 0.0:
            return
        if time.monotonic() - failed_at < _player_likes_RETRY_AFTER_SECONDS:
            return

    owner._likes_video_id = video_id
    owner._likes_failed_at = 0.0
    owner._likes_request_token = int(getattr(owner, "_likes_request_token", 0)) + 1
    token = int(owner._likes_request_token)

    Clock.schedule_once(lambda _dt: _player_likes_set_stats_ui(owner, None, None), 0)

    def worker() -> None:
        result = _player_likes_fetch_votes(video_id)
        if token != int(getattr(owner, "_likes_request_token", -1)):
            return
        if video_id != _player_likes_video_id_from_url(str(getattr(owner, "_last_video_url", "") or "")):
            return

        if result is None:
            owner._likes_failed_at = time.monotonic()
            Clock.schedule_once(lambda _dt: _player_likes_set_stats_ui(owner, None, None), 0)
            return

        owner._likes_failed_at = 0.0
        likes, dislikes = result
        Clock.schedule_once(
            lambda _dt, l=likes, d=dislikes: _player_likes_set_stats_ui(owner, l, d),
            0,
        )

    threading.Thread(
        target=worker,
        name=f"pymusic-like-stats-{video_id}",
        daemon=True,
    ).start()



def _player_likes_tune_like_widget(owner) -> None:
    """Make the thumb icon larger/lower without replacing its renderer."""
    try:
        holder = getattr(owner, "_likes_holder", None)
        if holder is None:
            return

        holder.size_hint = (None, None)
        holder.size = (dp(106), dp(46))
        holder.pos_hint = {"center_y": 0.5}

        like_icon = None
        for child in list(getattr(holder, "children", []) or []):
            if str(getattr(child, "icon", "") or "") == "thumb-up-outline":
                like_icon = child
                break

        if like_icon is not None:
            like_icon.size_hint = (None, None)
            like_icon.size = (dp(34), dp(34))
            like_icon.font_size = "30sp"
            like_icon.text_size = (dp(34), dp(34))
            # Align the actual thumb glyph with the neighboring PNG actions.
            like_icon.pos_hint = {"x": 0.0, "center_y": 0.5}
            like_icon.halign = "center"
            like_icon.valign = "middle"

        # Place the two labels symmetrically around the thumb's midpoint.
        # Their combined visual midpoint is holder.center_y, not the count
        # line itself; this keeps the percentage from dangling underneath.
        count_label = getattr(owner, "_likes_count_label", None)
        if count_label is not None:
            count_label.size_hint = (None, None)
            count_label.size = (dp(72), dp(18))
            count_label.pos_hint = {"x": 34.0 / 106.0, "center_y": 0.68}
            count_label.text_size = (dp(72), dp(18))

        ratio_label = getattr(owner, "_likes_ratio_label", None)
        if ratio_label is not None:
            ratio_label.size_hint = (None, None)
            ratio_label.size = (dp(72), dp(14))
            ratio_label.pos_hint = {"x": 34.0 / 106.0, "center_y": 0.32}
            ratio_label.text_size = (dp(72), dp(14))
    except Exception:
        pass


def _player_likes_tune_channel_row(owner) -> None:
    try:
        channel = owner.ids.get("audio_channel")
        favorite = owner.ids.get("favorite_btn")
        repeat = owner.ids.get("repeat_inline_btn")
        avatar = owner.ids.get("channel_avatar")
        parent = getattr(favorite, "parent", None) if favorite is not None else None

        # All channel-row elements share the same vertical center. Never
        # offset only the title/avatar while leaving action icons in place.
        if avatar is not None:
            try:
                avatar.pos_hint = {"center_y": 0.5}
            except Exception:
                pass

        if channel is not None:
            channel.bold = True
            channel.size_hint_x = 1
            channel.size_hint_y = None
            channel.height = dp(44)
            channel.pos_hint = {"center_y": 0.5}
            channel.shorten = True
            channel.shorten_from = "right"
            channel.halign = "left"
            channel.valign = "middle"
            channel.text_size = (channel.width, channel.height)
            if not bool(getattr(channel, "_pymusic_width_bound", False)):
                def _sync_text_width(instance, width):
                    instance.text_size = (width, instance.height)

                channel.bind(width=_sync_text_width)
                channel._pymusic_width_bound = True

        for button in (favorite, repeat):
            if button is not None:
                button.size_hint_y = None
                button.pos_hint = {"center_y": 0.5}
        _player_likes_tune_like_widget(owner)

        if parent is None:
            return

        known = {channel, favorite, repeat, avatar, getattr(owner, "_likes_holder", None)}
        for child in list(getattr(parent, "children", []) or []):
            if child in known or bool(getattr(child, "_pymusic_like_stats", False)):
                continue
            if type(child) is Widget:
                child.size_hint_x = None
                child.width = 0
    except Exception:
        pass



def _player_likes_initialize(owner):
    """Prepare stats UI once; no dynamic method monkey-patching."""
    owner._likes_video_id = ""
    owner._likes_failed_at = 0.0
    owner._likes_request_token = 0
    owner._likes_holder = None
    owner._likes_count_label = None
    owner._likes_ratio_label = None


def _player_likes_ensure_and_tune(owner):
    _player_likes_ensure_stats_widget(owner)
    _player_likes_tune_channel_row(owner)


def _player_likes_reset_on_change(owner):
    current = str(getattr(owner, "_last_video_url", "") or "")
    current_id = _player_likes_video_id_from_url(current)
    if current_id and current_id != str(getattr(owner, "_likes_video_id", "") or ""):
        owner._likes_video_id = ""
        owner._likes_failed_at = 0.0
        owner._likes_request_token = int(getattr(owner, "_likes_request_token", 0)) + 1
        Clock.schedule_once(lambda _dt: _player_likes_set_stats_ui(owner, None, None), 0)



def _pymusic_mix_video_url(value):
    """Treat YouTube RD/start_radio watch URLs as a single video."""
    try:
        parsed = urllib.parse.urlparse(str(value or "").strip())
        host = (parsed.netloc or "").lower()
        if "youtube.com" not in host and "youtu.be" not in host:
            return None
        query = urllib.parse.parse_qs(parsed.query or "")
        playlist_id = str((query.get("list") or [""])[0] or "")
        radio = str((query.get("start_radio") or [""])[0] or "")
        video_id = str((query.get("v") or [""])[0] or "")
        if not video_id and "youtu.be" in host:
            video_id = (parsed.path or "").strip("/").split("/")[0]
        if (playlist_id.upper().startswith("RD") or radio == "1") and video_id:
            return f"https://www.youtube.com/watch?v={video_id}", video_id
    except Exception:
        pass
    return None


def _request_player_layout(widget) -> None:
    if widget is None:
        return
    try:
        trigger = getattr(widget, "_trigger_layout", None)
        if callable(trigger):
            trigger()
    except Exception:
        pass
    try:
        widget.canvas.ask_update()
    except Exception:
        pass

def _player_set_audio_volume(audio, value: float) -> None:
    try:
        ma._mp_set_volume(float(value))
        return
    except Exception:
        pass
    try:
        if audio is not None:
            audio.setVolume(float(value), float(value))
    except Exception:
        pass

def _playlist_video_id_core(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) == 11 and all(c.isalnum() or c in "-_" for c in raw):
        return raw
    try:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query or "")
        vid = str((query.get("v") or [""])[0] or "")
        if vid:
            return vid
        if "youtu.be" in (parsed.netloc or "").lower():
            return (parsed.path or "").strip("/").split("/", 1)[0]
    except Exception:
        pass
    return ""


def _playlist_same_video_core(a: str, b: str) -> bool:
    a = str(a or "").strip()
    b = str(b or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    a_id = _playlist_video_id_core(a)
    b_id = _playlist_video_id_core(b)
    return bool(a_id and b_id and a_id == b_id)


def _playlist_requested_start_index_core(args, kwargs, count: int) -> int:
    try:
        start = int(kwargs.get("start_index", 0) or 0)
    except Exception:
        start = 0

    # play_playlist(tracks, maybe2, *, start_index=...).  A positional int is
    # itself the start index, while bool/string keep the keyword/default value.
    if args:
        maybe2 = args[0]
        if isinstance(maybe2, int) and not isinstance(maybe2, bool):
            try:
                start = int(maybe2)
            except Exception:
                start = 0

    if count <= 0 or start < 0 or start >= count:
        return 0
    return start

class _CurrentTapLabel(ButtonBehavior, Label):
    pass


def _related_duration_label(value: object) -> str:
    """Turn actual YouTube duration metadata into a compact badge.

    Seconds are valid when provided as a number. A missing/unknown duration
    is left blank, never estimated from the thumbnail or upload time.
    """
    if value is None or isinstance(value, bool):
        return ""
    raw = str(value).strip()
    if re.fullmatch(r"(?:\d{1,3}:)?\d{1,2}:\d{2}", raw):
        return raw
    if isinstance(value, (int, float)) or re.fullmatch(r"\d{1,7}", raw):
        try:
            seconds = int(float(value))
            if not 0 < seconds < 100 * 3600:
                return ""
            hours, remaining = divmod(seconds, 3600)
            minutes, seconds = divmod(remaining, 60)
            return (
                f"{hours}:{minutes:02d}:{seconds:02d}"
                if hours else f"{minutes}:{seconds:02d}"
            )
        except (ValueError, TypeError, OverflowError):
            return ""
    return ""


def _format_published_age(raw_date: object, *, today: date | None = None) -> str:
    """Format an actual yt-dlp upload/release date, never estimate missing data.

    Accept both YYYYMMDD and YYYY-MM-DD. Calendar years/months avoid
    claiming that a video published near a month boundary is a year older.
    """
    value = str(raw_date or "").strip()
    if not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", value):
        return ""
    try:
        published = datetime.strptime(value.replace("-", ""), "%Y%m%d").date()
        current = today or date.today()
        age = (current - published).days
    except (TypeError, ValueError, OverflowError):
        return ""
    if age < 0:
        return ""
    if age == 0:
        return "сьогодні"
    if age == 1:
        return "учора"

    def count_word(count: int, single: str, few: str, many: str) -> str:
        if count % 100 in (11, 12, 13, 14):
            word = many
        elif count % 10 == 1:
            word = single
        elif count % 10 in (2, 3, 4):
            word = few
        else:
            word = many
        return f"{count} {word} тому"

    years = current.year - published.year - (
        (current.month, current.day) < (published.month, published.day)
    )
    if years:
        return count_word(years, "рік", "роки", "років")
    months = (
        (current.year - published.year) * 12
        + current.month - published.month
        - (current.day < published.day)
    )
    if months:
        return count_word(months, "місяць", "місяці", "місяців")
    weeks = age // 7
    if weeks:
        return count_word(weeks, "тиждень", "тижні", "тижнів")
    return count_word(age, "день", "дні", "днів")


def _verified_channel_name(value: str, video_title: str = "") -> str:
    """Reject title/placeholder metadata masquerading as a channel name."""
    name = str(value or "").strip()
    title = str(video_title or "").strip()
    if not name or name.casefold() in {"youtube", "unknown", "video", "playing audio"}:
        return ""
    # Some YouTube search/playlist fallbacks erroneously store item.title as
    # item.channel. Never promote that unverified title to the channel field.
    if title and name.casefold() == title.casefold():
        return ""
    return name[:200]


def _current_canonical_video_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        if "youtube.com" in host:
            vid = str((parse_qs(parsed.query or "").get("v") or [""])[0] or "")
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
        if "youtu.be" in host:
            vid = (parsed.path or "").strip("/").split("/")[0]
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
    except Exception:
        pass
    return raw


def _current_make_label(text: str, *, height=34, font_size="14sp", bold=False, color=(0.12, 0.12, 0.12, 1)):
    lbl = Label(
        text=str(text or ""),
        size_hint_y=None,
        height=dp(height),
        font_size=font_size,
        bold=bold,
        color=color,
        halign="left",
        valign="middle",
    )
    lbl.bind(width=lambda instance, width: setattr(instance, "text_size", (max(dp(1), width), None)))
    return lbl


def _current_video_url_of(owner) -> str:
    return _current_canonical_video_url(
        getattr(owner, "_current_video_url_v4", "")
        or getattr(owner, "_last_video_url", "")
        or ""
    )


def _current_fetch_comments(video_url: str, limit: int = 3):
    """Fetch a tiny top-comment sample in a background thread.

    TLS verification stays enabled; this path intentionally does not use
    nocheckcertificate/unverified SSL contexts.
    """
    try:
        import ytdlp_helpers as ydlh

        opts = {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "getcomments": True,
            "extractor_retries": 1,
            "socket_timeout": 9,
            "logger": ydlh.YDLLogger(),
            "extractor_args": {
                "youtube": {
                    "comment_sort": ["top"],
                    "max_comments": [f"{int(limit)},all,all,0"],
                    "skip": ["hls", "dash"],
                }
            },
        }
        with ydlh.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(video_url, download=False) or {}
        out = []
        for raw in (info.get("comments") or [])[: max(1, int(limit))]:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("content") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "author": str(raw.get("author") or "Користувач YouTube").strip(),
                    "text": text,
                    "likes": int(raw.get("like_count") or 0),
                }
            )
        count = info.get("comment_count")
        try:
            count = int(count) if count is not None else None
        except Exception:
            count = None
        return out, count
    except Exception as exc:
        print("[CURRENT-V4] comments failed:", exc)
        return [], None

_current_comments_worker_lock = threading.Lock()

# ==================== STABLE LOCAL THUMBNAIL WIDGET ====================

class ThumbImage(Widget):
    """
    Stable local thumbnail widget for playlist/recommended rows.

    It does not use Kivy AsyncImage/Loader at all. Every row owns its own
    Canvas rectangles and keeps a reference to the loaded CoreImage, so a later
    thumbnail cannot visually overwrite only the last row.
    """
    source = StringProperty("")
    texture = ObjectProperty(None, allownone=True)
    allow_stretch = BooleanProperty(True)
    keep_ratio = BooleanProperty(True)
    nocache = BooleanProperty(True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._core_image_ref = None
        self._texture_ref = None
        with self.canvas:
            Color(0, 0, 0, 1)
            self._bg_rect = Rectangle(pos=self.pos, size=self.size)
            Color(1, 1, 1, 1)
            self._img_rect = Rectangle(pos=self.pos, size=self.size)
        self.bind(pos=self._update_rect, size=self._update_rect)
        self.bind(texture=self._update_texture)
        self.bind(source=self._on_source)

    def _update_rect(self, *_):
        try:
            self._bg_rect.pos = self.pos
            self._bg_rect.size = self.size
            self._img_rect.pos = self.pos
            self._img_rect.size = self.size
            self.canvas.ask_update()
        except Exception:
            pass

    def _update_texture(self, *_):
        try:
            self._texture_ref = self.texture
            self._img_rect.texture = self.texture
            self.canvas.ask_update()
        except Exception:
            pass

    def _on_source(self, *_):
        src = str(self.source or "")
        if not src:
            self.clear_image()
            return
        if src.startswith(("http://", "https://")):
            # Remote URLs are intentionally not loaded here. They must be
            # downloaded by _set_playlist_thumb first.
            return
        self.set_local_file(src)

    def clear_image(self):
        try:
            self._core_image_ref = None
            self._texture_ref = None
            self.texture = None
            self._img_rect.texture = None
            self.canvas.ask_update()
        except Exception:
            pass

    def set_local_file(self, path: str):
        try:
            if (
                path
                and str(getattr(self, "source", "") or "") == str(path)
                and getattr(self, "texture", None) is not None
            ):
                return True
            if not path or not os.path.exists(path) or os.path.getsize(path) <= 0:
                self.clear_image()
                return False
            try:
                img = CoreImage(path, nocache=True)
            except TypeError:
                img = CoreImage(path)
            self._core_image_ref = img
            self._texture_ref = img.texture
            self.texture = img.texture
            self._update_texture()
            return True
        except Exception as e:
            try:
                ma.log(f"[PLAYLIST] ThumbImage load fail: {path} err={e}")
            except Exception:
                pass
            return False

    def reload(self):
        self._on_source()

try:
    Factory.register("ThumbImage", cls=ThumbImage)
except Exception:
    pass

# ==================== PLAYLIST MODEL ====================

class Playlist:
    """
    Нормальний плейлист:
      - зберігає треки у вигляді dict:
        {"url", "title", "channel", "thumb"}
      - вміє нормалізувати різні формати ззовні (tuple, dict, просто id)
      - інкапсулює поточний індекс і переходи next/prev
    """

    def __init__(self) -> None:
        self.tracks: list[dict] = []
        self.index: int = 0
        self.name: str = ""

    def __len__(self) -> int:
        return len(self.tracks)

    def __bool__(self) -> bool:
        return bool(self.tracks)

    def clear(self) -> None:
        self.tracks.clear()
        self.index = 0
        self.name = ""

    @staticmethod
    def _normalize_img_url(url: str) -> str:
        u = str(url or "").strip()
        if u.startswith("//"):
            return f"https:{u}"
        return u

    @staticmethod
    def _video_id_from_url(url: str) -> str:
        try:
            from urllib.parse import urlparse, parse_qs
            u = str(url or "")
            p = urlparse(u)
            q = parse_qs(p.query or "")
            vid = (q.get("v") or [""])[0]
            if vid:
                return vid
            if "youtu.be" in (p.netloc or ""):
                return (p.path or "").lstrip("/")
        except Exception:
            pass
        return ""

    @staticmethod
    def _normalize_video_id(value: str) -> str:
        s = str(value or "").strip()
        if not s:
            return ""
        # Якщо це вже чистий youtube id.
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
            return s
        # Якщо прилетів URL або сміття - пробуємо витягнути id.
        vid = Playlist._video_id_from_url(s)
        if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return vid
        m = re.search(r"(?:v=|/vi/|youtu\.be/)([A-Za-z0-9_-]{11})", s)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _video_id_from_thumb_url(thumb_url: str) -> str:
        s = str(thumb_url or "")
        m = re.search(r"/(?:vi|vi_webp)/([A-Za-z0-9_-]{11})/", s)
        if m:
            return m.group(1)
        m = re.search(r"[?&]img=(?:https?:)?//i\.ytimg\.com/(?:vi|vi_webp)/([A-Za-z0-9_-]{11})/", s)
        if m:
            return m.group(1)
        return ""

    @staticmethod
    def _normalize_video_url(url: str) -> str:
        u = str(url or "").strip()
        if not u:
            return ""
        if u.startswith("//"):
            return f"https:{u}"
        if u.startswith("/watch") or u.startswith("/shorts/") or u.startswith("/live/"):
            return f"https://www.youtube.com{u}"
        if u.startswith("watch?"):
            return f"https://www.youtube.com/{u}"
        if u.startswith("youtu.be/") or u.startswith("www.youtube.com/") or u.startswith("youtube.com/"):
            return f"https://{u}"
        if u.startswith("http://") or u.startswith("https://"):
            return u
        # Інакше вважаємо, що це youtube id.
        return f"https://www.youtube.com/watch?v={u}"

    @staticmethod
    def _normalize_item(item) -> dict:
        """
        Приводить будь-який формат елемента до dict:
        {"url", "title", "channel", "thumb", "duration"}
        """
        url = ""
        title = ""
        channel = ""
        thumb = ""
        duration = ""
        video_id = ""

        if isinstance(item, (list, tuple)) and item:
            url = str(item[0] or "")
            if len(item) > 1 and item[1] is not None:
                title = str(item[1])
            if len(item) > 2 and item[2] is not None:
                channel = str(item[2])
            if len(item) > 3 and item[3] is not None:
                thumb = str(item[3])
            if len(item) > 4 and item[4] is not None:
                duration = str(item[4])
            if len(item) > 5 and item[5] is not None:
                video_id = str(item[5])
        elif isinstance(item, dict):
            url = str(
                item.get("url")
                or item.get("id")
                or item.get("video_id")
                or ""
            )
            title = str(item.get("title") or "")
            channel = str(item.get("channel") or item.get("uploader") or "")
            thumb = str(item.get("thumb") or item.get("thumbnail") or "")
            duration = str(item.get("duration") or item.get("duration_string") or "")
            video_id = str(item.get("video_id") or "")
        else:
            url = str(item or "")

        url = Playlist._normalize_video_url(url)
        thumb = Playlist._normalize_img_url(thumb)
        # У плейлистах YouTube/Music поле id інколи може бути нестабільним.
        # Першим беремо ID саме з URL рядка, бо він відповідає відео, по якому буде click.
        url_video_id = Playlist._normalize_video_id(url)
        explicit_video_id = Playlist._normalize_video_id(video_id)
        video_id = url_video_id or explicit_video_id or Playlist._video_id_from_thumb_url(thumb)
        if video_id:
            # Static JPEG без зайвих sqp/rs параметрів стабільніше для Kivy/Android.
            thumb = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"

        return {
            "url": url,
            "title": title,
            "channel": channel,
            "thumb": thumb,
            "duration": duration,
            "video_id": video_id,
        }

    def set_tracks(
        self,
        tracks,
        name: str = "",
        start_index: int = 0,
        extend: bool = False,
    ) -> None:
        """
        Оновити плейлист:
          - tracks: iterable елементів у будь-якому форматі
          - name: назва плейлиста (для майбутнього UI, якщо треба)
          - start_index: з якого елемента починати
          - extend: якщо True - дописати до існуючого, якщо False - замінити
        """
        norm = [self._normalize_item(x) for x in (tracks or [])]

        if extend and self.tracks:
            self.tracks.extend(norm)
        else:
            self.tracks = norm

        if self.tracks:
            self.index = start_index if 0 <= start_index < len(self.tracks) else 0
        else:
            self.index = 0

        if name:
            self.name = name

    def current(self) -> dict | None:
        if not self.tracks:
            return None
        return self.tracks[self.index]

    def next(self) -> dict | None:
        if not self.tracks:
            return None
        self.index = (self.index + 1) % len(self.tracks)
        return self.current()

    def prev(self) -> dict | None:
        if not self.tracks:
            return None
        self.index = (self.index - 1) % len(self.tracks)
        return self.current()


class AudioPlayerScreen(Screen):
    _pymusic_current_related_v4 = True
    _pymusic_playlist_first_track_v1 = True
    _pymusic_playlist_first_track_v2 = True
    _pymusic_background_next_v1 = True
    _pymusic_playback_regression_v5 = True
    """Аудіо через Android MediaPlayer, відео через AndroidVideoPlayer (SurfaceView)."""

    _pymusic_final_player_v2 = True

    _ms_next = None
    _ms_prev = None
    _ms_toggle = None
    _ms_play = None
    _ms_pause = None
    _ms_repeat = None
    _ms_favorite = None

    def __init__(self, **kw):
        super().__init__(**kw)
        _player_likes_initialize(self)
        # Ensure media callbacks are always callable (QS/headset/notification).
        self._ms_next = self._act_next
        self._ms_prev = self._act_prev
        self._ms_toggle = self._act_toggle
        self._ms_play = self._act_play
        self._ms_pause = self._act_pause
        self._ms_repeat = self._act_repeat
        self._ms_favorite = self._act_favorite
        self.repeat = False
        self._playback_desired = False
        self._user_paused = False
        self._update_ev = None
        self._refresh_ev = None

        self._title = ""
        self._channel = ""
        self._thumb = ""
        self._stream_url = None
        self._expire_ts = None
        self._headers = {}
        self._last_video_url = None

        self._media_session = None
        self._art_path = None

        self._url_cache = {}
        self._URL_CACHE_MAX = 50

        # тепер повноцінний обʼєкт замість "сирого" списку + індекс
        self.playlist = Playlist()

        self._last_click = 0.0
        self._last_media_ts = 0.0
        self._bind_uid = None

        self._load_gen = 0
        self._bg_endguard_fired_gen = -1
        self._completion_guard_gen = -1

        self._video_player: AndroidVideoPlayer | None = None

        # 🔹 нові поля
        self._buffer_watchdog_ev = None
        self._buffer_watchdog_ts = 0.0
        self._last_watch_pos = None
        self._buffering_started_at = 0.0
        self._next_prefetch_requested_gen = -1
        self._video_launch_requested_gen = -1
        self._audio_ready_for_extras_gen = -1

        self._resume_pos_ms = 0      # позиція для резюму після падіння/паузи
        # Android mobile/capped data: start in audio-only mode by default.
        # The video toggle still enables it explicitly on demand.
        self._video_enabled = not bool(ma.is_metered_connection())
        self._bg_tick_ev = Clock.schedule_interval(self._background_tick, 1.0)
        self._audio_cache_inflight = set()
        self._last_good_dur_ms = 0
        self._bad_dur_hits = 0
        self._last_stream_recover_ts = 0.0
        self._is_scrubbing = False
        self._last_video_sync_ts = 0.0
        self._bg_stall_since = 0.0
        self._video_active = False
        self._pymusic_video_starting = False
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1
        self._playlist_url = None
        self._playlist_refreshing = False
        self._playlist_last_refresh_ts = 0.0
        self._auto_skip = True
        self._views_text = ""
        self._published_date = ""
        self._related_items = []
        self._related_source = "youtube"
        self._channel_thumb = ""
        self._channel_thumb_local = ""
        self._meta_inflight = False
        self._meta_last_url = None
        self._metadata_loaded_urls = set()
        self._favorite = False
        self._video_controls_visible = False
        self._video_controls_ev = None
        self._last_bg_resume_ts = 0.0
        self._app_in_background = False
        self._prefetch_inflight = set()
        self._stream_error_hits = 0
        self._last_stream_error_ts = 0.0
        self._prefer_compat_audio = False
        self._stream_format_id = ""
        self._recover_loop_url = ""
        self._recover_loop_hits = 0
        self._recover_loop_ts = 0.0
        self._last_next_action_ts = 0.0
        self._extract_fail_skip_hits = 0
        self._extract_fail_handled_gen = -1
        self._playlist_ui_sig = None
        self._playlist_ui_last_ts = 0.0
        self._playlist_thumb_inflight = set()
        self._playlist_thumb_waiters = {}
        self._playlist_thumb_sem = threading.BoundedSemaphore(4)
        self._fav_cache_inflight = set()
        self._playlist_render_gen = 0
        self._similar_render_gen = 0
        self._playlist_collapsed = False
        # Avoid 1+ MB related/watch-page requests on capped data until the
        # user expands the related section. Audio stays the priority.
        self._similar_collapsed = bool(ma.is_metered_connection())
        self._initialize_java_transport()
        for delay in (0, 0.08, 0.25):
            Clock.schedule_once(lambda _dt: self._bind_player_geometry(), delay)

    # ==================== lifecycle ====================

    def _initialize_java_transport(self):
        """Start native playback state synchronization without class wrappers."""
        self._pymusic_java_audio_ref = None
        self._pymusic_java_video_ref = None
        bridge = _native_transport_bridge()
        try:
            self._pymusic_java_state_version = int(bridge.getStateVersion()) if bridge else 0
        except Exception:
            self._pymusic_java_state_version = 0
        self._pymusic_java_transport_ev = Clock.schedule_interval(
            self._sync_java_transport_state, 0.10
        )
        Clock.schedule_once(lambda _dt: self._register_java_transport_players(), 0)

    def _register_java_transport_players(self):
        bridge = _native_transport_bridge()
        if bridge is None:
            return
        try:
            audio = getattr(ma, "android_player", None)
            if audio is not getattr(self, "_pymusic_java_audio_ref", None):
                bridge.setAudioPlayer(audio)
                self._pymusic_java_audio_ref = audio
        except Exception:
            pass
        try:
            vp = self._video_player
            native = getattr(vp, "player", None) if vp is not None else None
            if native is not getattr(self, "_pymusic_java_video_ref", None):
                bridge.setVideoPlayer(native)
                self._pymusic_java_video_ref = native
        except Exception:
            pass

    def _mark_java_transport(self, paused):
        bridge = _native_transport_bridge()
        if bridge is not None:
            try:
                bridge.markPaused() if paused else bridge.markPlaying()
            except Exception:
                pass

    def _sync_java_transport_state(self, _dt):
        """Mirror native ACTION_DOWN commands to Python after Java executes."""
        bridge = _native_transport_bridge()
        if bridge is None:
            return True
        self._register_java_transport_players()
        try:
            version = int(bridge.getStateVersion())
            previous = int(getattr(self, "_pymusic_java_state_version", version) or 0)
            if version == previous:
                self._mark_java_transport(bool(getattr(self, "_user_paused", False)))
                return True
            self._pymusic_java_state_version = version
            event = int(bridge.getLastEvent())
        except Exception:
            return True

        if event == 2:
            self._user_paused = True
            self._playback_desired = False
            ma.is_playing = False
            try:
                if ma.android_player is not None:
                    self._resume_pos_ms = int(ma.android_player.getCurrentPosition() or 0)
            except Exception:
                pass
            self._ui_set_playing(False)
        elif event == 1:
            self._user_paused = False
            self._playback_desired = True
            ma.is_playing = True
            self._ui_set_playing(True)
        elif event in (3, 4):
            try:
                target = max(0, int(bridge.getLastTargetMs()))
                self._resume_pos_ms = target
                self.ids.current_time_label.text = self._fmt_ms(target)
                self.ids.progress_slider.value = float(target) / 1000.0
                if self._video_player is not None:
                    duration = int(ma.android_player.getDuration() or 0) if ma.android_player else 0
                    self._video_player.set_native_progress(target, max(1, duration))
            except Exception:
                pass
        try:
            self._set_video_controls_visible(True)
        except Exception:
            pass
        return True

    def _ensure_video_player(self) -> bool:
        if self._video_player is not None:
            return True
        try:
            self._video_player = AndroidVideoPlayer()
            self._video_player.set_tap_callback(self._native_video_tap)
            self._video_player.set_seek_callback(
                lambda pos_ms: Clock.schedule_once(lambda dt: self.seek(float(pos_ms) / 1000.0), 0)
            )
            print("[VIDEO] AndroidVideoPlayer created")
            Clock.schedule_once(lambda _dt: self._register_java_transport_players(), 0)
            return True
        except Exception as e:
            ma.log(f"[VIDEO] init err: {e}")
            self._video_player = None
            return False

    def on_kv_post(self, base_widget):
        super().on_kv_post(base_widget)
        Clock.schedule_once(lambda _dt: _player_likes_ensure_and_tune(self), 0)
        try:
            thumb = self.ids.audio_thumbnail
            thumb.bind(pos=self._align_video_to_thumb, size=self._align_video_to_thumb)
        except Exception as e:
            print("[VIDEO] on_kv_post bind err:", e)
        try:
            slider = self.ids.progress_slider
            slider.bind(on_touch_down=lambda inst, touch: self._set_scrubbing(True) if inst.collide_point(*touch.pos) else None)
            slider.bind(on_touch_up=lambda inst, touch: self._set_scrubbing(False))
        except Exception:
            pass

        self._bind_playlist_scroll_input()
        self._bind_similar_scroll_input()
        self._configure_player_scroll_bounds()
        for delay in (0.0, 0.10, 0.35):
            Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), delay)


    def _configure_player_scroll_bounds(self):
        """Clamp player scroll without a runtime class/method patch."""
        scroll = self.ids.get("player_details_scroll")
        if scroll is None:
            return
        try:
            from kivy.effects.scroll import ScrollEffect
            scroll.effect_cls = ScrollEffect
        except Exception:
            pass
        try:
            scroll.always_overscroll = False
            scroll.smooth_scroll_end = 0
            scroll.do_scroll_x = False
            scroll.do_scroll_y = True
        except Exception:
            pass
        if not getattr(scroll, "_pymusic_bounds_bound", False):
            def clamp(widget, *_args):
                widget.scroll_y = max(0.0, min(1.0, float(widget.scroll_y)))
            scroll.bind(on_scroll_stop=clamp)
            scroll._pymusic_bounds_bound = True
        scroll.scroll_y = max(0.0, min(1.0, float(scroll.scroll_y)))

    def _bind_playlist_scroll_input(self):
        """Own playlist gestures directly, without a runtime patch installer.

        The nested playlist_scroll is intentionally a 240dp viewport. Disable
        the parent only for a swipe inside it. Restore the parent on the
        physical finger UP at Window level, including when the nested
        ScrollView or a track button has grabbed the touch.
        """
        inner = self.ids.get("playlist_scroll")
        outer = self.ids.get("player_details_scroll")
        if inner is None or outer is None:
            return
        if getattr(inner, "_pymusic_touch_ownership_bound", False):
            return

        def restore():
            inner._pymusic_active_touch_uid = None
            outer.do_scroll_y = True

        def playlist_down(widget, touch):
            try:
                if (
                    self._is_screen_active()
                    and not getattr(self, "_playlist_collapsed", False)
                    and widget.height > 0
                    and widget.collide_point(*touch.pos)
                ):
                    inner._pymusic_active_touch_uid = touch.uid
                    outer.do_scroll_y = False
            except Exception as exc:
                print("[PLAYLIST] gesture down failed:", exc)
            return False

        def playlist_up(_widget, touch):
            if getattr(inner, "_pymusic_active_touch_uid", None) == touch.uid:
                restore()
            return False

        def window_up(_window, touch):
            if getattr(inner, "_pymusic_active_touch_uid", None) == touch.uid:
                restore()
            return False

        def window_down(_window, touch):
            # Only recover a genuinely stale earlier gesture. Never reset
            # the current DOWN even if Window dispatch runs after the child.
            previous = getattr(inner, "_pymusic_active_touch_uid", None)
            if previous is not None and previous != touch.uid:
                restore()
            return False

        inner._pymusic_touch_ownership_bound = True
        inner._pymusic_window_input_handlers = (
            playlist_down, playlist_up, window_up, window_down
        )
        inner.bind(on_touch_down=playlist_down, on_touch_up=playlist_up)
        Window.bind(on_touch_down=window_down, on_touch_up=window_up)

    def _bind_similar_scroll_input(self):
        """Reserve swipes inside recommendations for their own ScrollView."""
        inner = self.ids.get("similar_scroll")
        outer = self.ids.get("player_details_scroll")
        if inner is None or outer is None:
            return
        if getattr(inner, "_pymusic_touch_ownership_bound", False):
            return

        def release():
            inner._pymusic_active_touch_uid = None
            outer.do_scroll_y = True

        def touch_down(widget, touch):
            try:
                if (self._is_screen_active()
                        and not getattr(self, "_similar_collapsed", False)
                        and widget.height > 0
                        and widget.collide_point(*touch.pos)):
                    inner._pymusic_active_touch_uid = touch.uid
                    outer.do_scroll_y = False
            except Exception as exc:
                print("[SIMILAR] gesture down error:", exc)
            return False

        def touch_up(_widget, touch):
            if getattr(inner, "_pymusic_active_touch_uid", None) == touch.uid:
                release()
            return False

        def window_down(_window, touch):
            prev = getattr(inner, "_pymusic_active_touch_uid", None)
            if prev is not None and prev != touch.uid:
                release()
            return False

        inner._pymusic_touch_ownership_bound = True
        inner._pymusic_window_input_handlers = (touch_down, touch_up, window_down)
        inner.bind(on_touch_down=touch_down, on_touch_up=touch_up)
        Window.bind(on_touch_down=window_down, on_touch_up=touch_up)

    def _set_scrubbing(self, active: bool):
        self._is_scrubbing = bool(active)

    def _audio_pos_ms(self) -> int:
        try:
            if ma.android_player:
                return int(ma.android_player.getCurrentPosition() or 0)
        except Exception:
            pass
        return 0

    def _is_screen_active(self) -> bool:
        try:
            if self.manager is not None and self.manager.current != self.name:
                return False
        except Exception:
            pass
        try:
            app = App.get_running_app()
            root = getattr(app, "root", None)
            sm = getattr(root, "sm", None)
            if sm is not None and sm.current != self.name:
                return False
        except Exception:
            pass
        return True

    def on_pre_enter(self):
        for delay in (0.0, 0.06, 0.18, 0.40):
            Clock.schedule_once(lambda _dt: self._bind_player_geometry(), delay)
        self._app_in_background = False
        try:
            ma.request_post_notifications_permission()
        except Exception:
            pass
        ma.create_notification_channel()
        try:
            ma.webview_hide()
        except Exception:
            pass

        self._ensure_media_session()

        self._ms_next = self._act_next
        self._ms_prev = self._act_prev
        self._ms_toggle = self._act_toggle
        self._ms_play = self._act_play
        self._ms_pause = self._act_pause
        self._ms_repeat = self._act_repeat
        self._ms_favorite = self._act_favorite
        ma.bind_notification_action_router(self)

        self._bind_keys()
        self._bind_headset()
        self._sync_ui_loaded()

        self._ensure_video_player()
        Clock.schedule_once(self._align_video_to_thumb, 0.3)
        if getattr(self, "_last_video_url", None) and self._video_enabled:
            Clock.schedule_once(
                lambda _dt: self._set_video_controls_visible(
                    True, auto_hide=not bool(self._user_paused)
                ), 0.45,
            )

    def on_pre_leave(self):
        sheet = getattr(self, "_current_comments_popup_v4", None)
        if sheet is not None:
            sheet.dismiss()
        self._unbind_keys()
        self._unbind_headset()
        try:
            if self._media_session:
                is_playing = False
                try:
                    is_playing = bool(ma.android_player and ma.android_player.isPlaying())
                except Exception:
                    is_playing = False
                # Android 13/MIUI hides our QS media card when the session
                # becomes inactive. Pausing or navigating away is not a
                # Stop action: keep a loaded track's session available.
                if not (
                    is_playing or self._playback_desired
                    or bool(getattr(self, "_last_video_url", None))
                ):
                    self._media_session.setActive(False)
        except Exception:
            pass

        # Hand off muxed-video audio and release the SurfaceView once, before
        # the WebView screen becomes visible. The old code stopped the video
        # here, queued a second stop via _set_video_mode(False), and main.py
        # also stopped it twice around the same navigation event.
        try:
            self._navigating_away_from_video = True
            self._set_video_mode(False)
        except Exception as exc:
            print("[PLAYER-NAV] video handoff failed:", exc)
        finally:
            self._navigating_away_from_video = False

    def handle_app_pause(self):
        self._app_in_background = True
        self._last_bg_resume_ts = time.time()
        try:
            self._video_was_active = bool(self._video_active)
            if self._video_active:
                self._video_resume_url = self._last_video_url
                self._video_resume_gen = int(self._load_gen)
                self._set_video_mode(False)
            else:
                self._video_resume_url = None
                self._video_resume_gen = -1
        except Exception:
            pass

    def _open_player_from_notification(self, *args):
        """Called from the notification content tap: show the audio/video screen."""
        def _do_open(_dt=0):
            try:
                self._app_in_background = False
            except Exception:
                pass
            try:
                app = App.get_running_app()
                root = getattr(app, "root", None)
                if root and hasattr(root, "open_audio"):
                    root.open_audio()
                elif self.manager is not None:
                    self.manager.current = self.name
            except Exception:
                try:
                    if self.manager is not None:
                        self.manager.current = self.name
                except Exception:
                    pass
            try:
                self.handle_app_resume()
            except Exception:
                pass
            try:
                self._ensure_video_player()
                self._set_video_controls_visible(True)
                Clock.schedule_once(self._align_video_to_thumb, 0.15)
            except Exception:
                pass
        Clock.schedule_once(_do_open, 0)

    def handle_app_resume(self):
        try:
            if self._media_session and getattr(self, "_last_video_url", None):
                self._media_session.setActive(True)
        except Exception:
            pass
        for delay in (0.0, 0.06, 0.18, 0.40):
            Clock.schedule_once(lambda _dt: self._bind_player_geometry(), delay)
        self._app_in_background = False
        self._last_bg_resume_ts = time.time()
        self._last_watch_pos = None
        self._buffer_watchdog_ts = time.time()
        try:
            Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)
            Clock.schedule_once(lambda dt: self._render_playlist_ui(force=True), 0)
            Clock.schedule_once(lambda dt: self._render_similar_ui(), 0)
            Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)
        except Exception:
            pass
        try:
            video_resume_started = False
            if (
                self._video_was_active
                and self._video_resume_url
                and self._video_resume_url == self._last_video_url
                and int(self._video_resume_gen) == int(self._load_gen)
            ):
                self._video_was_active = False
                if self._last_video_url and self._video_enabled:
                    threading.Thread(
                        target=lambda: self._auto_video_for_current(self._load_gen, sync_start=False),
                        daemon=True, name="pymusic-resume-video",
                    ).start()
                    video_resume_started = True
            else:
                self._video_was_active = False
            if (
                not video_resume_started
                and self._playback_desired
                and not self._user_paused
                and self._video_enabled
                and self._last_video_url
                and not self._video_active
            ):
                threading.Thread(
                    target=lambda: self._auto_video_for_current(self._load_gen, sync_start=False),
                    daemon=True, name="pymusic-resume-video",
                ).start()
        except Exception:
            pass
        # Android 13 may discard the SurfaceView holder during pause.
        # Recreate/reposition the native touch controls on the audio screen
        # even when playback was paused (then show the thumbnail beneath).
        if (
            self._is_screen_active()
            and self._video_enabled
            and self._last_video_url
        ):
            self._ensure_video_player()
            Clock.schedule_once(
                lambda _dt: self._set_video_controls_visible(
                    True, auto_hide=not bool(self._user_paused)
                ), 0.16,
            )
            Clock.schedule_once(self._align_video_to_thumb, 0.21)
        if _current_video_url_of(self):
            self._ensure_related_current_v4(force=False)
        for delay in (0.0, 0.12, 0.5):
            Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), delay)


    def _configure_video_block(self) -> None:
        block = self.ids.get("video_block")
        thumb = self.ids.get("audio_thumbnail")
        if block is None:
            return
        try:
            block.padding = (0, 0, 0, 0)
            block.spacing = 0
        except Exception:
            pass
        wanted_h = max(dp(1), float(block.width or 0) * 9.0 / 16.0)
        if abs(float(block.height or 0) - wanted_h) > 0.25:
            block.height = wanted_h
        if thumb is not None:
            try:
                thumb.size_hint = (1, 1)
                thumb.pos_hint = {"x": 0, "y": 0}
                thumb.allow_stretch = True
                thumb.keep_ratio = True
                thumb.fit_mode = "contain"
            except Exception:
                pass
        _request_player_layout(block)

    def _align_video_to_thumb(self, *args):
        try:
            vp = getattr(self, "_video_player", None)
            if vp is None:
                return
            if not self._is_screen_active():
                return
            # Native SurfaceView needs real viewport bounds *before*
            # MediaPlayer.prepareAsync(). Waiting until video_active (set
            # only after onPrepared) left a 1x1 surface and no visible video.
            if not (bool(getattr(self, "_video_active", False))
                    or bool(getattr(self, "_pymusic_video_starting", False))):
                return

            self._configure_video_block()
            block = self.ids.get("video_block")
            if block is None:
                return

            win_w, win_h = Window.size
            if win_w <= 0 or win_h <= 0:
                return

            activity = ma.PythonActivity.mActivity
            try:
                metrics = activity.getResources().getDisplayMetrics()
                screen_w = int(metrics.widthPixels)
                screen_h = int(metrics.heightPixels)
            except Exception:
                screen_w = int(getattr(vp, "screen_w_px", 0) or 1080)
                screen_h = int(getattr(vp, "screen_h_px", 0) or 1920)

            kx = float(screen_w) / float(win_w)
            ky = float(screen_h) / float(win_h)
            wx, wy = block.to_window(block.x, block.y, relative=False)
            ww = float(block.width or 0)
            wh = float(block.height or 0)
            if ww <= 0 or wh <= 0:
                return

            left = int(round(float(wx) * kx))
            top = int(round((float(win_h) - (float(wy) + wh)) * ky))
            width = int(round(ww * kx))
            height = int(round(wh * ky))

            # Hide one-pixel compositor seams without covering nearby UI.
            left -= 2
            top -= 2
            width += 4
            height += 4
            if left < 0:
                width += left
                left = 0
            if top < 0:
                height += top
                top = 0
            width = max(1, min(width, screen_w - left))
            height = max(1, min(height, screen_h - top))
            vp.set_bounds(left, top, width, height)
        except Exception as exc:
            print("[FINAL] video block alignment failed:", exc)

    def _apply_metadata_layout(self, _dt=0) -> None:
        if bool(getattr(self, "_pymusic_final_layout_busy", False)):
            return
        self._pymusic_final_layout_busy = True
        try:
            title_view = self.ids.get("title_scroll")
            title = self.ids.get("audio_title")
            views = self.ids.get("audio_views")
            if title_view is None or title is None or views is None:
                return

            width = max(dp(1), float(title_view.width or 0))
            try:
                title.padding = (0, 0)
            except Exception:
                pass
            title.halign = "left"
            title.valign = "top"
            title.text_size = (width, None)
            try:
                title.texture_update()
            except Exception:
                pass

            title_text = str(title.text or "").strip()
            title_h = float((title.texture_size or (0, 0))[1] or 0)
            title_h = title_h if title_text else 0.0
            visible_title_h = min(title_h, dp(58)) if title_h > 0 else 0.0
            title_view.height = visible_title_h
            title.height = title_h
            title_view.do_scroll_x = False
            title_view.do_scroll_y = title_h > visible_title_h + dp(1)
            title_view.scroll_y = 1.0
            try:
                title_view.always_overscroll = False
                title_view.bar_width = 0
                title_view.smooth_scroll_end = 0
            except Exception:
                pass

            try:
                views.padding = (0, 0)
            except Exception:
                pass
            views.text_size = (max(dp(1), float(views.width or 0)), None)
            try:
                views.texture_update()
            except Exception:
                pass
            views_text = str(views.text or "").strip()
            views_h = float((views.texture_size or (0, 0))[1] or 0)
            views.height = views_h if views_text else 0.0

            details = title_view.parent
            if details is not None:
                details.spacing = 0
                details.padding = (dp(16), dp(8), dp(16), dp(1))
                _request_player_layout(details)

            outer = self.ids.get("player_details_scroll")
            if outer is not None:
                _request_player_layout(outer.children[0] if outer.children else None)
                _request_player_layout(outer)
        except Exception as exc:
            print("[FINAL] metadata layout failed:", exc)
        finally:
            self._pymusic_final_layout_busy = False

    def _queue_metadata_layout(self, delay=0.0) -> None:
        try:
            event = getattr(self, "_pymusic_final_layout_event", None)
            if event is not None:
                event.cancel()
        except Exception:
            pass
        self._pymusic_final_layout_event = Clock.schedule_once(
            lambda dt: self._apply_metadata_layout(dt), delay
        )

    def _bind_player_geometry(self) -> None:
        try:
            title_view = self.ids.get("title_scroll")
            title = self.ids.get("audio_title")
            views = self.ids.get("audio_views")
            block = self.ids.get("video_block")
            if title_view is not None and not getattr(
                title_view, "_pymusic_final_bound_v2", False
            ):
                if title is not None:
                    title.bind(text=lambda *_a: self._queue_metadata_layout(0.025))
                    title.bind(texture_size=lambda *_a: self._queue_metadata_layout(0.025))
                if views is not None:
                    views.bind(text=lambda *_a: self._queue_metadata_layout(0.025))
                    views.bind(texture_size=lambda *_a: self._queue_metadata_layout(0.025))
                title_view.bind(width=lambda *_a: self._queue_metadata_layout(0.025))
                title_view._pymusic_final_bound_v2 = True
            if block is not None and not getattr(
                block, "_pymusic_final_bound_v2", False
            ):
                block.bind(
                    width=lambda *_a: Clock.schedule_once(
                        lambda _dt: (
                            self._configure_video_block(),
                            self._align_video_to_thumb(),
                        ),
                        0,
                    )
                )
                block._pymusic_final_bound_v2 = True

            # video_block is a fixed sibling ABOVE player_details_scroll in
            # youtube_gui.kv. Moving the details list never moves the video.
            # Do not track scroll_y with a native SurfaceView setLayoutParams:
            # frequent Android UI-thread layout requests cause animation
            # jank, especially when switching to the real WebView.
            if block is not None and not getattr(
                block, "_pymusic_native_geometry_bound", False
            ):
                block.bind(pos=lambda *_a: self._align_video_to_thumb())
                block.bind(size=lambda *_a: self._align_video_to_thumb())
                block._pymusic_native_geometry_bound = True
        except Exception as exc:
            print("[FINAL] layout binding failed:", exc)

        self._configure_video_block()
        self._apply_metadata_layout()
        Clock.schedule_once(lambda _dt: self._align_video_to_thumb(), 0)

    def toggle_video_controls(self):
        self._set_video_controls_visible(not self._video_controls_visible)

    def _native_video_tap(self, zone="center"):
        """Handle SurfaceView ACTION_DOWN with no Python Clock playback delay.

        Java transport owns the three actual ImageButtons. This fallback
        handles taps on the video picture itself and defers only UI updates.
        """
        try:
            if not self._video_controls_visible:
                Clock.schedule_once(lambda _dt: self._set_video_controls_visible(True), 0)
                return

            zone = str(zone or "center")
            now = time.monotonic()
            last_ts = float(getattr(self, "_pymusic_native_touch_ts", 0.0) or 0.0)
            if (
                zone == getattr(self, "_pymusic_native_touch_zone", "")
                and now - last_ts < 0.10
            ):
                return
            self._pymusic_native_touch_ts = now
            self._pymusic_native_touch_zone = zone
            player = getattr(ma, "android_player", None)
            prepared = bool(player is not None and ma._is_prepared())
            vp = self._video_player

            if zone == "center":
                if not prepared:
                    Clock.schedule_once(lambda _dt: self.video_toggle_play(), 0)
                    return
                playing = bool(player.isPlaying())
                if playing:
                    self._resume_pos_ms = int(player.getCurrentPosition() or 0)
                    self._mark_java_transport(True)
                    self._user_paused = True
                    self._playback_desired = False
                    player.pause()
                    ma.is_playing = False
                    if vp and vp.player and vp._prepared and vp.player.isPlaying():
                        vp.player.pause()
                else:
                    self._mark_java_transport(False)
                    self._user_paused = False
                    self._playback_desired = True
                    player.start()
                    ma.is_playing = True
                    if vp and vp.player and vp._prepared and not vp.player.isPlaying():
                        vp.player.start()
                if vp:
                    vp.set_native_playing(not playing)
                Clock.schedule_once(
                    lambda _dt, state=not playing: self._ui_set_playing(state), 0
                )
                Clock.schedule_once(lambda _dt: self._set_video_controls_visible(True), 0)
                return

            delta = -10000 if zone == "left" else 10000
            pos = int(player.getCurrentPosition() or 0) if prepared else 0
            target = max(0, pos + delta)
            if prepared:
                try:
                    player.seekTo(target)
                except Exception:
                    ma._mp_seek_to(target)
            self._resume_pos_ms = target
            if vp is not None:
                if vp.player and vp._prepared:
                    try:
                        vp.player.seekTo(target, 3)
                    except Exception:
                        vp.player.seekTo(target)
                else:
                    vp._pending_start_pos_ms = target
                duration = int(player.getDuration() or 0) if prepared else 0
                vp.set_native_progress(target, max(1, duration))

            def update_ui(_dt):
                try:
                    self.ids.current_time_label.text = self._fmt_ms(target)
                    self.ids.progress_slider.value = target / 1000.0
                    self._set_video_controls_visible(True)
                except Exception:
                    pass
            Clock.schedule_once(update_ui, 0)
        except Exception as exc:
            print("[VIDEO-CTRL-FAST] fallback tap failed:", exc)

    def _handle_video_surface_tap(self, zone: str = "center"):
        zone = str(zone or "center")
        if not self._video_controls_visible:
            self._set_video_controls_visible(True)
            return
        if zone == "left":
            self.video_seek(-10)
        elif zone == "right":
            self.video_seek(10)
        else:
            self.video_toggle_play()
        self._set_video_controls_visible(True)

    def _set_video_controls_visible(self, visible: bool, auto_hide: bool = True):
        self._video_controls_visible = bool(visible)
        try:
            if self._video_player and hasattr(self._video_player, "set_native_controls_visible"):
                self._video_player.set_native_controls_visible(bool(visible))
        except Exception:
            pass
        try:
            if self._video_controls_ev:
                self._video_controls_ev.cancel()
                self._video_controls_ev = None
        except Exception:
            pass
        # A hidden overlay should never schedule another expensive native
        # SurfaceView bounds/layout update during screen navigation.
        if visible and self._is_screen_active():
            Clock.schedule_once(self._align_video_to_thumb, 0)
        if visible and auto_hide:
            self._video_controls_ev = Clock.schedule_once(self._hide_video_controls, 5.0)

    def _hide_video_controls(self, dt):
        self._set_video_controls_visible(False, auto_hide=False)

    def video_toggle_play(self):
        self.toggle_play_pause()

    def video_seek(self, delta_sec: int):
        try:
            pos = ma.android_player.getCurrentPosition() if ma.android_player else 0
        except Exception:
            pos = 0
        target = max(0, int(pos + (delta_sec * 1000)))
        try:
            ma._mp_seek_to(target)
            self._resume_pos_ms = target
            if self._video_player and self._video_enabled:
                self._video_player.seek_to(target)
        except Exception:
            pass
        self._set_video_controls_visible(True)

    # ==================== headset ====================
    # TODO дуже багато біндів я думаю їх можна якось оптимізувати
    def _bind_headset(self):
        try:
            headset_router.set_callbacks(
                on_play=self._act_play,
                on_pause=self._act_pause,
                on_toggle=self._act_toggle,
                on_next=self._act_next,
                on_prev=self._act_prev,
            )
            headset_router.set_active(True)
        except Exception as e:
            ma.log(f"[HEADSET] bind err: {e}")

    def _unbind_headset(self):
        try:
            headset_router.set_active(False)
        except Exception:
            pass

    # ==================== keys ====================

    def _background_tick_base(self, dt):
        # Native Java may pause before the next Python state mirror tick.
        bridge = _native_transport_bridge()
        try:
            if bridge is not None and bool(bridge.isUserPaused()):
                self._user_paused = True
                self._playback_desired = False
        except Exception:
            pass
        # працює навіть у фоні
        try:
            if ma.android_player and ma._is_prepared():
                pos = ma.android_player.getCurrentPosition() or 0
                dur = ma.android_player.getDuration() or 0
                if dur > 0:
                    self._last_good_dur_ms = int(dur)
                elif self._last_good_dur_ms > 0:
                    dur = int(self._last_good_dur_ms)
                is_playing = bool(ma.android_player.isPlaying())
                if self._maybe_handle_track_end(pos, dur):
                    return
                if self._playback_desired and not self._user_paused and not is_playing:
                    now = time.time()
                    # якщо ще не кінець треку — намагаємось м'яко відновити playback у фоні
                    if dur <= 0 or pos < max(0, dur - 2500):
                        if (now - self._last_bg_resume_ts) > 2.0:
                            self._last_bg_resume_ts = now
                            try:
                                ma._mp_start()
                                is_playing = bool(ma.android_player.isPlaying())
                            except Exception:
                                pass
                    if dur <= 0 and pos <= 0:
                        if self._bg_stall_since <= 0:
                            self._bg_stall_since = now
                        elif (now - self._bg_stall_since) > 5.0:
                            self._bg_stall_since = now
                            self._recover_stream("bg_stall", self._resume_pos_ms)
                    else:
                        self._bg_stall_since = 0.0
                else:
                    self._bg_stall_since = 0.0

                ma.update_media_session_state(
                    is_playing,
                    position_ms=int(pos),
                    duration_ms=int(dur),
                    can_seek=True,
                    repeat_enabled=bool(self.repeat),
                    favorite_enabled=bool(self._favorite),
                )

                token = self._media_session.getSessionToken() if self._media_session else None
                ma.create_or_update_media_notification(
                    title=self._title or "Playing",
                    subtitle=self._channel or "YouTube",
                    is_playing=is_playing,
                    session_token=token,
                    large_icon_path=self._channel_thumb_local,
                    repeat_enabled=bool(self.repeat),
                    favorite_enabled=bool(self._favorite),
                )
        except Exception as e:
            print("[BG TICK] err:", e)

    def _background_tick(self, dt):
        try:
            if (
                getattr(self, "_pymusic_muxed_video_master", False)
                and not getattr(self, "_app_in_background", False)
            ):
                audio = getattr(ma, "android_player", None)
                vp = getattr(self, "_video_player", None)
                native_video = getattr(vp, "player", None) if vp else None
                if audio is not None and native_video is not None:
                    audio_pos = int(audio.getCurrentPosition() or 0)
                    video_pos = int(native_video.getCurrentPosition() or 0)
                    drift = video_pos - audio_pos
                    now = time.monotonic()
                    last_sync = float(
                        getattr(self, "_pymusic_shadow_sync_ts", 0.0)
                        or 0.0
                    )
                    if abs(drift) > 650 and (now - last_sync) > 2.0:
                        self._pymusic_shadow_sync_ts = now
                        try:
                            ma._mp_seek_to(video_pos)
                            print(
                                "[PLAYBACK-V5] shadow clock corrected "
                                f"drift={drift}"
                            )
                        except Exception:
                            pass
        except Exception:
            pass
        return self._background_tick_base( dt)


    def _maybe_handle_track_end(self, pos: int, dur: int) -> bool:
        try:
            if not (
                dur > 0
                and (dur - pos) <= 1500
                and self._playback_desired
                and not self._user_paused
                and self._bg_endguard_fired_gen != self._load_gen
            ):
                return False
            self._bg_endguard_fired_gen = self._load_gen
            self._resume_pos_ms = 0
            if self.repeat:
                self._restart_same()
                return True
            if self._auto_skip and self._advance_to_next_track():
                return True
            self._playback_desired = False
            self._ui_set_playing(False)
            return True
        except Exception:
            return False

    def _start_completion_guard(self, gen: int, dur_ms: int):
        try:
            dur_ms = int(dur_ms or 0)
        except Exception:
            dur_ms = 0
        if dur_ms <= 0:
            return

        self._completion_guard_gen = gen
        if getattr(self, "_pymusic_end_lock", None) is None:
            self._pymusic_end_lock = threading.Lock()

        def fire_end_once(reason: str, pos: int, playing: bool) -> bool:
            lock = self._pymusic_end_lock
            with lock:
                if not self._is_current_gen(gen):
                    return False
                if self._completion_guard_gen != gen:
                    return False
                if self._bg_endguard_fired_gen == gen:
                    return False
                self._bg_endguard_fired_gen = gen

            print(
                "[BG-NEXT] completion guard fired "
                f"gen={gen} pos={pos} dur={dur_ms} playing={playing} "
                f"reason={reason}"
            )
            self._resume_pos_ms = 0
            if self.repeat:
                # Keep repeat off the watcher thread too.
                threading.Thread(target=self._restart_same, daemon=True).start()
            elif self._auto_skip:
                self._queue_auto_next()
            else:
                self._playback_desired = False
                self._user_paused = True
                Clock.schedule_once(lambda _dt: self._ui_set_playing(False), 0)
            return True

        def job():
            while (
                self._is_current_gen(gen)
                and self._completion_guard_gen == gen
            ):
                if not self._playback_desired or self._user_paused:
                    return
                if self._bg_endguard_fired_gen == gen:
                    return

                player = getattr(ma, "android_player", None)
                if player is None:
                    time.sleep(0.15)
                    continue

                try:
                    pos = max(0, int(player.getCurrentPosition() or 0))
                except Exception:
                    pos = 0
                try:
                    playing = bool(player.isPlaying())
                except Exception:
                    playing = True

                remaining = max(0, dur_ms - pos)

                # A missed Android onCompletion is detected immediately once
                # MediaPlayer stops at the end.  Do not treat buffering in
                # the middle of a track as completion.
                if (not playing) and remaining <= 1500:
                    fire_end_once("player-stopped-near-end", pos, playing)
                    return

                # Some vendor MediaPlayers keep reporting isPlaying() for a
                # fraction of a second after their position reaches duration.
                if remaining <= 80:
                    time.sleep(0.08)
                    try:
                        pos2 = max(0, int(player.getCurrentPosition() or 0))
                    except Exception:
                        pos2 = pos
                    try:
                        playing2 = bool(player.isPlaying())
                    except Exception:
                        playing2 = playing
                    if (not playing2) or pos2 >= max(0, dur_ms - 40):
                        fire_end_once("position-reached-end", pos2, playing2)
                        return

                # Frequent enough for a seamless transition, cheap enough to
                # keep a single daemon watcher alive for long tracks.
                if remaining > 5000:
                    delay = 0.75
                elif remaining > 1500:
                    delay = 0.35
                else:
                    delay = 0.12
                time.sleep(delay)

        threading.Thread(target=job, daemon=True).start()

    def _bind_keys(self):
        try:
            from jnius import autoclass
            KeyEvent = autoclass("android.view.KeyEvent")
            media_toggle = {
                int(KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE),
                int(KeyEvent.KEYCODE_HEADSETHOOK),
            }
            media_play = int(KeyEvent.KEYCODE_MEDIA_PLAY)
            media_pause = int(KeyEvent.KEYCODE_MEDIA_PAUSE)
            media_next = int(KeyEvent.KEYCODE_MEDIA_NEXT)
            media_prev = int(KeyEvent.KEYCODE_MEDIA_PREVIOUS)
        except Exception:
            media_toggle = {79, 85}
            media_play = 126
            media_pause = 127
            media_next = 87
            media_prev = 88

        def _on_key_down(window, keycode, scancode, text, modifiers):
            code = keycode[0] if isinstance(keycode, (tuple, list)) else keycode
            if code in media_toggle:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down toggle code={code}")
                except Exception:
                    pass
                self._act_toggle()
                return True
            if code == media_play:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down play code={code}")
                except Exception:
                    pass
                self._act_play()
                return True
            if code == media_pause:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down pause code={code}")
                except Exception:
                    pass
                self._act_pause()
                return True
            if code == media_next:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down next code={code}")
                except Exception:
                    pass
                self._act_next()
                return True
            if code == media_prev:
                if time.time() - getattr(self, "_last_media_ts", 0.0) < 0.3:
                    return True
                try:
                    ma.log(f"[KEY] on_key_down prev code={code}")
                except Exception:
                    pass
                self._act_prev()
                return True
            if code in (24, 25) and (time.time() - getattr(self, "_last_media_ts", 0.0) < 0.7):
                return True
            return False

        self._bind_uid = Window.bind(on_key_down=_on_key_down)

    def _unbind_keys(self):
        try:
            Window.unbind_uid("on_key_down", self._bind_uid)
        except Exception:
            pass

    # ==================== public API (audio) ====================
# TODO створи клас для відео з всіма полями тайлтлу і тд щоб їх отримувати тут
    def _remember_recent(self, url: str, title: str, channel: str, thumb: str):
        try:
            if not url:
                return
            recent = load_recent()
            existing = {}
            try:
                existing = next((r for r in recent if r.get("url") == url), {}) or {}
            except Exception:
                existing = {}
            entry = {
                "url": url,
                "title": title or existing.get("title") or "",
                "channel": channel or existing.get("channel") or "",
                "thumb": thumb or existing.get("thumb") or "",
            }
            for key in ("cache_path", "art_path"):
                if existing.get(key):
                    entry[key] = existing.get(key)
            recent = [r for r in recent if r.get("url") != url]
            recent.insert(0, entry)
            save_recent(recent)
        except Exception:
            pass

    def _hydrate_metadata_from_known_sources(self, video_url: str) -> bool:
        """Fill title/channel/thumb from playlist/recent/favorites without network."""
        changed = False
        if not video_url:
            return False

        candidates = []
        try:
            if self.playlist and self.playlist.tracks:
                candidates.extend(
                    t for t in self.playlist.tracks
                    if isinstance(t, dict) and t.get("url") == video_url
                )
        except Exception:
            pass
        try:
            candidates.extend(
                r for r in load_recent()
                if isinstance(r, dict) and r.get("url") == video_url
            )
        except Exception:
            pass
        try:
            candidates.extend(
                f for f in load_favorites()
                if isinstance(f, dict) and f.get("url") == video_url
            )
        except Exception:
            pass

        for item in candidates:
            title = str(item.get("title") or "").strip()
            channel = _verified_channel_name(
                item.get("channel") or item.get("artist"), title or self._title
            )
            thumb = str(item.get("thumb") or item.get("thumbnail") or item.get("art_path") or "").strip()
            if title and (not self._title or self._title.lower() in {"playing", "playing audio"}):
                self._title = title
                changed = True
            if channel and (not self._channel or self._channel.lower() in {"youtube", "unknown"}):
                self._channel = channel
                changed = True
            if thumb and not self._thumb:
                self._thumb = thumb
                changed = True
            if self._title and self._channel and self._thumb:
                break
        return changed

    def _play_audio_base(
        self,
        video_url: str,
        title: str = "",
        channel: str = "",
        duration_or_thumb=None,
        thumb: str | None = None,
        *,
        clear_playlist=True,
        hard_reset: bool = False,
    ):
        mix = _pymusic_mix_video_url(video_url)
        if mix is not None:
            video_url = mix[0]
            clear_playlist = True
        self._mark_java_transport(False)
        Clock.schedule_once(lambda _dt: self._register_java_transport_players(), 0)
        ui_allowed = not bool(getattr(self, "_app_in_background", False))
        self._ensure_media_session()
        same_active_url = bool(
            video_url
            and video_url == self._last_video_url
            and not hard_reset
            and ma.android_player
            and ma._is_prepared()
            and (self._playback_desired or self._user_paused)
        )
        if same_active_url:
            if title and not self._title:
                self._title = title
            if channel and not self._channel:
                self._channel = channel
            if thumb and not self._thumb:
                self._thumb = thumb
            self._hydrate_metadata_from_known_sources(video_url)
            self._publish_media_metadata_now()
            if ui_allowed:
                Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)
                Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)
                Clock.schedule_once(self._align_video_to_thumb, 0.2)
            print(f"[AUDIO] reuse active track url={video_url!r}")
            return
        _clear = bool(clear_playlist)
        if hard_reset:
            try:
                self._hard_transition_reset()
            except Exception:
                pass
        elif video_url and video_url != self._last_video_url:
            try:
                self._pre_start_cleanup()
            except Exception:
                pass
        if _clear:
            self._playlist_url = None
        self._last_video_url = video_url
        self._audio_play_request_t0 = time.monotonic()
        self._audio_first_progress_gen = -1
        self._audio_source_attempts = set()
        self._remember_recent(video_url, title, channel, str(thumb or ""))
        self._favorite = bool(is_favorite(video_url))
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1
        self._video_active = False
        self._stream_error_hits = 0
        self._last_stream_error_ts = 0.0
        self._prefer_compat_audio = False
        self._stream_format_id = ""
        self._next_prefetch_requested_gen = -1
        self._video_launch_requested_gen = -1
        self._audio_ready_for_extras_gen = -1
        self._buffering_started_at = 0.0
        self._views_text = ""
        self._published_date = ""
        self._related_items = []
        self._related_source = "youtube"
        if ui_allowed:
            self._render_similar_ui()

        # 🔹 новий трек - не відновлюємось з позиції попереднього
        self._resume_pos_ms = 0

        # зупиняємо відео, якщо було
        try:
            if self._video_player:
                self._video_player.stop()
            if ui_allowed:
                self._set_video_mode(False)
        except Exception:
            pass

        # якщо duration_or_thumb - це URL мініатюри
        if isinstance(duration_or_thumb, str) and duration_or_thumb.startswith("http") and thumb is None:
            thumb = duration_or_thumb

        if _clear:
            self.playlist.clear()
            if ui_allowed:
                self._render_playlist_ui(force=True)
            self._related_items = []
            self._related_source = "youtube"
            self._views_text = ""
            self._channel_thumb = ""
            self._channel_thumb_local = ""
            if ui_allowed:
                self._render_similar_ui()

        self._playback_desired = True
        self._user_paused = False
        try:
            ma.acquire_wake_lock()
            self._request_af()
        except Exception:
            pass

        self._title = title or ""
        self._channel = _verified_channel_name(channel, self._title)
        # Never show the previous video's artwork or channel avatar while
        # waiting for the new video's thumbnails to arrive.
        self._art_path = None
        self._channel_thumb = ""
        self._channel_thumb_local = ""
        thumb_url = thumb or ""
        self._thumb = thumb_url
        self._hydrate_metadata_from_known_sources(video_url)
        if self._last_video_url:
            cached_art = self._find_cached_art(self._last_video_url)
            if cached_art:
                self._art_path = cached_art
                self._thumb = cached_art
        if ui_allowed:
            self._sync_ui_loading()
            Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)

        # оновлюємо покоління завантаження
        self._load_gen += 1
        my_gen = self._load_gen
        self._bg_endguard_fired_gen = -1
        self._extract_fail_handled_gen = -1

        if thumb_url and thumb_url.startswith(("http://", "https://")):
            self._download_art_async(thumb_url)
        elif self._thumb and self._thumb.startswith(("http://", "https://")):
            self._download_art_async(self._thumb)

        # оновлення метаданих та нотифікації
        try:
            ma.set_media_metadata(
                title=self._title,
                artist=self._channel,
                art_path=self._art_path,
                art_uri=self._thumb or "",
                avatar_path=self._channel_thumb_local,
            )
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing",
                subtitle=self._channel or "YouTube",
                is_playing=True,
                session_token=token,
                large_icon_path=self._channel_thumb_local,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
        except Exception:
            pass

        # пробуємо локальний кеш (офлайн)
        if self._try_start_cached_audio(video_url, my_gen):
            if ui_allowed:
                Clock.schedule_once(self._align_video_to_thumb, 0.5)
            self._ensure_metadata_async(video_url)
            return

        # пробуємо взяти URL з кешу
        fast = self._url_cache.get(video_url)
        if fast and fast.get("audio_url"):
            # перевірка на протухання URL
            if not fast.get("expire_ts") or (int(time.time()) + 120) < int(fast["expire_ts"]):
                self._stream_url = fast["audio_url"]
                self._headers = dict(fast["headers"] or {})
                self._expire_ts = fast.get("expire_ts")
                threading.Thread(
                    target=lambda: self._start_from_known_stream(my_gen),
                    daemon=True,
                ).start()
                if ui_allowed:
                    Clock.schedule_once(self._align_video_to_thumb, 0.5)
                self._ensure_metadata_async(video_url)
                return
            else:
                # видаляємо старий запис
                self._url_cache.pop(video_url, None)

        # якщо в кеші нема або протух - витягуємо свіжий URL через yt_dlp
        threading.Thread(
            target=lambda: self._extract_and_start_gen(video_url, my_gen),
            daemon=True,
        ).start()

        # підлаштувати рамку відео під превʼю (навіть якщо відео зараз вимкнене)
        if ui_allowed:
            Clock.schedule_once(self._align_video_to_thumb, 0.5)
        self._ensure_metadata_async(video_url)

    def play_audio(self, video_url, *args, **kwargs):
        self._current_ensure_state()
        canonical = _current_canonical_video_url(video_url)
        previous_video = _current_video_url_of(self)
        # A comments sheet belongs to one specific video. Dismiss it before
        # switching tracks; do not auto-fetch the next video's comments.
        if canonical and canonical != previous_video:
            sheet = self._current_comments_popup_v4
            if sheet is not None:
                if threading.current_thread() is threading.main_thread():
                    sheet.dismiss()
                else:
                    Clock.schedule_once(lambda _dt, popup=sheet: popup.dismiss(), 0)
        self._current_video_url_v4 = canonical
        self._current_related_url_v4 = ""
        self._current_auto_next_pending_v4 = False
        self._current_comments_url_v4 = ""
        self._current_comments_v4 = []
        self._current_comment_count_v4 = None
        self._current_comments_loading_v4 = False
        print(f"[CURRENT-V4] current video set url={canonical!r}")
        result = self._play_audio_base( video_url, *args, **kwargs)
        final_url = _current_canonical_video_url(getattr(self, "_last_video_url", "") or canonical)
        if final_url:
            self._current_video_url_v4 = final_url
            if not getattr(self, "_last_video_url", None):
                self._last_video_url = final_url
        self._ensure_related_current_v4( force=False)
        for delay in (0.0, 0.10, 0.40):
            Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), delay)
        return result


    def _play_playlist_base(
        self,
        tracks,
        maybe2=None,
        *,
        start_index=0,
        clear_playlist=True,
        playlist_url=None,
        start_playback=True,
    ):
        """
        Старт плейлиста:
          play_playlist(tracks, playlist_title)
          play_playlist(tracks, start_index=3)
          play_playlist(tracks, False, start_index=3)  # не чистити, а дописати
        """
        playlist_name = ""

        if isinstance(maybe2, bool):
            _clear = maybe2
            _start = start_index
        elif isinstance(maybe2, int):
            _clear = clear_playlist
            _start = maybe2
        elif isinstance(maybe2, str):
            _clear = clear_playlist
            _start = start_index
            playlist_name = maybe2
        else:
            _clear = clear_playlist
            _start = start_index

        self.playlist.set_tracks(
            tracks,
            name=playlist_name,
            start_index=_start,
            extend=not _clear,
        )
        try:
            print(
                f"[PLAYLIST] applied tracks={len(self.playlist)} start={_start} bg={self._app_in_background} start_playback={start_playback}"
            )
        except Exception:
            pass
        if playlist_url:
            self._playlist_url = playlist_url
        try:
            if self._last_video_url and self._hydrate_metadata_from_known_sources(self._last_video_url):
                self._publish_media_metadata_now()
        except Exception:
            pass
        if not self._app_in_background:
            self._render_playlist_ui(force=True)

        current = self.playlist.current()
        if current and start_playback:
            self.play_audio(
                current["url"],
                current["title"],
                current["channel"],
                current.get("thumb") or "",
                clear_playlist=False,
            )

    # ==================== helpers ====================

    def _invalidate_previous_playlist_transition(self) -> None:
        """Make every queued callback from the previous track stale immediately."""
        try:
            self._load_gen = int(getattr(self, "_load_gen", 0) or 0) + 1
        except Exception:
            pass
    
        for attr, value in (
            ("_completion_guard_gen", -1),
            ("_bg_endguard_fired_gen", -1),
            ("_extract_fail_handled_gen", -1),
            ("_pymusic_next_started_gen", -1),
        ):
            try:
                setattr(self, attr, value)
            except Exception:
                pass
    
        # Clear pending intents owned by the later runtime layers too.  These attrs
        # are optional so this remains safe regardless of patch install timing.
        try:
            self._current_auto_next_pending_v4 = False
        except Exception:
            pass
        try:
            self._playlist_play_current_before_next_v1 = False
        except Exception:
            pass
        try:
            self._network_waiting_v6 = False
            self._network_wait_gen_v6 = -1
        except Exception:
            pass

    def play_playlist(self, tracks, *args, **kwargs):
        start_playback = bool(kwargs.get("start_playback", True))
        current_before = str(getattr(self, "_last_video_url", "") or "")

        # Materialize once.  yt-dlp callers normally pass a list, but doing
        # this here also makes generators deterministic for both the guard
        # and the original play_playlist implementation.
        try:
            incoming = list(tracks or [])
        except Exception:
            incoming = tracks

        requested_index = _playlist_requested_start_index_core(
            args,
            kwargs,
            len(incoming) if isinstance(incoming, list) else 0,
        )

        if start_playback:
            # Critical ordering: invalidate the old MediaPlayer generation
            # BEFORE old_play_playlist calls playlist.set_tracks().  A stale
            # completion can no longer advance the new queue from 0 to 1.
            self._invalidate_previous_playlist_transition()
            try:
                print(
                    "[PLAYLIST-FIRST-V2] opening queue "
                    f"requested_index={requested_index} pre_gen={getattr(self, '_load_gen', -1)}"
                )
            except Exception:
                pass

        result = self._play_playlist_base( incoming, *args, **kwargs)

        # Only the background queue-install path can have an already playing
        # seed video that is absent from the returned queue.
        pending = False
        if not start_playback and current_before:
            try:
                queue = list(getattr(getattr(self, "playlist", None), "tracks", []) or [])
                current_present = any(
                    _playlist_same_video_core(
                        current_before,
                        str((item or {}).get("url") or (item or {}).get("video_id") or ""),
                    )
                    for item in queue
                    if isinstance(item, dict)
                )
                pending = bool(queue and not current_present)
            except Exception:
                pending = False

        self._playlist_play_current_before_next_v1 = pending
        if pending:
            try:
                print(
                    "[PLAYLIST-FIRST-V2] current seed is outside queue; "
                    "first transition will play queue index 0"
                )
            except Exception:
                pass

        if start_playback:
            # Defensive verification.  Normally the generation invalidation
            # above is enough.  If another non-generation callback changed
            # the queue synchronously, restore the requested item now.
            try:
                playlist = getattr(self, "playlist", None)
                queue = list(getattr(playlist, "tracks", []) or []) if playlist else []
                if queue:
                    target_index = requested_index if 0 <= requested_index < len(queue) else 0
                    target = queue[target_index]
                    target_url = str((target or {}).get("url") or "")
                    current_url = str(getattr(self, "_last_video_url", "") or "")
                    actual_index = int(getattr(playlist, "index", 0) or 0)

                    if actual_index != target_index:
                        print(
                            "[PLAYLIST-FIRST-V2] repaired opening index "
                            f"{actual_index} -> {target_index}"
                        )
                        playlist.index = target_index

                    if target_url and not _playlist_same_video_core(current_url, target_url):
                        print(
                            "[PLAYLIST-FIRST-V2] stale transition selected wrong track; "
                            f"restoring index={target_index} url={target_url!r}"
                        )
                        self.play_audio(
                            target_url,
                            (target or {}).get("title") or "",
                            (target or {}).get("channel") or "",
                            (target or {}).get("thumb") or "",
                            clear_playlist=False,
                            hard_reset=True,
                        )
            except Exception as exc:
                print("[PLAYLIST-FIRST-V2] opening verification failed:", exc)

        return result


    def _download_art_async(self, url: str):
        if not url or not url.startswith(("http://", "https://")):
            return

        # Bound each artwork request to the selected video. A late image
        # download from the previous track must not replace current
        # MediaSession artwork or the notification's background colors.
        requested_video_url = str(self._last_video_url or "")

        def _job():
            try:
                art_path = None
                if requested_video_url:
                    art_path = self._art_cache_path(requested_video_url)
                if not art_path:
                    art_path = os.path.join(tempfile.gettempdir(), "pymusic_art.jpg")
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
                        "Referer": "https://www.youtube.com",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Connection": "keep-alive",
                    },
                )
                # Album art is a bounded, verified HTTPS asset. The old
                # _create_unverified_context accepted forged certs and wrote
                # arbitrary response sizes directly into the media cache.
                def trusted_art(hostname: str) -> bool:
                    host = str(hostname or "").lower()
                    return host in (
                        "i.ytimg.com", "img.youtube.com",
                        "yt3.ggpht.com", "yt3.googleusercontent.com",
                    )

                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme != "https" or not trusted_art(parsed.hostname):
                    return

                class SafeArtRedirect(urllib.request.HTTPRedirectHandler):
                    def redirect_request(self, request, fp, code, msg, hdrs, newurl):
                        target = urllib.parse.urlsplit(newurl)
                        if target.scheme != "https" or not trusted_art(target.hostname):
                            raise ValueError("untrusted thumbnail redirect")
                        return super().redirect_request(
                            request, fp, code, msg, hdrs, newurl
                        )

                context = ssl.create_default_context(cafile=certifi.where())
                opener = urllib.request.build_opener(
                    urllib.request.HTTPSHandler(context=context),
                    SafeArtRedirect(),
                )
                with opener.open(req, timeout=12) as response:
                    length = int(response.headers.get("Content-Length") or 0)
                    if length > 2 * 1024 * 1024:
                        return
                    image_bytes = response.read(2 * 1024 * 1024 + 1)
                    if len(image_bytes) > 2 * 1024 * 1024 or len(image_bytes) < 128:
                        return
                temp = art_path + ".part"
                try:
                    with open(temp, "wb") as output:
                        output.write(image_bytes)
                    os.replace(temp, art_path)
                finally:
                    try:
                        if os.path.exists(temp):
                            os.unlink(temp)
                    except OSError:
                        pass
                if requested_video_url:
                    update_recent_art(requested_video_url, art_path)
                if requested_video_url != str(self._last_video_url or ""):
                    return
                self._art_path = art_path
                try:
                    ma.set_media_metadata(
                        title=self._title, artist=self._channel,
                        art_path=self._art_path, art_uri=self._thumb,
                        avatar_path=self._channel_thumb_local,
                    )
                except Exception:
                    pass
                try:
                    token = self._media_session.getSessionToken() if self._media_session else None
                    ma.create_or_update_media_notification(
                        title=self._title or "Playing",
                        subtitle=self._channel or "YouTube",
                        is_playing=bool(ma.android_player and ma.android_player.isPlaying()),
                        session_token=token,
                        large_icon_path=self._channel_thumb_local,
                        repeat_enabled=bool(self.repeat),
                        favorite_enabled=bool(self._favorite),
                    )
                except Exception:
                    pass
            except Exception:
                pass

        threading.Thread(target=_job, daemon=True).start()

    def _channel_avatar_cache_path(self, channel_thumb_url: str) -> str:
        key = self._cache_key(channel_thumb_url or self._channel or "channel")
        return os.path.join(self._art_cache_dir(), f"ch_{key}.jpg")

    def _download_channel_avatar_async(self, url: str):
        """Cache only verified YouTube channel images, never HTML/video art."""
        avatar_url = ydlh._validated_yt_avatar_url(url)
        if not avatar_url:
            print("[META] rejected non-YouTube channel avatar URL")
            return

        def _apply_file(path: str) -> None:
            # UI updates must happen on the Kivy thread and belong to the
            # selected channel, even when the user switches tracks quickly.
            if str(getattr(self, "_channel_thumb", "") or "") != avatar_url:
                return
            self._channel_thumb_local = path
            try:
                ma.log(f"[META] avatar saved: {path}")
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)
            Clock.schedule_once(lambda dt: self._publish_media_metadata_now(), 0)

        def _job():
            tmp = None
            try:
                path = self._channel_avatar_cache_path(avatar_url)
                if os.path.isfile(path) and os.path.getsize(path) > 128:
                    _apply_file(path)
                    return

                req = urllib.request.Request(
                    avatar_url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Linux; Android 12; Mobile) "
                            "AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36"
                        ),
                        "Referer": "https://www.youtube.com",
                        "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
                    },
                )
                # Never disable HTTPS certificate verification to fetch art.
                context = ssl.create_default_context(cafile=certifi.where())
                with urllib.request.urlopen(req, timeout=9, context=context) as resp:
                    mime = str(resp.headers.get("Content-Type", "")).lower()
                    if not mime.startswith("image/"):
                        raise ValueError("avatar URL returned non-image content")
                    data = resp.read(2_000_001)
                if len(data) > 2_000_000 or len(data) < 128:
                    raise ValueError("avatar image size outside allowed limits")
                if not (
                    data.startswith(b"\xff\xd8\xff")
                    or data.startswith(b"\x89PNG\r\n\x1a\n")
                    or (data.startswith(b"RIFF") and data[8:12] == b"WEBP")
                ):
                    raise ValueError("avatar response has invalid image header")

                # Unique temporary file, atomic replacement, and no partial
                # images visible to the Kivy image loader.
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=os.path.dirname(path),
                    prefix="channel-avatar-", suffix=".tmp", delete=False
                ) as out:
                    tmp = out.name
                    out.write(data)
                os.replace(tmp, path)
                tmp = None
                _apply_file(path)
            except Exception as exc:
                ma.log(f"[META] avatar download failed: {type(exc).__name__}: {exc}")
            finally:
                if tmp and os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

        threading.Thread(
            target=_job, daemon=True, name="pymusic-channel-avatar"
        ).start()

    def _playlist_thumb_cache_path(self, thumb_url: str) -> str:
        key = self._cache_key(f"pl:{thumb_url}")
        return os.path.join(self._art_cache_dir(), f"pl_{key}.jpg")

    def _set_playlist_thumb(self, img_widget, thumb_url: str):
        """
        Стабільна заставка для рядків плейлиста/рекомендацій.

        Важливо: тут НЕ ставимо https URL напряму в AsyncImage/Image. На Android/Kivy
        це може дати баг, коли кілька превʼю відмальовуються тільки в останньому
        рядку. Тому спочатку качаємо файл у локальний кеш, а потім ставимо texture
        саме на той widget, який чекає цей thumb.
        """
        src = Playlist._normalize_img_url(str(thumb_url or "")).strip()
        src = src.replace("https://img.youtube.com/", "https://i.ytimg.com/")

        try:
            if hasattr(img_widget, "clear_image"):
                img_widget.clear_image()
            else:
                img_widget.source = ""
                img_widget.texture = None
        except Exception:
            pass

        if not src.startswith(("http://", "https://")):
            return

        path = self._playlist_thumb_cache_path(src)

        try:
            ma.log(f"[PLAYLIST] thumb row request src={src} path={path}")
        except Exception:
            pass

        try:
            setattr(img_widget, "_pymusic_expected_thumb", src)
            setattr(img_widget, "_pymusic_expected_thumb_path", path)
        except Exception:
            pass

        def _apply_file_to_widget(widget):
            try:
                if getattr(widget, "_pymusic_expected_thumb", None) != src:
                    return
                if getattr(widget, "_pymusic_expected_thumb_path", None) != path:
                    return
                if not (os.path.exists(path) and os.path.getsize(path) > 0):
                    return

                # Прибираємо старий cache по обох ключах. Далі ставимо texture напряму,
                # без remote loader і без повторного AsyncImage-завантаження.
                try:
                    Cache.remove("kv.image", path)
                    Cache.remove("kv.texture", path)
                    if getattr(widget, "source", None):
                        Cache.remove("kv.image", widget.source)
                        Cache.remove("kv.texture", widget.source)
                except Exception:
                    pass

                # Main path: custom ThumbImage owns its canvas texture, so rows cannot
                # accidentally reuse the last AsyncImage/Image loader result.
                if hasattr(widget, "set_local_file"):
                    try:
                        widget.source = path
                        widget.set_local_file(path)
                    except Exception:
                        pass
                else:
                    # Fallback for normal Kivy Image. Set a local file source and reload
                    # on the next frame; do not ever assign a remote https URL here.
                    try:
                        widget.source = path
                        if hasattr(widget, "reload"):
                            widget.reload()
                    except Exception:
                        pass
                    try:
                        texture = CoreImage(path, nocache=True).texture
                        widget.texture = texture
                    except TypeError:
                        try:
                            widget.texture = CoreImage(path).texture
                        except Exception:
                            pass
                    except Exception:
                        pass

                try:
                    widget.canvas.ask_update()
                except Exception:
                    pass
                try:
                    ma.log(f"[PLAYLIST] thumb applied path={path} widget={id(widget)}")
                except Exception:
                    pass
                # Some Kivy builds paint after layout stabilizes. Re-apply once more.
                try:
                    # The widget stored in ids/MDList can be a Kivy weak proxy.
                    # If the row is recycled/removed before this delayed callback runs,
                    # touching that proxy raises ReferenceError and kills the app.
                    def _safe_reapply_thumb(_dt, w=widget, p=path):
                        try:
                            if getattr(w, "_pymusic_expected_thumb_path", None) != p:
                                return
                            setter = getattr(w, "set_local_file", None)
                            if setter:
                                setter(p)
                        except ReferenceError:
                            return
                        except Exception:
                            return

                    Clock.schedule_once(_safe_reapply_thumb, 0.08)
                except Exception:
                    pass
            except ReferenceError:
                # The list can recycle its Kivy weak proxies before a delayed
                # thumbnail callback runs. This is normal and needs no retry.
                return
            except Exception as e:
                try:
                    ma.log(f"[PLAYLIST] apply thumb fail: path={path} err={e}")
                except Exception:
                    pass

        def _apply_all(_dt=None):
            try:
                waiters = self._playlist_thumb_waiters.pop(path, [])
            except Exception:
                waiters = []
            if img_widget not in waiters:
                waiters.append(img_widget)
            # Захист від дублювання одного й того самого widget у waiters.
            seen_widgets = set()
            for widget in list(waiters):
                try:
                    wid_key = id(widget)
                    if wid_key in seen_widgets:
                        continue
                    seen_widgets.add(wid_key)
                    _apply_file_to_widget(widget)
                except Exception:
                    pass

        def _retry_apply_to_this_widget(_dt=None):
            # Extra safety for Windows/Kivy: even if a waiter is lost due to a
            # fast re-render, this exact row checks the cache again later.
            try:
                _apply_file_to_widget(img_widget)
            except Exception:
                pass

        for _delay in (0.05, 0.25, 0.75, 1.5, 3.0):
            try:
                Clock.schedule_once(_retry_apply_to_this_widget, _delay)
            except Exception:
                pass

        def _local_file_valid() -> bool:
            try:
                if not (os.path.exists(path) and os.path.getsize(path) > 0):
                    return False
                try:
                    CoreImage(path, nocache=True)
                except TypeError:
                    CoreImage(path)
                return True
            except Exception:
                try:
                    os.remove(path)
                except Exception:
                    pass
                return False

        try:
            self._playlist_thumb_waiters.setdefault(path, []).append(img_widget)
        except Exception:
            pass

        if _local_file_valid():
            Clock.schedule_once(_apply_all, 0)
            return

        try:
            if path in self._playlist_thumb_inflight:
                return
            self._playlist_thumb_inflight.add(path)
        except Exception:
            pass

        def _download_to_cache(url: str) -> bool:
            tmp = f"{path}.tmp"
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
                    "Referer": "https://www.youtube.com",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "image/jpeg,image/png,image/*;q=0.9,*/*;q=0.8",
                    "Connection": "keep-alive",
                },
            )
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=12, context=ctx) as resp, open(tmp, "wb") as f:
                f.write(resp.read())

            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                # Перевіряємо, що файл реально відкривається Kivy, щоб не кешувати HTML/помилку.
                try:
                    try:
                        CoreImage(tmp, nocache=True)
                    except TypeError:
                        CoreImage(tmp)
                except Exception:
                    try:
                        os.remove(tmp)
                    except Exception:
                        pass
                    return False
                os.replace(tmp, path)
                return True

            try:
                os.remove(tmp)
            except Exception:
                pass
            return False

        def _job():
            ok = False
            try:
                with self._playlist_thumb_sem:
                    candidates = [src]
                    if "/vi/" in src or "/vi_webp/" in src:
                        for name in ("mqdefault.jpg", "hqdefault.jpg", "default.jpg"):
                            alt = re.sub(r"/(maxresdefault|sddefault|hqdefault|mqdefault|default)\.jpg(?:\?.*)?$", f"/{name}", src)
                            if alt not in candidates:
                                candidates.append(alt)
                    if "i.ytimg.com" in src:
                        alt_host = src.replace("i.ytimg.com", "img.youtube.com")
                        if alt_host not in candidates:
                            candidates.append(alt_host)

                    for candidate in candidates:
                        if _download_to_cache(candidate):
                            ok = True
                            break

                if ok:
                    Clock.schedule_once(_apply_all, 0)
                else:
                    try:
                        self._playlist_thumb_waiters.pop(path, None)
                    except Exception:
                        pass
                    try:
                        ma.log(f"[PLAYLIST] thumb not downloaded: {src}")
                    except Exception:
                        pass
            except Exception as e:
                try:
                    self._playlist_thumb_waiters.pop(path, None)
                except Exception:
                    pass
                try:
                    ma.log(f"[PLAYLIST] thumb download fail: url={src} err={e}")
                except Exception:
                    pass
            finally:
                try:
                    self._playlist_thumb_inflight.discard(path)
                except Exception:
                    pass

        threading.Thread(target=_job, daemon=True).start()


    def toggle_playlist_collapsed(self):
        self._playlist_collapsed = not bool(getattr(self, "_playlist_collapsed", False))
        self._render_playlist_ui(force=True)

    def toggle_similar_collapsed(self):
        self._similar_collapsed = not bool(getattr(self, "_similar_collapsed", False))
        if not self._similar_collapsed and not self._related_items:
            self._ensure_related_current_v4(force=False, user_requested=True)
        self._render_similar_ui(force=True)

    def _set_collapsible_header(self, row, label, button, visible: bool, title: str, collapsed: bool):
        try:
            if row is not None:
                row.height = dp(38) if visible else 0
                row.opacity = 1 if visible else 0
                row.disabled = not bool(visible)
        except Exception:
            pass
        try:
            if label is not None:
                label.text = title or ""
        except Exception:
            pass
        try:
            if button is not None:
                button.icon = "chevron-down" if collapsed else "chevron-up"
                button.opacity = 1 if visible else 0
                button.disabled = not bool(visible)
        except Exception:
            pass

    def _make_playlist_row(self, idx: int, item: dict):
        title = (item.get("title") or "").strip()
        channel = (item.get("channel") or "").strip()
        if not title:
            title = item.get("url") or "Track"
        row = Factory.PlaylistTrackItem()
        row.ids.pt_title.text = f"{idx + 1}. {title}"
        row.ids.pt_channel.text = channel
        thumb = str(item.get("thumb") or "")
        vid = Playlist._normalize_video_id(str(item.get("url") or ""))
        if not vid:
            vid = Playlist._normalize_video_id(str(item.get("video_id") or ""))
        if not vid:
            vid = Playlist._video_id_from_thumb_url(thumb)
        if vid:
            thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
        row.ids.pt_duration.text = str(item.get("duration") or "")
        row.bind(on_release=lambda _inst, i=idx: self._play_from_playlist_index(i))
        if thumb:
            try:
                row.ids.pt_thumb.source = ""
                row.ids.pt_thumb.texture = None
            except Exception:
                pass
            self._set_playlist_thumb(row.ids.pt_thumb, thumb)
        return row

    def _make_similar_row(self, idx: int, item: dict):
        title = (item.get("title") or "").strip()
        channel = (item.get("channel") or "").strip()
        if not title:
            title = item.get("url") or "Video"
        row = Factory.SimilarItem()
        row.ids.similar_title.text = title
        row.ids.similar_channel.text = channel

        # YouTube watch-next sends native badge text (14:36), a channel
        # field and the optional views/release-age metadata. Preserve those
        # through normalization; do not guess missing values.
        row.ids.similar_duration.text = _related_duration_label(
            item.get("duration") or item.get("duration_string")
        )
        views = str(item.get("views_text") or "").strip()
        published = str(item.get("published_text") or "").strip()
        row.ids.similar_meta.text = " • ".join(
            part for part in (views, published) if part
        )

        thumb = item.get("thumb") or ""
        vid = Playlist._normalize_video_id(str(item.get("url") or ""))
        if not vid:
            vid = Playlist._normalize_video_id(str(item.get("video_id") or ""))
        if not vid:
            vid = Playlist._video_id_from_thumb_url(str(thumb or ""))
        if vid:
            thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
        if thumb:
            self._set_playlist_thumb(row.ids.similar_thumb, thumb)
        row.bind(on_release=lambda _inst, i=idx: self._play_from_related_index(i))
        return row

    def _ensure_media_session(self):
        try:
            if not self._media_session:
                self._media_session = ma.MediaSession(
                    ma.PythonActivity.mActivity,
                    "PyMusicSession",
                )
                try:
                    flags = (
                        ma.MediaSession.FLAG_HANDLES_MEDIA_BUTTONS
                        | ma.MediaSession.FLAG_HANDLES_TRANSPORT_CONTROLS
                    )
                    self._media_session.setFlags(flags)
                except Exception:
                    pass
            self._media_session.setActive(True)
            ma.register_media_session(self._media_session)
            ma.configure_session_audio()
            ma.set_media_session_callback(self)
            ma.bind_notification_action_router(self)
        except Exception:
            pass

    def _render_playlist_ui(self, force: bool = False):
        """Bounded nested playlist scroll from the user-verified working APK.

        The playlist scrolls *inside* its own 240dp viewport, independently
        of player_details_scroll. Preserve row instances for the same queue:
        playback/metadata callbacks may call force=True while a finger drags.
        """
        try:
            listing = self.ids.get("playlist_list")
            viewport = self.ids.get("playlist_scroll")
            if listing is None or viewport is None:
                print("[PLAYLIST-APK] missing nested playlist widgets")
                return

            tracks = list(self.playlist.tracks if self.playlist else [])
            signature = tuple(
                (
                    str(item.get("url") or ""),
                    str(item.get("video_id") or ""),
                    str(item.get("title") or ""),
                    str(item.get("thumb") or ""),
                    str(item.get("duration") or ""),
                )
                for item in tracks
            )
            old_signature = getattr(self, "_apk_playlist_signature", None)
            changed = (
                signature != old_signature
                or len(listing.children) != len(tracks)
            )

            if changed:
                # Only a genuine queue change may invalidate existing rows.
                listing.clear_widgets()
                for index, track in enumerate(tracks):
                    listing.add_widget(self._make_playlist_row(index, track))
                self._apk_playlist_signature = signature
                self._playlist_ui_sig = signature
                print(f"[PLAYLIST-APK] rows={len(listing.children)}/{len(tracks)}")

            collapsed = bool(getattr(self, "_playlist_collapsed", False))
            self._set_collapsible_header(
                self.ids.get("playlist_header_row"),
                self.ids.get("playlist_header"),
                self.ids.get("playlist_toggle_btn"),
                bool(tracks),
                (self.playlist.name or "Черга") if self.playlist else "Черга",
                collapsed,
            )

            expanded = bool(tracks and not collapsed)
            listing.size_hint_y = None
            # MDList.minimum_height owns the complete scrollable content;
            # the viewport remains bounded, as in the working APK.
            listing.height = max(0, float(listing.minimum_height or 0))
            viewport.size_hint_y = None
            viewport.height = dp(240) if expanded else 0
            viewport.opacity = 1 if expanded else 0
            # Do not toggle disabled on the viewport: it propagates to all row
            # ButtonBehavior children and may break/kill their touch tracking.
            viewport.disabled = False
            viewport.do_scroll_x = False
            viewport.do_scroll_y = bool(expanded)
            # Keep all row touch targets alive even when the view is collapsed.
            # A zero-height viewport cannot receive touches.
            listing.disabled = not expanded

            if not getattr(self, "_apk_playlist_bound", False):
                self._apk_playlist_bound = True

                def sync_list_height(_instance, height):
                    listing.height = max(0, float(height or 0))

                listing.bind(minimum_height=sync_list_height)

                def log_scroll_start(widget, touch):
                    print(
                        "[PLAYLIST-APK] drag start "
                        f"y={float(widget.scroll_y):.3f} "
                        f"extent={max(0.0, float(listing.height)-float(widget.height)):.1f}"
                    )

                def log_scroll_stop(widget, touch):
                    print(f"[PLAYLIST-APK] drag stop y={float(widget.scroll_y):.3f}")

                viewport.bind(
                    on_scroll_start=log_scroll_start,
                    on_scroll_stop=log_scroll_stop,
                )
                print("[PLAYLIST-APK] working-APK bounded nested scroll active")

            def report(_dt):
                try:
                    print(
                        "[PLAYLIST-APK] geometry "
                        f"rows={len(listing.children)}/{len(tracks)} "
                        f"list_h={float(listing.height):.1f} "
                        f"viewport_h={float(viewport.height):.1f} "
                        f"range={max(0.0, float(listing.height)-float(viewport.height)):.1f} "
                        f"scroll_y={float(viewport.scroll_y):.3f}"
                    )
                except Exception:
                    pass
            if changed:
                Clock.schedule_once(report, 0.12)
        except Exception as exc:
            try:
                ma.log(f"[PLAYLIST-APK] renderer failed: {exc}")
            except Exception:
                print("[PLAYLIST-APK] renderer failed:", exc)

    def _current_ensure_state(self):
        if not hasattr(self, "_current_video_url_v4"):
            self._current_video_url_v4 = ""
        if not hasattr(self, "_current_related_inflight_v4"):
            self._current_related_inflight_v4 = set()
        if not hasattr(self, "_current_related_url_v4"):
            self._current_related_url_v4 = ""
        if not hasattr(self, "_current_auto_next_pending_v4"):
            self._current_auto_next_pending_v4 = False
        if not hasattr(self, "_current_comments_v4"):
            self._current_comments_v4 = []
        if not hasattr(self, "_current_comment_count_v4"):
            self._current_comment_count_v4 = None
        if not hasattr(self, "_current_comments_url_v4"):
            self._current_comments_url_v4 = ""
        if not hasattr(self, "_current_comments_loading_v4"):
            self._current_comments_loading_v4 = False
        if not hasattr(self, "_current_comments_popup_v4"):
            self._current_comments_popup_v4 = None
        if not hasattr(self, "_current_comments_rows_v4"):
            self._current_comments_rows_v4 = None
        if not hasattr(self, "_current_comments_sheet_count_v4"):
            self._current_comments_sheet_count_v4 = None

    def _current_get_slot(self):
        """Use a *bounded* independent ScrollView exclusively for related videos."""
        scroll = self.ids.get("similar_scroll")
        listing = self.ids.get("similar_list")
        if scroll is None or listing is None:
            return None, None
        try:
            scroll.size_hint_y = None
            scroll.do_scroll_x = False
            scroll.do_scroll_y = True
            scroll.bar_width = dp(3)
            scroll.scroll_distance = dp(5)
            scroll.scroll_timeout = 180
            scroll.scroll_type = ["bars", "content"]
            try:
                scroll.always_overscroll = False
            except Exception:
                pass
            listing.size_hint_y = None
            listing.padding = (dp(6), dp(6))
            listing.spacing = dp(4)
        except Exception as exc:
            print("[SIMILAR] scroll config error:", exc)
        return scroll, listing

    def _sync_current_lower_controls(self, has_video: bool):
        """Update lightweight buttons without recreating the related rows."""
        try:
            trigger = self.ids.get("comments_trigger")
            if trigger is not None:
                trigger.height = dp(60) if has_video else 0
                trigger.opacity = 1 if has_video else 0
                trigger.disabled = not has_video
            description = self.ids.get("comments_preview_meta")
            if description is not None:
                count = getattr(self, "_current_comment_count_v4", None)
                if count is not None:
                    description.text = f"{int(count):,} коментарів".replace(",", " ")
                else:
                    description.text = "Натисніть, щоб переглянути"
            autoplay_button = self.ids.get("autoplay_btn")
            if autoplay_button is not None:
                enabled = bool(getattr(self, "_auto_skip", True))
                autoplay_button.source = (
                    "ico/icoauto_on.png" if enabled else "ico/icoauto_off.png"
                )
                autoplay_button.disabled = not has_video
            collapsed = bool(getattr(self, "_similar_collapsed", False))
            self._set_collapsible_header(
                self.ids.get("similar_header_row"),
                self.ids.get("similar_header"),
                self.ids.get("similar_toggle_btn"),
                has_video,
                "Схожі відео",
                collapsed,
            )
        except Exception as exc:
            print("[CURRENT-V4] control refresh failed:", exc)

    def _current_apply_height(self, scroll, listing):
        """Keep only recommendations in a 280dp nested swipe viewport."""
        try:
            children = list(getattr(listing, "children", []) or [])
            content_h = sum(
                max(0.0, float(getattr(child, "height", 0) or 0))
                for child in children
            )
            spacing = getattr(listing, "spacing", 0) or 0
            if isinstance(spacing, (tuple, list)):
                spacing = spacing[1] if len(spacing) > 1 else (spacing[0] if spacing else 0)
            content_h += max(0, len(children) - 1) * float(spacing or 0)
            content_h += dp(16)
            content_h = max(content_h, float(getattr(listing, "minimum_height", 0) or 0))
            listing.height = content_h
            has_video = bool(_current_video_url_of(self))
            collapsed = bool(getattr(self, "_similar_collapsed", False))
            expanded = has_video and not collapsed
            viewport_h = min(dp(280), max(dp(70), content_h)) if expanded else 0
            scroll._current_v4_height = viewport_h
            scroll.height = viewport_h
            scroll.opacity = 1 if expanded else 0
            scroll.disabled = not expanded
            scroll.do_scroll_y = bool(expanded)
            self._sync_current_lower_controls(has_video)
        except Exception as exc:
            print("[CURRENT-V4] panel height failed:", exc)

    def _render_current_lower_v4(self, *_args, **_kwargs):
        # Network/MediaPlayer callbacks also arrive on non-Kivy threads.
        if threading.current_thread() is not threading.main_thread():
            Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), 0)
            return
        self._current_ensure_state()
        scroll, listing = self._current_get_slot()
        if scroll is None or listing is None:
            return
        url = _current_video_url_of(self)
        has_video = bool(url)
        self._sync_current_lower_controls(has_video)
        if not has_video:
            listing.clear_widgets()
            scroll._current_v4_height = 0
            scroll.height = 0
            scroll.opacity = 0
            scroll.disabled = True
            self._current_v4_render_signature = None
            return

        related = list(getattr(self, "_related_items", []) or [])[:8]
        signature = (
            url,
            tuple(
                (str(item.get("url") or ""), str(item.get("video_id") or ""),
                 str(item.get("title") or ""), str(item.get("thumb") or ""),
                 str(item.get("channel") or ""), str(item.get("duration") or ""),
                 str(item.get("views_text") or ""),
                 str(item.get("published_text") or ""))
                for item in related
            ),
        )
        if (getattr(self, "_current_v4_render_signature", None) == signature
                and bool(listing.children)):
            self._current_apply_height(scroll, listing)
            return

        previous_signature = getattr(self, "_current_v4_render_signature", None)
        self._current_v4_render_signature = signature
        listing.clear_widgets()
        if related:
            for index, item in enumerate(related):
                try:
                    listing.add_widget(self._make_similar_row(index, item))
                except Exception as exc:
                    print("[SIMILAR] row fallback:", exc)
                    title = str(item.get("title") or "Відео")
                    row = _CurrentTapLabel(
                        text=title, size_hint_y=None, height=dp(64),
                        font_size="15sp", halign="left", valign="middle",
                        color=(0.12, 0.12, 0.12, 1),
                    )
                    row.bind(
                        width=lambda instance, width: setattr(
                            instance, "text_size", (max(dp(1), width - dp(24)), None)
                        )
                    )
                    row.bind(on_release=lambda _instance, i=index: self._play_from_related_index(i))
                    listing.add_widget(row)
        else:
            listing.add_widget(_current_make_label(
                "Завантаження рекомендацій…", height=54,
                font_size="13sp", color=(0.48, 0.48, 0.48, 1),
            ))

        # Reset only after the actual current-video/related list changed.
        if previous_signature != signature:
            scroll.scroll_y = 1.0
        self._current_apply_height(scroll, listing)
        for delay in (0.04, 0.14):
            Clock.schedule_once(
                lambda _dt, s=scroll, l=listing: self._current_apply_height(s, l),
                delay,
            )
        print(f"[CURRENT-V4] related list rendered url={url!r} count={len(related)}")

    def _current_play_related_item(self, item) -> bool:
        if not isinstance(item, dict):
            return False
        url = _current_canonical_video_url(item.get("url") or item.get("video_id") or "")
        if not url:
            return False
        self._current_auto_next_pending_v4 = False
        print(f"[CURRENT-V4] auto-next related url={url!r}")
        self.play_audio(
            url,
            str(item.get("title") or ""),
            str(item.get("channel") or ""),
            str(item.get("thumb") or item.get("thumbnail") or ""),
            clear_playlist=True,
            hard_reset=True,
        )
        return True

    def _current_fallback_search(self, url: str, title: str, channel: str):
        output = []
        try:
            from youtube_search import fetch_youtube_results
            query = " ".join(part for part in (title.strip(), channel.strip()) if part).strip()
            if not query:
                return []
            videos, _playlists, _cont, _cfg = fetch_youtube_results(query)
            for entry in videos or []:
                if not isinstance(entry, (tuple, list)) or not entry:
                    continue
                output.append(
                    {
                        "url": str(entry[0] or ""),
                        "title": str(entry[1] or "") if len(entry) > 1 else "",
                        "channel": str(entry[2] or "") if len(entry) > 2 else "",
                        "thumbnail": str(entry[3] or "") if len(entry) > 3 else "",
                        "duration": str(entry[4] or "") if len(entry) > 4 else "",
                    }
                )
                if len(output) >= 8:
                    break
        except Exception as exc:
            print("[CURRENT-V4] search fallback failed:", exc)
        return output

    def _ensure_related_current_v4(self, force=False, auto_advance=False, user_requested=False):
        self._current_ensure_state()
        # Skip nonessential related extraction until audio has *actually*
        # started. On metered links, only auto-next or an explicit expansion
        # of the section may fetch these bandwidth-heavy watch-next pages.
        if not auto_advance and not user_requested:
            if ma.is_metered_connection() or getattr(
                self, "_audio_ready_for_extras_gen", -1
            ) != int(getattr(self, "_load_gen", -1)):
                return False
        url = _current_video_url_of(self)
        if not url:
            print("[CURRENT-V4] related skipped: no current video URL")
            return False
        if auto_advance:
            self._current_auto_next_pending_v4 = True
        if getattr(self, "_related_items", None) and getattr(self, "_current_related_url_v4", "") == url and not force:
            if self._current_auto_next_pending_v4:
                return self._current_play_related_item( self._related_items[0])
            Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), 0)
            return True
        if url in self._current_related_inflight_v4:
            return True
        self._current_related_inflight_v4.add(url)
        title = str(getattr(self, "_title", "") or "")
        channel = str(getattr(self, "_channel", "") or "")
        print(f"[CURRENT-V4] related fetch url={url!r} title={title!r} channel={channel!r}")

        def job():
            items = []
            try:
                from related_videos import fetch_related_videos
                items = fetch_related_videos(url, title, channel, limit=8) or []
            except Exception as exc:
                print("[CURRENT-V4] related fetch failed:", exc)
            if not items:
                items = self._current_fallback_search( url, title, channel)
            try:
                normalized = self._normalize_related(items or [])
            except Exception:
                normalized = []
            self._current_related_inflight_v4.discard(url)
            if _current_video_url_of(self) != url:
                return
            self._related_items = normalized[:8]
            self._current_related_url_v4 = url
            print(f"[CURRENT-V4] related applied count={len(self._related_items)} url={url!r}")

            pending = bool(getattr(self, "_current_auto_next_pending_v4", False))
            if pending and self._related_items:
                if bool(getattr(self, "_app_in_background", False)):
                    self._current_play_related_item( self._related_items[0])
                else:
                    Clock.schedule_once(lambda _dt: self._current_play_related_item( self._related_items[0]), 0)
                return
            if pending and not self._related_items:
                self._current_auto_next_pending_v4 = False
                self._playback_desired = False
                self._user_paused = True
                Clock.schedule_once(lambda _dt: self._ui_set_playing(False), 0)
            Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), 0)

        threading.Thread(target=job, name="pymusic-current-related-v4", daemon=True).start()
        return True

    def open_comments_sheet(self):
        """Mobile-style bottom sheet: comments are requested on tap only."""
        if threading.current_thread() is not threading.main_thread():
            Clock.schedule_once(lambda _dt: self.open_comments_sheet(), 0)
            return
        self._current_ensure_state()
        url = _current_video_url_of(self)
        if not url:
            return
        existing = self._current_comments_popup_v4
        if existing is not None:
            return

        # Full-screen transparent modal with a *bottom-aligned* child:
        # ModalView itself is always centered by Kivy, so changing its
        # pos_hint would not actually create a YouTube-style bottom sheet.
        sheet = ModalView(
            size_hint=(1, 1),
            auto_dismiss=False,
            background="",
            background_color=(0, 0, 0, 0),
            overlay_color=(0, 0, 0, 0.55),
        )
        backdrop = FloatLayout()
        panel = BoxLayout(
            orientation="vertical",
            size_hint=(1, None),
            height=min(float(Window.height) * 0.82, dp(740)),
            pos_hint={"x": 0, "y": 0},
            padding=(dp(12), dp(10), dp(12), dp(14)),
            spacing=dp(8),
        )
        with panel.canvas.before:
            Color(1, 1, 1, 1)
            panel_bg = RoundedRectangle(
                pos=panel.pos,
                size=panel.size,
                radius=[dp(16), dp(16), 0, 0],
            )
        def resize_panel(widget, *_args):
            panel_bg.pos = widget.pos
            panel_bg.size = widget.size
        panel.bind(pos=resize_panel, size=resize_panel)

        header = BoxLayout(
            orientation="horizontal",
            size_hint_y=None,
            height=dp(48),
            spacing=dp(6),
        )
        title = Label(
            text="Коментарі",
            color=(0.08, 0.08, 0.08, 1),
            bold=True, font_size="19sp",
            halign="left", valign="middle",
        )
        title.bind(width=lambda widget, value: setattr(
            widget, "text_size", (value, None)
        ))
        count_label = Label(
            text="",
            color=(0.45, 0.45, 0.45, 1),
            font_size="12sp",
            size_hint_x=None, width=dp(76),
        )
        close = Button(
            text="×", font_size="24sp",
            size_hint=(None, None), size=(dp(40), dp(40)),
            background_normal="",
            background_color=(0.94, 0.94, 0.94, 1),
            color=(0.12, 0.12, 0.12, 1),
        )
        close.bind(on_release=lambda *_a: sheet.dismiss())
        header.add_widget(title)
        header.add_widget(count_label)
        header.add_widget(close)
        panel.add_widget(header)

        scroller = ScrollView(
            do_scroll_x=False, do_scroll_y=True,
            scroll_type=["content", "bars"],
            scroll_distance=dp(6), bar_width=dp(3),
        )
        rows = BoxLayout(
            orientation="vertical",
            size_hint_y=None,
            spacing=dp(12),
            padding=(dp(4), dp(6), dp(4), dp(18)),
        )
        rows.bind(minimum_height=rows.setter("height"))
        scroller.add_widget(rows)
        panel.add_widget(scroller)
        backdrop.add_widget(panel)
        sheet.add_widget(backdrop)

        def dismiss_outside(_widget, touch):
            if not panel.collide_point(*touch.pos):
                sheet.dismiss()
                return True
            return False
        backdrop.bind(on_touch_down=dismiss_outside)

        self._current_comments_popup_v4 = sheet
        self._current_comments_rows_v4 = rows
        self._current_comments_sheet_count_v4 = count_label

        def dismissed(_sheet):
            if self._current_comments_popup_v4 is sheet:
                self._current_comments_popup_v4 = None
                self._current_comments_rows_v4 = None
                self._current_comments_sheet_count_v4 = None
        sheet.bind(on_dismiss=dismissed)
        sheet.open()
        self._current_ensure_comments()
        self._refresh_comments_sheet_v4()

    def _refresh_comments_sheet_v4(self):
        """Only touch Kivy widgets from the UI thread."""
        if threading.current_thread() is not threading.main_thread():
            Clock.schedule_once(lambda _dt: self._refresh_comments_sheet_v4(), 0)
            return
        sheet = getattr(self, "_current_comments_popup_v4", None)
        rows = getattr(self, "_current_comments_rows_v4", None)
        if sheet is None or rows is None:
            return
        count = getattr(self, "_current_comment_count_v4", None)
        count_label = getattr(self, "_current_comments_sheet_count_v4", None)
        if count_label is not None:
            count_label.text = (
                f"{int(count):,}".replace(",", " ") if count is not None else ""
            )
        rows.clear_widgets()

        comments = list(getattr(self, "_current_comments_v4", []) or [])
        if not comments:
            message = (
                "Завантаження коментарів…"
                if bool(getattr(self, "_current_comments_loading_v4", False))
                else "Коментарі недоступні для цього відео"
            )
            rows.add_widget(_current_make_label(
                message, height=68, font_size="14sp",
                color=(0.45, 0.45, 0.45, 1),
            ))
            return

        for item in comments:
            row = BoxLayout(
                orientation="vertical",
                size_hint_y=None,
                spacing=dp(4),
                padding=(dp(8), dp(5), dp(8), dp(8)),
            )
            row.bind(minimum_height=row.setter("height"))
            author = _current_make_label(
                str(item.get("author") or "Користувач YouTube"),
                height=24, font_size="13sp", bold=True,
            )
            comment_text = _current_make_label(
                str(item.get("text") or "")[:1500],
                height=32, font_size="14sp",
                color=(0.16, 0.16, 0.16, 1),
            )
            comment_text.valign = "top"
            comment_text.bind(texture_size=lambda widget, texture: setattr(
                widget, "height", max(dp(28), float(texture[1]) + dp(8))
            ))
            row.add_widget(author)
            row.add_widget(comment_text)
            rows.add_widget(row)

    def _current_ensure_comments(self):
        self._current_ensure_state()
        # No background comment extraction on every play/metadata refresh.
        # A user must explicitly open the comments sheet first.
        if self._current_comments_popup_v4 is None:
            return
        if bool(getattr(self, "_app_in_background", False)):
            return
        url = _current_video_url_of(self)
        if not url:
            return
        if self._current_comments_url_v4 == url and (
            self._current_comments_v4 or self._current_comments_loading_v4
        ):
            return
        self._current_comments_url_v4 = url
        self._current_comments_v4 = []
        self._current_comment_count_v4 = None
        self._current_comments_loading_v4 = True
        Clock.schedule_once(lambda _dt: self._refresh_comments_sheet_v4(), 0)

        def job():
            comments, count = [], None
            try:
                # Optional comments must not compete with audio startup or
                # launch multiple yt-dlp comment extractors simultaneously.
                # Opening the sheet is an explicit user request. Do not
                # impose the old 2.5-second delay; still yield briefly if
                # native playback is preparing to avoid resource contention.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if _current_video_url_of(self) != url:
                        return
                    if not ma.is_network_available():
                        return
                    player = getattr(ma, "android_player", None)
                    try:
                        if player is not None and ma._is_prepared() and player.isPlaying():
                            break
                    except Exception:
                        pass
                    time.sleep(0.1)
                if not _current_comments_worker_lock.acquire(blocking=False):
                    return
                try:
                    if _current_video_url_of(self) == url:
                        comments, count = _current_fetch_comments(url, limit=24)
                finally:
                    _current_comments_worker_lock.release()
            except Exception as exc:
                print("[CURRENT-V4] deferred comments failed:", exc)
            finally:
                if _current_video_url_of(self) == url:
                    self._current_comments_v4 = comments
                    self._current_comment_count_v4 = count
                    self._current_comments_loading_v4 = False
                    Clock.schedule_once(lambda _dt: self._sync_current_lower_controls(True), 0)
                    Clock.schedule_once(lambda _dt: self._refresh_comments_sheet_v4(), 0)

        threading.Thread(target=job, name="pymusic-comments-v4", daemon=True).start()

    def _render_similar_ui_base(self, force: bool = False):
        try:
            header = self.ids.get("similar_header")
            header_row = self.ids.get("similar_header_row")
            toggle_btn = self.ids.get("similar_toggle_btn")
            scroll = self.ids.get("similar_scroll")
            lst = self.ids.get("similar_list")
            if not lst:
                return
            self._similar_render_gen += 1
            render_gen = int(self._similar_render_gen)
            lst.clear_widgets()
            if not self._related_items:
                self._set_collapsible_header(header_row, header, toggle_btn, False, "Схожі відео", False)
                if scroll:
                    scroll.height = 0
                    scroll.opacity = 0
                    scroll.disabled = True
                return
            collapsed = bool(getattr(self, "_similar_collapsed", False))
            self._set_collapsible_header(header_row, header, toggle_btn, True, "Схожі відео", collapsed)
            if collapsed:
                if scroll:
                    scroll.height = 0
                    scroll.opacity = 0
                    scroll.disabled = True
                return
            if scroll:
                scroll.height = dp(220)
                scroll.opacity = 1
                scroll.disabled = False

            items = list(self._related_items)
            chunk_size = 6

            def _add_chunk(start_idx: int):
                try:
                    if render_gen != int(getattr(self, "_similar_render_gen", -1)):
                        return
                    end_idx = min(len(items), start_idx + chunk_size)
                    for idx in range(start_idx, end_idx):
                        row = self._make_similar_row(idx, items[idx])
                        lst.add_widget(row)
                    if end_idx < len(items):
                        Clock.schedule_once(lambda dt, n=end_idx: _add_chunk(n), 0)
                except Exception as e:
                    try:
                        ma.log(f"[SIMILAR] chunk render err: {e}")
                    except Exception:
                        pass

            Clock.schedule_once(lambda dt: _add_chunk(0), 0)
        except Exception as e:
            try:
                ma.log(f"[SIMILAR] render err: {e}")
            except Exception:
                pass

    def _render_similar_ui(self, force: bool = False):
        return self._render_current_lower_v4()


    def _ensure_metadata_async(self, video_url: str):
        _player_likes_ensure_likes_async(self, video_url)
        if not video_url:
            return
        if video_url in self._metadata_loaded_urls and _verified_channel_name(self._channel, self._title):
            return
        if self._meta_inflight and self._meta_last_url == video_url:
            return
        self._meta_inflight = True
        self._meta_last_url = video_url

        def _job():
            try:
                # A cached track may already have the title/thumb but no
                # channel. Avoid a second full yt-dlp extraction in that case.
                if video_url in self._metadata_loaded_urls:
                    info = {}
                else:
                    info = ydlh.extract_audio_info(video_url)
                if not (
                    _verified_channel_name(info.get("channel"), info.get("title") or self._title)
                    or _verified_channel_name(info.get("uploader"), info.get("title") or self._title)
                    or _verified_channel_name(self._channel, self._title)
                ):
                    # Official YouTube oEmbed gives author_name even when the
                    # Android player response omits owner/channel metadata.
                    # This runs only in the metadata worker, never in audio
                    # extraction or the Kivy rendering thread.
                    name = ydlh._channel_name_from_oembed(video_url)
                    if name:
                        info["channel"] = name
                if video_url != self._last_video_url:
                    return
                if info:
                    self._apply_info_metadata(info)
                    self._metadata_loaded_urls.add(video_url)
            except Exception as exc:
                print("[META] async channel lookup failed:", exc)
            finally:
                self._meta_inflight = False

        threading.Thread(target=_job, daemon=True).start()

    def _apply_info_metadata_base(self, info: dict):
        updated = False
        title = info.get("title") or ""
        channel = _verified_channel_name(
            info.get("channel") or info.get("uploader"), title or self._title
        )
        try:
            ma.log(f"[META] info title={title!r} channel={channel!r} cthumb={(info.get('channel_thumb') or '')!r}")
        except Exception:
            pass
        if title and (not self._title or self._title.lower() == "playing"):
            self._title = title
            updated = True
        if channel and not _verified_channel_name(self._channel, self._title):
            self._channel = channel
            updated = True
        cthumb = info.get("channel_thumb") or info.get("uploader_thumb") or ""
        if isinstance(cthumb, str) and cthumb.startswith("//"):
            cthumb = f"https:{cthumb}"
        if cthumb:
            self._channel_thumb = cthumb
            self._download_channel_avatar_async(cthumb)
            updated = True

        vthumb = info.get("thumb") or info.get("thumbnail") or ""
        if isinstance(vthumb, str) and vthumb.startswith("//"):
            vthumb = f"https:{vthumb}"
        if vthumb:
            try:
                if not self._thumb or not str(self._thumb).startswith(("http://", "https://", "/")):
                    self._thumb = vthumb
                    self._download_art_async(vthumb)
                    updated = True
            except Exception:
                pass
            try:
                changed_playlist_thumb = False
                for track in self.playlist.tracks:
                    if track.get("url") == self._last_video_url and not track.get("thumb"):
                        track["thumb"] = vthumb
                        changed_playlist_thumb = True
                        break
                if changed_playlist_thumb:
                    Clock.schedule_once(lambda dt: self._render_playlist_ui(force=True), 0)
            except Exception:
                pass

        vc = info.get("view_count")
        if vc is not None:
            new_views = self._fmt_views(vc)
            if self._views_text != new_views:
                self._views_text = new_views
                updated = True
        published = str(info.get("upload_date") or info.get("release_date") or "")
        if published and published != self._published_date:
            self._published_date = published
            updated = True
        related = info.get("related_videos") or []
        if related:
            self._related_items = self._normalize_related(related)
            Clock.schedule_once(lambda dt: self._render_similar_ui(), 0)
        if updated:
            self._publish_media_metadata_now()
            Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)

    def _apply_info_metadata(self, info):
        result = self._apply_info_metadata_base( info)
        self._current_ensure_state()
        info_url = _current_canonical_video_url(
            (info or {}).get("webpage_url")
            or (info or {}).get("original_url")
            or ""
        )
        if info_url and not _current_video_url_of(self):
            self._current_video_url_v4 = info_url
        if not getattr(self, "_related_items", None):
            self._ensure_related_current_v4( force=True)
        Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), 0)
        return result


    def _publish_media_metadata_now(self):
        try:
            is_playing = False
            try:
                is_playing = bool(ma.android_player and ma.android_player.isPlaying())
            except Exception:
                is_playing = bool(self._playback_desired and not self._user_paused)
            pos = 0
            dur = None
            try:
                if ma.android_player and ma._is_prepared():
                    pos = int(ma.android_player.getCurrentPosition() or 0)
                    dur = int(ma.android_player.getDuration() or 0) or None
            except Exception:
                pass
            ma.set_media_metadata(
                title=self._title or "Playing",
                artist=self._channel or "",
                duration_ms=dur,
                art_path=self._art_path,
                art_uri=self._thumb or "",
                avatar_path=self._channel_thumb_local,
            )
            ma.update_media_session_state(
                is_playing,
                position_ms=int(pos or 0),
                duration_ms=dur,
                can_seek=True,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing",
                subtitle=self._channel or "",
                is_playing=is_playing,
                session_token=token,
                large_icon_path=self._channel_thumb_local,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
        except Exception as e:
            try:
                ma.log(f"[META] publish err: {e}")
            except Exception:
                pass

    def _play_from_playlist_index(self, idx: int):
        if not self.playlist or not self.playlist.tracks:
            return
        if idx < 0 or idx >= len(self.playlist.tracks):
            return
        self.playlist.index = idx
        track = self.playlist.current()
        if not track:
            return
        self.play_audio(
            track["url"],
            track.get("title") or "",
            track.get("channel") or "",
            track.get("thumb") or "",
            clear_playlist=False,
            hard_reset=True,
        )

    def _play_from_related_index(self, idx: int):
        if not self._related_items:
            return
        if idx < 0 or idx >= len(self._related_items):
            return
        item = self._related_items[idx]
        self.play_audio(
            item.get("url") or "",
            item.get("title") or "",
            item.get("channel") or "",
            item.get("thumb") or "",
            clear_playlist=True,
        )

    def _art_cache_dir(self) -> str:
        base_dir = None
        try:
            app = App.get_running_app()
            base_dir = getattr(app, "user_data_dir", None)
        except Exception:
            base_dir = None
        if not base_dir:
            base_dir = tempfile.gettempdir()
        cache_dir = os.path.join(base_dir, "pymusic_art_cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass
        return cache_dir

    def _art_cache_path(self, video_url: str) -> str:
        key = self._cache_key(video_url)
        return os.path.join(self._art_cache_dir(), f"{key}.jpg")

    def _find_cached_art(self, video_url: str) -> str | None:
        if not video_url:
            return None
        try:
            recent = load_recent()
            for r in recent:
                if r.get("url") == video_url:
                    p = str(r.get("art_path") or "")
                    if p and os.path.exists(p) and os.path.getsize(p) > 0:
                        return p
        except Exception:
            pass
        try:
            path = self._art_cache_path(video_url)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
        except Exception:
            pass
        return None

    def _is_current_gen(self, gen: int) -> bool:
        return gen == self._load_gen

    def _stop_buffer_watchdog(self):
        try:
            if self._buffer_watchdog_ev:
                self._buffer_watchdog_ev.cancel()
                self._buffer_watchdog_ev = None
        except Exception:
            pass

    def _start_buffer_watchdog(self):
        try:
            self._buffer_watchdog_ts = time.time()
            self._last_watch_pos = None
            self._stop_buffer_watchdog()
            self._buffer_watchdog_ev = Clock.schedule_interval(self._buffer_watchdog, 3.0)
        except Exception:
            pass
# TODO буфер ламає відтворення відео(через repeat) і ламає роботу з плейлистами
    def _buffer_watchdog(self, dt):
        """Wait for a slow network's buffer before re-creating MediaPlayer.

        An 8-second timeout triggered repeat downloads and infinite restarts
        on mobile links. Recovery now uses the existing bounded error-loop
        handler and retains playback position.
        """
        if self._app_in_background or (time.time() - float(self._last_bg_resume_ts or 0.0)) < 6.0:
            return
        if not self._playback_desired or self._user_paused:
            return
        if not ma.android_player or not ma._is_prepared():
            return
        try:
            pos = int(ma.android_player.getCurrentPosition() or 0)
        except Exception:
            return

        now = time.time()
        last_pos = self._last_watch_pos
        self._last_watch_pos = pos
        if last_pos is None or pos != last_pos:
            self._buffer_watchdog_ts = now
            return

        # Mobile/explicitly metered Wi-Fi may require a much longer stall
        # window. A buffering event is normal on a slow CDN, not a crash.
        timeout = 45.0 if ma.is_metered_connection() else 28.0
        if getattr(self, "_buffering_started_at", 0.0):
            timeout += 15.0
        if now - float(self._buffer_watchdog_ts or now) < timeout:
            return
        if not ma.is_network_available():
            # Preserve native player/last-known buffer while disconnected.
            return

        self._buffer_watchdog_ts = now
        self._resume_pos_ms = pos
        print(f"[AUDIO] stalled {timeout:.0f}s; recover at {pos} ms")
        self._recover_stream("buffer_watchdog", pos, force_fresh=True)

    def _pre_start_cleanup(self):
        try:
            if self._update_ev:
                Clock.unschedule(self._tick)
                self._update_ev = None
            if self._refresh_ev:
                self._refresh_ev.cancel()
                self._refresh_ev = None
        except Exception:
            pass
        self._stop_buffer_watchdog()
        try:
            ma._mp_reset_release()
            ma.release_wake_lock()
        except Exception:
            pass

    def _hard_transition_reset(self):
        self._playback_desired = False
        self._user_paused = False
        self._bg_stall_since = 0.0
        self._bad_dur_hits = 0
        self._resume_pos_ms = 0
        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass
        if not self._app_in_background:
            try:
                self._set_video_mode(False)
            except Exception:
                pass
        self._pre_start_cleanup()

    # ==================== fast path start ====================

    def _on_native_audio_info(self, what: int, extra: int, gen: int) -> None:
        # Android MEDIA_INFO_BUFFERING_START/END. Watchdog uses the event
        # to avoid destroying a player that is correctly waiting for bytes.
        if not self._is_current_gen(gen):
            return
        if int(what) == 701:
            self._buffering_started_at = time.monotonic()
            print("[AUDIO] native buffering started")
        elif int(what) == 702:
            self._buffering_started_at = 0.0
            self._buffer_watchdog_ts = time.time()
            print("[AUDIO] native buffering completed")

    def _start_from_known_stream(self, gen: int | None = None):
        gen = self._load_gen if gen is None else gen
        if not self._is_current_gen(gen):
            return
        source = str(getattr(self, "_stream_url", "") or "")
        if source:
            attempts = getattr(self, "_audio_source_attempts", None)
            if isinstance(attempts, set):
                attempts.add(source)

        self._pre_start_cleanup()

        try:
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing audio",
                subtitle=self._channel or "YouTube",
                is_playing=True,
                session_token=token,
                large_icon_path=self._channel_thumb_local,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
        except Exception:
            pass

        try:
            jheaders = ydlh.py_headers_to_javamap(self._headers, ma.HashMap) if self._headers else None
        except Exception:
            jheaders = None

        def _on_prepared(mp):
            if not self._is_current_gen(gen) or not self._playback_desired:
                return
            t0 = float(getattr(self, "_audio_play_request_t0", 0) or 0)
            if t0:
                print(f"[LATENCY] native prepared after {time.monotonic() - t0:.2f}s")
            # не стартуємо відразу, лише базові метадані
            try:
                ma.set_media_metadata(
                    title=self._title,
                    artist=self._channel,
                    art_path=self._art_path,
                    art_uri=self._thumb,
                    avatar_path=self._channel_thumb_local,
                )
            except Exception:
                pass

            # Start audio first. Video extraction/CDN negotiation must not
            # compete with MediaPlayer's first audible bytes on weak Wi-Fi.
            # A bounded optional video launch happens *after* audio starts.
            threading.Thread(
                target=lambda: self._start_audio_only_after_prepared(gen),
                daemon=True, name="pymusic-audio-start",
            ).start()

        def _on_complete():
            if not self._is_current_gen(gen):
                return
            if self._bg_endguard_fired_gen == gen:
                return
            self._bg_endguard_fired_gen = gen
            try:
                if self._video_player:
                    self._video_player.stop()
            except Exception:
                pass

            # нормальне завершення - не треба резюму
            self._resume_pos_ms = 0

            if self.repeat:
                self._restart_same()
                return
            if self._auto_skip:
                self._queue_auto_next()
                return
            self._playback_desired = False
            self._user_paused = True
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)

        def _on_error(what, extra):
            print(f"[AUDIO] MediaPlayer error: what={what}, extra={extra}")

            if not self._is_current_gen(gen):
                return True

            now = time.time()
            # Count consecutive errors for the current track, not only errors
            # within five seconds: YouTube/CDN requests commonly take >5 s,
            # otherwise we retry the same broken format forever.
            self._stream_error_hits += 1
            self._last_stream_error_ts = now

            # збережемо поточну позицію перед перезапуском
            try:
                if ma.android_player:
                    self._resume_pos_ms = ma.android_player.getCurrentPosition() or 0
            except Exception:
                pass

            current_source = str(getattr(self, "_stream_url", "") or "")
            if what == -38 or extra == -38:
                # Learn a device-specific native codec failure. This turns
                # off future Opus-first attempts if the handset rejects
                # WebM and keeps AAC as a safe fallback.
                failed_format = str(getattr(self, "_stream_format_id", "") or "")
                ydlh.note_native_audio_format_failure(failed_format)
                if failed_format in ("249", "250", "251"):
                    ydlh.invalidate_audio_info_cache(
                        str(getattr(self, "_last_video_url", "") or "")
                    )
            used = getattr(self, "_audio_source_attempts", None)
            if not isinstance(used, set):
                used = set()
                self._audio_source_attempts = used
            if current_source:
                used.add(current_source)

            # Android sometimes raises (-38,0) just after prepareAsync().
            # On the previous build this triggered another 5-10s yt-dlp
            # request for exactly the same AAC 140 stream. Use an already
            # extracted, different signed audio URL first (no network I/O).
            if (
                (what == -38 or extra == -38)
                and self._stream_error_hits <= 2
                and self._last_video_url
            ):
                other = ydlh.get_cached_alternate_audio_source(
                    self._last_video_url, current_source, used
                )
                if other and other.get("audio_url"):
                    self._stream_url = other["audio_url"]
                    self._stream_format_id = str(other.get("format_id") or "")
                    self._headers = dict(other.get("http_headers") or {})
                    used.add(self._stream_url)
                    self._put_cache(
                        self._last_video_url, self._stream_url,
                        self._headers, self._expire_ts
                    )
                    print(
                        "[AUDIO] native -38: retry from cached alternate "
                        f"format={self._stream_format_id!r} without yt-dlp"
                    )
                    threading.Thread(
                        target=lambda: self._start_from_known_stream(gen),
                        name="pymusic-audio-alternate-native",
                        daemon=True,
                    ).start()
                    return True

            # Invalidate only after local sources have failed; a normal
            # network recovery then refreshes expired CDN URLs.
            ydlh.invalidate_audio_info_cache(
                str(getattr(self, "_last_video_url", "") or "")
            )
            # типові помилки нестабільного інтернету
            NETWORK_ERRORS = (-1004, -110, -38, 1)

            if what in NETWORK_ERRORS or extra in NETWORK_ERRORS:
                # Частий кейс проблемних треків: Unknown error(1,-2147483648).
                # Після кількох помилок форсимо сумісний AAC/M4A профіль.
                # A failed muxed format 18 should immediately try the
                # alternative audio-only client instead of retrying the same
                # broken Googlevideo/CDN URL for this specific track.
                if (self._stream_error_hits >= 2
                        or str(getattr(self, "_stream_format_id", "")) == "18"):
                    if not self._prefer_compat_audio:
                        self._prefer_compat_audio = True
                        print("[AUDIO] switch to alternate audio-only AAC/M4A source")
                if self._stream_error_hits >= 6:
                    print("[AUDIO] too many stream errors, skip/stop track")
                    if self._auto_skip:
                        self._queue_auto_next()
                    else:
                        self._playback_desired = False
                        self._user_paused = True
                        Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0.1)
                    return True
                print("[AUDIO] Network stream failed, re-extracting fresh URL...")
                if self._last_video_url:
                    # Do not reuse cached 503/invalid-state CDN URLs. Refresh
                    # stream extraction, which now tries audio-only AAC clients.
                    force_fresh = (what in (1, -38, -1004)
                                   or extra in (-1004, -2147483648))
                    self._recover_stream("media_error", self._resume_pos_ms, force_fresh=force_fresh)
                return True

            # якщо інша помилка - мʼякий рестарт з того ж потоку
            print("[AUDIO] Unknown error, soft restart...")
            Clock.schedule_once(lambda dt: self._start_from_known_stream(gen), 0.2)
            return True

        if hasattr(ma, "_mp_create_set_source_and_prepare_async"):
            ma._mp_create_set_source_and_prepare_async(
                self._stream_url, jheaders,
                _on_prepared, _on_complete,
                lambda w, e: _on_error(w, e),
                lambda w, e: self._on_native_audio_info(w, e, gen)
            )
        else:
            ma._mp_create(self._stream_url, _on_prepared, _on_complete, lambda w, e: _on_error(w, e))

    def _audio_cache_dir(self) -> str:
        base_dir = None
        try:
            app = App.get_running_app()
            base_dir = getattr(app, "user_data_dir", None)
        except Exception:
            base_dir = None
        if not base_dir:
            base_dir = tempfile.gettempdir()
        cache_dir = os.path.join(base_dir, "pymusic_audio_cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass
        return cache_dir

    def _cache_key(self, video_url: str) -> str:
        return hashlib.md5((video_url or "").encode("utf-8")).hexdigest()

    def _guess_audio_ext(self, audio_url: str) -> str:
        try:
            path = urllib.parse.urlparse(audio_url).path or ""
            ext = os.path.splitext(path)[1].lstrip(".").lower()
            if ext in {"m4a", "webm", "mp4", "mp3", "aac", "ogg"}:
                return ext
        except Exception:
            pass
        return "audio"

    def _audio_cache_path(self, video_url: str, ext: str | None = None) -> str:
        ext = ext or "audio"
        key = self._cache_key(video_url)
        return os.path.join(self._audio_cache_dir(), f"{key}.{ext}")

    def _find_cached_audio(self, video_url: str) -> str | None:
        """Only use finalized files within this app's audio cache directory."""
        if not video_url:
            return None
        cache_root = os.path.realpath(self._audio_cache_dir())
        key = self._cache_key(video_url)

        def valid(candidate: str) -> bool:
            try:
                path = os.path.realpath(str(candidate or ""))
                if os.path.commonpath([path, cache_root]) != cache_root:
                    return False
                name = os.path.basename(path)
                if not name.startswith(key + ".") or name.endswith(".part"):
                    return False
                return os.path.isfile(path) and os.path.getsize(path) > 128
            except (OSError, ValueError, TypeError):
                return False

        try:
            for entry in load_recent():
                if entry.get("url") == video_url:
                    candidate = str(entry.get("cache_path") or "")
                    if valid(candidate):
                        return os.path.realpath(candidate)
        except Exception:
            pass
        try:
            for name in os.listdir(cache_root):
                path = os.path.join(cache_root, name)
                if valid(path):
                    return os.path.realpath(path)
        except Exception:
            pass
        return None

    def _download_audio_locally(self, video_url: str, audio_url: str, headers: dict | None = None) -> str | None:
        """Explicit favorite audio cache, with verified HTTPS and size limit.

        Automatic caching during *streaming* is disabled: fetching the same
        track twice was expensive, especially over weak/mobile networks.
        Only an explicit favorite action can call this full-file download.
        """
        if not video_url or not audio_url or audio_url.startswith("file://") or "m3u8" in audio_url:
            return None

        # A compromised extractor/cache entry must never fetch loopback,
        # file:// or another service, or redirect to an untrusted host.
        try:
            parsed = urllib.parse.urlsplit(str(audio_url))
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not (
                host == "googlevideo.com" or host.endswith(".googlevideo.com")
            ):
                return None
        except (TypeError, ValueError):
            return None

        ext = self._guess_audio_ext(audio_url)
        path = self._audio_cache_path(video_url, ext)
        existing = self._find_cached_audio(video_url)
        if existing:
            return existing

        # Keep user-requested offline favorites bounded without evicting
        # any existing favorites behind the user's back. When the allowance
        # is full, streaming still works; only the optional save is skipped.
        try:
            cache_root = self._audio_cache_dir()
            total = sum(
                entry.stat(follow_symlinks=False).st_size
                for entry in os.scandir(cache_root)
                if entry.is_file(follow_symlinks=False)
                and not entry.name.endswith(".part")
            )
            # Reserve capacity for the entire maximum single-file download,
            # so the final total can never exceed the advertised 128 MiB.
            if total + 24 * 1024 * 1024 > 128 * 1024 * 1024:
                print("[AUDIO] optional favorite cache is at 128 MiB limit")
                return None
        except (OSError, ValueError):
            return None

        headers = headers or {}
        req_headers = {
            "User-Agent": headers.get("User-Agent") or (
                "Mozilla/5.0 (Linux; Android 12; Mobile) "
                "AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36"
            ),
            "Referer": headers.get("Referer") or "https://www.youtube.com",
            "Accept-Language": headers.get("Accept-Language") or "en-US,en;q=0.9",
        }
        # Cap a user-requested favorite cache entry. Very long recordings
        # remain streamable but never fill the device's storage unexpectedly.
        max_bytes = 24 * 1024 * 1024
        tmp_path = f"{path}.part"
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            request = urllib.request.Request(audio_url, headers=req_headers)

            class RejectRedirect(urllib.request.HTTPRedirectHandler):
                # Do not connect to an untrusted redirect destination at all.
                def redirect_request(self, req, fp, code, msg, hdrs, newurl):
                    parsed = urllib.parse.urlsplit(newurl)
                    host = (parsed.hostname or "").lower()
                    if parsed.scheme != "https" or not (
                        host == "googlevideo.com"
                        or host.endswith(".googlevideo.com")
                    ):
                        raise ValueError("untrusted audio redirect destination")
                    return super().redirect_request(
                        req, fp, code, msg, hdrs, newurl
                    )

            opener = urllib.request.build_opener(
                urllib.request.HTTPSHandler(context=context),
                RejectRedirect(),
            )
            with opener.open(request, timeout=20) as resp:
                final_url = urllib.parse.urlsplit(resp.geturl())
                final_host = (final_url.hostname or "").lower()
                if final_url.scheme != "https" or not (
                    final_host == "googlevideo.com"
                    or final_host.endswith(".googlevideo.com")
                ):
                    raise ValueError("untrusted audio redirect")
                expected = int(resp.headers.get("Content-Length") or 0)
                if expected > max_bytes:
                    raise ValueError("favorite audio exceeds 24 MiB cache limit")
                received = 0
                with open(tmp_path, "wb") as output:
                    while True:
                        chunk = resp.read(128 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        if received > max_bytes:
                            raise ValueError("favorite audio exceeded cache limit")
                        output.write(chunk)
            if received < 128 or (expected and received != expected):
                raise IOError("favorite audio download was incomplete")
            os.replace(tmp_path, path)
            update_recent_cache(video_url, path)
            update_favorite_cache(video_url, path)
            return path
        except Exception as exc:
            print("[AUDIO] optional favorite cache download failed:", type(exc).__name__)
            try:
                if os.path.isfile(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            return None

    def _cache_audio_async(self, video_url: str, audio_url: str, headers: dict | None = None):
        if not video_url or not audio_url:
            return
        key = self._cache_key(video_url)
        if key in self._audio_cache_inflight:
            return
        if self._find_cached_audio(video_url):
            return

        self._audio_cache_inflight.add(key)

        def _job():
            try:
                self._download_audio_locally(video_url, audio_url, headers)
            finally:
                self._audio_cache_inflight.discard(key)

        threading.Thread(target=_job, daemon=True).start()

    def _try_start_cached_audio(self, video_url: str, gen: int | None = None) -> bool:
        cached = self._find_cached_audio(video_url)
        if not cached:
            return False
        print(f"[AUDIO] start local cached stream: {cached}")
        self._hydrate_metadata_from_known_sources(video_url)
        self._stream_url = cached if cached.startswith("file://") else f"file://{cached}"
        self._headers = {}
        self._expire_ts = None
        threading.Thread(
            target=lambda: self._start_from_known_stream(gen),
            daemon=True,
        ).start()
        return True

    def _discard_current_cached_stream(self):
        stream_url = str(self._stream_url or "")
        if not stream_url.startswith("file://"):
            return
        try:
            path = urllib.parse.unquote(urllib.parse.urlparse(stream_url).path or "")
            if path and os.path.isfile(path):
                os.remove(path)
                print(f"[AUDIO] removed broken cached stream: {path}")
        except Exception as e:
            print(f"[AUDIO] failed to remove broken cached stream: {e}")
# TODO треба скинути всю роботу з відео в video_player.py тут тільки виклики базові і все
    def _download_video_locally(self, video_url: str, headers: dict | None = None) -> str | None:
        """
        Скачує відеофайл у тимчасову теку та повертає шлях або None при помилці.
        (Залишено як резервний варіант.)
        """
        if not video_url or video_url.startswith("file://") or "m3u8" in video_url:
            return None

        headers = headers or {}
        cache_dir = os.path.join(tempfile.gettempdir(), "pymusic_video_cache")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            pass

        safe_name = hashlib.md5(video_url.encode("utf-8")).hexdigest() + ".mp4"
        path = os.path.join(cache_dir, safe_name)

        try:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return path
        except Exception:
            pass

        req_headers = {
            "User-Agent": headers.get("User-Agent") or "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome Mobile Safari/537.36",
            "Referer": headers.get("Referer") or "https://www.youtube.com",
            "Accept-Language": headers.get("Accept-Language") or "en-US,en;q=0.9",
            "Connection": headers.get("Connection") or "keep-alive",
        }

        try:
            ctx = ssl._create_unverified_context()
            req = urllib.request.Request(video_url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp, open(path, "wb") as f:
                while True:
                    chunk = resp.read(512 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            return path if os.path.exists(path) and os.path.getsize(path) > 0 else None
        except Exception as e:
            try:
                print("[VIDEO] download fail:", e)
            except Exception:
                pass
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            return None

    def _auto_video_for_current_base(self, gen: int, sync_start: bool = False):
        """Автоматичний показ YouTube-відео для поточного треку."""
        # Audio is the master clock; loading video must never hold up audio.
        sync_start = False
        if self._app_in_background or not self._is_screen_active():
            return
        if not self._is_current_gen(gen) or not self._playback_desired:
            return
        if not self._last_video_url:
            return
        if not self._ensure_video_player():
            return
        if getattr(self._video_player, "is_embed", False):
            if not self._video_enabled:
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
                if sync_start:
                    self._start_audio_only_after_prepared(gen)
                return
            if not self._is_screen_active():
                Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
                return
            Clock.schedule_once(lambda dt: self._set_video_mode(True), 0)
            Clock.schedule_once(lambda dt: self._play_embed_if_screen_active(), 0)
            if sync_start:
                self._start_audio_only_after_prepared(gen)
            return

        # якщо користувач вимкнув відео - не тягнемо потік
        if not self._video_enabled:
            Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
            if sync_start:
                self._start_audio_only_after_prepared(gen)
            return

        # Capture the selected track and its request ID. A late yt-dlp or
        # Android MediaPlayer callback must not resurrect the previous video.
        track_url = str(self._last_video_url or "")
        request_id = int(getattr(self, "_pymusic_video_start_request", 0) or 0)

        def current_request():
            return (
                self._is_current_gen(gen)
                and int(getattr(self, "_pymusic_video_start_request", -1)) == request_id
                and str(self._last_video_url or "") == track_url
                and self._video_enabled
                and self._playback_desired
                and not self._user_paused
                and not self._app_in_background
                and self._is_screen_active()
            )

        def _job():
            try:
                if not current_request():
                    return
                info_v = ydlh.safe_extract_video_info(track_url) or {}
                if not current_request():
                    return
                streams = list(info_v.get("streams") or [])
                if not streams and info_v.get("video_url"):
                    streams = [{
                        "video_url": info_v["video_url"],
                        "http_headers": info_v.get("http_headers") or {},
                        "format_id": info_v.get("format_id") or "",
                        "protocol": "legacy",
                    }]

                def start_candidate(index: int):
                    if not current_request():
                        return
                    if index >= len(streams):
                        self._pymusic_video_starting = False
                        print(
                            f"[VIDEO] all {len(streams)} video formats failed; "
                            "keeping audio and thumbnail"
                        )
                        self._set_video_mode(False)
                        return

                    choice = streams[index]
                    video_stream = str(choice.get("video_url") or "")
                    if not video_stream:
                        start_candidate(index + 1)
                        return
                    video_headers = dict(choice.get("http_headers") or {})
                    self._pymusic_video_starting = True
                    print(
                        "[VIDEO] native candidate "
                        f"{index+1}/{len(streams)} "
                        f"format={choice.get('format_id')} "
                        f"protocol={choice.get('protocol')}"
                    )
                    # Set native viewport geometry *before* play/prepareAsync;
                    # never wait for onPrepared to move from the old 1x1 view.
                    self._align_video_to_thumb()

                    # A MediaPlayer may stay forever in prepareAsync()
                    # without producing an error callback. Bound each
                    # candidate so the next HLS resolution can be tried.
                    attempt_state = {"failed": False, "watchdog": None}

                    def ready():
                        if attempt_state["failed"] or not current_request():
                            return
                        timer = attempt_state.get("watchdog")
                        if timer is not None:
                            timer.cancel()
                        self._pymusic_video_starting = False
                        self._set_video_mode(True)
                        self._align_video_to_thumb()
                        self._set_video_controls_visible(True)
                        print(
                            f"[VIDEO] native playback ready "
                            f"format={choice.get('format_id')}"
                        )

                    def failed(reason):
                        if attempt_state["failed"]:
                            return
                        attempt_state["failed"] = True
                        timer = attempt_state.get("watchdog")
                        if timer is not None:
                            timer.cancel()
                        if not current_request():
                            return
                        print(
                            f"[VIDEO] candidate {choice.get('format_id')} "
                            f"failed ({reason}); trying next source"
                        )
                        if reason == "prepare timeout":
                            # The native error callback didn't fire; retire
                            # the stuck decoder before trying another stream.
                            try:
                                self._video_player.stop()
                            except Exception:
                                pass
                        start_candidate(index + 1)

                    attempt_state["watchdog"] = Clock.schedule_once(
                        lambda _dt: failed("prepare timeout"), 13.0
                    )
                    # A broken/older runtime wrapper must never escape a
                    # Kivy Clock event and terminate the whole Android app.
                    # Treat startup exceptions as one failed video candidate;
                    # audio playback remains independent.
                    try:
                        self._play_video_if_screen_active(
                            video_stream,
                            video_headers,
                            on_ready=ready,
                            on_error=failed,
                        )
                    except Exception as exc:
                        print(
                            "[VIDEO] candidate startup failed safely: "
                            f"{type(exc).__name__}: {exc}"
                        )
                        failed(f"startup {type(exc).__name__}")

                if not streams:
                    Clock.schedule_once(
                        lambda _dt: start_candidate(0), 0
                    )
                    return
                Clock.schedule_once(lambda _dt: start_candidate(0), 0)
            except Exception as exc:
                print(f"[VIDEO] automatic video extraction failed: {exc}")
                if current_request():
                    Clock.schedule_once(
                        lambda _dt: self._set_video_mode(False), 0
                    )

        threading.Thread(target=_job, daemon=True).start()

    def _auto_video_for_current(self, gen: int, sync_start: bool = False):
        request_id = int(
            getattr(self, "_pymusic_video_start_request", 0) or 0
        ) + 1
        self._pymusic_video_start_request = request_id

        def worker():
            deadline = time.monotonic() + (0.9 if not sync_start else 0.35)
            while time.monotonic() < deadline:
                if request_id != int(
                    getattr(self, "_pymusic_video_start_request", -1)
                ):
                    return
                if int(getattr(self, "_load_gen", -1)) != int(gen):
                    return
                if getattr(self, "_app_in_background", False):
                    return
                if not getattr(self, "_playback_desired", False):
                    return
                if getattr(self, "_user_paused", False):
                    return
                try:
                    audio = getattr(ma, "android_player", None)
                    if audio is not None and bool(audio.isPlaying()):
                        break
                except Exception:
                    pass
                time.sleep(0.025)

            if request_id != int(
                getattr(self, "_pymusic_video_start_request", -1)
            ):
                return
            if int(getattr(self, "_load_gen", -1)) != int(gen):
                return
            self._auto_video_for_current_base( gen, sync_start=sync_start)

        # Some resume paths can call this method from Kivy's UI thread.
        # Never block that thread while waiting for the audio player.
        if threading.current_thread() is threading.main_thread():
            threading.Thread(target=worker, daemon=True).start()
            return None
        return worker()


    def _play_embed_if_screen_active(self):
        if not self._is_screen_active() or self._app_in_background or not self._video_enabled:
            self._set_video_mode(False)
            return
        try:
            if self._video_player:
                self._video_player.play(self._last_video_url, start_pos_provider=self._audio_pos_ms)
        except Exception as e:
            try:
                print("[VIDEO] embed play err:", e)
            except Exception:
                pass
            self._set_video_mode(False)

    def _play_video_if_screen_active(self, vurl: str, vheaders: dict, *, on_ready=None, on_error=None):
        if not self._is_screen_active() or self._app_in_background or not self._video_enabled:
            self._set_video_mode(False)
            return
        if self._video_player:
            def _show_video_after_first_frame():
                try:
                    if callable(on_ready):
                        Clock.schedule_once(lambda dt: on_ready(), 0)
                    else:
                        Clock.schedule_once(lambda dt: self._set_video_mode(True), 0)
                except Exception:
                    pass

            self._video_player.play(
                vurl,
                headers=(vheaders or {}),
                loop=False,
                start_pos_provider=self._audio_pos_ms,
                on_prepared=_show_video_after_first_frame,
                on_error=on_error,
            )

    def _start_synced_audio_and_video(self, gen: int, vurl: str, vheaders: dict):
        """Legacy caller: start audio now, schedule independent muted video."""
        try:
            already_playing = bool(ma.android_player and ma.android_player.isPlaying())
        except Exception:
            already_playing = False
        if not already_playing:
            threading.Thread(
                target=lambda: self._start_audio_only_after_prepared(gen),
                name="pymusic-audio-first-start",
                daemon=True,
            ).start()
        Clock.schedule_once(
            lambda _dt: self._play_video_if_screen_active(vurl, vheaders or {}),
            0,
        )

    def _start_audio_only_after_prepared(self, gen: int):
        """Фолбек: якщо відео не вдалось або вимкнене - стартуємо тільки аудіо."""
        if not self._is_current_gen(gen) or not self._playback_desired:
            return
        try:
            ma.acquire_wake_lock()
            self._request_af()

            dur = ma.android_player.getDuration() if ma.android_player else 0

            # відновлення з останньої позиції, якщо не майже кінець
            try:
                if self._resume_pos_ms and dur and self._resume_pos_ms < (dur - 5000):
                    ma._mp_seek_to(int(self._resume_pos_ms))
                    print(f"[AUDIO] resume (audio-only) from {self._resume_pos_ms} ms")
            except Exception:
                pass

            ma._mp_start()
            pending_art = getattr(self, "_pending_audio_art", None)
            if (
                isinstance(pending_art, tuple)
                and len(pending_art) == 2 and pending_art[0] == gen
            ):
                def fetch_art(_dt, expect=gen, url=pending_art[1]):
                    if self._is_current_gen(expect) and self._playback_desired:
                        self._download_art_async(url)
                Clock.schedule_once(fetch_art, 2.0)
                self._pending_audio_art = None
            t0 = float(getattr(self, "_audio_play_request_t0", 0) or 0)
            if t0:
                print(f"[LATENCY] audio start queued after {time.monotonic() - t0:.2f}s")
            self._audio_ready_for_extras_gen = gen
            Clock.schedule_once(
                lambda _dt, expected=gen: (
                    self._render_similar_ui(force=True)
                    if self._is_current_gen(expected) else None
                ), 0.8,
            )
            if not ma.is_metered_connection():
                Clock.schedule_once(
                    lambda _dt, expected=gen: (
                        self._ensure_related_current_v4(force=False)
                        if self._is_current_gen(expected) else None
                    ),
                    2.5,
                )
            try:
                # Native video starts independently after audio. Do not
                # stop a video that is already preparing merely because the
                # shadow audio MediaPlayer has just become ready.
                if not self._video_enabled or self._app_in_background:
                    Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)
            except Exception:
                pass

            try:
                ma.set_media_metadata(
                    title=self._title,
                    artist=self._channel,
                    duration_ms=int(dur or 0),
                    art_path=self._art_path,
                    art_uri=self._thumb,
                    avatar_path=self._channel_thumb_local,
                )
            except Exception:
                pass

            try:
                ma.update_media_session_state(
                    True,
                    position_ms=int(self._resume_pos_ms or 0),
                    duration_ms=int(dur or 0),
                    can_seek=True,
                    repeat_enabled=bool(self.repeat),
                    favorite_enabled=bool(self._favorite),
                )
                token2 = self._media_session.getSessionToken() if self._media_session else None
                ma.create_or_update_media_notification(
                    title=self._title or "Playing",
                    subtitle=self._channel or "YouTube",
                    is_playing=True,
                    session_token=token2,
                    large_icon_path=self._channel_thumb_local,
                    repeat_enabled=bool(self.repeat),
                    favorite_enabled=bool(self._favorite),
                )
            except Exception:
                pass

            self._start_completion_guard(gen, int(dur or 0))
            Clock.schedule_once(lambda dt: self._ui_set_playing(True), 0)
            self._schedule_progress()
            self._schedule_expiry()
            self._launch_video_after_audio_start(gen)
        except Exception as e:
            ma.log(f"audio-only start err: {e}")

    def _launch_video_after_audio_start(self, gen: int) -> None:
        """Prioritize starting the audio stream before video network work.

        On metered networks the default is audio-only; if the user manually
        enables video, load it after a larger audio head start. Every delayed
        operation is generation-checked so switching tracks cannot resurrect
        stale video or old CDN requests.
        """
        if (
            not self._video_enabled or self._app_in_background
            or not self._is_screen_active()
            or getattr(self, "_video_launch_requested_gen", -1) == gen
        ):
            return
        self._video_launch_requested_gen = gen

        def worker():
            delay = 4.0 if ma.is_metered_connection() else 1.5
            time.sleep(delay)
            if (
                not self._is_current_gen(gen)
                or not self._video_enabled
                or self._app_in_background
                or not self._is_screen_active()
                or not self._playback_desired
                or self._user_paused
                or not ma._is_prepared()
            ):
                return
            self._auto_video_for_current(gen, sync_start=False)

        threading.Thread(
            target=worker, daemon=True, name="pymusic-video-after-audio"
        ).start()

    def _prefetch_next_track_audio(self):
        # Prefetch only shortly before a real track transition, on Wi-Fi.
        # Metered connections must never use data for a track not requested.
        if ma.is_metered_connection() or not self._auto_skip:
            return None
        try:
            playlist = getattr(self, "playlist", None)
            tracks = getattr(playlist, "tracks", None) if playlist else None
            if not tracks or len(tracks) < 2:
                return None

            current_index = int(getattr(playlist, "index", 0) or 0)
            next_index = (current_index + 1) % len(tracks)
            next_item = tracks[next_index] or {}
            next_url = str(next_item.get("url") or "")
            current_url = str(getattr(self, "_last_video_url", "") or "")
            expected_gen = int(getattr(self, "_load_gen", 0) or 0)

            if not next_url or next_url == current_url:
                return None

            cached = getattr(self, "_url_cache", {}).get(next_url)
            if cached and cached.get("audio_url"):
                return None

            inflight = getattr(self, "_prefetch_inflight", None)
            if inflight is None:
                self._prefetch_inflight = set()
                inflight = self._prefetch_inflight
            if next_url in inflight:
                return None
            inflight.add(next_url)
        except Exception:
            return None

        def job():
            try:
                # A short delay avoids competing with the current track's
                # prepare/start, but unlike Clock.schedule_once it continues
                # to run when PythonActivity is paused by screen-off.
                time.sleep(0.35)

                if int(getattr(self, "_load_gen", -1)) != expected_gen:
                    return
                if str(getattr(self, "_last_video_url", "") or "") != current_url:
                    return

                cached_now = getattr(self, "_url_cache", {}).get(next_url)
                if cached_now and cached_now.get("audio_url"):
                    return

                try:
                    info = ydlh.extract_audio_info(
                        next_url,
                        prefer_compat=bool(
                            getattr(self, "_prefer_compat_audio", False)
                        ),
                    )
                except TypeError:
                    info = ydlh.extract_audio_info(next_url)

                audio_url = str((info or {}).get("audio_url") or "")
                if not audio_url:
                    return

                headers = dict((info or {}).get("http_headers") or {})
                expire_ts = (info or {}).get("expire_ts")
                self._put_cache(next_url, audio_url, headers, expire_ts)

                # Do not download the whole next song here.  A full-file
                # prefetch competes for mobile bandwidth with the current
                # stream.  The direct URL cache is enough for instant start.
                print(f"[BG-NEXT] prefetched next URL: {next_url}")
            except Exception as exc:
                print("[BG-NEXT] prefetch failed:", exc)
            finally:
                try:
                    inflight.discard(next_url)
                except Exception:
                    pass

        threading.Thread(target=job, daemon=True).start()
        return None

    # ==================== extract & start ====================
# TODO в нас дуже багато подібних екстаріктів і gen оптимізуй їх
    def _extract_and_start_gen(self, video_url: str, gen: int | None = None, *, prefer_compat: bool | None = None):

        if gen is None:
            gen = self._load_gen

        if not self._is_current_gen(gen):
            return

        self._extract_and_start(video_url, gen, prefer_compat=prefer_compat)

    def _extract_and_start(self, video_url: str, gen: int, *, prefer_compat: bool | None = None):

        if not self._is_current_gen(gen):
            return

        use_compat = self._prefer_compat_audio if prefer_compat is None else bool(prefer_compat)

        self._pre_start_cleanup()

        try:
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing audio",
                subtitle=self._channel or "YouTube",
                is_playing=True,
                session_token=token,
                large_icon_path=self._channel_thumb_local,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
        except Exception:
            pass

        try:
            info = ydlh.extract_audio_info(video_url, prefer_compat=use_compat)
            if not self._is_current_gen(gen):
                return

            self._stream_url = info["audio_url"]
            getattr(self, "_audio_source_attempts", set()).add(self._stream_url)
            self._stream_format_id = str(info.get("format_id") or "")
            started = float(getattr(self, "_audio_play_request_t0", 0) or 0)
            if started:
                print(f"[LATENCY] audio URL ready after {time.monotonic() - started:.2f}s")
            self._expire_ts = info.get("expire_ts")
            self._headers = info.get("http_headers", {})
            print(
                "[AUDIO] extracted stream "
                f"format={info.get('format_id')!r} ext={info.get('ext')!r} "
                f"acodec={info.get('acodec')!r} compat={use_compat}"
            )

            updated_meta = False
            if not self._title:
                t = info.get("title") or ""
                if t:
                    self._title = t
                    updated_meta = True
            if not self._channel:
                ch = _verified_channel_name(
                    info.get("channel") or info.get("uploader"),
                    info.get("title") or self._title,
                )
                if ch:
                    self._channel = ch
                    updated_meta = True
            cthumb = info.get("channel_thumb") or info.get("uploader_thumb") or ""
            if isinstance(cthumb, str) and cthumb.startswith("//"):
                cthumb = f"https:{cthumb}"
            if cthumb:
                self._channel_thumb = cthumb
                self._download_channel_avatar_async(cthumb)
            vc = info.get("view_count")
            if vc is not None:
                self._views_text = self._fmt_views(vc)
            published = str(info.get("upload_date") or info.get("release_date") or "")
            if published and published != self._published_date:
                self._published_date = published
                updated_meta = True
            related = info.get("related_videos") or []
            self._related_items = self._normalize_related(related)
            # Rendering related thumbnails starts 8 concurrent image fetches.
            # Never compete with the current audio's native prepareAsync().
            if getattr(self, "_audio_ready_for_extras_gen", -1) == gen:
                Clock.schedule_once(lambda dt: self._render_similar_ui(), 0)

            if not self._thumb:
                self._thumb = info.get("thumb") or ""
                Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)
                if self._thumb:
                    # Defer the cover HTTP request until audio can play.
                    # This keeps thumbnail transfer off the critical path.
                    self._pending_audio_art = (gen, self._thumb)
                elif self._last_video_url:
                    cached_art = self._find_cached_art(self._last_video_url)
                    if cached_art:
                        self._art_path = cached_art
                        self._thumb = cached_art
                        Clock.schedule_once(lambda dt: self._sync_thumb_now(), 0)

            try:
                try:
                    ma.log(f"[META] extract title={self._title!r} channel={self._channel!r} cthumb={self._channel_thumb!r}")
                except Exception:
                    pass
                ma.set_media_metadata(
                    title=self._title, artist=self._channel, art_uri=self._thumb,
                    avatar_path=self._channel_thumb_local,
                )
            except Exception:
                pass

            if updated_meta:
                Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)

            self._put_cache(video_url, self._stream_url, self._headers, self._expire_ts)
            # Do NOT auto-download the same song a second time while the
            # native player is streaming it. That doubled mobile bandwidth
            # and competed with the audio startup buffer. Explicitly saved
            # favorite tracks retain their existing cache workflow.
            self._metadata_loaded_urls.add(video_url)
            self._extract_fail_skip_hits = 0

        except Exception as e:
            if self._extract_fail_handled_gen == gen:
                return
            self._extract_fail_handled_gen = gen
            ma.log(f"extract fail: {e}")
            try:
                print(f"[AUDIO] extract failed for {video_url!r}: {e}")
            except Exception:
                pass
            if self._last_video_url and self._try_start_cached_audio(self._last_video_url, gen):
                return
            if self._auto_skip and self.playlist and len(self.playlist) > 1:
                self._extract_fail_skip_hits += 1
                if self._extract_fail_skip_hits <= 5:
                    try:
                        print(f"[AUDIO] extract failed, skipping to next ({self._extract_fail_skip_hits}/5)")
                    except Exception:
                        pass
                    self._resume_pos_ms = 0
                    if self._app_in_background:
                        self._advance_to_next_track()
                    else:
                        Clock.schedule_once(lambda dt: self._advance_to_next_track(), 0)
                    return
                try:
                    print("[AUDIO] too many extract failures, stopping playlist")
                except Exception:
                    pass
            try:
                if not ma.is_network_available():
                    print("[AUDIO] network unavailable during extract")
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
            return

        if self._is_current_gen(gen):
            self._start_from_known_stream(gen)

    def _put_cache(self, url, audio_url, headers, expire_ts):
        try:
            if len(self._url_cache) >= self._URL_CACHE_MAX:
                old_key = next(iter(self._url_cache.keys()))
                self._url_cache.pop(old_key, None)
            self._url_cache[url] = {
                "audio_url": audio_url,
                "headers": dict(headers or {}),
                "expire_ts": expire_ts,
                "ts_put": time.time(),
            }
        except Exception:
            pass

    def _restart_same(self):
        if not self._playback_desired or not self._last_video_url:
            return
        try:
            ma._mp_reset_release()
        except Exception:
            pass
        # при повному рестарті (repeat) починаємо з 0
        self._resume_pos_ms = 0
        fast = self._url_cache.get(self._last_video_url)
        if fast and fast.get("audio_url"):
            self._stream_url = fast["audio_url"]
            self._headers = dict(fast.get("headers") or {})
            self._expire_ts = fast.get("expire_ts")
            self._start_from_known_stream(self._load_gen)
        else:
            self._extract_and_start_gen(self._last_video_url, self._load_gen)

    def _request_af(self):
        try:
            am = ma.activity.getSystemService(ma.Context.AUDIO_SERVICE)
            if ma.Build_VERSION.SDK_INT >= 26:
                aa = (ma.AudioAttributesBuilder()
                      .setUsage(ma.AudioAttributes.USAGE_MEDIA)
                      .setContentType(ma.AudioAttributes.CONTENT_TYPE_MUSIC)
                      .build())
                afr = ma.AudioFocusRequestBuilder(ma.AudioManager.AUDIOFOCUS_GAIN).setAudioAttributes(aa).build()
                am.requestAudioFocus(afr)
            else:
                am.requestAudioFocus(None, ma.AudioManager.STREAM_MUSIC, ma.AudioManager.AUDIOFOCUS_GAIN)
        except Exception:
            pass

    def _try_playlist_refresh_next(self) -> bool:
        if self._playlist_refreshing:
            print("[PLAYLIST] refresh skip: already refreshing")
            return False
        now = time.time()
        if (now - self._playlist_last_refresh_ts) < 3.0:
            print("[PLAYLIST] refresh skip: throttled")
            return False

        playlist_url = self._playlist_url
        start_video_id = None

        try:
            from urllib.parse import urlparse, parse_qs

            def _extract_vid(u: str | None) -> str | None:
                if not u:
                    return None
                parsed = urlparse(u)
                q = parse_qs(parsed.query or "")
                vid = (q.get("v") or [None])[0]
                if vid:
                    return vid
                if "youtu.be" in (parsed.netloc or ""):
                    return (parsed.path or "").lstrip("/") or None
                return None

            if not playlist_url and self._last_video_url:
                parsed = urlparse(self._last_video_url)
                q = parse_qs(parsed.query or "")
                playlist_id = (q.get("list") or [None])[0]
                if playlist_id:
                    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                start_video_id = _extract_vid(self._last_video_url)
            else:
                start_video_id = _extract_vid(self._last_video_url)
        except Exception:
            playlist_url = playlist_url or None
            start_video_id = start_video_id or None

        if not playlist_url or not start_video_id:
            print(f"[PLAYLIST] refresh skip: playlist_url={playlist_url!r} start_video_id={start_video_id!r}")
            return False

        self._playlist_refreshing = True
        self._playlist_last_refresh_ts = now
        try:
            threading.Timer(2.0, lambda: setattr(self, "_playlist_refreshing", False)).start()
        except Exception:
            Clock.schedule_once(lambda dt: setattr(self, "_playlist_refreshing", False), 2.0)

        try:
            print(f"[PLAYLIST] refresh request url={playlist_url!r} start_after={start_video_id!r}")
            search_screen = self.manager.get_screen("search")
            search_screen.open_playlist(
                playlist_url,
                self.playlist.name or "Черга",
                start_video_id=start_video_id,
                start_after=True,
                fallback_url=self._last_video_url,
            )
            return True
        except Exception as e:
            print(f"[PLAYLIST] refresh request err: {e}")
            return False

    # ==================== UI wiring ====================

    def open_current_channel(self):
        """Open the selected video's verified YouTube author in the Web tab.

        Both the avatar and name call this function. Network resolution runs
        off the Kivy thread; a generation check prevents a delayed lookup
        from opening an earlier track's channel after a fast switch.
        """
        video_url = str(getattr(self, "_last_video_url", "") or "")
        if not video_url:
            return
        gen = int(getattr(self, "_load_gen", 0) or 0)
        key = (video_url, gen)
        if getattr(self, "_opening_channel_for", None) == key:
            return
        self._opening_channel_for = key

        def navigate(channel_url: str):
            if getattr(self, "_opening_channel_for", None) != key:
                return
            self._opening_channel_for = None
            if (
                str(getattr(self, "_last_video_url", "") or "") != video_url
                or int(getattr(self, "_load_gen", 0) or 0) != gen
                or not self._is_screen_active()
            ):
                return
            safe_url = ydlh._validated_youtube_channel_url(channel_url)
            if not safe_url:
                ma.log("[CHANNEL] verified channel URL unavailable")
                return
            try:
                app = App.get_running_app()
                root = getattr(app, "root", None)
                if root is None or not hasattr(root, "set_screen"):
                    ma.log("[CHANNEL] Web tab unavailable")
                    return
                # Navigate using the existing WebView rather than creating
                # an external browser or handling Android intents.
                root.set_screen("web")
                ma.webview_show(safe_url)
                ma.log("[CHANNEL] opened verified YouTube author")
            except Exception as exc:
                ma.log(f"[CHANNEL] navigation failed: {type(exc).__name__}")

        def lookup():
            try:
                channel_url = ydlh._channel_url_from_oembed(video_url)
            except Exception as exc:
                ma.log(f"[CHANNEL] author URL lookup failed: {type(exc).__name__}")
                channel_url = ""
            Clock.schedule_once(
                lambda _dt, url=channel_url: navigate(url), 0
            )

        threading.Thread(
            target=lookup, name="pymusic-channel-navigation", daemon=True
        ).start()

    def _sync_ui_loaded(self):
        self._queue_metadata_layout(0.025)
        _player_likes_ensure_and_tune(self)
        current = str(getattr(self, "_last_video_url", "") or "")
        if current:
            _player_likes_ensure_likes_async(self, current)
        try:
            self.ids.audio_title.text = self._title or ""
            views_lbl = self.ids.get("audio_views")
            if views_lbl:
                age = _format_published_age(self._published_date)
                views_lbl.text = " • ".join(
                    part for part in (self._views_text, age) if part
                )
            ch_lbl = self.ids.get("audio_channel")
            if ch_lbl:
                ch_lbl.text = self._channel or ""
            self.ids.audio_thumbnail.source = self._thumb or ""
            self.ids.current_time_label.text = "0:00"
            self.ids.total_time_label.text = "0:00"
            self.ids.progress_slider.value = 0
            self.ids.progress_slider.max = 1
            self.ids.repeat_btn.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
            inline_repeat = self.ids.get("repeat_inline_btn")
            if inline_repeat:
                inline_repeat.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
            auto_btn = self.ids.get("autoskip_btn")
            if auto_btn:
                auto_btn.source = "ico/icoauto_on.png" if self._auto_skip else "ico/icoauto_off.png"
            fav_btn = self.ids.get("favorite_btn")
            if fav_btn:
                fav_btn.source = "ico/icofavorite_active.png" if self._favorite else "ico/icofavorite.png"
            # кнопка відео
            vid_btn = self.ids.get("vid")
            if vid_btn:
                vid_btn.icon = "ico/icovid_on.png" if self._video_enabled else "ico/icovid_off.png"
            avatar = self.ids.get("channel_avatar")
            if avatar:
                src = self._channel_thumb_local or self._channel_thumb or ""
                if src:
                    if src.startswith("http"):
                        sep = "&" if "?" in src else "?"
                        src = f"{src}{sep}ts={int(time.time())}"
                    avatar.source = src
                    avatar.opacity = 1
                    try:
                        avatar.reload()
                    except Exception:
                        pass
                else:
                    avatar.source = ""
                    avatar.opacity = 0
                    try:
                        avatar.reload()
                    except Exception:
                        pass
        except Exception:
            pass

        # Оновлення аватара окремо, щоб не губилось через помилки в інших id.
        try:
            avatar = self.ids.get("channel_avatar")
            if avatar:
                src = self._channel_thumb_local or self._channel_thumb or ""
                if src:
                    avatar.source = src
                    avatar.opacity = 1
                    try:
                        avatar.reload()
                    except Exception:
                        pass
                else:
                    avatar.source = ""
                    avatar.opacity = 0
        except Exception:
            pass

        # Не вимикаємо відео при кожному UI refresh.
        # Інакше метадані/оновлення заголовка глушать відеоряд.
        try:
            thumb = self.ids.get("audio_thumbnail")
            if thumb:
                thumb.opacity = 0.0 if self._video_active else 1.0
        except Exception:
            pass
        Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), 0)


    def _set_video_mode_core(self, video_on: bool):
        """
        video_on = True  -> відео поверх, мініатюра прозора
        video_on = False -> тільки мініатюра, без відео-ряду
        """
        if video_on and not self._is_screen_active():
            video_on = False
        self._video_active = bool(video_on)
        try:
            if not video_on:
                ma._mp_set_volume(1.0)
        except Exception:
            pass
        if not video_on:
            self._set_video_controls_visible(False, auto_hide=False)
        try:
            thumb = self.ids.get("audio_thumbnail")
            if thumb:
                thumb.opacity = 0.0 if video_on else 1.0
        except Exception:
            pass
        if not video_on:
            try:
                vp = self._video_player
                if vp is not None:
                    if getattr(self, "_navigating_away_from_video", False):
                        # WebView/SDL transitions must not wait for Android
                        # MediaPlayer.stop/release on the UI thread. Hide
                        # immediately and clean up after the destination is
                        # already visible. Guard against a quick return/play.
                        vp.hide_native_views()
                        leaving_gen = int(getattr(vp, "_play_gen", -1))

                        def finish_video_release(_dt):
                            if (
                                not self._is_screen_active()
                                and int(getattr(vp, "_play_gen", -2)) == leaving_gen
                            ):
                                vp.stop()

                        Clock.schedule_once(finish_video_release, 0.30)
                    else:
                        vp.stop()
            except Exception as exc:
                print("[PLAYER-NAV] native video release failed:", exc)

    def _set_video_mode(self, video_on: bool):
        if video_on:
            return self._set_video_mode_core( True)

        was_master = bool(
            getattr(self, "_pymusic_muxed_video_master", False)
        )
        was_pending = bool(
            getattr(self, "_pymusic_muxed_handoff_pending", False)
        )

        if was_master or was_pending:
            self._pymusic_muxed_sync_gen = int(
                getattr(self, "_pymusic_muxed_sync_gen", 0) or 0
            ) + 1
            self._pymusic_muxed_handoff_pending = False

            audio = getattr(ma, "android_player", None)
            vp = getattr(self, "_video_player", None)
            native_video = getattr(vp, "player", None) if vp else None

            try:
                audio_pos = int(audio.getCurrentPosition() or 0) if audio else 0
            except Exception:
                audio_pos = 0
            try:
                video_pos = (
                    int(native_video.getCurrentPosition() or 0)
                    if native_video is not None
                    else 0
                )
            except Exception:
                video_pos = 0

            # Make the already-running shadow audio audible before the
            # SurfaceView/video player is hidden or stopped.
            _player_set_audio_volume(audio, 1.0)
            try:
                if (
                    audio is not None
                    and getattr(self, "_playback_desired", False)
                    and not getattr(self, "_user_paused", False)
                    and not bool(audio.isPlaying())
                ):
                    audio.start()
            except Exception:
                try:
                    if (
                        getattr(self, "_playback_desired", False)
                        and not getattr(self, "_user_paused", False)
                    ):
                        ma._mp_start()
                except Exception:
                    pass

            # Preserve the continuous audio clock.  Do not overwrite it
            # with the video position during a screen-off transition.
            if audio_pos > 0:
                self._resume_pos_ms = audio_pos
            elif video_pos > 0:
                self._resume_pos_ms = video_pos

            try:
                if native_video is not None:
                    native_video.setVolume(0.0, 0.0)
            except Exception:
                pass

            # Clear these before calling the V4 wrapper.  V4 will then only
            # hide the video UI and will skip its old seek/restart handoff.
            self._pymusic_muxed_video_master = False
            self._pymusic_muxed_handoff_pending = False
            print(
                "[PLAYBACK-V5] seamless audio handoff "
                f"audio={audio_pos} video={video_pos} "
                f"drift={video_pos - audio_pos}"
            )

        return self._set_video_mode_core( False)


    def hide_video_overlay_fast(self):
        # Stale geometry callbacks can run after leaving this screen. Avoid
        # re-stopping the native MediaPlayer after on_pre_leave already did so.
        if not (
            self._video_active
            or self._video_controls_visible
            or bool(getattr(self, "_pymusic_muxed_video_master", False))
            or bool(getattr(self, "_pymusic_muxed_handoff_pending", False))
        ):
            return
        self._video_was_active = False
        self._video_resume_url = None
        self._video_resume_gen = -1
        self._set_video_mode(False)

    def _normalize_related(self, items):
        """Keep YouTube's real card metadata, while normalizing video IDs.

        Previous implementation dropped duration, view count and upload age.
        That caused thumbnails with no time badge and metadata-free rows
        even though the watch-next API had supplied the relevant fields.
        """
        out = []
        seen = set()
        current_vid = ""
        try:
            current_vid = Playlist._normalize_video_id(self._last_video_url or "")
        except Exception:
            current_vid = ""

        for e in items or []:
            if not isinstance(e, dict):
                continue
            raw_id = str(e.get("id") or e.get("video_id") or "")
            raw_url = str(e.get("url") or e.get("webpage_url") or raw_id or "")
            raw_thumb = str(e.get("thumbnail") or e.get("thumb") or "")

            vid = (
                Playlist._normalize_video_id(raw_url)
                or Playlist._normalize_video_id(raw_id)
                or Playlist._video_id_from_thumb_url(raw_thumb)
            )
            if current_vid and vid == current_vid:
                continue

            url = Playlist._normalize_video_url(raw_url or vid)
            if not url:
                continue
            key = vid or url
            if key in seen:
                continue
            seen.add(key)

            title = str(e.get("title") or "").strip()
            channel = _verified_channel_name(
                e.get("uploader") or e.get("channel") or e.get("creator"),
                title,
            )
            thumb = Playlist._normalize_img_url(raw_thumb)
            if vid:
                thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"

            # Use official watch-next text when present; numeric yt-dlp
            # counts are a supported fallback. Do not synthesize unknown
            # metrics or scrape eight extra pages in the UI worker.
            views_text = e.get("views_text") or e.get("view_count_text") or ""
            views_text = str(views_text).strip() if isinstance(views_text, str) else ""
            if not views_text and e.get("view_count") is not None:
                views_text = self._fmt_views(e["view_count"])

            published_text = (
                e.get("published_text")
                or e.get("published_time_text")
                or ""
            )
            published_text = (
                str(published_text).strip()
                if isinstance(published_text, str) else ""
            )
            if not published_text:
                published_text = _format_published_age(
                    e.get("upload_date") or e.get("release_date")
                )

            out.append(
                {
                    "url": url,
                    "title": title,
                    "channel": channel,
                    "thumb": thumb,
                    "video_id": vid,
                    "duration": _related_duration_label(
                        e.get("duration")
                        or e.get("duration_string")
                        or e.get("length_seconds")
                    ),
                    "views_text": views_text,
                    "published_text": published_text,
                }
            )
            if len(out) >= 12:
                break
        return out

    def _build_default_channel_thumb(self) -> str:
        return ""

    def _sync_ui_loading(self):
        self._queue_metadata_layout(0.025)
        _player_likes_ensure_and_tune(self)
        _player_likes_reset_on_change(self)
        Clock.schedule_once(lambda dt: self._sync_ui_loaded(), 0)

    def _sync_thumb_now(self):
        try:
            if self._thumb:
                try:
                    Cache.remove("kv.image", self.ids.audio_thumbnail.source)
                except Exception:
                    pass
                src = self._thumb
                if src.startswith("http"):
                    sep = "&" if "?" in src else "?"
                    src = f"{src}{sep}ts={int(time.time())}"
                self.ids.audio_thumbnail.source = src
        except Exception:
            pass

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
        try:
            vbtn = self.ids.get("video_play_btn")
            if vbtn:
                vbtn.icon = "pause" if playing else "play"
        except Exception:
            pass
        try:
            if self._video_player and hasattr(self._video_player, "set_native_playing"):
                self._video_player.set_native_playing(bool(playing))
        except Exception:
            pass

        try:
            pos = ma.android_player.getCurrentPosition() if (ma.android_player and ma._is_prepared()) else 0
            dur = ma.android_player.getDuration() if (ma.android_player and ma._is_prepared()) else None
            ma.update_media_session_state(
                bool(playing),
                position_ms=int(pos or 0),
                duration_ms=(int(dur) if dur else None),
                can_seek=True,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Playing audio",
                subtitle=self._channel or "YouTube",
                is_playing=bool(playing),
                session_token=token,
                large_icon_path=self._channel_thumb_local,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
        except Exception:
            pass

    # ==================== progress / expiry ====================

    def _schedule_progress(self):
        if self._update_ev:
            Clock.unschedule(self._tick)
        self._update_ev = Clock.schedule_interval(self._tick, 0.5)
        self._start_buffer_watchdog()

    def _recover_stream(self, reason: str, pos: int, *, force_fresh: bool = False):
        # MediaPlayer error callbacks run in worker threads. Recovery can skip
        # tracks and rebuild visible Kivy widgets; marshal that path to Kivy.
        # In background mode the Clock may be paused, so leave audio-only
        # recovery on the worker and keep UI mutations guarded separately.
        if (threading.current_thread() is not threading.main_thread()
                and not bool(getattr(self, "_app_in_background", False))):
            Clock.schedule_once(
                lambda _dt: self._recover_stream(reason, pos, force_fresh=force_fresh), 0
            )
            return
        now = time.time()
        if (now - self._last_stream_recover_ts) < 3.0:
            return
        self._last_stream_recover_ts = now
        self._resume_pos_ms = int(pos or 0)
        print(f"[AUDIO] recover stream ({reason}) pos={self._resume_pos_ms} force_fresh={force_fresh}")

        cur_url = str(self._last_video_url or "")
        if cur_url and cur_url == self._recover_loop_url and (now - self._recover_loop_ts) < 90.0:
            self._recover_loop_hits += 1
        else:
            self._recover_loop_url = cur_url
            self._recover_loop_hits = 1
        self._recover_loop_ts = now

        # Захист від нескінченного циклу ресетів одного й того ж треку.
        if self._recover_loop_hits >= 4:
            print("[AUDIO] recover loop detected, skipping track")
            self._recover_loop_hits = 0
            if self._auto_skip and self._advance_to_next_track():
                return
            self._playback_desired = False
            self._user_paused = True
            Clock.schedule_once(lambda dt: self._ui_set_playing(False), 0)
            return

        if force_fresh and self._last_video_url:
            self._discard_current_cached_stream()
            self._url_cache.pop(self._last_video_url, None)
        elif self._last_video_url and self._try_start_cached_audio(self._last_video_url, self._load_gen):
            return
        if self._last_video_url:
            threading.Thread(
                target=lambda: self._extract_and_start_gen(self._last_video_url, self._load_gen),
                daemon=True,
            ).start()

    def _tick(self, dt):
        if not ma.android_player:
            return
        try:
            pos = ma.android_player.getCurrentPosition() or 0
            dur = ma.android_player.getDuration() or 0
            if dur > 0:
                self._last_good_dur_ms = dur
                self._bad_dur_hits = 0
            else:
                self._bad_dur_hits += 1
                if self._last_good_dur_ms > 0:
                    dur = self._last_good_dur_ms
            self.ids.current_time_label.text = self._fmt_ms(pos)
            self.ids.total_time_label.text = self._fmt_ms(dur or 0)
            self.ids.progress_slider.max = int((dur or 1) / 1000)
            if not self._is_scrubbing:
                self.ids.progress_slider.value = int(pos / 1000)
            try:
                if self._video_player:
                    self._video_player.set_native_progress(int(pos or 0), int(dur or 0))
            except Exception:
                pass
        except Exception:
            try:
                pos = ma.android_player.getCurrentPosition() or 0
                dur = ma.android_player.getDuration() or 0
            except Exception:
                return

        # Start-to-first-real-progress is the metric users perceive.
        # MediaPlayer.start() can return before a single frame is audible.
        # Measure the *actual* time from Play to position advancing.
        # This distinguishes decoder rebuffering from a slow UI callback.
        try:
            resumed_at = float(getattr(self, "_resume_requested_t0", 0) or 0)
            before = int(getattr(self, "_resume_probe_pos_ms", 0) or 0)
            if (
                resumed_at and self._playback_desired and not self._user_paused
                and int(pos or 0) >= before + 250
            ):
                print(
                    f"[LATENCY] pause-to-first-progress "
                    f"{time.monotonic() - resumed_at:.2f}s"
                )
                self._resume_requested_t0 = 0.0
        except Exception:
            pass

        try:
            gen = int(self._load_gen)
            if (
                int(pos or 0) > 0
                and self._playback_desired
                and not self._user_paused
                and getattr(self, "_audio_first_progress_gen", -1) != gen
            ):
                self._audio_first_progress_gen = gen
                started = float(getattr(self, "_audio_play_request_t0", 0) or 0)
                if started:
                    print(
                        f"[LATENCY] first real audio progress after "
                        f"{time.monotonic() - started:.2f}s "
                        f"format={self._stream_format_id!r}"
                    )
        except Exception:
            pass

        if (
            self._bad_dur_hits >= 4
            and self._playback_desired
            and not self._user_paused
            and not self._app_in_background
            and (time.time() - float(self._last_bg_resume_ts or 0.0)) >= 6.0
        ):
            self._recover_stream("duration=0", pos)
            self._bad_dur_hits = 0

        try:
            if self._maybe_handle_track_end(pos, dur):
                return
        except Exception:
            pass

        try:
            gen = int(self._load_gen)
            remaining = int(dur or 0) - int(pos or 0)
            if (
                self._auto_skip
                and not ma.is_metered_connection()
                and self._playback_desired
                and not self._user_paused
                and int(pos or 0) > 1000
                and 5000 < remaining <= 25000
                and self._next_prefetch_requested_gen != gen
            ):
                self._next_prefetch_requested_gen = gen
                self._prefetch_next_track_audio()
        except Exception:
            pass

        try:
            ma.update_media_session_state(
                bool(ma.android_player and ma.android_player.isPlaying()),
                position_ms=int(pos or 0),
                duration_ms=int(dur or 0),
                can_seek=True,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
        except Exception:
            pass

        # Не робимо частий hard seek відео до аудіо: Android MediaPlayer після
        # seekTo() флашить декодер, що виглядає як ресет відеоряду.
        try:
            if (
                self._video_enabled
                and self._video_active
                and self._video_player
                and self._playback_desired
                and not self._user_paused
                and not self._is_scrubbing
                and not self._app_in_background
                and ma.android_player
                and ma._is_prepared()
                and ma.android_player.isPlaying()
            ):
                now = time.time()
                video_pos = None
                try:
                    video_pos = self._video_player.get_current_position()
                except Exception:
                    video_pos = None
                drift = abs(int(pos or 0) - int(video_pos or 0)) if video_pos is not None else 0
                if drift >= 5000 and (now - float(self._last_video_sync_ts or 0.0)) >= 15.0:
                    print(f"[VIDEO] drift resync audio={int(pos or 0)} video={int(video_pos or 0)} drift={drift}")
                    self._video_player.seek_to(int(pos or 0))
                    self._last_video_sync_ts = now
        except Exception:
            pass

    def _schedule_expiry(self):
        # The current MediaPlayer already owns an open stream. Recreating
        # it proactively at CDN URL expiration interrupts playback, downloads
        # the same bytes again and may thrash when the network is weak.
        # Signed URL caches are checked on *reuse*. Native playback errors
        # trigger a fresh extraction when actually needed.
        return None

    # ==================== buttons ====================

    def toggle_play_pause(self, *a):
        if not self._debounce():
            return
        # During HTTP buffering MediaPlayer.isPlaying() can be False
        # although the user explicitly wants playback. Use logical state
        # for the button so tapping Pause actually pauses the pending stream.
        if self._playback_desired and not self._user_paused:
            self._pause_playback()
        else:
            self._resume_playback()

    def toggle_repeat(self, *a):
        self.repeat = not self.repeat
        try:
            self.ids.repeat_btn.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
            inline_repeat = self.ids.get("repeat_inline_btn")
            if inline_repeat:
                inline_repeat.source = "ico/icorepeat_active.png" if self.repeat else "ico/icorepeat.png"
        except Exception:
            pass
        # Refresh Android 13's fourth media button immediately, including
        # when the song is paused and PlaybackState position is unchanged.
        if self._last_video_url:
            self._publish_media_metadata_now()

    def toggle_autoskip(self, *a):
        self._auto_skip = not self._auto_skip
        try:
            src = "ico/icoauto_on.png" if self._auto_skip else "ico/icoauto_off.png"
            for button_id in ("autoskip_btn", "autoplay_btn"):
                btn = self.ids.get(button_id)
                if btn:
                    btn.source = src
        except Exception:
            pass
        Clock.schedule_once(lambda _dt: self._render_current_lower_v4(), 0)


    def toggle_favorite(self, *a):
        url = str(self._last_video_url or "")
        if not url:
            return
        self._favorite = not self._favorite
        try:
            btn = self.ids.get("favorite_btn")
            if btn:
                btn.source = "ico/icofavorite_active.png" if self._favorite else "ico/icofavorite.png"
        except Exception:
            pass
        if self._favorite:
            item = {
                "url": url,
                "title": self._title or "",
                "channel": self._channel or "",
                "thumb": self._thumb or "",
                "art_path": self._art_path or "",
                "added_at": int(time.time()),
            }
            try:
                cached = self._find_cached_audio(url)
                if cached:
                    item["cache_path"] = cached
            except Exception:
                pass
            upsert_favorite(item)
            self._cache_current_favorite_async(url)
        else:
            try:
                cached = self._find_cached_audio(url)
                if cached and os.path.exists(cached):
                    os.remove(cached)
            except Exception:
                pass
            update_recent_cache(url, None)
            update_favorite_cache(url, None)
            remove_favorite(url)
        # Update the rightmost Favorite action with the user's original
        # selected/unselected PNG, without waiting for the playback ticker.
        self._publish_media_metadata_now()

    def _cache_current_favorite_async(self, url: str):
        if not url or url in self._fav_cache_inflight:
            return
        self._fav_cache_inflight.add(url)

        def _job():
            try:
                cached = self._find_cached_audio(url)
                if cached:
                    update_favorite_cache(url, cached)
                    return
                stream_url = str(self._stream_url or "")
                headers = dict(self._headers or {})
                if not stream_url or stream_url.startswith("file://"):
                    try:
                        info = ydlh.extract_audio_info(url, prefer_compat=self._prefer_compat_audio)
                        stream_url = str(info.get("audio_url") or "")
                        headers = dict(info.get("http_headers") or headers)
                    except Exception:
                        stream_url = ""
                if stream_url:
                    path = self._download_audio_locally(url, stream_url, headers)
                    if path:
                        update_favorite_cache(url, path)
            finally:
                self._fav_cache_inflight.discard(url)

        threading.Thread(target=_job, daemon=True).start()

    def seek(self, value):
        try:
            ms = int(value * 1000)
            ma._mp_seek_to(ms)
            self._resume_pos_ms = ms
            try:
                if self._video_player and self._video_enabled:
                    self._video_player.seek_to(ms)
            except Exception:
                pass
        except Exception:
            pass

    def vid(self, *a):
        """Обробка кнопки MDIconButton id: vid"""
        if not self._debounce(0.2):
            return
        self._video_enabled = not self._video_enabled
        try:
            vid_btn = self.ids.get("vid")
            if vid_btn:
                vid_btn.icon = "ico/icovid_on.png" if self._video_enabled else "ico/icovid_off.png"
        except Exception:
            pass

        if self._video_enabled:
            # вмикаємо відео поверх поточного аудіо
            if not self._last_video_url:
                return
            if not self._ensure_video_player():
                return
            Clock.schedule_once(self._align_video_to_thumb, 0)
            threading.Thread(
                target=lambda: self._auto_video_for_current(self._load_gen, sync_start=False),
                daemon=True,
            ).start()
        else:
            # вимикаємо відео, залишаємо тільки аудіо
            try:
                if self._video_player:
                    self._video_player.stop()
            except Exception:
                pass
            Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

    # ==================== navigation ====================

    def go_back(self, *a):
        self.stop_audio()
        try:
            from kivy.app import App
            app = App.get_running_app()
            root = getattr(app, "root", None)
            if root and hasattr(root, "go_to_active_tab"):
                root.go_to_active_tab()
            else:
                self.manager.current = "web"
        except Exception:
            pass

    # ==================== media actions ====================

    def _advance_to_next_track_playlist_only(self) -> bool:
        try:
            print(
                f"[AUDIO] advance next playlist_len={len(self.playlist) if self.playlist else 0} index={getattr(self.playlist, 'index', -1)} bg={self._app_in_background}"
            )
        except Exception:
            pass
        if self.playlist and len(self.playlist) > 1:
            track = self.playlist.next()
            if not track:
                print("[AUDIO] advance next failed: playlist.next() returned empty")
                return False
            try:
                print(f"[AUDIO] advance next track title={track.get('title')!r} url={track.get('url')!r}")
            except Exception:
                pass
            self.play_audio(
                track["url"],
                track["title"],
                track["channel"],
                track.get("thumb") or "",
                clear_playlist=False,
                hard_reset=True,
            )
            return True
        print("[AUDIO] advance next fallback: playlist missing or single")
        return self._try_playlist_refresh_next()

    def _advance_to_next_track(self) -> bool:
        if bool(getattr(self, "_playlist_play_current_before_next_v1", False)):
            self._playlist_play_current_before_next_v1 = False
            try:
                playlist = getattr(self, "playlist", None)
                track = playlist.current() if playlist else None
                if track:
                    print(
                        f"[PLAYLIST-FIRST-V2] playing first queued track "
                        f"index={getattr(playlist, 'index', 0)} "
                        f"url={track.get('url')!r}"
                    )
                    self.play_audio(
                        track["url"],
                        track.get("title") or "",
                        track.get("channel") or "",
                        track.get("thumb") or "",
                        clear_playlist=False,
                        hard_reset=True,
                    )
                    return True
            except Exception as exc:
                print("[PLAYLIST-FIRST-V2] first queued track failed:", exc)
        return bool(self._advance_to_next_track_base())


    def _advance_to_next_track_base(self) -> bool:
        self._current_ensure_state()
        try:
            playlist = getattr(self, "playlist", None)
            if playlist and len(playlist) > 1:
                return bool(self._advance_to_next_track_playlist_only())
        except Exception:
            pass
        related = list(getattr(self, "_related_items", []) or [])
        if related:
            return self._current_play_related_item( related[0])
        if _current_video_url_of(self):
            print("[CURRENT-V4] auto-next waiting for related videos")
            self._playback_desired = True
            self._user_paused = False
            self._ensure_related_current_v4( force=True, auto_advance=True)
            return True
        return bool(self._advance_to_next_track_playlist_only())


    def _queue_auto_next(self) -> bool:
        try:
            gen = int(getattr(self, "_load_gen", 0) or 0)
        except Exception:
            gen = 0

        if getattr(self, "_pymusic_next_lock", None) is None:
            self._pymusic_next_lock = threading.Lock()

        with self._pymusic_next_lock:
            if int(getattr(self, "_pymusic_next_started_gen", -1)) == gen:
                return True
            self._pymusic_next_started_gen = gen

        def run():
            try:
                # If another action already moved to a new generation, this
                # stale completion event must not advance again.
                if int(getattr(self, "_load_gen", -1)) != gen:
                    return
                print(
                    f"[BG-NEXT] advancing playlist bg={bool(getattr(self, '_app_in_background', False))} gen={gen}"
                )
                if self._advance_to_next_track():
                    return

                self._playback_desired = False
                self._user_paused = True
                Clock.schedule_once(lambda _dt: self._ui_set_playing(False), 0)
            except Exception as exc:
                print("[BG-NEXT] advance failed:", exc)

        if bool(getattr(self, "_app_in_background", False)):
            threading.Thread(target=run, daemon=True).start()
        else:
            Clock.schedule_once(lambda _dt: run(), 0)
        return True

    def _act_next(self, *a):
        now = time.time()
        if (now - self._last_next_action_ts) < 0.45:
            return
        self._last_next_action_ts = now
        self._advance_to_next_track()

    def _act_prev(self, *a):
        if not self.playlist:
            return
        try:
            cur = ma.android_player.getCurrentPosition() if ma.android_player else 0
        except Exception:
            cur = 0
        # як і раніше: якщо >5 секунд - просто перемотуємо на початок
        if cur and cur > 5000:
            try:
                ma._mp_seek_to(0)
                self._resume_pos_ms = 0
                return
            except Exception:
                pass

        track = self.playlist.prev()
        if not track:
            return
        self.play_audio(
            track["url"],
            track["title"],
            track["channel"],
            track.get("thumb") or "",
            clear_playlist=False,
            hard_reset=True,
        )

    def _act_play(self, *a):
        self._last_media_ts = time.time()
        if self._user_paused or not self._playback_desired:
            self._resume_playback()

    def _act_pause(self, *a):
        self._last_media_ts = time.time()
        if self._playback_desired and not self._user_paused:
            self._pause_playback()

    def _act_toggle(self, *a):
        self._last_media_ts = time.time()
        if self._playback_desired and not self._user_paused:
            self._pause_playback()
        else:
            self._resume_playback()

    def _act_repeat(self, *a):
        self._last_media_ts = time.time()
        self.toggle_repeat()

    def _act_favorite(self, *a):
        self._last_media_ts = time.time()
        self.toggle_favorite()

    def _pause_playback(self):
        self._mark_java_transport(True)
        self._user_paused = True
        self._playback_desired = False
        self._resume_requested_t0 = 0.0
        try:
            self._resume_pos_ms = ma.android_player.getCurrentPosition() or 0
        except Exception:
            pass
        try:
            ma._mp_pause()
        except Exception:
            pass
        try:
            if self._video_player:
                self._video_player.pause()
        except Exception:
            pass
        self._ui_set_playing(False)

    def _resume_playback(self):
        self._mark_java_transport(False)
        self._user_paused = False
        self._playback_desired = True
        self._resume_requested_t0 = time.monotonic()
        self._resume_probe_pos_ms = int(getattr(self, "_resume_pos_ms", 0) or 0)

        # Resume the existing prepared audio decoder and its buffered bytes.
        # Do not seek, rebuild the video stream, or run yt-dlp on a normal
        # Play press. Background/surface recovery is handled separately.
        try:
            if ma.android_player is not None:
                ma._mp_start()
            elif self._last_video_url:
                # The player was actually released (not merely paused).
                # This is the only case where fresh extraction is required.
                threading.Thread(
                    target=lambda: self._extract_and_start_gen(
                        self._last_video_url, self._load_gen
                    ),
                    name="pymusic-resume-missing-audio",
                    daemon=True,
                ).start()
        except Exception as exc:
            print("[AUDIO] native resume command failed:", type(exc).__name__)

        try:
            if (
                self._video_player is not None
                and self._video_enabled and self._video_active
                and getattr(self._video_player, "player", None) is not None
            ):
                self._video_player.resume()
            # The old 0.35s forced seekTo on every resume needlessly flushed
            # remote video buffers. Periodic drift correction remains active.
        except Exception as exc:
            print("[VIDEO] optional resume failed:", type(exc).__name__)
        try:
            self._ui_set_playing(True)
        except Exception as exc:
            print("[AUDIO] resume UI update failed:", type(exc).__name__)

    def _force_video_resync(self):
        try:
            if (
                self._app_in_background
                or not self._video_enabled
                or not self._video_active
                or not self._video_player
                or not self._playback_desired
                or self._user_paused
            ):
                return
            if not (ma.android_player and ma._is_prepared() and ma.android_player.isPlaying()):
                return
            pos = self._audio_pos_ms()
            video_pos = None
            try:
                video_pos = self._video_player.get_current_position()
            except Exception:
                video_pos = None
            if video_pos is not None and abs(int(pos or 0) - int(video_pos or 0)) < 1500:
                return
            if pos >= 0:
                self._video_player.seek_to(int(pos))
                self._last_video_sync_ts = time.time()
        except Exception:
            pass

    # ==================== stop / cleanup ====================

    def stop_audio(self, *a):
        self._mark_java_transport(True)
        self._playback_desired = False
        try:
            if self._update_ev:
                Clock.unschedule(self._tick)
                self._update_ev = None
            if self._refresh_ev:
                self._refresh_ev.cancel()
                self._refresh_ev = None
        except Exception:
            pass
        self._stop_buffer_watchdog()
        try:
            ma._mp_reset_release()
            ma.release_wake_lock()
        except Exception:
            pass

        try:
            if self._video_player:
                self._video_player.stop()
        except Exception:
            pass

        try:
            token = self._media_session.getSessionToken() if self._media_session else None
            ma.create_or_update_media_notification(
                title=self._title or "Stopped",
                subtitle=self._channel or "",
                is_playing=False,
                session_token=token,
                large_icon_path=self._channel_thumb_local,
                repeat_enabled=bool(self.repeat),
                favorite_enabled=bool(self._favorite),
            )
        except Exception:
            pass

        self._resume_pos_ms = 0
        self._ui_set_playing(False)
        Clock.schedule_once(lambda dt: self._set_video_mode(False), 0)

    # ==================== utils ====================

    def _fmt_ms(self, ms):
        s = int(ms / 1000)
        m, s = divmod(s, 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    def _fmt_views(self, count):
        try:
            n = int(count)
        except Exception:
            return ""
        if n >= 1_000_000_000:
            text = f"{n / 1_000_000_000:.1f} млрд переглядів"
        elif n >= 1_000_000:
            text = f"{n / 1_000_000:.1f} млн переглядів"
        elif n >= 1_000:
            text = f"{n / 1_000:.1f} тис. переглядів"
        else:
            text = f"{n} переглядів"
        return text.replace(".", ",")

    def _debounce(self, delay: float = 0.25) -> bool:
        now = time.time()
        if now - self._last_click < delay:
            return False
        self._last_click = now
        return True
