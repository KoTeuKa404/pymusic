
# ytdlp_helpers.py
from __future__ import annotations

import html
import json
import os
import re
import ssl
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


def _pick_yt3_avatar_from_text(text: str) -> str:
    # Дуже надійний fallback: беремо перший yt3.ggpht URL з watch/channel HTML.
    matches = re.findall(r'(https?:)?//yt3\.ggpht\.com/[^"\\\s<]+', text or "")
    if not matches:
        return ""
    raw = matches[0]
    if raw.startswith("//"):
        raw = "https:" + raw
    return _normalize_img_url(raw)


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
            raw = resp.read(450_000)
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


def _extract_channel_thumb_from_channel_page(channel_url: str, ua: str) -> str:
    if not channel_url:
        return ""
    try:
        req = urllib.request.Request(
            channel_url,
            headers={
                "User-Agent": ua or _ANDROID_WEB_UA,
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.youtube.com",
                "Connection": "keep-alive",
            },
        )
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            raw = resp.read(350_000)
        text = raw.decode("utf-8", errors="ignore")
        m = re.search(r'<meta property="og:image" content="([^"]+)"', text)
        if not m:
            m = re.search(r'"avatar"\s*:\s*\{[^}]*"thumbnails"\s*:\s*\[\s*\{"url":"([^"]+)"', text)
        if m:
            return _normalize_img_url(m.group(1) or "")
        return _pick_yt3_avatar_from_text(text)
    except Exception:
        return ""

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
    """
    Пріоритезуємо аудіо потоки:
      1) OPUS/webm
      2) webm
      3) m4a
      4) будь-який audio-only
    """
    if not formats:
        return None

    def is_audio(f: Dict[str, Any]) -> bool:
        return f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none") and f.get("url")

    if prefer_compat:
        # Режим сумісності для Android MediaPlayer: AAC/M4A спочатку.
        for f in formats:
            ac = str(f.get("acodec", "")).lower()
            if is_audio(f) and (f.get("ext") in ("m4a", "mp4") or "mp4a" in ac or ac == "aac"):
                return f
        for f in formats:
            if is_audio(f) and f.get("ext") in ("mp3", "aac"):
                return f
    else:
        # 1) opus/webm
        for f in formats:
            if is_audio(f) and (str(f.get("acodec", "")).lower().startswith("opus") or f.get("ext") == "webm"):
                return f

        # 2) webm будь-який аудіо
        for f in formats:
            if is_audio(f) and f.get("ext") == "webm":
                return f

        # 3) m4a
        for f in formats:
            if is_audio(f) and f.get("ext") == "m4a":
                return f

    # 4) інший audio-only
    for f in formats:
        if is_audio(f):
            return f

    return None


def _pick_best_video(formats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Відео для Android MediaPlayer:
      1) HLS (m3u8) з відео
      2) muxed mp4 (vcodec!=none & acodec!=none, ext=mp4)
      3) будь-який відеопотік (vcodec!=none)
    """
    if not formats:
        return None

    # 1) HLS (m3u8)
    for f in formats:
        u = f.get("url") or ""
        if "m3u8" in u and f.get("vcodec") not in (None, "none"):
            return f

    # 2) muxed mp4
    for f in formats:
        if (
            f.get("url")
            and f.get("vcodec") not in (None, "none")
            and f.get("acodec") not in (None, "none")
            and f.get("ext") == "mp4"
        ):
            return f

    # 3) будь-який відеопотік
    for f in formats:
        if f.get("url") and f.get("vcodec") not in (None, "none"):
            return f

    return None



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
    for client in clients:
        opts = dict(base_opts)
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if isinstance(info, dict):
                    return info, None
        except Exception as e:
            last_err = e
    return None, last_err


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
def extract_audio_info(video_url: str, *, prefer_compat: bool = False) -> Dict[str, Any]:
    """
    Повертає:
      {
        'audio_url': str,                 # прямий URL аудіо
        'thumb': str,                     # мініатюра
        'title': str,                     # назва відео
        'channel': str,                   # канал/автор
        'view_count': Optional[int],      # кількість переглядів
        'channel_thumb': str,             # аватар/іконка каналу
        'related_videos': list,           # raw related videos list
        'expire_ts': Optional[int],       # UNIX time з параметра expire, якщо є
        'http_headers': Dict[str, str],   # заголовки для доступу до CDN
      }
    Стійко працює при збоях nsig, пріоритет Android-клієнт.
    """
    BASE_OPTS = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
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
        "extractor_retries": 2,
        "ignoreerrors": "only_download",
    }

    # На Android часто ламається web-client через PO Token/EJS,
    # що дає "Only images are available" і цикл -1004/-38 у MediaPlayer.
    # Тому за замовчуванням НЕ використовуємо web-client.
    allow_web_client = _env_bool("YTDLP_ALLOW_WEB_CLIENT", False)
    if allow_web_client:
        clients = ("android", "web")
    else:
        clients = ("android",)
    info, err = _extract_info_with_clients(video_url, BASE_OPTS, clients)
    if not info:
        print(f"[AUDIO] extract failed: {repr(err)}")
        raise RuntimeError("YouTube не повернув метадані (онови yt-dlp в APK).")

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
    if (not channel) or (not channel_thumb):
        page_channel, page_thumb = _extract_channel_meta_from_watch_page(
            str(video_url or ""),
            BASE_OPTS.get("http_headers", {}).get("User-Agent", _ANDROID_WEB_UA),
        )
        if (not channel) and page_channel:
            channel = page_channel
        if (not channel_thumb) and page_thumb:
            channel_thumb = _normalize_img_url(page_thumb)
    if not channel_thumb:
        channel_thumb = _extract_channel_thumb_from_watch_page(
            str(video_url or ""),
            BASE_OPTS.get("http_headers", {}).get("User-Agent", _ANDROID_WEB_UA),
        )
    if not channel_thumb:
        channel_thumb = _extract_channel_thumb_from_channel_page(
            str(channel_url or ""),
            BASE_OPTS.get("http_headers", {}).get("User-Agent", _ANDROID_WEB_UA),
        )
    channel_thumb = _normalize_img_url(channel_thumb or "")
    related_videos = info.get("related_videos") or []
    if not related_videos:
        related_videos = _extract_related_from_watch_page(
            str(video_url or ""),
            _ANDROID_WEB_UA,
            limit=12,
        )
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

    return {
        "audio_url": url,
        "thumb": thumb,
        "title": title,
        "channel": channel,
        "view_count": view_count,
        "channel_thumb": channel_thumb,
        "related_videos": related_videos,
        "expire_ts": expire_ts,
        "http_headers": headers,
        "format_id": selected_format_id,
        "ext": selected_ext,
        "acodec": selected_acodec,
    }


# ============================ VIDEO API ================================
def extract_video_info(video_url: str) -> Dict[str, Any]:
    """
    Повертає:
      {
        'video_url': Optional[str],       # відтворюваний відео-URL або None (якщо лише images)
        'http_headers': Dict[str, str],   # заголовки для setDataSource
        'thumb': str,                     # мініатюра
      }
    Пріоритет: muxed MP4 → HLS m3u8 → будь-який відеопотік.
    """
    BASE_OPTS = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "logger": YDLLogger(),
        # ВАЖЛИВО: тут формат тільки для внутрішньої логіки yt-dlp,
        # але ми все одно будемо добирати формат самі через formats.
        "format": (
            "bestvideo[height<=720][vcodec!*='none'][protocol^=m3u8]+bestaudio/best[ext=mp4][height<=720]/"
            "best[protocol^=m3u8][height<=720]"
        ),
        "http_headers": {
            "User-Agent": _ANDROID_YT_UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.youtube.com",
            "Connection": "keep-alive",
        },
        "extractor_retries": 2,
        "ignoreerrors": "only_download",
    }

    # Web-client на Android часто тягне EJS/PO-token шлях і може запускати
    # зовнішні subprocess runtime. За замовчуванням тримаємось android client.
    allow_web_client = _env_bool("YTDLP_ALLOW_WEB_CLIENT", False)
    clients = ("android", "web") if allow_web_client else ("android",)
    info, err = _extract_info_with_clients(video_url, BASE_OPTS, clients)
    if not info:
        print(f"[VIDEO] extract failed: {repr(err)}")
        return {"video_url": None, "http_headers": {}, "thumb": ""}

    url = info.get("url") or ""
    fmts = info.get("formats") or []
    headers = _best_effort_headers(info.get("http_headers"), BASE_OPTS["http_headers"])

    def _looks_playable(u: str) -> bool:
        return (
            ("googlevideo.com" in u)
            or ("m3u8" in u)
            or u.endswith(".mp4")
        )

    # Якщо головний url пустий або дивний - шукаємо самі
    if not url or not _looks_playable(url):
        chosen = _pick_best_video(fmts)
        if chosen and chosen.get("url"):
            url = chosen["url"]
            headers = _best_effort_headers(chosen.get("http_headers"), BASE_OPTS["http_headers"])

    # Якщо навіть так немає відео - значить YouTube реально віддав тільки images
    if not url or not _looks_playable(url):
        print("[VIDEO] no playable video formats, only images or EJS breakage")
        return {
            "video_url": None,
            "http_headers": {},
            "thumb": info.get("thumbnail", "") or "",
        }

    return {
        "video_url": url,
        "http_headers": headers,
        "thumb": info.get("thumbnail", "") or "",
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
    
