"""MediaSession notification, native layer and resume regressions.

These tests run offline on CPython without installing Android/JNI dependencies.
The isolated methods exercise state de-duplication and WebView recovery.
"""
from __future__ import annotations

import ast
import os
import time
import unittest
import zlib
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


def _png_rgba_alpha_bounds(path: Path):
    """Return the actual painted extent of a small, noninterlaced RGBA PNG."""
    png = path.read_bytes()
    assert png[:8] == bytes((137, 80, 78, 71, 13, 10, 26, 10))
    width = int.from_bytes(png[16:20], "big")
    height = int.from_bytes(png[20:24], "big")
    assert (width, height, png[24], png[25], png[28]) == (24, 24, 8, 6, 0)
    pos, compressed = 8, []
    while pos < len(png):
        length = int.from_bytes(png[pos:pos + 4], "big")
        tag = png[pos + 4:pos + 8]
        if tag == b"IDAT":
            compressed.append(png[pos + 8:pos + 8 + length])
        pos += 12 + length
        if tag == b"IEND":
            break
    stride = width * 4
    data = zlib.decompress(b"".join(compressed))
    assert len(data) == height * (stride + 1)
    offset, previous, painted = 0, bytearray(stride), []
    for y in range(height):
        filter_type = data[offset]
        offset += 1
        scanline = data[offset:offset + stride]
        offset += stride
        row = bytearray(stride)
        for i, v in enumerate(scanline):
            a = row[i - 4] if i >= 4 else 0
            b = previous[i]
            c = previous[i - 4] if i >= 4 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = a
            elif filter_type == 2:
                predictor = b
            elif filter_type == 3:
                predictor = (a + b) // 2
            elif filter_type == 4:
                guess = a + b - c
                diffs = (abs(guess - a), abs(guess - b), abs(guess - c))
                predictor = (a, b, c)[diffs.index(min(diffs))]
            else:
                raise AssertionError("unsupported PNG scanline filter")
            row[i] = (v + predictor) & 255
        for x in range(width):
            if row[4 * x + 3] > 8:
                painted.append((x, y))
        previous = row
    assert painted, "empty notification icon"
    return (min(x for x, _ in painted), max(x for x, _ in painted),
            min(y for _, y in painted), max(y for _, y in painted))


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
            "ACTION_FAVORITE": "org.koteuka404.pymusic.FAVORITE",
            "_media_custom_action_icons": lambda repeat, fav: (
                42 if not repeat else 44,
                43 if not fav else 45,
            ),
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
        self.assertIn(
            ("custom", "org.koteuka404.pymusic.FAVORITE", "Favorite", 43),
            updates[-1],
        )
        # Pressing Favorite or Repeat while paused must rebuild the custom
        # buttons despite an unchanged MediaPlayer position.
        update(False, 103900, 180000, True)
        self.assertEqual(len(updates), 4)
        update(False, 103900, 180000, True, favorite_enabled=True)
        self.assertEqual(len(updates), 5)
        self.assertIn(
            ("custom", "org.koteuka404.pymusic.FAVORITE", "Favorite", 45),
            updates[-1],
        )
        update(False, 103900, 180000, True,
               repeat_enabled=True, favorite_enabled=True)
        self.assertEqual(len(updates), 6)
        self.assertIn(
            ("custom", "org.koteuka404.pymusic.REPEAT", "Repeat", 44),
            updates[-1],
        )

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
        self.assertIn("large_icon_path=self._channel_thumb_local", audio)
        self.assertNotIn(
            "large_icon_path=self._art_path or self._channel_thumb_local", audio
        )
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
        self.assertIn(
            'or bool(getattr(self, "_last_video_url", None))', audio
        )
        self.assertIn(
            'self._media_session.setActive(True)', resume
        )

    def test_media_metadata_keeps_video_art_separate_from_channel_avatar(self):
        recorded = []
        class Keys:
            METADATA_KEY_TITLE = "title"
            METADATA_KEY_ARTIST = "artist"
            METADATA_KEY_ALBUM = "album"
            METADATA_KEY_DURATION = "duration"
            METADATA_KEY_ALBUM_ART = "album_art"
            METADATA_KEY_ART = "art"
            METADATA_KEY_DISPLAY_ICON = "display_icon"
            METADATA_KEY_DISPLAY_ICON_URI = "display_icon_uri"
            METADATA_KEY_ART_URI = "art_uri"
            METADATA_KEY_ALBUM_ART_URI = "album_art_uri"
        class Builder:
            def __init__(self):
                self.data = {}
            def putString(self, key, val):
                self.data[key] = val
            def putLong(self, key, val):
                self.data[key] = val
            def putBitmap(self, key, value):
                self.data[key] = value
            def build(self):
                return dict(self.data)
        session = SimpleNamespace(setMetadata=lambda val: recorded.append(val))
        ns = {
            "_get_session": lambda: session,
            "_load_bitmap": lambda path: {
                "/cache/video.jpg": "VIDEO_BITMAP",
                "/cache/channel.jpg": "CHANNEL_BITMAP",
            }.get(path),
            "MediaMetadataBuilder": Builder,
            "MediaMetadata": Keys,
            "log": lambda *_: None,
            "vlog": lambda *_: None,
        }
        publish = isolate("media_android.py", "set_media_metadata", ns)
        publish(
            title="video", artist="channel",
            art_path="/cache/video.jpg",
            avatar_path="/cache/channel.jpg",
            avatar_uri="https://yt3.ggpht.com/channel-image=s88",
        )
        self.assertEqual(
            recorded[-1]["display_icon_uri"],
            "https://yt3.ggpht.com/channel-image=s88",
        )
        self.assertEqual(recorded[-1]["album_art"], "VIDEO_BITMAP")
        self.assertEqual(recorded[-1]["art"], "VIDEO_BITMAP")
        self.assertEqual(recorded[-1]["display_icon"], "CHANNEL_BITMAP")

        publish(
            title="next", artist="no avatar yet",
            art_path="/cache/video.jpg",
        )
        self.assertEqual(recorded[-1]["album_art"], "VIDEO_BITMAP")
        self.assertNotIn("display_icon", recorded[-1])
        self.assertNotIn("display_icon_uri", recorded[-1])
        for disallowed in (
            "http://yt3.ggpht.com/avatar",
            "https://yt3.ggpht.com.evil.example/avatar",
            "https://user:pass@yt3.ggpht.com/avatar",
            "https://example.com/avatar",
        ):
            publish(title="safety", art_path="/cache/video.jpg",
                    avatar_uri=disallowed)
            self.assertNotIn("display_icon_uri", recorded[-1])
        self.assertIn("album_art", recorded[-1])

    def test_notification_uses_channel_icon_not_video_artwork(self):
        source = (ANDROID / "media_android.py").read_text(encoding="utf-8")
        notification = source.split(
            "def create_or_update_media_notification(", 1
        )[1].split("def cancel_media_notification(", 1)[0]
        self.assertIn("icon = _load_bitmap(large_icon_path)", notification)
        self.assertIn("b.setLargeIcon(icon)", notification)
        small_icon = isolate("media_android.py", "_safe_small_icon", {
            "R_draw": SimpleNamespace(ic_media_play=12345),
            "PythonActivity": SimpleNamespace(mActivity=SimpleNamespace(
                getApplicationInfo=lambda: SimpleNamespace(icon=98765)
            )),
        })
        self.assertEqual(small_icon(), 12345)

    def test_previous_video_cover_cannot_reappear_after_track_switch(self):
        audio = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        play = audio.split("def _play_audio_base(", 1)[1].split(
            "def _download_art_async(", 1
        )[0] if "def _play_audio_base(" in audio else audio
        self.assertIn("self._art_path = None", play)
        method = audio.split("def _download_art_async(", 1)[1].split(
            "def _channel_avatar_cache_path(", 1
        )[0]
        self.assertIn("requested_video_url = str(self._last_video_url or \"\")", method)
        self.assertIn(
            'if requested_video_url != str(self._last_video_url or ""):',
            method,
        )
        self.assertLess(
            method.index('if requested_video_url != str(self._last_video_url or ""):'),
            method.index("self._art_path = art_path"),
        )

    def test_media_icons_are_compiled_android_drawables(self):
        build = (ROOT / "buildozer.spec").read_text(encoding="utf-8")
        resources = (
            ("notification_repeat.png", "pymusic_repeat.png"),
            ("notification_repeat_active.png", "pymusic_repeat_active.png"),
            ("notification_favorite.png", "pymusic_favorite.png"),
            ("notification_favorite_active.png", "pymusic_favorite_active.png"),
        )
        for original, drawable in resources:
            self.assertTrue(
                (ANDROID / "ico" / original).exists(),
                original + " is missing",
            )
            self.assertIn(
                f"android_src/ico/{original}:drawable/{drawable}", build
            )
            icon_data = (ANDROID / "ico" / original).read_bytes()
            self.assertEqual(icon_data[:8], b"\x89PNG\r\n\x1a\n")
            self.assertEqual(
                (int.from_bytes(icon_data[16:20], "big"),
                 int.from_bytes(icon_data[20:24], "big")), (24, 24)
            )
            self.assertEqual(icon_data[25], 6)  # RGBA
            # Keep original 512px Kivy icons intact, but constrain SystemUI's
            # custom actions to a centered 12px glyph with 6px transparent
            # padding per side (MIUI scales the resource up in its 5 slots).
            left, right, top, bottom = _png_rgba_alpha_bounds(
                ANDROID / "ico" / original
            )
            self.assertGreaterEqual(left, 6)
            self.assertLessEqual(right, 17)
            self.assertGreaterEqual(top, 6)
            self.assertLessEqual(bottom, 17)
        ns = {
            "_ACTION_DRAWABLE_NAMES": frozenset(
                Path(name).stem for _, name in resources
            ),
            "PythonActivity": SimpleNamespace(mActivity=SimpleNamespace(
                getPackageName=lambda: "org.koteuka404.pymusic",
                getResources=lambda: SimpleNamespace(
                    getIdentifier=lambda name, kind, package: (
                        123 if name == "pymusic_repeat_active"
                        and kind == "drawable" and package == "org.koteuka404.pymusic"
                        else 456 if name == "pymusic_favorite" else 0
                    )
                )
            )),
            "log": lambda _: None,
        }
        resolved = isolate("media_android.py", "_media_action_icon", ns)
        self.assertEqual(resolved("pymusic_repeat_active", 33), 123)
        self.assertEqual(resolved("pymusic_favorite", 33), 456)
        self.assertEqual(resolved("not_allowlisted", 33), 33)
        self.assertEqual(resolved("pymusic_favorite_active", 33), 33)

    def test_album_art_republishes_when_downloaded_or_cached(self):
        audio = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        extract = audio.split("def _extract_and_start(", 1)[1]
        self.assertIn("art_path=self._art_path, art_uri=self._thumb", extract)
        download = audio.split("def _download_art_async(", 1)[1].split(
            "def _channel_avatar_cache_path(", 1
        )[0]
        self.assertIn("Clock.schedule_once(publish_art, 0)", download)
        self.assertIn("self._publish_media_metadata_now()", download)
        self.assertIn("artwork_path=self._art_path", audio)
        media = (ANDROID / "media_android.py").read_text(encoding="utf-8")
        self.assertIn("b.setColorized(True)", media)
        self.assertIn('str(artwork_path or ""), cover_version', media)

    def test_favorite_and_repeat_use_same_player_state_as_kivy_buttons(self):
        player = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn("self._ms_favorite = self._act_favorite", player)
        self.assertIn("self._ms_repeat = self._act_repeat", player)
        self.assertIn("self.toggle_favorite()", player)
        self.assertIn("self.toggle_repeat()", player)
        self.assertIn(
            "favorite_enabled=bool(self._favorite)", player
        )
        self.assertIn(
            "repeat_enabled=bool(self.repeat)", player
        )
        code = (ANDROID / "media_android.py").read_text(encoding="utf-8")
        self.assertIn("elif act == ACTION_FAVORITE:", code)
        self.assertIn('_pi_action(ACTION_FAVORITE, 207)', code)
        self.assertIn("pb.addCustomAction(str(ACTION_FAVORITE)", code)
        callback = (
            ROOT / "java_src/org/koteuka404/pymusic/MediaSessionCallback.java"
        ).read_text(encoding="utf-8")
        self.assertIn('"org.koteuka404.pymusic.FAVORITE".equals(action)', callback)

    def test_cover_download_does_not_disable_tls_or_allow_unbounded_files(self):
        audio = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        method = audio.split("def _download_art_async(", 1)[1].split(
            "def _channel_avatar_cache_path(", 1
        )[0]
        self.assertNotIn("ssl._create_unverified_context()", method)
        self.assertIn("ssl.create_default_context(cafile=certifi.where())", method)
        self.assertIn('class SafeArtRedirect(urllib.request.HTTPRedirectHandler)', method)
        self.assertIn('image_bytes = response.read(2 * 1024 * 1024 + 1)', method)
        self.assertIn("os.replace(temp, art_path)", method)


if __name__ == "__main__":
    unittest.main()
