"""Touch/layout fix for the in-video rewind/play/forward controls.

The KV layout historically handled these actions on ``on_release``.  On a
phone that makes the controls feel a little late, and the compact 220dp row
puts the rewind/forward touch targets too close to the central play/pause
button.  This patch keeps the existing KV as the visual source of truth, but:

* widens the control row and increases the gap around play/pause;
* makes the central play/pause hit target slightly larger than seek buttons;
* executes the action on ``on_press`` for immediate feedback;
* suppresses the matching legacy ``on_release`` call so one tap never fires
  the same action twice.
"""
from __future__ import annotations

import time

from kivy.clock import Clock
from kivy.metrics import dp


_PATCHED = False
_RELEASE_SUPPRESS_SECONDS = 2.0


def _guard_map(owner) -> dict[str, float]:
    guards = getattr(owner, "_video_control_release_guards", None)
    if not isinstance(guards, dict):
        guards = {}
        owner._video_control_release_guards = guards
    return guards


def _mark_press(owner, key: str) -> None:
    _guard_map(owner)[key] = time.monotonic() + _RELEASE_SUPPRESS_SECONDS


def _consume_release(owner, key: str) -> bool:
    guards = _guard_map(owner)
    deadline = float(guards.get(key, 0.0) or 0.0)
    if deadline <= 0.0:
        return False

    # A press is paired with only one release.  Clear immediately so a later
    # real action cannot be swallowed by stale state.
    guards.pop(key, None)
    return time.monotonic() <= deadline


def _press_seek(owner, seconds: int, original_seek) -> None:
    key = f"seek:{int(seconds)}"
    _mark_press(owner, key)
    try:
        original_seek(owner, seconds)
    except Exception as exc:
        print(f"[VIDEO-CTRL] immediate seek failed ({seconds}): {exc}")


def _press_toggle(owner, original_toggle) -> None:
    _mark_press(owner, "toggle")
    try:
        original_toggle(owner)
    except Exception as exc:
        print(f"[VIDEO-CTRL] immediate toggle failed: {exc}")


def _tune_controls(owner, original_seek, original_toggle) -> None:
    try:
        controls = owner.ids.get("video_controls")
        if controls is None:
            return

        # Keep the group centered, but add a real dead zone between the
        # rewind/forward targets and the central play/pause target.
        controls.size_hint = (None, None)
        controls.width = dp(260)
        controls.height = dp(58)
        controls.spacing = dp(28)
        controls.padding = [dp(30), dp(3), dp(30), dp(3)]

        for button in list(getattr(controls, "children", []) or []):
            icon = str(getattr(button, "icon", "") or "")
            if icon not in {"rewind-10", "play", "pause", "fast-forward-10"}:
                continue

            button.size_hint = (None, None)
            if icon in {"play", "pause"} or button is owner.ids.get("video_play_btn"):
                button.size = (dp(52), dp(52))
            else:
                button.size = (dp(44), dp(44))

            if bool(getattr(button, "_pymusic_fast_press_bound", False)):
                continue

            if icon == "rewind-10":
                button.bind(
                    on_press=lambda _button, o=owner, fn=original_seek: _press_seek(o, -10, fn)
                )
            elif icon == "fast-forward-10":
                button.bind(
                    on_press=lambda _button, o=owner, fn=original_seek: _press_seek(o, 10, fn)
                )
            else:
                button.bind(
                    on_press=lambda _button, o=owner, fn=original_toggle: _press_toggle(o, fn)
                )

            button._pymusic_fast_press_bound = True

        print("[VIDEO-CTRL] fast press + safer spacing enabled")
    except Exception as exc:
        print("[VIDEO-CTRL] tuning failed:", exc)


def install_video_controls_ui_fix() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        import audio_screen

        screen_cls = getattr(audio_screen, "AudioPlayerScreen", None)
        if screen_cls is None:
            return False
        if bool(getattr(screen_cls, "_pymusic_video_controls_v1", False)):
            _PATCHED = True
            return True

        original_seek = screen_cls.video_seek
        original_toggle = screen_cls.video_toggle_play
        old_on_kv_post = getattr(screen_cls, "on_kv_post", None)

        def video_seek_release_guarded(self, seconds):
            key = f"seek:{int(seconds)}"
            if _consume_release(self, key):
                return None
            return original_seek(self, seconds)

        def video_toggle_release_guarded(self):
            if _consume_release(self, "toggle"):
                return None
            return original_toggle(self)

        def on_kv_post_video_controls(self, base_widget):
            if callable(old_on_kv_post):
                old_on_kv_post(self, base_widget)
            Clock.schedule_once(
                lambda _dt: _tune_controls(self, original_seek, original_toggle),
                0,
            )

        screen_cls.video_seek = video_seek_release_guarded
        screen_cls.video_toggle_play = video_toggle_release_guarded
        screen_cls.on_kv_post = on_kv_post_video_controls
        screen_cls._pymusic_video_controls_v1 = True

        _PATCHED = True
        print("[VIDEO-CTRL] runtime patch v1 installed")
        return True
    except Exception as exc:
        print("[VIDEO-CTRL] patch install failed:", exc)
        return False
