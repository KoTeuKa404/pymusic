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
    import startup_sync_barrier as _startup_sync_barrier
    import resume_ui_fix as _resume_ui_fix
    import runtime_stability_fix as _runtime_stability_fix
    import scroll_bounds_fix as _scroll_bounds_fix
    import player_polish_fix as _player_polish_fix
    import visual_fill_fix as _visual_fill_fix
    import startup_dual_seek_fix as _startup_dual_seek_fix

    def _install_player_hotfix_when_ready():
        for _ in range(200):
            try:
                player_ready = bool(
                    _player_hotfix._patch_audio_screen()
                )
                scroll_ready = False
                video_ready = False
                barrier_ready = False
                resume_ready = False
                stability_ready = False
                bounds_ready = False
                polish_ready = False
                visual_ready = False
                dual_seek_ready = False
                if player_ready:
                    scroll_ready = bool(
                        _playlist_scroll_fix._patch_playlist_scroll()
                    )
                if player_ready and scroll_ready:
                    video_ready = bool(
                        _video_sync_fix._patch_video_sync()
                    )
                # The startup barrier must replace video _on_prepared after the
                # normal drift synchronizer has installed its v3 hooks.
                if player_ready and scroll_ready and video_ready:
                    barrier_ready = bool(
                        _startup_sync_barrier._patch_startup_sync_barrier()
                    )
                if player_ready and scroll_ready and video_ready and barrier_ready:
                    resume_ready = bool(
                        _resume_ui_fix._patch_resume_ui()
                    )
                if (
                    player_ready
                    and scroll_ready
                    and video_ready
                    and barrier_ready
                    and resume_ready
                ):
                    stability_ready = bool(
                        _runtime_stability_fix._patch_runtime_stability()
                    )
                if (
                    player_ready
                    and scroll_ready
                    and video_ready
                    and barrier_ready
                    and resume_ready
                    and stability_ready
                ):
                    bounds_ready = bool(
                        _scroll_bounds_fix._patch_scroll_bounds()
                    )
                if (
                    player_ready
                    and scroll_ready
                    and video_ready
                    and barrier_ready
                    and resume_ready
                    and stability_ready
                    and bounds_ready
                ):
                    polish_ready = bool(
                        _player_polish_fix._patch_player_polish()
                    )
                # This must remain after the older layout wrappers. It overrides
                # thumbnail-based SurfaceView bounds and title-height estimates.
                if (
                    player_ready
                    and scroll_ready
                    and video_ready
                    and barrier_ready
                    and resume_ready
                    and stability_ready
                    and bounds_ready
                    and polish_ready
                ):
                    visual_ready = bool(
                        _visual_fill_fix._patch_visual_fill()
                    )
                # Final sync layer: repeat the exact dual-player seek operation
                # that the native slider/buttons use and that is known to sync
                # perfectly on the user's phone.
                if (
                    player_ready
                    and scroll_ready
                    and video_ready
                    and barrier_ready
                    and resume_ready
                    and stability_ready
                    and bounds_ready
                    and polish_ready
                    and visual_ready
                ):
                    dual_seek_ready = bool(
                        _startup_dual_seek_fix._patch_startup_dual_seek()
                    )
                if (
                    player_ready
                    and scroll_ready
                    and video_ready
                    and barrier_ready
                    and resume_ready
                    and stability_ready
                    and bounds_ready
                    and polish_ready
                    and visual_ready
                    and dual_seek_ready
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
