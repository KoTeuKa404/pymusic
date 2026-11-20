"""Persistent listening history and favorites.

Recent entries retain cached audio, artwork, channel avatar and metadata so
offline playback does not need to re-extract the YouTube watch page.
"""
from __future__ import annotations

import json
import os
import threading
import time

RECENT_PATH = "recent.json"
FAVORITES_PATH = "favorites.json"
MAX_RECENT = 80
_LOCK = threading.RLock()


def _read_list(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _write_list(path: str, entries: list) -> None:
    # Atomic replacement prevents an Android process kill during a save
    # from corrupting the previous session's history/favorites.
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def load_recent():
    with _LOCK:
        return _read_list(RECENT_PATH)


def save_recent(recent_list):
    with _LOCK:
        try:
            entries = [x for x in recent_list if isinstance(x, dict)]
            _write_list(RECENT_PATH, entries[:MAX_RECENT])
        except Exception as exc:
            print("[RECENT] saving failed:", type(exc).__name__)


def upsert_recent(url: str, title: str = "", channel: str = "", thumb: str = "") -> None:
    if not url:
        return
    with _LOCK:
        items = load_recent()
        entry = next((r for r in items if r.get("url") == url), {}).copy()
        entry["url"] = url
        entry["title"] = str(title or entry.get("title") or "")
        entry["channel"] = str(channel or entry.get("channel") or "")
        entry["thumb"] = str(thumb or entry.get("thumb") or "")
        entry["last_played"] = int(time.time())
        items = [r for r in items if r.get("url") != url]
        save_recent([entry] + items)


def _patch_recent(url: str, **fields) -> None:
    if not url:
        return
    with _LOCK:
        items = load_recent()
        for entry in items:
            if entry.get("url") != url:
                continue
            for key, value in fields.items():
                if value:
                    entry[key] = str(value)
                else:
                    entry.pop(key, None)
            save_recent(items)
            break


def update_recent_cache(url: str, cache_path: str | None):
    _patch_recent(url, cache_path=cache_path)


def update_recent_art(url: str, art_path: str | None):
    _patch_recent(url, art_path=art_path)


def update_recent_avatar(
    url: str, avatar_url: str | None, avatar_path: str | None
) -> None:
    values = {}
    if avatar_url:
        values["avatar_url"] = avatar_url
    if avatar_path:
        values["avatar_path"] = avatar_path
    if values:
        _patch_recent(url, **values)


def load_favorites():
    with _LOCK:
        return _read_list(FAVORITES_PATH)


def save_favorites(items):
    with _LOCK:
        try:
            _write_list(FAVORITES_PATH, [f for f in items if isinstance(f, dict)])
        except Exception as exc:
            print("[FAV] saving failed:", type(exc).__name__)


def is_favorite(url: str) -> bool:
    if not url:
        return False
    return any(item.get("url") == url for item in load_favorites())


def upsert_favorite(item: dict):
    url = str((item or {}).get("url") or "")
    if not url:
        return
    with _LOCK:
        items = load_favorites()
        found = False
        for index, favorite in enumerate(items):
            if favorite.get("url") == url:
                items[index] = dict(favorite, **item)
                found = True
                break
        if not found:
            items.insert(0, dict(item))
        save_favorites(items)


def remove_favorite(url: str):
    if not url:
        return
    with _LOCK:
        save_favorites([item for item in load_favorites() if item.get("url") != url])


def update_favorite_cache(url: str, cache_path: str | None):
    if not url:
        return
    with _LOCK:
        items = load_favorites()
        for item in items:
            if item.get("url") != url:
                continue
            if cache_path:
                item["cache_path"] = cache_path
            else:
                item.pop("cache_path", None)
            save_favorites(items)
            break
