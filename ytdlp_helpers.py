
# ytdlp_helpers.py
from __future__ import annotations

import urllib.parse as urlparse
from typing import Any, Dict, Optional, Tuple, List

import yt_dlp.cache
from yt_dlp import YoutubeDL


# ========================== CACHE OFF ==========================
yt_dlp.cache.store  = lambda *a, **k: None
yt_dlp.cache.load   = lambda *a, **k: None
yt_dlp.cache.remove = lambda *a, **k: None


# ========================== LOGGER ============================
class YDLLogger:
    def debug(self, msg: str):
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
    "com.google.android.youtube/19.20.0 (Linux; U; Android 12) gzip"
)
_ANDROID_WEB_UA = (
    "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome Mobile Safari/537.36"
)

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
    headers.setdefault("User-Agent", defaults.get("User-Agent", _ANDROID_WEB_UA))
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    headers.setdefault("Referer", "https://www.youtube.com")
    headers.setdefault("Connection", "keep-alive")
    return headers


def _pick_best_audio(formats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:

    if not formats:
        return None

    def is_audio(f: Dict[str, Any]) -> bool:
        return f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none") and f.get("url")

    # 1) opus/webm
    for f in formats:
        if is_audio(f) and (str(f.get("acodec", "")).lower().startswith("opus") or f.get("ext") == "webm"):
            return f

    # 2) webm
    for f in formats:
        if is_audio(f) and f.get("ext") == "webm":
            return f

    # 3) m4a
    for f in formats:
        if is_audio(f) and f.get("ext") == "m4a":
            return f

    for f in formats:
        if is_audio(f):
            return f

    return None


def _pick_best_video(formats: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:

    if not formats:
        return None

    # 1) muxed mp4
    for f in formats:
        if f.get("url") and f.get("vcodec") not in (None, "none") and f.get("acodec") not in (None, "none") and f.get("ext") == "mp4":
            return f

    # 2) HLS (m3u8)
    for f in formats:
        u = f.get("url") or ""
        if "m3u8" in u:
            return f

    for f in formats:
        if f.get("url") and f.get("vcodec") not in (None, "none"):
            return f

    return None


def _extract_info_with_clients(
    url: str,
    base_opts: Dict[str, Any],
    clients: Tuple[str, ...]
) -> Tuple[Optional[Dict[str, Any]], Optional[Exception]]:

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
def extract_audio_info(video_url: str) -> Dict[str, Any]:

    BASE_OPTS = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "logger": YDLLogger(),
        "format": (
            "bestaudio[acodec^=opus]/"
            "bestaudio[ext=webm]/"
            "bestaudio[ext=m4a]/"
            "bestaudio/best/"
            "best[protocol^=m3u8]"
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

    info, err = _extract_info_with_clients(video_url, BASE_OPTS, ("android", "web"))
    if not info:
        print(f"[AUDIO] extract failed: {repr(err)}")
        raise RuntimeError("YouTube не повернув метадані (онови yt-dlp в APK).")

    url = info.get("url") or ""
    fmts = info.get("formats") or []
    headers = _best_effort_headers(info.get("http_headers"), BASE_OPTS["http_headers"])

    chosen: Optional[Dict[str, Any]] = None

    if not url:
        chosen = _pick_best_audio(fmts)
        if chosen and chosen.get("url"):
            url = chosen["url"]
            headers = _best_effort_headers(chosen.get("http_headers"), BASE_OPTS["http_headers"])

    if not url:
        raise RuntimeError("Не знайдено жодного аудіо-формату (YouTube віддав тільки зображення).")

    thumb = info.get("thumbnail", "") or ""
    expire_ts = _parse_expire_ts(url)

    return {
        "audio_url": url,
        "thumb": thumb,
        "expire_ts": expire_ts,
        "http_headers": headers,
    }


# ============================ VIDEO API ================================
def extract_video_info(video_url: str) -> Dict[str, Any]:

    BASE_OPTS = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "logger": YDLLogger(),
        "format": (
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
            "best[protocol^=m3u8][vcodec!=none]/"
            "best[vcodec!=none]/"
            "best"
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

    info, err = _extract_info_with_clients(video_url, BASE_OPTS, ("android", "web"))
    if not info:
        print(f"[VIDEO] extract failed: {repr(err)}")
        return {"video_url": None, "http_headers": {}, "thumb": ""}

    url = info.get("url") or ""
    headers = _best_effort_headers(info.get("http_headers"), BASE_OPTS["http_headers"])
    fmts = info.get("formats") or []

    def _looks_playable(u: str) -> bool:
        return ("googlevideo.com" in u) or ("m3u8" in u) or u.endswith(".mp4")

    if not url or not _looks_playable(url):
        chosen = _pick_best_video(fmts)
        if chosen and chosen.get("url"):
            url = chosen["url"]
            headers = _best_effort_headers(chosen.get("http_headers"), BASE_OPTS["http_headers"])

    if not url:
        print("[VIDEO] no playable video formats, only images for this video")
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
    try:
        return extract_audio_info(video_url)
    except Exception as e:
        print("[AUDIO] safe_extract_audio_info error:", repr(e))
        return None


def safe_extract_video_info(video_url: str) -> Optional[Dict[str, Any]]:
    try:
        return extract_video_info(video_url)
    except Exception as e:
        print("[VIDEO] safe_extract_video_info error:", repr(e))
        return None
    