# recent_utils.py

import os
import json
import threading
import time

# Runtime player patches are imported from a module that audio_screen always
# loads. Python-for-Android does not guarantee that the app directory is ready
# during the interpreter's automatic sitecustomize lookup, so a short poll waits
# until AudioPlayerScreen has finished being defined before applying the fixes.
try:
    import sitecustomize as _player_hotfix
    import playlist_scroll_fix as _playlist_scroll_fix
    import video_sync_fix as _video_sync_fix
    import resume_ui_fix as _resume_ui_fix
    import runtime_stability_fix as _runtime_stability_fix

    def _install_player_hotfix_when_ready():
        for _ in range(200):
            try:
                player_ready = bool(
                    _player_hotfix._patch_audio_screen()
                )
                scroll_ready = False
                video_ready = False
                resume_ready = False
                stability_ready = False
                if player_ready:
                    scroll_ready = bool(
                        _playlist_scroll_fix._patch_playlist_scroll()
                    )
                if player_ready and scroll_ready:
                    video_ready = bool(
                        _video_sync_fix._patch_video_sync()
                    )
                if player_ready and scroll_ready and video_ready:
                    resume_ready = bool(
                        _resume_ui_fix._patch_resume_ui()
                    )
                # This final layer moves AV checks off Kivy Clock and applies
                # the compact title/views geometry after every UI refresh.
                if player_ready and scroll_ready and video_ready and resume_ready:
                    stability_ready = bool(
                        _runtime_stability_fix._patch_runtime_stability()
                    )
                if (
                    player_ready
                    and scroll_ready
                    and video_ready
                    and resume_ready
                    and stability_ready
                ):
                    return
            except Exception:
                pass
            time.sleep(0.05)

    threading.Thread(
        target=_install_player_hotfix_when_ready,
        name="pymusic-player-hotfix",
        daemon=True,
    ).start()
except Exception as _hotfix_error:
    print("[HOTFIX] loader failed:", _hotfix_error)


RECENT_PATH = "recent.json"
FAVORITES_PATH = "favorites.json"
MAX_RECENT = 10


def load_recent():
    if os.path.exists(RECENT_PATH):
        try:
            with open(RECENT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_recent(recent_list):
    try:
        with open(RECENT_PATH, "w", encoding="utf-8") as f:
            json.dump(recent_list[:MAX_RECENT], f, ensure_ascii=False)
    except Exception as e:
        print("[RECENT] Error saving recent list:", e)


def update_recent_cache(url: str, cache_path: str | None):
    if not url:
        return
    try:
        recent = load_recent()
        updated = False
        for r in recent:
            if r.get("url") == url:
                if cache_path:
                    r["cache_path"] = cache_path
                else:
                    r.pop("cache_path", None)
                updated = True
                break
        if updated:
            save_recent(recent)
    except Exception as e:
        print("[RECENT] Error updating cache path:", e)


def update_recent_art(url: str, art_path: str | None):
    if not url:
        return
    try:
        recent = load_recent()
        updated = False
        for r in recent:
            if r.get("url") == url:
                if art_path:
                    r["art_path"] = art_path
                else:
                    r.pop("art_path", None)
                updated = True
                break
        if updated:
            save_recent(recent)
    except Exception as e:
        print("[RECENT] Error updating art path:", e)


def load_favorites():
    if os.path.exists(FAVORITES_PATH):
        try:
            with open(FAVORITES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def save_favorites(items):
    try:
        with open(FAVORITES_PATH, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False)
    except Exception as e:
        print("[FAV] Error saving favorites:", e)


def is_favorite(url: str) -> bool:
    if not url:
        return False
    try:
        for it in load_favorites():
            if it.get("url") == url:
                return True
    except Exception:
        pass
    return False


def upsert_favorite(item: dict):
    url = str((item or {}).get("url") or "")
    if not url:
        return
    favs = load_favorites()
    found = False
    for i, f in enumerate(favs):
        if f.get("url") == url:
            favs[i] = dict(favs[i], **item)
            found = True
            break
    if not found:
        favs.insert(0, dict(item))
    save_favorites(favs)


def remove_favorite(url: str):
    if not url:
        return
    favs = load_favorites()
    favs = [f for f in favs if f.get("url") != url]
    save_favorites(favs)


def update_favorite_cache(url: str, cache_path: str | None):
    if not url:
        return
    favs = load_favorites()
    updated = False
    for f in favs:
        if f.get("url") == url:
            if cache_path:
                f["cache_path"] = cache_path
            else:
                f.pop("cache_path", None)
            updated = True
            break
    if updated:
        save_favorites(favs)
