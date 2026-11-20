"""Persistent, bounded image cache for video covers, channel avatars and lists.

Only HTTPS images from known YouTube CDN hosts are accepted. Downloads are
validated and atomically installed; Kivy never sees a partial file. Reusing the
same URL across a search result, playlist row and Recent item shares one task.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import shutil
import ssl
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import weakref
from concurrent.futures import ThreadPoolExecutor

import certifi
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.image import AsyncImage

IMAGE_MAX_BYTES = 2 * 1024 * 1024
IMAGE_CACHE_LIMIT_BYTES = 64 * 1024 * 1024
IMAGE_MIN_FREE_BYTES = 64 * 1024 * 1024
_IMAGE_HOSTS = frozenset({
    "i.ytimg.com", "img.youtube.com", "i1.ytimg.com",
    "yt3.ggpht.com", "yt3.googleusercontent.com",
})
_IMAGE_EXTENSIONS = ("jpg", "png", "webp")
_POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="pymusic-images")
_LOCK = threading.RLock()
_INFLIGHT = {}


def _url(value: str) -> str:
    raw = html.unescape(str(value or "").strip())
    if raw.startswith("//"):
        raw = "https:" + raw
    try:
        parsed = urllib.parse.urlsplit(raw)
        if (parsed.scheme.lower() != "https"
                or (parsed.hostname or "").lower() not in _IMAGE_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            return ""
        # The old player appended ts= on every UI refresh. That makes disk
        # caching impossible; ignore only this artificial cache-busting key.
        args = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        query = urllib.parse.urlencode([(k, v) for k, v in args if k != "ts"])
        return urllib.parse.urlunsplit((
            "https", parsed.hostname.lower(), parsed.path, query, ""
        ))
    except (ValueError, TypeError):
        return ""


def _directory() -> str:
    try:
        app = App.get_running_app()
        base = getattr(app, "user_data_dir", None)
    except Exception:
        base = None
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".pymusic")
    root = os.path.join(base, "pymusic_image_cache")
    os.makedirs(root, exist_ok=True)
    return root


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _image_type(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    return ""


def get_cached_image_path(value: str) -> str | None:
    """Return a completed image file, never a signed CDN URL or temp file."""
    raw = str(value or "").strip()
    if raw and not raw.startswith(("http://", "https://", "//")):
        try:
            return raw if os.path.isfile(raw) and not os.path.islink(raw) else None
        except OSError:
            return None
    normalized = _url(raw)
    if not normalized:
        return None
    try:
        root = _directory()
        stem = _digest(normalized)
        for ext in _IMAGE_EXTENSIONS:
            path = os.path.join(root, stem + "." + ext)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            if not (128 <= os.path.getsize(path) <= IMAGE_MAX_BYTES):
                continue
            with open(path, "rb") as f:
                if _image_type(f.read(16)) != ext:
                    continue
            try:
                if time.time() - os.path.getmtime(path) > 60:
                    os.utime(path, None)
            except OSError:
                pass
            return path
    except (OSError, ValueError):
        pass
    return None


def _make_room(root: str, required: int) -> bool:
    """Evict oldest cached images, never audio or hand-picked favorites."""
    entries, total = [], 0
    for entry in os.scandir(root):
        if not entry.is_file(follow_symlinks=False):
            continue
        if not re.fullmatch(r"[0-9a-f]{64}\.(jpg|png|webp)", entry.name):
            continue
        stat = entry.stat(follow_symlinks=False)
        total += stat.st_size
        entries.append((stat.st_mtime, entry.path, stat.st_size))
    for _, path, size in sorted(entries):
        if total + required <= IMAGE_CACHE_LIMIT_BYTES:
            break
        try:
            os.unlink(path)
            total -= size
        except OSError:
            pass
    return total + required <= IMAGE_CACHE_LIMIT_BYTES


def _download(url: str) -> str | None:
    existing = get_cached_image_path(url)
    if existing:
        return existing
    normalized = _url(url)
    if not normalized:
        return None
    root = _directory()
    if shutil.disk_usage(root).free < IMAGE_MIN_FREE_BYTES + IMAGE_MAX_BYTES:
        return None

    class SafeRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            if not _url(newurl):
                raise ValueError("image redirect left trusted YouTube HTTPS CDN")
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    context = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), SafeRedirect()
    )
    req = urllib.request.Request(normalized, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36",
        "Referer": "https://www.youtube.com/",
        "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
    })
    try:
        with opener.open(req, timeout=12) as response:
            if not _url(response.geturl()) or response.status != 200:
                return None
            mime = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
            if mime not in ("image/jpeg", "image/png", "image/webp"):
                return None
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > IMAGE_MAX_BYTES:
                return None
            data = response.read(IMAGE_MAX_BYTES + 1)
        if not (128 <= len(data) <= IMAGE_MAX_BYTES):
            return None
        if declared and len(data) != declared:
            return None
        ext = _image_type(data)
        if not ext:
            return None
        fd, tmp = tempfile.mkstemp(prefix=".image-", suffix=".part", dir=root)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            with _LOCK:
                if not _make_room(root, len(data)):
                    return None
                path = os.path.join(root, _digest(normalized) + "." + ext)
                os.replace(tmp, path)
                tmp = None
            return path
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print("[IMAGE-CACHE] download skipped:", type(exc).__name__)
        return None


def request_cached_image(url: str, callback) -> None:
    """Resolve images asynchronously; callback(path_or_None) runs on Kivy thread."""
    normalized = _url(url)
    if not normalized:
        Clock.schedule_once(lambda _dt: callback(None), 0)
        return
    cached = get_cached_image_path(normalized)
    if cached:
        Clock.schedule_once(lambda _dt: callback(cached), 0)
        return
    with _LOCK:
        future = _INFLIGHT.get(normalized)
        if future is None:
            future = _POOL.submit(_download, normalized)
            _INFLIGHT[normalized] = future

            def forget(done, key=normalized):
                with _LOCK:
                    if _INFLIGHT.get(key) is done:
                        _INFLIGHT.pop(key, None)

            future.add_done_callback(forget)

    def finished(done):
        try:
            result = done.result()
        except Exception:
            result = None
        Clock.schedule_once(lambda _dt, path=result: callback(path), 0)

    future.add_done_callback(finished)


class CachedAsyncImage(AsyncImage):
    """Drop-in search/Recent artwork that reads an offline file before network."""

    def __init__(self, **kwargs):
        requested = kwargs.pop("source", "")
        super().__init__(source="", **kwargs)
        self._requested_cache_url = ""
        self.set_cached_source(requested)

    def set_cached_source(self, value: str) -> None:
        raw = str(value or "")
        self._requested_cache_url = raw
        self.source = ""
        if not raw:
            return
        normalized = _url(raw)
        if not normalized:
            path = get_cached_image_path(raw)
            if path:
                self.source = path
            return
        path = get_cached_image_path(normalized)
        if path:
            self.source = path
            return
        owner = weakref.ref(self)

        def apply(path):
            instance = owner()
            if instance is None or instance._requested_cache_url != raw:
                return
            if path and os.path.isfile(path):
                instance.source = path

        request_cached_image(normalized, apply)
