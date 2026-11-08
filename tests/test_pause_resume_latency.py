"""Regression tests for low-latency Pause → Play on remote Android media.

Uses AST-extracted Python functions; does not require an Android device.
Java NativeTransportBridge source invariants are checked until instrumented
Android media tests can run on a real device.
"""
from __future__ import annotations

import ast
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android_src"
TRANSPORT = ROOT / "java_src/org/koteuka404/pymusic/NativeTransportBridge.java"


def extract_method(name: str, namespace: dict):
    module = ast.parse((ANDROID / "audio_screen.py").read_text(encoding="utf-8"))
    methods = [
        item
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "AudioPlayerScreen"
        for item in node.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    ]
    # The screen may be extended/mixed-in elsewhere, so use the unique AST name.
    if not methods:
        methods = [
            node for node in ast.walk(module)
            if isinstance(node, ast.FunctionDef) and node.name == name
        ]
    if len(methods) != 1:
        raise AssertionError(f"{name}: expected once, got {len(methods)}")
    fn = ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[]))
    exec(compile(fn, "audio_screen.py", "exec"), namespace)
    return namespace[name]


class PauseResumeRegressions(unittest.TestCase):
    def test_native_transport_never_seeks_on_pause_or_resume(self):
        java = TRANSPORT.read_text(encoding="utf-8")
        toggle = java.split("void toggle(", 1)[1].split(
            "void seekBy(", 1
        )[0]
        explicit_seek = java.split("void seekBy(", 1)[1]
        self.assertNotRegex(toggle, r"(?m)^\s*safeSeekFast\(")
        self.assertIn("safeSeekFast(audio, target)", explicit_seek)
        self.assertIn("safeSeekFast(video, target)", explicit_seek)
        self.assertLess(toggle.index("safeUnmute(audio)"), toggle.index("safeStart(audio)"))
        self.assertLess(toggle.index("safeStart(audio)"), toggle.index("safeStart(video)"))
        self.assertIn("toggledOnce", toggle)
        self.assertIn("if (audio == audioPlayer)", toggle)

    def test_resume_reuses_prepared_audio_without_extraction_or_seek(self):
        actions = []
        media = SimpleNamespace(
            android_player=object(),
            _mp_start=lambda: actions.append("audio_start"),
        )
        video = SimpleNamespace(
            player=object(),
            resume=lambda: actions.append("video_resume"),
        )
        namespace = {"ma": media, "time": time, "threading": SimpleNamespace(
            Thread=lambda **kw: (_ for _ in ()).throw(
                AssertionError("no thread should spawn on normal resume")
            )
        )}
        resume = extract_method("_resume_playback", namespace)
        owner = SimpleNamespace(
            _mark_java_transport=lambda state: actions.append(("mark", state)),
            _user_paused=True,
            _playback_desired=False,
            _resume_pos_ms=55000,
            _video_player=video,
            _video_enabled=True,
            _video_active=True,
            _last_video_url="https://www.youtube.com/watch?v=abc123def45",
            _load_gen=4,
            _ui_set_playing=lambda state: actions.append(("ui", state)),
            _auto_video_for_current=lambda *args, **kwargs: (
                (_ for _ in ()).throw(AssertionError("video re-extracted"))
            ),
            _extract_and_start_gen=lambda *args: (
                (_ for _ in ()).throw(AssertionError("audio re-extracted"))
            ),
            _force_video_resync=lambda: (
                (_ for _ in ()).throw(AssertionError("forced video seek"))
            ),
        )
        resume(owner)
        self.assertEqual(actions, [
            ("mark", False), "audio_start", "video_resume", ("ui", True)
        ])
        self.assertTrue(owner._playback_desired)
        self.assertFalse(owner._user_paused)
        self.assertGreater(owner._resume_requested_t0, 0)

    def test_resume_without_native_video_does_not_extract_again(self):
        actions = []
        media = SimpleNamespace(
            android_player=object(),
            _mp_start=lambda: actions.append("audio"),
        )
        resume = extract_method("_resume_playback", {"ma": media, "time": time})
        owner = SimpleNamespace(
            _mark_java_transport=lambda _: None,
            _resume_pos_ms=15000,
            _video_player=SimpleNamespace(player=None),
            _video_enabled=True, _video_active=False,
            _last_video_url="https://www.youtube.com/watch?v=abc123def45",
            _ui_set_playing=lambda state: actions.append(("ui", state)),
            _force_video_resync=lambda: (
                (_ for _ in ()).throw(AssertionError("seek on resume"))
            ),
        )
        resume(owner)
        self.assertEqual(actions, ["audio", ("ui", True)])

    def test_pause_during_buffering_uses_logical_state(self):
        namespace = {"ma": SimpleNamespace(android_player=SimpleNamespace(
            isPlaying=lambda: False
        ))}
        toggle = extract_method("toggle_play_pause", namespace)
        actions = []
        owner = SimpleNamespace(
            _debounce=lambda: True,
            _playback_desired=True,
            _user_paused=False,
            _pause_playback=lambda: actions.append("pause"),
            _resume_playback=lambda: actions.append("play"),
        )
        toggle(owner)
        self.assertEqual(actions, ["pause"])
        owner._playback_desired = False
        owner._user_paused = True
        toggle(owner)
        self.assertEqual(actions, ["pause", "play"])

    def test_pause_resume_metric_is_logged_only_after_position_moves(self):
        python = (ANDROID / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn("[LATENCY] pause-to-first-progress", python)
        self.assertIn("int(pos or 0) >= before + 250", python)
        self.assertIn("self._resume_requested_t0 = 0.0", python)
        self.assertNotIn(
            "Clock.schedule_once(lambda dt: self._force_video_resync(), 0.35)",
            python.split("def _resume_playback(self):", 1)[1].split(
                "def _force_video_resync(self):", 1
            )[0],
        )


if __name__ == "__main__":
    unittest.main()
