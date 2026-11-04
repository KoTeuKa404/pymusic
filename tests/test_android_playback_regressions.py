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
        retired = []
        media = SimpleNamespace(
            run_on_ui_thread=deferred_ui,
            _mp_state_lock=threading.RLock(),
            android_player=old_player,
            _mp_listeners=listeners,
            _mp_generation=4,
            _mp_prepared=True,
            is_playing=True,
            _mp_detach_listeners=lambda player: events.append((player.name, "detach")),
            _mp_retire_listeners=retired.append,
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
        self.assertEqual(retired, [listeners])

    def test_current_panel_accepts_two_dimensional_kivy_spacing(self):
        namespace = {
            "dp": float,
            "_current_video_url_of": lambda screen: "https://www.youtube.com/watch?v=LTsbyX-l06Y",
        }
        apply_height = isolated_definition(
            "audio_screen.py", "_current_apply_height", namespace
        )
        listing = SimpleNamespace(
            children=[SimpleNamespace(height=160), SimpleNamespace(height=160)],
            spacing=[2, 7], height=0, minimum_height=0,
        )
        scroll = SimpleNamespace(height=0, opacity=0, disabled=True)
        controls = []
        screen = SimpleNamespace(
            ids={}, _similar_collapsed=False,
            _sync_current_lower_controls=lambda has_video: controls.append(has_video),
        )
        apply_height(screen, scroll, listing)
        self.assertEqual(listing.height, 343)
        self.assertEqual(scroll.height, 280)
        self.assertEqual(scroll._current_v4_height, 280)
        self.assertTrue(scroll.do_scroll_y)
        self.assertEqual(controls, [True])
        screen._similar_collapsed = True
        apply_height(screen, scroll, listing)
        self.assertEqual(scroll.height, 0)
        self.assertTrue(scroll.disabled)

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


    def test_healthy_tracks_keep_android_client_and_only_recover_on_failure(self):
        from typing import Tuple
        chooser = isolated_definition(
            "ytdlp_helpers.py", "_audio_client_order",
            {"Tuple": Tuple, "_env_bool": lambda *_args: False},
        )
        self.assertEqual(chooser(False), ("android",))
        self.assertEqual(
            chooser(True), ("web_embedded", "android", "web_embedded")
        )

    def test_channel_name_fallback_uses_official_oembed_and_validated_id(self):
        import json
        import re
        import urllib.parse
        calls = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _bytes):
                return b'{"author_name":"Grimwind"}'

        def fake_request(url, headers):
            calls.append(url)
            self.assertEqual(headers["Accept"], "application/json")
            return url

        network = SimpleNamespace(request=SimpleNamespace(
            Request=fake_request,
            urlopen=lambda _request, timeout=12: FakeResponse(),
        ))
        namespace = {
            "re": re, "json": json, "urllib": network,
            "urlparse": urllib.parse,
            "_ANDROID_WEB_UA": "Mozilla/5.0",
            "_extract_video_id_from_url": lambda url: (
                "LTsbyX-l06Y" if url.endswith("watch?v=LTsbyX-l06Y") else ""
            ),
        }
        channel = isolated_definition(
            "ytdlp_helpers.py", "_channel_name_from_oembed", namespace
        )
        url = "https://www.youtube.com/watch?v=LTsbyX-l06Y"
        self.assertEqual(channel(url), "Grimwind")
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("https://www.youtube.com/oembed?"))
        self.assertEqual(channel("http://127.0.0.1/secrets"), "")
        self.assertEqual(len(calls), 1)


    def test_notification_intent_callback_bound_only_once_after_many_tracks(self):
        bound, unbound = [], []
        callback = object()
        state = {
            "_action_owner": None,
            "_intent_router_bound": False,
            "_intent_router_lock": threading.RLock(),
            "_py_activity": SimpleNamespace(
                bind=lambda **kwargs: bound.append(kwargs),
                unbind=lambda **kwargs: unbound.append(kwargs),
            ),
            "_on_new_intent": callback,
            "log": lambda _message: None,
        }
        bind_intent = isolated_definition(
            "media_android.py", "bind_intent_router", state
        )
        bind_owner = isolated_definition(
            "media_android.py", "bind_notification_action_router", state
        )
        unbind = isolated_definition(
            "media_android.py", "unbind_notification_action_router", state
        )
        state["bind_intent_router"] = bind_intent
        for index in range(50):
            bind_owner(index)
            bind_intent()
        self.assertEqual(len(bound), 1)
        self.assertIs(bound[0]["on_new_intent"], callback)
        self.assertEqual(state["_action_owner"], 49)
        unbind()
        unbind()
        self.assertEqual(len(unbound), 1)
        bind_owner(51)
        self.assertEqual(len(bound), 2)

    def test_webview_repeated_navigation_is_delivered_once(self):
        clock = SimpleNamespace(now=10.0)
        state = {
            "threading": threading,
            "time": SimpleNamespace(monotonic=lambda: clock.now),
            "_web_dispatch_lock": threading.Lock(),
            "_web_dispatch_last": ("", 0.0),
        }
        should = isolated_definition(
            "media_android.py", "_should_dispatch_webview_video", state
        )
        first = (
            "https://m.youtube.com/watch?v=LTsbyX-l06Y"
            "&list=RDLTsbyX-l06Y&start_radio=1"
        )
        duplicate_with_different_tracking = (
            "https://www.youtube.com/watch?v=LTsbyX-l06Y"
            "&list=RDLTsbyX-l06Y&pp=tracking"
        )
        second = "https://m.youtube.com/watch?v=BSxesC31kto"
        self.assertTrue(should(first))
        self.assertEqual([should(duplicate_with_different_tracking)
                          for _ in range(60)], [False] * 60)
        self.assertTrue(should(second))
        clock.now += 2.0
        self.assertTrue(should(first))

    def test_retired_jni_proxies_remain_bounded_and_strongly_referenced(self):
        from collections import deque
        retained = deque(maxlen=64)
        state = {"_mp_retired_listeners": retained}
        retire = isolated_definition(
            "media_android.py", "_mp_retire_listeners", state
        )
        callbacks = [(object(),) for _ in range(100)]
        for callback in callbacks:
            retire(callback)
        self.assertEqual(len(retained), 64)
        self.assertIs(retained[-1], callbacks[-1])
        self.assertIs(retained[0], callbacks[-64])



    def test_comments_are_never_extracted_before_sheet_open(self):
        # A track/metadata refresh must be a no-op until the user taps.
        calls = []
        namespace = {
            "_current_video_url_of": lambda _screen: "https://www.youtube.com/watch?v=LTsbyX-l06Y",
        }
        ensure = isolated_definition(
            "audio_screen.py", "_current_ensure_comments", namespace
        )
        screen = SimpleNamespace(
            _current_ensure_state=lambda: calls.append("ensure"),
            _current_comments_popup_v4=None,
            _app_in_background=False,
        )
        ensure(screen)
        self.assertEqual(calls, ["ensure"])

    def test_similar_swipe_owns_touch_and_restores_parent_scroll(self):
        bindings = {}
        window_handlers = {}

        class FakeWidget:
            def bind(self, **kwargs):
                bindings.update(kwargs)

            def collide_point(self, *_pos):
                return True

        class FakeWindow:
            def bind(self, **kwargs):
                window_handlers.update(kwargs)

        inner = FakeWidget()
        inner.height = 280
        outer = SimpleNamespace(do_scroll_y=True)
        namespace = {"Window": FakeWindow()}
        bind = isolated_definition(
            "audio_screen.py", "_bind_similar_scroll_input", namespace
        )
        screen = SimpleNamespace(
            ids={"similar_scroll": inner, "player_details_scroll": outer},
            _similar_collapsed=False,
            _is_screen_active=lambda: True,
        )
        bind(screen)
        self.assertTrue(inner._pymusic_touch_ownership_bound)
        touch = SimpleNamespace(uid="finger-1", pos=(10, 10))
        bindings["on_touch_down"](inner, touch)
        self.assertFalse(outer.do_scroll_y)
        window_handlers["on_touch_up"](None, touch)
        self.assertTrue(outer.do_scroll_y)
        self.assertIsNone(inner._pymusic_active_touch_uid)

    def test_current_player_kv_has_compact_autoplay_and_tap_to_open_comments(self):
        kv = (ROOT / "youtube_gui.kv").read_text(encoding="utf-8")
        self.assertIn("id: autoplay_btn", kv)
        self.assertIn("on_release: root.toggle_autoskip()", kv)
        self.assertIn("id: comments_trigger", kv)
        self.assertIn("on_release: root.open_comments_sheet()", kv)
        self.assertIn("id: similar_scroll", kv)


if __name__ == "__main__":
    unittest.main()
