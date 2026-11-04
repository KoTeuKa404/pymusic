"""Offline regression checks for Android playback fixes (no device required)."""
from __future__ import annotations

import ast
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from android_src import fast_track_switch_v9 as fast_switch

ROOT = Path(__file__).resolve().parents[1] / "android_src"


def load_definition(filename, name):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {name} in {filename}, got {len(matches)}")
    return matches[0]


def isolated_definition(filename, name, namespace):
    node = load_definition(filename, name)
    isolated = ast.Module(body=[node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(isolated), filename, "exec"), namespace)
    return namespace[name]


class PlaybackRegressionTests(unittest.TestCase):
    def test_prepared_listener_does_not_pass_borrowed_jni_proxy_to_thread(self):
        class FakeThread:
            def __init__(self, target, args=(), daemon=False):
                self.target, self.args = target, args

            def start(self):
                self.target(*self.args)

        namespace = {
            "PythonJavaClass": object,
            "java_method": lambda _signature: lambda fn: fn,
            "threading": SimpleNamespace(Thread=FakeThread),
        }
        listener_type = isolated_definition(
            "media_android.py", "OnPreparedListener", namespace
        )
        stable, borrowed = object(), object()
        received = []
        listener_type(received.append, stable).onPrepared(borrowed)
        self.assertEqual(received, [stable])
        self.assertIsNot(received[0], borrowed)

    def test_fast_switch_detaches_old_player_without_releasing_new_one(self):
        jobs = []
        events = []

        def deferred_ui(fn):
            def schedule(*args):
                jobs.append((fn, args))
            return schedule

        class Player:
            def __init__(self, name):
                self.name = name

            def setVolume(self, left, right):
                events.append((self.name, "mute"))

            def isPlaying(self):
                return True

            def pause(self):
                events.append((self.name, "pause"))

            def release(self):
                events.append((self.name, "release"))

        old_player, new_player = Player("old"), Player("new")
        listeners = (object(), object())
        media = SimpleNamespace(
            run_on_ui_thread=deferred_ui,
            _mp_state_lock=threading.RLock(),
            android_player=old_player,
            _mp_listeners=listeners,
            _mp_generation=4,
            _mp_prepared=True,
            is_playing=True,
            _mp_detach_listeners=lambda player: events.append((player.name, "detach")),
            log=lambda _msg: None,
        )
        fast_switch._patch_media_release(media)
        media._mp_reset_release()
        self.assertIsNone(media.android_player)
        self.assertIsNone(media._mp_listeners)
        self.assertEqual(media._mp_generation, 5)
        self.assertEqual(len(jobs), 1)
        self.assertIs(jobs[0][1][1], listeners)
        media.android_player = new_player  # New track starts before UI release.
        for fn, args in jobs:
            fn(*args)
        self.assertEqual(
            events,
            [("old", "detach"), ("old", "mute"),
             ("old", "pause"), ("old", "release")],
        )
        self.assertIs(media.android_player, new_player)

    def test_current_panel_accepts_two_dimensional_kivy_spacing(self):
        namespace = {"dp": float}
        apply_height = isolated_definition(
            "audio_screen.py", "_current_apply_height", namespace
        )
        listing = SimpleNamespace(
            children=[SimpleNamespace(height=160), SimpleNamespace(height=160)],
            spacing=[2, 7], height=0,
        )
        scroll = SimpleNamespace(height=0, opacity=0, disabled=True)
        screen = SimpleNamespace(ids={})
        apply_height(screen, scroll, listing)
        self.assertEqual(listing.height, 341)
        self.assertEqual(scroll.height, 341)
        self.assertEqual(scroll._current_v4_height, 341)

    def test_recommendation_render_is_deferred_off_kivy_thread(self):
        jobs = []
        namespace = {
            "on_kivy_thread": lambda: False,
            "Clock": SimpleNamespace(
                schedule_once=lambda callback, delay: jobs.append((callback, delay))
            ),
        }
        render = isolated_definition("pymusic_runtime_patch.py", "render_similar", namespace)
        self.assertIsNone(render(object(), force=True))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0][1], 0)


    def test_prefers_real_aac_over_muxed_format_18(self):
        from typing import Any, Dict, List, Optional
        namespace = {"Any": Any, "Dict": Dict, "List": List, "Optional": Optional}
        pick = isolated_definition("ytdlp_helpers.py", "_pick_best_audio", namespace)
        muxed = {"url": "https://cdn.example/18", "format_id": "18",
                 "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2"}
        opus = {"url": "https://cdn.example/251", "format_id": "251",
                "ext": "webm", "vcodec": "none", "acodec": "opus"}
        aac = {"url": "https://cdn.example/140", "format_id": "140",
               "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}
        self.assertEqual(pick([muxed, opus, aac])["format_id"], "140")
        self.assertEqual(pick([muxed, opus, aac], prefer_compat=True)["format_id"], "140")

    def test_youtube_clients_preserve_author_and_continue_until_audio_only(self):
        from typing import Any, Dict, Optional, Tuple
        queries = []
        muxed = {"url": "https://cdn.example/18", "format_id": "18",
                 "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2"}
        aac = {"url": "https://cdn.example/140", "format_id": "140",
               "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}

        class FakeYoutubeDL:
            def __init__(self, options):
                self.client = options["extractor_args"]["youtube"]["player_client"][0]

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download=False):
                queries.append(self.client)
                if self.client == "android":
                    return {"formats": [muxed], "thumbnail": "https://images.example/cover"}
                if self.client == "web_embedded":
                    return {"formats": [muxed, aac], "channel": "Grimwind"}
                raise AssertionError(self.client)

        namespace = {
            "Any": Any, "Dict": Dict, "Optional": Optional,
            "Tuple": Tuple, "YoutubeDL": FakeYoutubeDL,
        }
        choose = isolated_definition(
            "ytdlp_helpers.py", "_extract_info_with_clients", namespace
        )
        info, error = choose(
            "https://www.youtube.com/watch?v=LTsbyX-l06Y",
            {"quiet": True},
            ("android", "web_embedded"),
        )
        self.assertIsNone(error)
        self.assertEqual(queries, ["android", "web_embedded"])
        self.assertEqual(info["channel"], "Grimwind")
        self.assertEqual(info["thumbnail"], "https://images.example/cover")
        self.assertEqual(len(info["formats"]), 2)


if __name__ == "__main__":
    unittest.main()
