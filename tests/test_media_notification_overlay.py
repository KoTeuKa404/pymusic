"""MediaSession notification, native layer and resume regressions.

These tests run offline on CPython without installing Android/JNI dependencies.
The isolated methods exercise state de-duplication and WebView recovery.
"""
from __future__ import annotations

import ast
import os
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android_src"


def isolate(filename: str, func_name: str, namespace: dict):
    tree = ast.parse((ANDROID / filename).read_text(encoding="utf-8"))
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == func_name
    ]
    if len(matches) != 1:
        raise AssertionError(f"{filename}:{func_name} found {len(matches)} times")
    fn = matches[0]
    fn.decorator_list = []  # remove Android's UI thread decorator in CPython
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))
    exec(compile(module, filename, "exec"), namespace)
    return namespace[func_name]


class NativeLayerAndNotificationTests(unittest.TestCase):
    def test_native_webview_hide_repairs_stale_python_flag(self):
        events = []
        visible = SimpleNamespace(value=0)
        class FakeView:
            VISIBLE = 0
            GONE = 8
            def getVisibility(self):
                return visible.value
            def setVisibility(self, value):
                visible.value = value
                events.append(("visibility", value))
        ns = {
            "_webview_container": FakeView(),
            "_webview_shown": False,  # stale, despite visible Java window
            "_webview_stop_media": lambda: events.append("stop"),
            "View": FakeView,
            "log": lambda s: events.append(s),
        }
        hide = isolate("media_android.py", "webview_hide", ns)
        hide()
        self.assertEqual(visible.value, FakeView.GONE)
        self.assertFalse(ns["_webview_shown"])
        self.assertIn("stop", events)

    def test_paused_media_state_is_not_republished_every_half_second(self):
        class FakePlaybackState:
            ACTION_PLAY = 1
            ACTION_PAUSE = 2
            ACTION_PLAY_PAUSE = 4
            ACTION_SKIP_TO_NEXT = 8
            ACTION_SKIP_TO_PREVIOUS = 16
            ACTION_SEEK_TO = 32
            STATE_PLAYING = 3
            STATE_PAUSED = 2
        class FakeBuilder:
            def __init__(self):
                self.actions = []
            def setActions(self, actions):
                self.actions.append(("actions", actions))
            def setState(self, state, position, speed):
                self.actions.append(("state", state, position, speed))
            def addCustomAction(self, action, text, icon):
                self.actions.append(("custom", action, text, icon))
            def build(self):
                return self.actions
        updates = []
        session = SimpleNamespace(setPlaybackState=lambda value: updates.append(value))
        ns = {
            "_LAST_MEDIA_STATE_DETAILS": None,
            "_get_session": lambda: session,
            "_ensure_action_constants": lambda: None,
            "ACTION_REPEAT": "org.koteuka404.pymusic.REPEAT",
            "PlaybackState": FakePlaybackState,
            "PlaybackStateBuilder": FakeBuilder,
            "R_draw": SimpleNamespace(ic_menu_revert=42),
            "time": time,
            "log": lambda *_: None,
            "vlog": lambda *_: None,
        }
        update = isolate("media_android.py", "update_media_session_state", ns)
        update(False, 93900, 180000, True)
        for _ in range(50):
            update(False, 93900, 180000, True)
        self.assertEqual(len(updates), 1)
        self.assertIn(
            ("custom", "org.koteuka404.pymusic.REPEAT", "Repeat", 42),
            updates[0],
        )
        update(False, 103900, 180000, True)
        self.assertEqual(len(updates), 2)
        update(True, 103900, 180000, True)
        self.assertEqual(len(updates), 3)

    def test_notification_has_validated_signature_and_cover(self):
        source = (ANDROID / "media_android.py").read_text(encoding="utf-8")
        updater = source.split("def create_or_update_media_notification(", 1)[1].split(
            "def cancel_media_notification(", 1
        )[0]
        self.assertIn("signature == _LAST_NOTIFICATION_SIGNATURE", updater)
        self.assertIn("nm.notify(NOTIF_ID, b.build())", updater)
        self.assertIn("_LAST_NOTIFICATION_SIGNATURE = signature", updater)
        self.assertIn("style.setMediaSession(session_token)", updater)
        self.assertIn('style.setShowActionsInCompactView(*idx)', updater)
        audio = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn("large_icon_path=self._art_path or self._channel_thumb_local", audio)
        self.assertNotIn(
            "large_icon_path=self._channel_thumb_local or self._art_path", audio
        )
        java = (
            ROOT / "java_src/org/koteuka404/pymusic/MediaSessionCallback.java"
        ).read_text(encoding="utf-8")
        self.assertIn("void onCustomAction(String action, Bundle extras)", java)
        self.assertIn('"org.koteuka404.pymusic.REPEAT".equals(action)', java)

    def test_resume_rebinds_native_touch_controls(self):
        video = (ANDROID / "video_player.py").read_text(encoding="utf-8")
        self.assertIn("self.controls_overlay.getParent() is not None", video)
        self.assertIn("self._controls_touch_views = []", video)
        self.assertIn("self._ensure_controls_overlay()", video)
        self.assertNotIn("self.surface_view.bringToFront()", video.split(
            "def _bring_controls_or_surface_front(self)", 1
        )[1].split("def _ensure_controls_overlay(", 1)[0])
        audio = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        resume = audio.split("def handle_app_resume(self):", 1)[1].split(
            "def _configure_video_block(", 1
        )[0]
        self.assertIn("self._set_video_controls_visible(", resume)
        self.assertIn("self._align_video_to_thumb", resume)
        self.assertIn("name=\"pymusic-resume-video\"", audio)

    def test_cover_download_does_not_disable_tls_or_allow_unbounded_files(self):
        audio = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        method = audio.split("def _download_art_async(", 1)[1].split(
            "def _channel_avatar_cache_path(", 1
        )[0]
        self.assertNotIn("_create_unverified_context", method)
        self.assertIn("ssl.create_default_context(cafile=certifi.where())", method)
        self.assertIn('class SafeArtRedirect(urllib.request.HTTPRedirectHandler)', method)
        self.assertIn('image_bytes = response.read(2 * 1024 * 1024 + 1)', method)
        self.assertIn("os.replace(temp, art_path)", method)


if __name__ == "__main__":
    unittest.main()
