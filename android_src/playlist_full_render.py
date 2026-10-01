"""Deterministic full playlist renderer for the single-scroll player.

The previous cleanup still rendered queue rows in 8-item Clock chunks. During
player startup several metadata/video callbacks may call _render_playlist_ui
again before the next chunk runs. That bumps _playlist_render_gen, invalidates
the pending chunk and starts again from the first 8 rows. Visually the user can
scroll a little, then the outer ScrollView reaches the height of that partial
queue and appears frozen.

This module becomes the final owner of playlist rendering after
player_input_cleanup. It renders the complete queue in one pass and derives the
list height from all real row widgets. No nested ScrollView is reintroduced.
"""
from __future__ import annotations

import sys
import threading

_PATCHED = False
_LOCK = threading.RLock()


def _spacing_y(widget) -> float:
    try:
        value = getattr(widget, "spacing", 0) or 0
        if isinstance(value, (list, tuple)):
            return float(value[-1] or 0) if value else 0.0
        return float(value)
    except Exception:
        return 0.0


def _padding_y(widget) -> float:
    try:
        value = getattr(widget, "padding", 0) or 0
        if isinstance(value, (int, float)):
            return float(value) * 2.0
        values = list(value)
        if len(values) >= 4:
            return float(values[1] or 0) + float(values[3] or 0)
        if len(values) == 2:
            return float(values[1] or 0) * 2.0
        if len(values) == 1:
            return float(values[0] or 0) * 2.0
    except Exception:
        pass
    return 0.0


def _real_list_height(listing) -> float:
    children = list(getattr(listing, "children", []) or [])
    total = _padding_y(listing)
    for child in children:
        try:
            total += max(0.0, float(getattr(child, "height", 0) or 0))
        except Exception:
            pass
    if len(children) > 1:
        total += _spacing_y(listing) * float(len(children) - 1)
    try:
        total = max(total, float(getattr(listing, "minimum_height", 0) or 0))
    except Exception:
        pass
    return max(0.0, total)


def install_full_playlist_renderer() -> bool:
    global _PATCHED

    with _LOCK:
        if _PATCHED:
            return True

        module = sys.modules.get("audio_screen")
        if module is None:
            return False
        cls = getattr(module, "AudioPlayerScreen", None)
        if cls is None:
            return False
        if not bool(getattr(cls, "_pymusic_clean_single_scroll_v1", False)):
            return False
        if bool(getattr(cls, "_pymusic_full_playlist_render_v1", False)):
            _PATCHED = True
            return True

        Clock = module.Clock

        def signature(self):
            try:
                return tuple(
                    (
                        str(item.get("url") or ""),
                        str(item.get("video_id") or ""),
                        str(item.get("title") or ""),
                        str(item.get("channel") or ""),
                        str(item.get("duration") or ""),
                        str(item.get("thumb") or ""),
                    )
                    for item in (self.playlist.tracks if self.playlist else [])
                )
            except Exception:
                return None

        def request_layout(widget) -> None:
            if widget is None:
                return
            try:
                trigger = getattr(widget, "_trigger_layout", None)
                if callable(trigger):
                    trigger()
            except Exception:
                pass
            try:
                do_layout = getattr(widget, "do_layout", None)
                if callable(do_layout):
                    do_layout()
            except Exception:
                pass
            try:
                widget.canvas.ask_update()
            except Exception:
                pass

        def update_header(self, tracks) -> None:
            try:
                visible = bool(tracks)
                collapsed = bool(getattr(self, "_playlist_collapsed", False))
                title = self.playlist.name or "Черга"
                if tracks:
                    title = f"{title} · {len(tracks)}"
                self._set_collapsible_header(
                    self.ids.get("playlist_header_row"),
                    self.ids.get("playlist_header"),
                    self.ids.get("playlist_toggle_btn"),
                    visible,
                    title,
                    collapsed,
                )
            except Exception as exc:
                print("[PLAYLIST-FULL] header failed:", exc)

        def sync_geometry(self, *, log=False) -> None:
            try:
                listing = self.ids.get("playlist_list")
                outer = self.ids.get("player_details_scroll")
                if listing is None or outer is None:
                    return

                tracks = list(self.playlist.tracks if self.playlist else [])
                expanded = bool(tracks and not getattr(self, "_playlist_collapsed", False))
                height = _real_list_height(listing) if expanded else 0.0

                listing.size_hint_y = None
                listing.height = height
                listing.opacity = 1 if expanded else 0
                listing.disabled = not expanded

                outer.disabled = False
                outer.do_scroll_x = False
                outer.do_scroll_y = True
                try:
                    outer.always_overscroll = False
                except Exception:
                    pass

                request_layout(listing)
                parent = getattr(listing, "parent", None)
                request_layout(parent)
                content = outer.children[0] if outer.children else None
                request_layout(content)
                request_layout(outer)

                if log:
                    content_h = float(getattr(content, "height", 0) or 0) if content is not None else 0.0
                    viewport_h = float(getattr(outer, "height", 0) or 0)
                    print(
                        "[PLAYLIST-FULL] geometry "
                        f"rows={len(getattr(listing, 'children', []) or [])}/{len(tracks)} "
                        f"list_h={float(listing.height or 0):.1f} "
                        f"content_h={content_h:.1f} viewport_h={viewport_h:.1f} "
                        f"range={max(0.0, content_h - viewport_h):.1f}"
                    )
            except Exception as exc:
                print("[PLAYLIST-FULL] geometry failed:", exc)

        def render_full(self, force=False):
            listing = self.ids.get("playlist_list")
            if listing is None:
                return None

            tracks = list(self.playlist.tracks if self.playlist else [])
            sig = signature(self)
            old_sig = getattr(self, "_pymusic_full_playlist_sig_v1", None)
            complete = len(getattr(listing, "children", []) or []) == len(tracks)

            # Invalidate every pending 8-row callback left by the earlier
            # renderer. Once this renderer is installed, no stale chunk may add
            # or remove rows behind our back.
            self._playlist_render_gen = int(getattr(self, "_playlist_render_gen", 0) or 0) + 1

            if not force and sig == old_sig and complete:
                update_header(self, tracks)
                sync_geometry(self)
                return None

            self._pymusic_full_playlist_sig_v1 = sig
            listing.clear_widgets()
            update_header(self, tracks)

            if tracks and not bool(getattr(self, "_playlist_collapsed", False)):
                for idx, item in enumerate(tracks):
                    try:
                        listing.add_widget(self._make_playlist_row(idx, item))
                    except Exception as exc:
                        print(f"[PLAYLIST-FULL] row {idx} failed: {exc}")

            if sig != old_sig:
                try:
                    self.ids.player_details_scroll.scroll_y = 1.0
                except Exception:
                    pass

            sync_geometry(self, log=True)
            for delay in (0.0, 0.03, 0.10, 0.25):
                Clock.schedule_once(
                    lambda _dt, owner=self, final=(delay == 0.25): sync_geometry(owner, log=final),
                    delay,
                )
            return None

        def toggle_full(self):
            self._playlist_collapsed = not bool(getattr(self, "_playlist_collapsed", False))
            return render_full(self, force=True)

        old_on_kv_post = cls.on_kv_post
        old_pre_enter = cls.on_pre_enter

        def on_kv_post_full(self, base_widget):
            result = old_on_kv_post(self, base_widget)
            Clock.schedule_once(lambda _dt, owner=self: render_full(owner, force=True), 0)
            return result

        def pre_enter_full(self, *args, **kwargs):
            result = old_pre_enter(self, *args, **kwargs)
            Clock.schedule_once(lambda _dt, owner=self: render_full(owner, force=False), 0)
            return result

        cls._render_playlist_ui = render_full
        cls.toggle_playlist_collapsed = toggle_full
        cls.on_kv_post = on_kv_post_full
        cls.on_pre_enter = pre_enter_full
        cls._pymusic_full_playlist_render_v1 = True

        _PATCHED = True
        print("[PLAYLIST-FULL] synchronous full-queue renderer enabled")
        return True
