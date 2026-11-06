
# ytdlp_helpers.py
from __future__ import annotations

import html
import json
import os
import re
import ssl
import threading
import time
import certifi
import urllib.request
import urllib.parse as urlparse
from typing import Any, Dict, Optional, Tuple, List

import yt_dlp.cache
from yt_dlp import YoutubeDL


# ========================== CACHE OFF ==========================
# YouTube часто міняє сторінки, кеш у APK дає "застряглі" сигнатури/формати.
yt_dlp.cache.store  = lambda *a, **k: None
yt_dlp.cache.load   = lambda *a, **k: None
yt_dlp.cache.remove = lambda *a, **k: None


# A small, *in-memory* TTL cache of signed audio stream URLs/metadata.
# This avoids repeated yt-dlp client requests for replay/track switches,
# but does not store copyrighted media bytes or pretend URLs work offline.
_AUDIO_INFO_CACHE: dict[str, tuple[float, dict]] = {}
_AUDIO_INFO_CACHE_LOCK = threading.RLock()
_AUDIO_INFO_CACHE_MAX = 24
_AUDIO_INFO_CACHE_TTL = 120.0

# Learn when the Android yt-dlp player client only offers a muxed format 18.
# Once observed, try TV's audio-only AAC first on subsequent tracks, falling
# back to Android if TV becomes unavailable. This avoids a redundant failed
# extractor request without slowing down healthy Android audio clients.
_AUDIO_CLIENT_HINT_LOCK = threading.RLock()
# On Android 13 the "android" client consistently returned only muxed format
# 18 (no audio-only sources), wasting ~6 seconds before TV's AAC URL.
# Start with TV's proven audio-only source; fall back to android when needed.
_ANDROID_CLIENT_MUXED_ONLY = True


def _get_cached_audio_info(video_url: str) -> dict | None:
    video_id = _extract_video_id_from_url(video_url)
    if not video_id:
        return None
    with _AUDIO_INFO_CACHE_LOCK:
        entry = _AUDIO_INFO_CACHE.get(video_id)
        if not entry:
            return None
        expiry, info = entry
        if expiry <= time.monotonic():
            _AUDIO_INFO_CACHE.pop(video_id, None)
            return None
        # A fresh dict avoids cross-thread mutation of metadata fields.
        return dict(info)


def _cache_audio_info(video_url: str, result: dict) -> None:
    video_id = _extract_video_id_from_url(video_url)
    source = str((result or {}).get("audio_url") or "")
    if not video_id or not source.startswith("https://"):
        return
    ttl = _AUDIO_INFO_CACHE_TTL
    expiry = (result or {}).get("expire_ts")
    try:
        if expiry is not None:
            ttl = min(ttl, max(0.0, float(expiry) - time.time() - 120))
    except (TypeError, ValueError, OverflowError):
        ttl = 0
    if ttl <= 10:
        return
    with _AUDIO_INFO_CACHE_LOCK:
        _AUDIO_INFO_CACHE[video_id] = (
            time.monotonic() + ttl, dict(result)
        )
        while len(_AUDIO_INFO_CACHE) > _AUDIO_INFO_CACHE_MAX:
            _AUDIO_INFO_CACHE.pop(next(iter(_AUDIO_INFO_CACHE)))


def get_cached_alternate_audio_source(
    video_url: str,
    failed_url: str,
    attempted_urls: set[str] | None = None,
) -> dict | None:
    """Find another already-extracted *audio-only* Googlevideo stream.

    Android MediaPlayer sometimes reports (-38, 0) immediately on a signed
    AAC URL. Re-running yt-dlp to receive the same format-140 URL costs
    5-10 seconds and may repeat the failure. Try an alternate audio-only
    source from the same in-memory TV format listing first, without an HTTP
    request. Never retry URLs already attempted in this playback generation.
    """
    cached = _get_cached_formats(video_url)
    if not cached:
        return None
    attempted = set(attempted_urls or ())
    attempted.add(str(failed_url or ""))
    now = time.time()
    options = []
    for fmt in cached.get("formats") or ():
        if not isinstance(fmt, dict):
            continue
        source = str(fmt.get("url") or "")
        if not source or source in attempted:
            continue
        try:
            parsed = urlparse.urlsplit(source)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not (
                host == "googlevideo.com" or host.endswith(".googlevideo.com")
            ):
                continue
        except (ValueError, TypeError):
            continue
        expiry = _parse_expire_ts(source)
        if expiry and expiry <= now + 120:
            continue
        if str(fmt.get("vcodec") or "none").lower() != "none":
            continue
        codec = str(fmt.get("acodec") or "none").lower()
        if codec == "none":
            continue
        format_id = str(fmt.get("format_id") or "")
        ext = str(fmt.get("ext") or "").lower()
        # Prefer another AAC source (format 139 is smaller, HE-AAC); use
        # Opus only as a last resort because device support can vary.
        if codec.startswith("mp4a") and ext in ("m4a", "mp4", "aac"):
            rank = 0 if format_id == "139" else 1
        elif codec.startswith("opus") and ext in ("webm", "opus"):
            rank = 2
        else:
            continue
        try:
            bitrate = float(fmt.get("abr") or fmt.get("tbr") or 0)
        except (ValueError, TypeError):
            bitrate = 0
        options.append((rank, bitrate, source, fmt))
    if not options:
        return None
    options.sort(key=lambda entry: (entry[0], entry[1]))
    _, _, source, fmt = options[0]
    return {
        "audio_url": source,
        "format_id": str(fmt.get("format_id") or ""),
        "http_headers": _best_effort_headers(
            fmt.get("http_headers"), {
                "User-Agent": _ANDROID_YT_UA,
                "Referer": "https://www.youtube.com",
            }
        ),
    }


def invalidate_audio_info_cache(video_url: str) -> None:
    """Forget a failed/expired CDN URL before recovery re-extracts it."""
    video_id = _extract_video_id_from_url(video_url)
    if video_id:
        with _AUDIO_INFO_CACHE_LOCK:
            _AUDIO_INFO_CACHE.pop(video_id, None)


# Cache only a bounded number of freshly extracted YouTube manifests. Audio
# and video workers often request exactly the same track within seconds.
_VIDEO_FORMAT_CACHE: dict[str, tuple[float, dict]] = {}
_VIDEO_FORMAT_CACHE_LOCK = threading.RLock()
_CHANNEL_NAME_CACHE: dict[str, tuple[float, str]] = {}
_CHANNEL_NAME_CACHE_LOCK = threading.RLock()
_CHANNEL_AUTHOR_URL_CACHE: dict[str, tuple[float, str]] = {}
_CHANNEL_AVATAR_URL_CACHE: dict[str, tuple[float, str]] = {}


def _cache_extracted_formats(video_url: str, info: dict) -> None:
    video_id = _extract_video_id_from_url(video_url)
    formats = (info or {}).get("formats") or []
    if not video_id or not formats:
        return
    # Never hold CDN links beyond their expiration or more than 3 minutes.
    ttl = 180.0
    for fmt in formats:
        url = str((fmt or {}).get("url") or "")
        expire = _parse_expire_ts(url)
        if expire:
            ttl = min(ttl, max(0.0, float(expire - time.time() - 120)))
    if ttl < 10:
        return
    entry = {
        "formats": [dict(x) for x in formats if isinstance(x, dict)],
        "thumbnail": str((info or {}).get("thumbnail") or ""),
    }
    # Capture the canonical channel link if yt-dlp already knows it, so a
    # tap on the current creator never needs an extra network request.
    author_url = _validated_youtube_channel_url(
        str((info or {}).get("channel_url")
            or (info or {}).get("uploader_url") or "")
    )
    if author_url:
        with _CHANNEL_NAME_CACHE_LOCK:
            _CHANNEL_AUTHOR_URL_CACHE[video_id] = (
                time.monotonic() + 3600, author_url
            )
            while len(_CHANNEL_AUTHOR_URL_CACHE) > 64:
                _CHANNEL_AUTHOR_URL_CACHE.pop(
                    next(iter(_CHANNEL_AUTHOR_URL_CACHE))
                )
    with _VIDEO_FORMAT_CACHE_LOCK:
        _VIDEO_FORMAT_CACHE[video_id] = (time.monotonic() + ttl, entry)
        while len(_VIDEO_FORMAT_CACHE) > 16:
            _VIDEO_FORMAT_CACHE.pop(next(iter(_VIDEO_FORMAT_CACHE)))


def _get_cached_formats(video_url: str) -> dict | None:
    video_id = _extract_video_id_from_url(video_url)
    if not video_id:
        return None
    with _VIDEO_FORMAT_CACHE_LOCK:
        record = _VIDEO_FORMAT_CACHE.get(video_id)
        if record and record[0] > time.monotonic():
            return record[1]
        _VIDEO_FORMAT_CACHE.pop(video_id, None)
    return None


# ========================== LOGGER ============================
class YDLLogger:
    def debug(self, msg: str):
        # не шумимо; залишимо важливі маркери
        if ("Downloading webpage" in msg
            or "player =" in msg
            or "nsig extraction failed" in msg
            or "Falling back to generic n function search" in msg):
            print(f"[YDL] {msg}")

    def warning(self, msg: str):
        print(f"[YDL WARN] {msg}")

    def error(self, msg: str):
        print(f"[YDL ERROR] {msg}")


# ========================== HELPERS ===========================
_ANDROID_YT_UA = (
    # UA схожий на офіційний клієнт YouTube для Android
    "com.google.android.youtube/19.20.0 (Linux; U; Android 12) gzip"
)
_ANDROID_WEB_UA = (
    "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome Mobile Safari/537.36"
)


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.environ.get(name)
    except Exception:
        raw = None
    if raw is None:
        return bool(default)
    v = str(raw).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return bool(default)


def _normalize_img_url(url: str) -> str:
    u = html.unescape(url or "")
    u = u.replace("\\u0026", "&").replace("\\/", "/")
    if u.startswith("//"):
        u = "https:" + u
    return u


def _extract_video_id_from_url(url: str) -> str:
    try:
        u = _normalize_img_url(str(url or ""))
        parsed = urlparse.urlparse(u)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        qs = urlparse.parse_qs(parsed.query or "")
        if qs.get("v"):
            return str(qs["v"][0] or "")[:20]
        if "youtu.be" in host:
            return path.strip("/").split("/")[0][:20]
        m = re.search(r"/(?:shorts|live|embed)/([A-Za-z0-9_-]{6,20})", path)
        if m:
            return m.group(1)
        m = re.search(r"(?:v=|/)([A-Za-z0-9_-]{11})(?:[&/?#]|$)", u)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def _json_after_marker(text: str, marker: str) -> Optional[Any]:
    """Витягує JSON-обʼєкт після `marker` з HTML YouTube без важких залежностей."""
    try:
        pos = (text or "").find(marker)
        if pos < 0:
            return None
        start = text.find("{", pos)
        if start < 0:
            return None
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    except Exception:
        return None
    return None


def _yt_text(node: Any) -> str:
    try:
        if not node:
            return ""
        if isinstance(node, str):
            return html.unescape(node)
        if isinstance(node, dict):
            if node.get("simpleText"):
                return html.unescape(str(node.get("simpleText") or ""))
            runs = node.get("runs") or []
            if isinstance(runs, list):
                return html.unescape("".join(str(r.get("text") or "") for r in runs if isinstance(r, dict)))
            content = node.get("content")
            if content:
                return html.unescape(str(content))
    except Exception:
        pass
    return ""


def _yt_thumb(node: Any) -> str:
    try:
        if not isinstance(node, dict):
            return ""
        thumbs = None
        if isinstance(node.get("thumbnail"), dict):
            thumbs = node.get("thumbnail", {}).get("thumbnails")
        if thumbs is None:
            thumbs = node.get("thumbnails")
        if isinstance(thumbs, list) and thumbs:
            best = thumbs[-1]
            if isinstance(best, dict):
                return _normalize_img_url(str(best.get("url") or ""))
    except Exception:
        pass
    return ""


def _extract_related_from_watch_page(video_url: str, ua: str, limit: int = 12) -> List[Dict[str, Any]]:
    """Fallback для блоку рекомендованих відео, якщо yt-dlp не повернув related_videos."""
    if not video_url:
        return []
    current_id = _extract_video_id_from_url(video_url)
    try:
        req = urllib.request.Request(
            video_url,
            headers={
                "User-Agent": ua or _ANDROID_WEB_UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.youtube.com",
                "Connection": "keep-alive",
            },
        )
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            raw = resp.read(900_000)
        text = raw.decode("utf-8", errors="ignore")

        data = None
        for marker in ("var ytInitialData =", "ytInitialData =", "window[\"ytInitialData\"] ="):
            data = _json_after_marker(text, marker)
            if data:
                break

        out: List[Dict[str, Any]] = []
        seen = set()

        def add_item(renderer: Dict[str, Any]):
            try:
                vid = str(renderer.get("videoId") or "")
                if not vid or vid == current_id or vid in seen:
                    return
                title = _yt_text(renderer.get("title"))
                if not title:
                    return
                channel = (
                    _yt_text(renderer.get("shortBylineText"))
                    or _yt_text(renderer.get("longBylineText"))
                    or _yt_text(renderer.get("ownerText"))
                )
                thumb = _yt_thumb(renderer)
                if not thumb:
                    thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
                seen.add(vid)
                out.append({
                    "id": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title": title,
                    "channel": channel,
                    "thumbnail": thumb,
                })
            except Exception:
                pass

        def walk(node: Any):
            if len(out) >= int(limit or 12):
                return
            if isinstance(node, dict):
                for key in ("compactVideoRenderer", "videoRenderer", "gridVideoRenderer", "playlistVideoRenderer"):
                    val = node.get(key)
                    if isinstance(val, dict):
                        add_item(val)
                        if len(out) >= int(limit or 12):
                            return
                for val in node.values():
                    walk(val)
                    if len(out) >= int(limit or 12):
                        return
            elif isinstance(node, list):
                for val in node:
                    walk(val)
                    if len(out) >= int(limit or 12):
                        return

        if data:
            walk(data)

        # Regex-фолбек, якщо YouTube поміняв структуру ytInitialData.
        if len(out) < 4:
            for m in re.finditer(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', text):
                vid = m.group(1)
                if not vid or vid == current_id or vid in seen:
                    continue
                start = max(0, m.start() - 2500)
                end = min(len(text), m.end() + 2500)
                chunk = text[start:end]
                mt = re.search(r'"title"\s*:\s*\{\s*"simpleText"\s*:\s*"([^"]+)"', chunk)
                if not mt:
                    mt = re.search(r'"runs"\s*:\s*\[\s*\{\s*"text"\s*:\s*"([^"]+)"', chunk)
                title = html.unescape((mt.group(1) if mt else "").replace("\\u0026", "&"))
                if not title:
                    continue
                seen.add(vid)
                out.append({
                    "id": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "title": title,
                    "channel": "",
                    "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                })
                if len(out) >= int(limit or 12):
                    break

        return out[: int(limit or 12)]
    except Exception:
        return []


def _validated_youtube_channel_url(value: str) -> str:
    """Accept only YouTube channel pages; never fetch arbitrary oEmbed URLs."""
    try:
        parsed = urlparse.urlsplit(str(value or "").strip())
        host = str(parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in (
            "youtube.com", "www.youtube.com", "m.youtube.com"
        ):
            return ""
        path = parsed.path or ""
        if not (
            re.fullmatch(r"/@[A-Za-z0-9_.-]{1,100}/?", path)
            or re.fullmatch(r"/channel/UC[A-Za-z0-9_-]{20,32}/?", path)
        ):
            return ""
        return "https://www.youtube.com" + path.rstrip("/")
    except (ValueError, TypeError):
        return ""


def _validated_yt_avatar_url(value: str) -> str:
    """Only allow HTTPS images on YouTube's own avatar CDN hosts."""
    try:
        candidate = html.unescape(str(value or "").strip())
        candidate = candidate.replace("\\u0026", "&").replace("\\/", "/")
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        parsed = urlparse.urlsplit(candidate)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
            "yt3.googleusercontent.com", "yt3.ggpht.com",
        }:
            return ""
        if not parsed.path or len(candidate) > 3072:
            return ""
        return candidate
    except (ValueError, TypeError):
        return ""


def _pick_yt3_avatar_from_text(text: str) -> str:
    """Get the actual URL, not the optional 'https:' regexp capture group."""
    for match in re.finditer(
        r'https?:?//(?:yt3\.ggpht\.com|yt3\.googleusercontent\.com)/'
        r'[^"\\\s<>]+',
        str(text or ""),
    ):
        value = _validated_yt_avatar_url(match.group(0))
        if value:
            return value
    return ""

def _extract_channel_thumb_from_watch_page(video_url: str, ua: str) -> str:
    try:
        req = urllib.request.Request(
            video_url,
            headers={
                "User-Agent": ua or _ANDROID_WEB_UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.youtube.com",
                "Connection": "keep-alive",
            },
        )
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            raw = resp.read(400_000)
        text = raw.decode("utf-8", errors="ignore")
        m = re.search(r'ytProfileIconImage[^>]+src="([^"]+)"', text)
        if not m:
            m = re.search(r'"avatar"\s*:\s*\{[^}]*"thumbnails"\s*:\s*\[\s*\{"url":"([^"]+)"', text)
        if not m:
            m = re.search(r'"channelAvatar"\s*:\s*\{[^}]*"thumbnails"\s*:\s*\[\s*\{"url":"([^"]+)"', text)
        if m:
            return _normalize_img_url(m.group(1) or "")
        return _pick_yt3_avatar_from_text(text)
    except Exception:
        return ""


def _extract_channel_meta_from_watch_page(video_url: str, ua: str) -> Tuple[str, str]:
    """
    Повертає (channel_name, channel_thumb) з watch-page.
    """
    # Never request arbitrary URLs for metadata; only a verified YouTube ID.
    vid = _extract_video_id_from_url(video_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid or ""):
        return "", ""
    canonical_url = "https://www.youtube.com/watch?v=" + vid
    try:
        req = urllib.request.Request(
            canonical_url,
            headers={
                "User-Agent": ua or _ANDROID_WEB_UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.youtube.com",
                "Connection": "keep-alive",
            },
        )
        ctx = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=7, context=ctx) as resp:
            raw = resp.read(1_500_000)
        text = raw.decode("utf-8", errors="ignore")

        channel_name = ""
        for p in (
            r'"ownerChannelName":"([^"]+)"',
            r'"channelName":"([^"]+)"',
            r'"ownerText"\s*:\s*\{"runs"\s*:\s*\[\{"text":"([^"]+)"',
            r'"shortBylineText"\s*:\s*\{"runs"\s*:\s*\[\{"text":"([^"]+)"',
            r'"longBylineText"\s*:\s*\{"runs"\s*:\s*\[\{"text":"([^"]+)"',
            r'"author":"([^"]+)"',
        ):
            m = re.search(p, text)
            if m:
                channel_name = html.unescape(m.group(1) or "")
                channel_name = channel_name.replace("\\u0026", "&").replace("\\/", "/")
                break

        thumb = ""
        for p in (
            r'ytProfileIconImage[^>]+src="([^"]+)"',
            r'"channelThumbnailSupportedRenderers"\s*:\s*\{"channelThumbnailWithLinkRenderer"\s*:\s*\{"thumbnail"\s*:\s*\{"thumbnails"\s*:\s*\[\{"url":"([^"]+)"',
            r'"channelAvatar"\s*:\s*\{[^}]*"thumbnails"\s*:\s*\[\s*\{"url":"([^"]+)"',
            r'"avatar"\s*:\s*\{[^}]*"thumbnails"\s*:\s*\[\s*\{"url":"([^"]+)"',
        ):
            m = re.search(p, text)
            if m:
                thumb = _normalize_img_url(m.group(1) or "")
                break
        if not thumb:
            thumb = _pick_yt3_avatar_from_text(text)

        return channel_name, thumb
    except Exception:
        return "", ""


def _channel_name_from_oembed(video_url: str) -> str:
    """Resolve the real author from official YouTube oEmbed, never the title.

    Android yt-dlp responses for SABR-only videos often lack channel.
    Keep a small positive-result cache, require a real YouTube video ID and
    verify TLS against certifi's CA bundle (works in python-for-android too).
    """
    video_id = _extract_video_id_from_url(video_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return ""
    with _CHANNEL_NAME_CACHE_LOCK:
        cached = _CHANNEL_NAME_CACHE.get(video_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]

    endpoint = (
        "https://www.youtube.com/oembed?"
        + urlparse.urlencode({
            "url": "https://www.youtube.com/watch?v=" + video_id,
            "format": "json",
        })
    )
    try:
        req = urllib.request.Request(
            endpoint,
            headers={
                "User-Agent": _ANDROID_WEB_UA,
                "Accept": "application/json",
            },
        )
        tls = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=7, context=tls) as response:
            data = json.loads(response.read(8192).decode("utf-8"))
        name = str(data.get("author_name") or "").strip()[:200]
        if name:
            author_url = _validated_youtube_channel_url(
                str(data.get("author_url") or "")
            )
            with _CHANNEL_NAME_CACHE_LOCK:
                expires = time.monotonic() + 3600
                _CHANNEL_NAME_CACHE[video_id] = (expires, name)
                if author_url:
                    _CHANNEL_AUTHOR_URL_CACHE[video_id] = (expires, author_url)
                while len(_CHANNEL_NAME_CACHE) > 64:
                    oldest = next(iter(_CHANNEL_NAME_CACHE))
                    _CHANNEL_NAME_CACHE.pop(oldest, None)
                    _CHANNEL_AUTHOR_URL_CACHE.pop(oldest, None)
            print(f"[META] official YouTube author resolved id={video_id}")
        return name
    except (OSError, ValueError, TypeError) as exc:
        print(f"[META] official YouTube author lookup failed: {type(exc).__name__}")
        return ""



def _channel_url_from_oembed(video_url: str) -> str:
    """Return the verified owner link for this exact YouTube video.

    yt-dlp and the early author worker populate the URL cache. If neither
    supplied a link, make one bounded oEmbed lookup. Never build a channel
    address from a display name (different creators can share that name).
    Call from a worker thread, not the Kivy UI thread.
    """
    video_id = _extract_video_id_from_url(video_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return ""
    with _CHANNEL_NAME_CACHE_LOCK:
        cached = _CHANNEL_AUTHOR_URL_CACHE.get(video_id)
        if cached and cached[0] > time.monotonic():
            return _validated_youtube_channel_url(cached[1])

    _channel_name_from_oembed(video_url)
    with _CHANNEL_NAME_CACHE_LOCK:
        cached = _CHANNEL_AUTHOR_URL_CACHE.get(video_id)
        if cached and cached[0] > time.monotonic():
            return _validated_youtube_channel_url(cached[1])
    return ""


def _extract_channel_thumb_from_channel_page(channel_url: str, ua: str) -> str:
    """Read the canonical channel's og:image (real avatar, not video cover)."""
    channel_url = _validated_youtube_channel_url(channel_url)
    if not channel_url:
        return ""
    try:
        req = urllib.request.Request(
            channel_url,
            headers={
                "User-Agent": ua or _ANDROID_WEB_UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.youtube.com",
            },
        )
        ctx = ssl.create_default_context(cafile=certifi.where())
        # YouTube sometimes emits >800 KB of scripts BEFORE its <meta>
        # channel OG image (confirmed ~828 KB for @Grimwind). A 220 KB
        # response silently misses it. Read a bounded 1.2 MB instead.
        # This runs in the separate avatar worker, never the audio path.
        with urllib.request.urlopen(req, timeout=7, context=ctx) as resp:
            raw = resp.read(1_200_000)
        page = raw.decode("utf-8", errors="replace")
        match = re.search(
            r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)',
            page, re.IGNORECASE,
        )
        if match:
            image_url = _validated_yt_avatar_url(match.group(1))
            if image_url:
                return image_url
        return _pick_yt3_avatar_from_text(page)
    except Exception as exc:
        print(f"[META] channel avatar HTML unavailable: {type(exc).__name__}")
        return ""


def _channel_avatar_from_oembed(video_url: str) -> str:
    """Resolve a real channel avatar using oEmbed author_url, with no JS API.

    The oEmbed worker already obtained author_name; reuse its validated
    author_url instead of scraping the video's huge watch page. Cache the
    positive result for an hour and never confuse video thumbnails with the
    actual channel avatar.
    """
    video_id = _extract_video_id_from_url(video_url)
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        return ""
    with _CHANNEL_NAME_CACHE_LOCK:
        cached = _CHANNEL_AVATAR_URL_CACHE.get(video_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        author = _CHANNEL_AUTHOR_URL_CACHE.get(video_id)
        author_url = author[1] if author and author[0] > time.monotonic() else ""
    if not author_url:
        _channel_name_from_oembed(video_url)
        with _CHANNEL_NAME_CACHE_LOCK:
            author = _CHANNEL_AUTHOR_URL_CACHE.get(video_id)
            author_url = author[1] if author and author[0] > time.monotonic() else ""
    if not author_url:
        return ""
    avatar = _extract_channel_thumb_from_channel_page(author_url, _ANDROID_WEB_UA)
    if avatar:
        with _CHANNEL_NAME_CACHE_LOCK:
            _CHANNEL_AVATAR_URL_CACHE[video_id] = (time.monotonic() + 3600, avatar)
            while len(_CHANNEL_AVATAR_URL_CACHE) > 64:
                _CHANNEL_AVATAR_URL_CACHE.pop(next(iter(_CHANNEL_AVATAR_URL_CACHE)))
        print(f"[META] official YouTube channel avatar resolved id={video_id}")
    return avatar


def _parse_expire_ts(url: str) -> Optional[int]:
    try:
        q = urlparse.urlparse(url).query
        params = urlparse.parse_qs(q)
        if "expire" in params and params["expire"]:
            return int(params["expire"][0])
    except Exception:
        pass
    return None


def _best_effort_headers(src: Dict[str, str] | None, defaults: Dict[str, str]) -> Dict[str, str]:
    headers = dict(src or {})
    # Обов'язкові заголовки для стабільного доступу до googlevideo CDN
    headers.setdefault("User-Agent", defaults.get("User-Agent", _ANDROID_WEB_UA))
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    headers.setdefault("Referer", "https://www.youtube.com")
    headers.setdefault("Connection", "keep-alive")
    return headers


def _pick_best_audio(
    formats: List[Dict[str, Any]],
    *,
    prefer_compat: bool = False,
) -> Optional[Dict[str, Any]]:
    """Select audio-only streams: prefer standard LC-AAC format 140.

    Format 139 (HE-AAC, mp4a.40.5) can take much longer to prepare on some
    Android codecs. Never choose format 18 merely because it contains audio:
    that muxed stream is known to fail for some YouTube videos.
    """
    valid = [
        fmt for fmt in formats or []
        if fmt.get("url")
        and fmt.get("vcodec") in (None, "none")
        and fmt.get("acodec") not in (None, "none")
    ]
    if not valid:
        return None

    def bitrate(fmt):
        try:
            return float(fmt.get("abr") or fmt.get("tbr") or 0)
        except (ValueError, TypeError):
            return 0.0

    aac = [
        fmt for fmt in valid
        if fmt.get("ext") in ("m4a", "mp4", "aac")
        or "mp4a" in str(fmt.get("acodec") or "").lower()
    ]
    if aac:
        return max(
            aac,
            key=lambda fmt: (
                str(fmt.get("acodec") or "").lower().startswith("mp4a.40.2"),
                str(fmt.get("format_id") or "") == "140",
                bitrate(fmt),
            ),
        )
    if not prefer_compat:
        opus = [
            fmt for fmt in valid
            if str(fmt.get("acodec") or "").lower().startswith("opus")
            or fmt.get("ext") == "webm"
        ]
        if opus:
            return max(opus, key=bitrate)
    return max(valid, key=bitrate)


def _video_stream_candidates(
    formats: List[Dict[str, Any]],
    default_headers: Dict[str, str] | None = None,
    *,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Native decodable streams, including *video-only* AVC for separate audio.

    Android's yt-dlp client frequently exposes only unusable format 18.
    tv_embedded exposes independent video-only HLS (230/231/232) and MP4
    (134/135/136). They are valid sources for a muted video decoder while
    audio continues on its own MediaPlayer. Restrict to AVC/H.264 to avoid
    device-specific AV1/VP9 decoder failures. Use only HTTPS googlevideo hosts.
    """
    hls_muxed, hls_silent, direct_silent, muxed, seen = [], [], [], [], set()
    for fmt in formats or []:
        if not isinstance(fmt, dict):
            continue
        source = str(fmt.get("url") or "")
        try:
            parsed = urlparse.urlparse(source)
            host = (parsed.hostname or "").lower()
            if parsed.scheme != "https" or not (
                host == "googlevideo.com" or host.endswith(".googlevideo.com")
            ):
                continue
        except (ValueError, TypeError):
            continue
        if source in seen:
            continue
        video_codec = str(fmt.get("vcodec") or "none").lower()
        audio_codec = str(fmt.get("acodec") or "none").lower()
        if not video_codec.startswith("avc1"):
            continue
        has_audio = audio_codec != "none"
        proto = str(fmt.get("protocol") or "").lower()
        is_hls = proto in ("m3u8", "m3u8_native") or (
            host == "manifest.googlevideo.com"
            and "/hls_playlist/" in parsed.path
        )
        if not is_hls and str(fmt.get("ext") or "").lower() != "mp4":
            continue
        try:
            height = int(fmt.get("height") or 0)
        except (ValueError, TypeError):
            height = 0
        if height > 720 or height <= 0:
            continue
        seen.add(source)
        entry = {
            "video_url": source,
            "http_headers": _best_effort_headers(
                fmt.get("http_headers"), dict(default_headers or {})
            ),
            "format_id": str(fmt.get("format_id") or ""),
            "height": height,
            "protocol": "hls" if is_hls else "mp4",
            "video_only": not has_audio,
        }
        if is_hls and has_audio:
            hls_muxed.append(entry)
        elif is_hls:
            hls_silent.append(entry)
        elif has_audio:
            muxed.append(entry)
        else:
            direct_silent.append(entry)

    # 480p makes first-frame startup lighter; then 360p/720p fallbacks.
    quality = lambda x: (abs(x["height"] - 480), -x["height"])
    for group in (hls_muxed, hls_silent, direct_silent, muxed):
        group.sort(key=quality)
    ordered = (
        hls_muxed[:2] + hls_silent[:2] +
        direct_silent[:2] + muxed[:1]
    )
    return ordered[:max(1, min(int(limit), 6))]



def _pick_best_video(formats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Legacy helper: use the same safe HLS-first selection as the player."""
    streams = _video_stream_candidates(formats)
    if not streams:
        return None
    top = streams[0]
    return {
        "url": top["video_url"],
        "http_headers": top["http_headers"],
        "format_id": top["format_id"],
        "height": top["height"],
        "protocol": top["protocol"],
    }



def _extract_info_with_clients(
    url: str,
    base_opts: Dict[str, Any],
    clients: Tuple[str, ...]
) -> Tuple[Optional[Dict[str, Any]], Optional[Exception]]:
    """
    По черзі пробуємо кілька player_client: ('android', 'web', ...)
    Повертаємо (info_dict | None, last_exception | None)
    """
    last_err: Optional[Exception] = None
    best_info: Optional[Dict[str, Any]] = None
    best_rank = -1
    known_meta: Dict[str, Any] = {}
    for client in clients:
        opts = dict(base_opts)
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        # _pick_best_audio() and _pick_best_video() select from the FULL
        # formats list. Let yt-dlp extract metadata without prematurely
        # raising "Requested format is not available" on SABR-only clients.
        # In particular web_embedded exposes AAC format 140 for some videos
        # whose Android player client exposes only broken muxed format 18.
        if client != "android":
            opts["format"] = None
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:
            last_err = exc
            continue
        if not isinstance(info, dict):
            continue

        # SABR experiments can return only muxed format 18 for some clients.
        # Continue until an actual audio-only stream is available.
        for key in ("channel", "uploader", "creator", "artist",
                    "channel_url", "channel_id", "channel_thumbnail",
                    "uploader_thumbnail", "title", "thumbnail",
                    "upload_date", "release_date"):
            if info.get(key) and not known_meta.get(key):
                known_meta[key] = info[key]
        formats = info.get("formats") or []
        audio_only = [
            fmt for fmt in formats
            if fmt.get("url") and fmt.get("vcodec") in (None, "none")
            and fmt.get("acodec") not in (None, "none")
        ]
        aac = any(
            fmt.get("ext") in ("m4a", "mp4", "aac")
            or "mp4a" in str(fmt.get("acodec") or "").lower()
            for fmt in audio_only
        )
        hls = any(
            fmt.get("url") and fmt.get("acodec") not in (None, "none")
            and "m3u8" in str(fmt.get("protocol") or "")
            for fmt in formats
        )
        rank = 3 if aac else (2 if audio_only else (1 if hls else 0))
        print(
            f"[YTDLP] client={client} formats={len(formats)} "
            f"audio_only={len(audio_only)} aac={aac} hls={hls} rank={rank}"
        )
        if rank > best_rank:
            best_info, best_rank = info, rank
        if client == "android":
            global _ANDROID_CLIENT_MUXED_ONLY
            with _AUDIO_CLIENT_HINT_LOCK:
                _ANDROID_CLIENT_MUXED_ONLY = (rank < 2)
        if rank == 3 or (client == "android" and rank >= 2):
            # Normal Android audio-only tracks stay on the fast path. Only
            # muxed-only/SABR clients fall through to tv_embedded.
            break

    if best_info is not None:
        for key, value in known_meta.items():
            if not best_info.get(key):
                best_info[key] = value
    return best_info, last_err


# ==================== PUBLIC: HEADERS → Java HashMap ====================
def py_headers_to_javamap(headers: Dict[str, str], HashMapClass) -> Any:
    """
    Конвертація Python dict → Java HashMap (для MediaPlayer.setDataSource(url, headers)).
    Додає обов'язкові заголовки, якщо їх немає.
    """
    m = HashMapClass()
    if headers:
        for k, v in headers.items():
            if k and v:
                m.put(str(k), str(v))

    if not m.containsKey("User-Agent"):
        m.put("User-Agent", _ANDROID_WEB_UA)
    if not m.containsKey("Referer"):
        m.put("Referer", "https://www.youtube.com")
    if not m.containsKey("Connection"):
        m.put("Connection", "keep-alive")
    if not m.containsKey("Accept-Language"):
        m.put("Accept-Language", "en-US,en;q=0.9")

    return m


# ============================ AUDIO API ================================
def _audio_client_order(prefer_compat: bool = False) -> Tuple[str, ...]:
    """Avoid repeating the Android muxed-only client on every track.

    Learn from successful extraction data in the current process, retaining
    fallback to the previous client if the preferred one fails.
    """
    with _AUDIO_CLIENT_HINT_LOCK:
        prefer_tv = _ANDROID_CLIENT_MUXED_ONLY

    if prefer_compat:
        clients = ("web_embedded", "tv_embedded", "android", "ios")
    elif prefer_tv:
        clients = ("tv_embedded", "android")
    else:
        clients = ("android", "tv_embedded")
    if _env_bool("YTDLP_ALLOW_WEB_CLIENT", False):
        clients += ("web",)
    return clients


def extract_audio_info(video_url: str, *, prefer_compat: bool = False) -> Dict[str, Any]:
    """
    Повертає:
      {
        'audio_url': str,                 # прямий URL аудіо
        'thumb': str,                     # мініатюра
        'title': str,                     # назва відео
        'channel': str,                   # канал/автор
        'view_count': Optional[int],      # кількість переглядів
        'upload_date': str,                # YYYYMMDD від YouTube (якщо є)
        'channel_thumb': str,             # аватар/іконка каналу
        'related_videos': list,           # raw related videos list
        'expire_ts': Optional[int],       # UNIX time з параметра expire, якщо є
        'http_headers': Dict[str, str],   # заголовки для доступу до CDN
      }
    Стійко працює при збоях nsig, пріоритет Android-клієнт.
    """
    if not prefer_compat:
        cached = _get_cached_audio_info(video_url)
        if cached is not None:
            print("[AUDIO] signed stream metadata cache hit")
            return cached

    BASE_OPTS = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": False,
        "logger": YDLLogger(),
        "format": (
            "bestaudio[ext=m4a]/"
            "bestaudio[ext=mp4]/"
            "bestaudio[acodec*=mp4a]/"
            "bestaudio/best/"
            "best[protocol^=m3u8]"
            if prefer_compat else
            "bestaudio[acodec^=opus]/"
            "bestaudio[ext=webm]/"
            "bestaudio[ext=m4a]/"
            "bestaudio/best/"
            "best[protocol^=m3u8]"  # як крайній випадок
        ),
        "http_headers": {
            "User-Agent": _ANDROID_YT_UA,    # підказка для бекенда
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.youtube.com",
            "Connection": "keep-alive",
        },
        "extractor_retries": 1,
        "socket_timeout": 8,
        "ignoreerrors": "only_download",
    }

    # Keep the proven Android-client path for normally playable videos.
    # Only after an actual playback failure (prefer_compat=True) ask embedded
    # clients for an alternative audio-only stream, usually AAC/M4A format 140.
    # Do not make healthy tracks wait for extra YouTube requests.
    clients = _audio_client_order(prefer_compat)
    info, err = _extract_info_with_clients(video_url, BASE_OPTS, clients)
    if not info:
        print(f"[AUDIO] extract failed: {repr(err)}")
        raise RuntimeError("YouTube не повернув метадані (онови yt-dlp в APK).")

    _cache_extracted_formats(video_url, info)
    fmts = info.get("formats") or []
    chosen: Optional[Dict[str, Any]] = _pick_best_audio(fmts, prefer_compat=prefer_compat)
    if chosen and chosen.get("url"):
        url = chosen["url"]
        headers = _best_effort_headers(chosen.get("http_headers"), BASE_OPTS["http_headers"])
    else:
        # fallback: інколи info['url'] вже містить audio-only
        url = info.get("url") or ""
        headers = _best_effort_headers(info.get("http_headers"), BASE_OPTS["http_headers"])

    selected_format_id = str((chosen or {}).get("format_id") or info.get("format_id") or "")
    selected_ext = str((chosen or {}).get("ext") or info.get("ext") or "")
    selected_acodec = str((chosen or {}).get("acodec") or info.get("acodec") or "")

    if prefer_compat and selected_format_id == "18":
        # The caller is specifically recovering from format-18 playback
        # failure. Returning the same muxed stream as an "AAC fallback"
        # creates a slow retry loop and cannot fix that CDN/decoder error.
        # Make the missing alternative visible to the recovery UI instead.
        raise RuntimeError(
            "Alternative audio-only stream is not available; "
            "YouTube still returned failing muxed format 18"
        )

    if not url:
        # типовий випадок "Only images are available"
        raise RuntimeError("Не знайдено жодного аудіо-формату (YouTube віддав тільки зображення).")

    thumb = _normalize_img_url(info.get("thumbnail", "") or "")
    title = info.get("title") or ""
    channel = info.get("channel") or info.get("uploader") or info.get("creator") or info.get("artist") or ""
    view_count = info.get("view_count")
    channel_thumb = (
        info.get("channel_thumbnail")
        or info.get("uploader_thumbnail")
        or ""
    )
    channel_thumb = _normalize_img_url(channel_thumb or "")
    channel_url = (
        info.get("channel_url")
        or info.get("uploader_url")
        or ""
    )
    channel_id = info.get("channel_id") or info.get("uploader_id") or ""
    if not channel_url and channel_id:
        channel_url = f"https://www.youtube.com/channel/{channel_id}"
    # Never block stream startup on watch-page/avatar/recommendation
    # requests. Dedicated metadata and related-video workers fetch those
    # after playback starts; the audio URL can now be passed to MediaPlayer
    # immediately after yt-dlp completes.
    related_videos = info.get("related_videos") or []
    expire_ts = _parse_expire_ts(url)
    try:
        print(
            f"[YTDLP] meta title={title!r} channel={channel!r} "
            f"thumb={'yes' if channel_thumb else 'no'} views={view_count} "
            f"audio_format={selected_format_id!r}/{selected_ext!r}/{selected_acodec!r} "
            f"compat={prefer_compat}"
        )
    except Exception:
        pass

    result = {
        "audio_url": url,
        "thumb": thumb,
        "title": title,
        "channel": channel,
        "view_count": view_count,
        # Use only yt-dlp's actual publication date. Never guess the
        # upload date from the current time or the audio extraction time.
        "upload_date": info.get("upload_date") or info.get("release_date") or "",
        "channel_thumb": channel_thumb,
        "related_videos": related_videos,
        "expire_ts": expire_ts,
        "http_headers": headers,
        "format_id": selected_format_id,
        "ext": selected_ext,
        "acodec": selected_acodec,
    }
    if not prefer_compat:
        _cache_audio_info(video_url, result)
    return result


# ============================ VIDEO API ================================
def extract_video_info(video_url: str) -> Dict[str, Any]:
    """Fetch muted native video candidates, using the audio worker's URL cache.

    The audio worker often already queried tv_embedded to obtain AAC 140.
    Reuse that same short-lived format list rather than re-requesting three
    YouTube clients. If it is absent, try web HLS, TV video-only AVC, then
    Android progressive MP4, in that order.
    """
    headers = {
        "User-Agent": _ANDROID_WEB_UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.youtube.com",
    }
    base_options = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "logger": YDLLogger(),
        "format": None,
        "http_headers": headers,
        "extractor_retries": 1,
        "socket_timeout": 7,
        "ignoreerrors": "only_download",
    }
    thumbnail = ""
    selected = []
    cached = _get_cached_formats(video_url)
    if cached:
        selected = _video_stream_candidates(
            cached.get("formats") or [], headers, limit=5
        )
        thumbnail = str(cached.get("thumbnail") or "")
        # An Android-only cache may contain just broken format 18. Let the
        # web/TV clients provide better candidates before using that stream.
        if selected and any(
            x["protocol"] == "hls" or x["video_only"] for x in selected
        ):
            print(f"[VIDEO] reused {len(selected)} cached video candidates")
            first = selected[0]
            return {
                "video_url": first["video_url"],
                "http_headers": first["http_headers"],
                "thumb": thumbnail,
                "format_id": first["format_id"],
                "streams": selected,
            }

    fallback = selected
    for client in ("web_embedded", "tv_embedded", "android"):
        opts = dict(base_options)
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
            if not isinstance(info, dict):
                continue
            thumbnail = thumbnail or str(info.get("thumbnail") or "")
            streams = _video_stream_candidates(
                info.get("formats") or [], headers, limit=5
            )
            if streams:
                _cache_extracted_formats(video_url, info)
                if any(
                    x["protocol"] == "hls" or x["video_only"] for x in streams
                ):
                    selected = streams
                    print(
                        f"[VIDEO] selected client={client} formats="
                        f"{[(x['format_id'], x['protocol'], x['height']) for x in selected]}"
                    )
                    break
                if not fallback:
                    fallback = streams
        except Exception as exc:
            print(f"[VIDEO] {client} extraction unavailable: {type(exc).__name__}")
    if not selected:
        selected = fallback
    if not selected:
        print("[VIDEO] no compatible HLS or AVC video streams")
        return {
            "video_url": None,
            "http_headers": {},
            "thumb": thumbnail,
            "streams": [],
        }
    first = selected[0]
    return {
        "video_url": first["video_url"],
        "http_headers": first["http_headers"],
        "thumb": thumbnail,
        "format_id": first["format_id"],
        "streams": selected,
    }


# ==================== (OPTIONAL) SAFE WRAPPERS ====================
def safe_extract_audio_info(video_url: str) -> Optional[Dict[str, Any]]:
    """Обгортка, що ніколи не падає. Повертає None у разі помилки."""
    try:
        return extract_audio_info(video_url)
    except Exception as e:
        print("[AUDIO] safe_extract_audio_info error:", repr(e))
        return None


def safe_extract_video_info(video_url: str) -> Optional[Dict[str, Any]]:
    """Обгортка, що ніколи не падає. Повертає None у разі помилки."""
    try:
        return extract_video_info(video_url)
    except Exception as e:
        print("[VIDEO] safe_extract_video_info error:", repr(e))
        return None
    
