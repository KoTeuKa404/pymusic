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
        formats_by_client = {}
        muxed = {"url": "https://cdn.example/18", "format_id": "18",
                 "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a.40.2"}
        aac = {"url": "https://cdn.example/140", "format_id": "140",
               "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}

        class FakeYoutubeDL:
            def __init__(self, options):
                self.client = options["extractor_args"]["youtube"]["player_client"][0]
                self.selected_format = options.get("format")
                formats_by_client[self.client] = self.selected_format

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
            "_AUDIO_CLIENT_HINT_LOCK": threading.RLock(),
            "_ANDROID_CLIENT_MUXED_ONLY": False,
        }
        choose = isolated_definition(
            "ytdlp_helpers.py", "_extract_info_with_clients", namespace
        )
        info, error = choose(
            "https://www.youtube.com/watch?v=LTsbyX-l06Y",
            {"quiet": True, "format": "bestaudio[ext=m4a]/best"},
            ("android", "web_embedded"),
        )
        self.assertIsNone(error)
        self.assertEqual(queries, ["android", "web_embedded"])
        self.assertEqual(formats_by_client["android"], "bestaudio[ext=m4a]/best")
        self.assertIsNone(formats_by_client["web_embedded"])
        self.assertEqual(info["channel"], "Grimwind")
        self.assertEqual(info["thumbnail"], "https://images.example/cover")
        self.assertEqual(len(info["formats"]), 2)
        self.assertTrue(namespace["_ANDROID_CLIENT_MUXED_ONLY"])


    def test_healthy_tracks_keep_android_client_and_only_recover_on_failure(self):
        from typing import Tuple
        chooser = isolated_definition(
            "ytdlp_helpers.py", "_audio_client_order",
            {"Tuple": Tuple, "_env_bool": lambda *_args: False,
             "_AUDIO_CLIENT_HINT_LOCK": threading.RLock(),
             "_ANDROID_CLIENT_MUXED_ONLY": False},
        )
        self.assertEqual(chooser(False), ("android", "tv_embedded"))
        self.assertEqual(
            chooser(True), ("web_embedded", "tv_embedded", "android", "ios")
        )

    def test_muxed_only_client_learning_skips_redundant_android_probe(self):
        from typing import Tuple
        ns = {
            "Tuple": Tuple,
            "_env_bool": lambda *_args: False,
            "_AUDIO_CLIENT_HINT_LOCK": threading.RLock(),
            "_ANDROID_CLIENT_MUXED_ONLY": True,
        }
        choose = isolated_definition(
            "ytdlp_helpers.py", "_audio_client_order", ns
        )
        self.assertEqual(choose(False), ("tv_embedded", "android"))
        self.assertEqual(
            choose(True), ("web_embedded", "tv_embedded", "android", "ios")
        )
        ns["_ANDROID_CLIENT_MUXED_ONLY"] = False
        self.assertEqual(choose(False), ("android", "tv_embedded"))

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
            urlopen=lambda _request, timeout=7, context=None: FakeResponse(),
        ))
        namespace = {
            "re": re, "json": json, "urllib": network,
            "urlparse": urllib.parse, "time": __import__("time"),
            "_CHANNEL_NAME_CACHE": {},
            "_CHANNEL_AUTHOR_URL_CACHE": {},
            "_CHANNEL_NAME_CACHE_LOCK": threading.RLock(),
            "_validated_youtube_channel_url": lambda v: v,
            "ssl": SimpleNamespace(create_default_context=lambda cafile: object()),
            "certifi": SimpleNamespace(where=lambda: "/trusted/cert.pem"),
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


    def test_video_surface_attach_waits_for_valid_native_surface(self):
        jobs = []
        valid_checks = []
        attached = []
        prepared = []

        class Surface:
            def isValid(self):
                valid_checks.append(True)
                return len(valid_checks) >= 4

        class Holder:
            def getSurface(self):
                return Surface()

        class SurfaceView:
            def getParent(self):
                return object()

            def setVisibility(self, _value):
                pass

            def getHolder(self):
                return Holder()

        class Player:
            def setDisplay(self, holder):
                attached.append(holder)

            def prepareAsync(self):
                prepared.append(True)

        namespace = {
            "Clock": SimpleNamespace(
                schedule_once=lambda cb, _delay=0: jobs.append(cb)),
            "run_on_ui_thread": lambda fn: fn,
            "View": SimpleNamespace(VISIBLE=0),
        }
        method = isolated_definition(
            "video_player.py", "_attach_and_prepare_when_surface_ready",
            namespace,
        )
        owner = SimpleNamespace(
            _play_gen=12, player=Player(),
            surface_view=SurfaceView(),
            _frame_bounds=(10, 20, 160, 90),
            _surface_retry_limit=7,
            _apply_surface_bounds=lambda: None,
            _bring_controls_or_surface_front=lambda: None,
            create_surface=lambda: None,
            stop=lambda: self.fail("unexpected stop"),
        )
        method(owner, 12)
        for _ in range(10):
            if not jobs:
                break
            jobs.pop(0)(0)
        self.assertEqual(len(valid_checks), 4)
        self.assertEqual(len(attached), 1)
        self.assertEqual(prepared, [True])
        self.assertFalse(jobs)

    def test_video_surface_never_attaches_released_surface(self):
        jobs, calls = [], []

        class Surface:
            def isValid(self):
                return False

        class Holder:
            def getSurface(self):
                return Surface()

        class SurfaceView:
            def getParent(self):
                return object()

            def setVisibility(self, _value):
                pass

            def getHolder(self):
                return Holder()

        namespace = {
            "Clock": SimpleNamespace(
                schedule_once=lambda cb, _delay=0: jobs.append(cb)),
            "run_on_ui_thread": lambda fn: fn,
            "View": SimpleNamespace(VISIBLE=0),
        }
        method = isolated_definition(
            "video_player.py", "_attach_and_prepare_when_surface_ready",
            namespace,
        )
        owner = SimpleNamespace(
            _play_gen=3, player=SimpleNamespace(
                setDisplay=lambda holder: calls.append("attached"),
                prepareAsync=lambda: calls.append("prepared"),
            ), surface_view=SurfaceView(), _frame_bounds=None,
            _surface_retry_limit=2, _prepared=True,
            _apply_surface_bounds=lambda: None,
            _bring_controls_or_surface_front=lambda: None,
            create_surface=lambda: None,
            stop=lambda: calls.append("stopped"),
        )
        def report_failure(gen, reason):
            self.assertEqual(gen, 3)
            self.assertEqual(reason, "native surface unavailable")
            owner._prepared = False
            owner.stop()
        owner._fail_video_play = report_failure
        method(owner, 3)
        for _ in range(8):
            if not jobs:
                break
            jobs.pop(0)(0)
        self.assertEqual(calls, ["stopped"])
        self.assertFalse(owner._prepared)

    def test_video_title_never_becomes_channel_from_recommendations(self):
        verified = isolated_definition(
            "audio_screen.py", "_verified_channel_name", {}
        )
        title = "Grimwind - Гвардия! Только Вперед!"
        self.assertEqual(verified(title, title), "")
        self.assertEqual(verified("Grimwind", title), "Grimwind")
        self.assertEqual(verified("YouTube", title), "")
        self.assertEqual(verified("", title), "")

    def test_failed_format18_recovery_does_not_reuse_same_muxed_url(self):
        from typing import Any, Dict, Optional
        muxed = {
            "url": "https://cdn.example/18", "format_id": "18",
            "ext": "mp4", "acodec": "mp4a.40.2", "vcodec": "avc1"
        }
        ns = {
            "Any": Any, "Dict": Dict, "Optional": Optional,
            "YDLLogger": lambda: None, "_ANDROID_YT_UA": "Android",
            "_audio_client_order": lambda _: ("web_embedded", "android"),
            "_extract_info_with_clients": lambda *_a: (
                {"formats": [muxed], "format_id": "18"}, None),
            "_pick_best_audio": lambda *_a, **_k: None,
            "_cache_extracted_formats": lambda *_args: None,
            "_best_effort_headers": lambda src, defaults: defaults,
        }
        extract = isolated_definition("ytdlp_helpers.py", "extract_audio_info", ns)
        with self.assertRaisesRegex(RuntimeError, "Alternative audio-only"):
            extract("https://www.youtube.com/watch?v=LTsbyX-l06Y",
                    prefer_compat=True)


    def test_android_video_sources_prefer_hls_before_broken_format18(self):
        from typing import Any, Dict, List
        from urllib import parse
        hls_host = "https://manifest.googlevideo.com/api/manifest/hls_playlist"
        mp4_host = "https://rr4---sn-abc.googlevideo.com/videoplayback"
        hls = [
            {"format_id": str(fid), "url": f"{hls_host}/{fid}",
             "height": height, "protocol": "m3u8_native",
             "vcodec": "avc1.4d401f", "acodec": "mp4a.40.2",
             "ext": "mp4"}
            for fid, height in ((93, 360), (94, 480), (95, 720))
        ]
        muxed = {"format_id": "18", "url": mp4_host + "?itag=18",
                 "height": 360, "protocol": "https",
                 "vcodec": "avc1.42001e", "acodec": "mp4a.40.2",
                 "ext": "mp4"}
        dash = {"format_id": "137", "url": mp4_host + "?itag=137",
                "height": 1080, "protocol": "https",
                "vcodec": "avc1.640028", "acodec": "none",
                "ext": "mp4"}
        malicious = {"format_id": "666",
                     "url": "https://manifest.googlevideo.com.evil.example/watch",
                     "protocol": "m3u8_native", "vcodec": "avc1",
                     "acodec": "aac", "height": 720}
        namespace = {
            "Any": Any, "Dict": Dict, "List": List, "urlparse": parse,
            "_best_effort_headers": lambda src, defaults: dict(src or defaults),
        }
        chooser = isolated_definition(
            "ytdlp_helpers.py", "_video_stream_candidates", namespace
        )
        selected = chooser([muxed, dash, malicious, *hls], {}, limit=4)
        self.assertEqual(
            [item["format_id"] for item in selected],
            ["94", "93", "18"]
        )
        self.assertEqual(
            [item["protocol"] for item in selected],
            ["hls", "hls", "mp4"]
        )

    def test_video_extractor_uses_web_embedded_hls_not_android_only_mp4(self):
        from typing import Any, Dict, List
        requests = []

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
                requests.append(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, url, download=False):
                self_url = "https://manifest.googlevideo.com/api/manifest/hls_playlist/95"
                return {
                    "thumbnail": "https://i.ytimg.com/vi/example/maxresdefault.jpg",
                    "formats": [{
                        "format_id": "95", "url": self_url,
                        "protocol": "m3u8_native", "vcodec": "avc1",
                        "acodec": "mp4a.40.2", "height": 720,
                        "ext": "mp4",
                    }]
                }
        namespace = {
            "Any": Any, "Dict": Dict, "List": List,
            "YoutubeDL": FakeYoutubeDL,
            "YDLLogger": lambda: None,
            "_ANDROID_WEB_UA": "android web",
            "_video_stream_candidates": lambda formats, headers, limit=5: [{
                "video_url": formats[0]["url"], "http_headers": headers,
                "format_id": formats[0]["format_id"], "protocol": "hls",
                "height": 720, "video_only": False,
            }],
            "_get_cached_formats": lambda video_url: None,
            "_cache_extracted_formats": lambda *args: None,
        }
        extract = isolated_definition(
            "ytdlp_helpers.py", "extract_video_info", namespace
        )
        info = extract("https://www.youtube.com/watch?v=LTsbyX-l06Y")
        self.assertIn("/hls_playlist/", info["video_url"])
        self.assertEqual(len(requests), 1)
        self.assertIsNone(requests[0]["format"])
        self.assertFalse(requests[0].get("nocheckcertificate", False))
        self.assertEqual(
            requests[0]["extractor_args"]["youtube"]["player_client"],
            ["web_embedded"],
        )

    def test_video_controls_and_geometry_activated_while_preparing(self):
        source = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn("_pymusic_video_starting", source)
        self.assertIn('self._video_player.play(', source)
        self.assertIn('on_error=on_error', source)
        self.assertIn('attempt_state["watchdog"]', source)
        self.assertIn('start_candidate(index + 1)', source)
        player = (ROOT / "video_player.py").read_text(encoding="utf-8")
        self.assertIn("self._fail_video_play(gen,", player)
        self.assertIn('self._on_error_cb = on_error', player)


    def test_runtime_video_wrapper_keeps_hls_extractor_and_callback_contract(self):
        """End-to-end bootstrap regression for the Redmi on_ready crash.

        Runtime patch installation must not silently revert the HLS extractor
        to muxed format 18, nor replace the current callback-aware native
        video method with an incompatible two-argument method.
        """
        import sys
        from unittest.mock import patch
        from android_src import core_player_runtime as runtime

        events = []
        original_video_extractor = object()

        class FakeScreen:
            def _set_video_mode_core(self, video_on):
                events.append(("video_mode", video_on))

            def _play_video_if_screen_active(
                self, url, headers, *, on_ready=None, on_error=None
            ):
                events.append(("play", url, dict(headers)))
                if on_ready is not None:
                    on_ready()

        class FakeVideo:
            pass

        fake_audio = SimpleNamespace(
            AudioPlayerScreen=FakeScreen,
            Clock=SimpleNamespace(
                schedule_once=lambda cb, delay=0: cb(0)
            ),
            ma=SimpleNamespace(),
        )
        fake_video = SimpleNamespace(
            AndroidVideoPlayer=FakeVideo,
            run_on_ui_thread=lambda f: f,
        )
        fake_ydl = SimpleNamespace(
            safe_extract_video_info=original_video_extractor
        )
        original_installed = runtime._INSTALLED
        original_muxed_urls = set(runtime._MUXED_URLS)
        try:
            runtime._INSTALLED = False
            runtime._MUXED_URLS.clear()
            with patch.dict(sys.modules, {
                "audio_screen": fake_audio,
                "video_player": fake_video,
                "ytdlp_helpers": fake_ydl,
            }):
                self.assertTrue(runtime.install_core_player_fix())
                self.assertIs(
                    fake_ydl.safe_extract_video_info, original_video_extractor,
                    "HLS extractor must not be replaced by format-18-only muxed picker",
                )
                screen = FakeScreen()
                stream = (
                    "https://manifest.googlevideo.com/api/manifest/hls_playlist/95"
                )
                # Even when an older muxed-prefetch worker has registered
                # the URL, explicit HLS candidate callbacks must win.
                runtime._MUXED_URLS.add(stream)
                screen._play_video_if_screen_active(
                    stream, {"User-Agent": "TestUA"},
                    on_ready=lambda: events.append(("ready",)),
                    on_error=lambda reason: events.append(("error", reason)),
                )
                self.assertEqual(events, [
                    ("play", stream, {"User-Agent": "TestUA"}),
                    ("ready",),
                ])
                runtime._MUXED_URLS.discard(stream)
                # The pre-HLS call signature still works for legacy clients.
                screen._play_video_if_screen_active(
                    stream, {}, on_ready=None, on_error=None
                )
                self.assertEqual(events[-1], ("play", stream, {}))
        finally:
            runtime._INSTALLED = original_installed
            runtime._MUXED_URLS.clear()
            runtime._MUXED_URLS.update(original_muxed_urls)

    def test_video_candidate_startup_exceptions_are_caught(self):
        source = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        target = source.index('def start_candidate(index: int):')
        handler = source.index('except Exception as exc:', target)
        self.assertLess(target, handler)
        self.assertIn(
            'failed(f"startup {type(exc).__name__}")',
            source[handler:handler + 350],
        )


    def test_tv_client_video_only_avc_can_drive_muted_android_video(self):
        from typing import Any, Dict, List
        from urllib import parse
        base = "https://rr3---sn-abc.googlevideo.com/videoplayback"
        hls = "https://manifest.googlevideo.com/api/manifest/hls_playlist/"
        ns = {
            "Any": Any, "Dict": Dict, "List": List, "urlparse": parse,
            "_best_effort_headers": lambda src, default: dict(src or default),
        }
        chooser = isolated_definition(
            "ytdlp_helpers.py", "_video_stream_candidates", ns
        )
        fmts = [
            {"format_id": "18", "url": base + "?itag=18",
             "height": 360, "vcodec": "avc1.42001e", "acodec": "mp4a.40.2",
             "protocol": "https", "ext": "mp4"},
            {"format_id": "231", "url": hls + "231",
             "height": 480, "vcodec": "avc1.4d401f", "acodec": "none",
             "protocol": "m3u8_native", "ext": "mp4"},
            {"format_id": "230", "url": hls + "230",
             "height": 360, "vcodec": "avc1.4d401e", "acodec": "none",
             "protocol": "m3u8_native", "ext": "mp4"},
            {"format_id": "135", "url": base + "?itag=135",
             "height": 480, "vcodec": "avc1.4d401f", "acodec": "none",
             "protocol": "https", "ext": "mp4"},
            {"format_id": "247", "url": base + "?itag=247",
             "height": 720, "vcodec": "vp9", "acodec": "none",
             "protocol": "https", "ext": "webm"},
        ]
        result = chooser(fmts, {"User-Agent": "TestUA"})
        self.assertEqual(
            [item["format_id"] for item in result],
            ["231", "230", "135", "18"],
        )
        self.assertTrue(result[0]["video_only"])
        self.assertFalse(result[-1]["video_only"])
        self.assertNotIn("247", [x["format_id"] for x in result])

    def test_audio_picker_chooses_standard_aac_140_not_he_aac_139(self):
        from typing import Any, Dict, List, Optional
        chooser = isolated_definition(
            "ytdlp_helpers.py", "_pick_best_audio",
            {"Any": Any, "Dict": Dict, "List": List, "Optional": Optional},
        )
        candidates = [
            {"format_id": "139", "url": "https://cdn.example/139",
             "height": 0, "ext": "m4a", "vcodec": "none",
             "acodec": "mp4a.40.5", "abr": 48},
            {"format_id": "140", "url": "https://cdn.example/140",
             "height": 0, "ext": "m4a", "vcodec": "none",
             "acodec": "mp4a.40.2", "abr": 129},
        ]
        self.assertEqual(chooser(candidates)["format_id"], "140")
        self.assertEqual(
            chooser(candidates, prefer_compat=True)["format_id"], "140"
        )

    def test_video_uses_cached_tv_embedded_formats_with_no_network_wait(self):
        from typing import Any, Dict, List
        requests = []
        selected = [{
            "video_url": "https://manifest.googlevideo.com/api/manifest/hls_playlist/231",
            "http_headers": {},
            "format_id": "231", "protocol": "hls",
            "height": 480, "video_only": True,
        }]
        ns = {
            "Any": Any, "Dict": Dict, "List": List,
            "_ANDROID_WEB_UA": "Mozilla/5.0",
            "_get_cached_formats": lambda _url: {"formats": [{"format_id":"231"}]},
            "_video_stream_candidates": lambda *_a, **_k: selected,
            "YDLLogger": lambda: None,
        }
        extract = isolated_definition("ytdlp_helpers.py", "extract_video_info", ns)
        info = extract("https://www.youtube.com/watch?v=LTsbyX-l06Y")
        self.assertEqual(info["format_id"], "231")
        self.assertEqual(info["streams"], selected)

    def test_channel_author_lookups_started_before_audio_is_prepared(self):
        source = (ROOT / "fast_track_switch_v9.py").read_text(encoding="utf-8")
        self.assertIn("start_author_lookup(self, url, gen)", source)
        self.assertIn("_channel_name_from_oembed(video_url)", source)
        self.assertIn("self._apply_info_metadata(details)", source)
        self.assertIn("pymusic-early-author", source)

    def test_audio_critical_path_skips_blocking_watch_page_requests(self):
        source = (ROOT / "ytdlp_helpers.py").read_text(encoding="utf-8")
        body = source.split("def extract_audio_info(", 1)[1].split(
            "# ============================ VIDEO API", 1
        )[0]
        self.assertNotIn("_extract_channel_meta_from_watch_page(", body)
        self.assertNotIn("_extract_related_from_watch_page(", body)


    def test_channel_avatar_url_parser_returns_full_yt3_url(self):
        import re
        import html
        from urllib import parse
        validator = isolated_definition(
            "ytdlp_helpers.py", "_validated_yt_avatar_url",
            {"html": html, "urlparse": parse},
        )
        picker = isolated_definition(
            "ytdlp_helpers.py", "_pick_yt3_avatar_from_text",
            {"re": re, "_validated_yt_avatar_url": validator},
        )
        raw = (
            '<meta property="og:image" content="'
            'https://yt3.googleusercontent.com/ytc/avatar-test=s900-c-k">'
        )
        self.assertEqual(
            picker(raw),
            "https://yt3.googleusercontent.com/ytc/avatar-test=s900-c-k",
        )
        self.assertEqual(validator("https://yt3.googleusercontent.com.evil.com/abc"), "")
        self.assertEqual(validator("http://yt3.googleusercontent.com/abc"), "")
        self.assertEqual(validator("https://i.ytimg.com/vi/abc/hqdefault.jpg"), "")

    def test_channel_avatar_resolves_official_oembed_author_page(self):
        import re
        import ssl
        import html
        import json
        import time
        from urllib import parse

        video_url = "https://www.youtube.com/watch?v=LTsbyX-l06Y"
        avatar_url = "https://yt3.googleusercontent.com/ytc/grimwind-avatar=s900"
        calls = []

        class Response:
            def __init__(self, data):
                self.data = data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, n=-1):
                return self.data if n < 0 else self.data[:n]

        def fake_urlopen(req, timeout=7, context=None):
            url = req.full_url
            calls.append(url)
            self.assertIsNotNone(context, "TLS must use verified CA roots")
            if url.startswith("https://www.youtube.com/oembed?"):
                return Response(
                    b'{"author_name":"Grimwind",'
                    b'"author_url":"https://www.youtube.com/@Grimwind",'
                    b'"thumbnail_url":"https://i.ytimg.com/vi/LTsbyX-l06Y/hqdefault.jpg"}'
                )
            if url == "https://www.youtube.com/@Grimwind":
                # Real YouTube places og:image after >800 KB of scripts.
                # A regression to the old 220 KB read limit must fail.
                return Response((
                    "<html><head>" + ("x" * 850_000)
                    + '<meta property="og:image" content="'
                    + avatar_url + '"></head></html>'
                ).encode("utf-8"))
            self.fail("Unexpected outbound request: " + url)

        ns = {
            "re": re,
            "html": html,
            "json": json,
            "time": time,
            "urlparse": parse,
            "threading": threading,
            "ssl": SimpleNamespace(
                create_default_context=lambda cafile: object()
            ),
            "certifi": SimpleNamespace(where=lambda: "/trusted/ca-bundle.pem"),
            "urllib": SimpleNamespace(
                request=SimpleNamespace(
                    Request=lambda url, headers: SimpleNamespace(full_url=url),
                    urlopen=fake_urlopen,
                )
            ),
            "_CHANNEL_NAME_CACHE": {},
            "_CHANNEL_AUTHOR_URL_CACHE": {},
            "_CHANNEL_AVATAR_URL_CACHE": {},
            "_CHANNEL_NAME_CACHE_LOCK": threading.RLock(),
            "_ANDROID_WEB_UA": "Mozilla/5.0",
            "_extract_video_id_from_url": lambda url: (
                "LTsbyX-l06Y" if url == video_url else ""
            ),
        }
        for func in (
            "_validated_youtube_channel_url",
            "_validated_yt_avatar_url",
            "_pick_yt3_avatar_from_text",
            "_channel_name_from_oembed",
            "_extract_channel_thumb_from_channel_page",
            "_channel_avatar_from_oembed",
        ):
            isolated_definition("ytdlp_helpers.py", func, ns)

        self.assertEqual(ns["_channel_name_from_oembed"](video_url), "Grimwind")
        self.assertEqual(ns["_channel_avatar_from_oembed"](video_url), avatar_url)
        self.assertEqual(ns["_channel_avatar_from_oembed"](video_url), avatar_url)
        self.assertEqual(calls, [
            calls[0], "https://www.youtube.com/@Grimwind"
        ])
        self.assertTrue(calls[0].startswith("https://www.youtube.com/oembed?"))
        self.assertEqual(len(calls), 2)

    def test_oembed_rejects_arbitrary_channel_author_url(self):
        import re
        from urllib import parse
        validated = isolated_definition(
            "ytdlp_helpers.py", "_validated_youtube_channel_url",
            {"re": re, "urlparse": parse},
        )
        self.assertEqual(
            validated("https://www.youtube.com.evil.test/@Grimwind"), ""
        )
        self.assertEqual(
            validated("https://www.youtube.com/redirect?url=http://127.0.0.1"),
            "",
        )
        self.assertEqual(
            validated("https://www.youtube.com/@Grimwind"),
            "https://www.youtube.com/@Grimwind",
        )

    def test_author_worker_fetches_avatar_on_cached_author(self):
        s = (ROOT / "fast_track_switch_v9.py").read_text(encoding="utf-8")
        self.assertIn("_channel_avatar_from_oembed(", s)
        start = s.index("def metadata_after_audio(")
        author_call = s.index("start_author_lookup(self, url, gen)", start)
        cached_return = s.index('if (url in getattr(self, "_metadata_loaded_urls"', start)
        self.assertLess(author_call, cached_return)
        self.assertIn('and getattr(self, "_channel_thumb", "")', s)

    def test_avatar_downloader_checks_image_mime_https_and_atomic_write(self):
        s = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        start = s.index("def _download_channel_avatar_async(")
        end = s.index("def _playlist_thumb_cache_path(", start)
        fn = s[start:end]
        self.assertIn("_validated_yt_avatar_url(url)", fn)
        self.assertIn("ssl.create_default_context(cafile=certifi.where())", fn)
        self.assertIn('mime.startswith("image/")', fn)
        self.assertIn("resp.read(2_000_001)", fn)
        self.assertIn("os.replace(tmp, path)", fn)


    def test_youtube_age_comes_from_real_upload_date_and_handles_boundaries(self):
        from datetime import date, datetime
        import re
        age = isolated_definition(
            "audio_screen.py", "_format_published_age",
            {"date": date, "datetime": datetime, "re": re},
        )
        today = date(2026, 11, 6)
        self.assertEqual(age("20220501", today=today), "4 роки тому")
        self.assertEqual(age("2022-05-01", today=today), "4 роки тому")
        self.assertEqual(age("20261105", today=today), "учора")
        self.assertEqual(age("20261106", today=today), "сьогодні")
        self.assertEqual(age("20261101", today=today), "5 днів тому")
        self.assertEqual(age("20261006", today=today), "1 місяць тому")
        self.assertEqual(age("20251107", today=today), "11 місяців тому")
        self.assertEqual(age("20261107", today=today), "")
        self.assertEqual(age("20260230", today=today), "")
        self.assertEqual(age("bogus", today=today), "")
        self.assertEqual(age(None, today=today), "")

    def test_youtube_video_header_has_no_hidden_spacer_gap(self):
        kv = (ROOT / "youtube_gui.kv").read_text(encoding="utf-8")
        content = kv.split("id: player_details_scroll", 1)[1]
        before_comments = content.split("# Comments are loaded only", 1)[0]
        # The layout contains invisible playlist/controls rows. A 10dp gap
        # after each 0-height child previously created a ~50dp blank band.
        self.assertIn("spacing: 0\n                padding: 0, 0, 0, dp(6)", content)
        self.assertIn("spacing: 0\n                    padding: 0, 0, 0, dp(2)", content)
        self.assertNotIn("id: now_playing_status", content)
        self.assertIn("id: channel_actions_row", content)
        self.assertIn("padding: dp(2), dp(8), dp(2), dp(4)", content)
        self.assertNotIn(
            "height: dp(4)\n    \n                    # Comments",
            content,
        )
        self.assertIn("height: dp(1)", before_comments)

    def test_youtube_channel_row_offset_survives_style_presets(self):
        styles = (ROOT / "player_ui_styles.py").read_text(encoding="utf-8")
        screen = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn(
            "row.padding = (dp(2), dp(7), dp(2), dp(3))", styles
        )
        self.assertIn("details.padding = (dp(16), dp(8), dp(16), dp(1))", screen)
        self.assertIn("visible_title_h = min(title_h, dp(58))", screen)
        self.assertNotIn("_update_now_playing_badge", screen)

    def test_youtube_upload_date_propagates_to_header_without_guesswork(self):
        source = (ROOT / "ytdlp_helpers.py").read_text(encoding="utf-8")
        screen = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn('"upload_date": info.get("upload_date")', source)
        self.assertIn('self._published_date = ""', screen)
        self.assertIn('self._published_date = published', screen)
        self.assertIn('_format_published_age(self._published_date)', screen)
        self.assertIn('" • ".join(', screen)

    def test_channel_avatar_and_name_have_real_click_handlers(self):
        kv = (ROOT / "youtube_gui.kv").read_text(encoding="utf-8")
        self.assertIn("<CircularAvatar@ButtonBehavior+AsyncImage>:", kv)
        self.assertIn("<ChannelNameButton@ButtonBehavior+Label>:", kv)
        self.assertIn("CircularAvatar:\n                                id: channel_avatar", kv)
        self.assertIn("ChannelNameButton:\n                                id: audio_channel", kv)
        self.assertEqual(kv.count("on_release: root.open_current_channel()"), 2)
        self.assertNotIn("id: now_playing_status", kv)

    def test_channel_link_resolves_verified_author_not_guessed_search(self):
        import re
        import time

        video = "https://www.youtube.com/watch?v=LTsbyX-l06Y"
        expected = "https://www.youtube.com/@Grimwind"
        requests = []
        ns = {
            "re": re,
            "time": time,
            "_extract_video_id_from_url": lambda url: (
                "LTsbyX-l06Y" if url == video else ""
            ),
            "_CHANNEL_NAME_CACHE_LOCK": threading.RLock(),
            "_CHANNEL_AUTHOR_URL_CACHE": {
                "LTsbyX-l06Y": (time.monotonic() + 300, expected)
            },
            "_validated_youtube_channel_url": lambda raw: (
                expected if raw == expected else ""
            ),
            "_channel_name_from_oembed": lambda url: requests.append(url),
        }
        resolve = isolated_definition(
            "ytdlp_helpers.py", "_channel_url_from_oembed", ns
        )
        self.assertEqual(resolve(video), expected)
        self.assertEqual(requests, [])
        ns["_CHANNEL_AUTHOR_URL_CACHE"].clear()
        self.assertEqual(resolve(video), "")
        self.assertEqual(requests, [video])
        self.assertEqual(resolve("http://malicious.invalid/v"), "")

    def test_channel_link_opens_existing_youtube_web_tab_safely(self):
        calls = []
        jobs = []
        channel_url = "https://www.youtube.com/@Grimwind"

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                self.target = target
            def start(self):
                jobs.append(self.target)

        root = SimpleNamespace(
            set_screen=lambda tab: calls.append(("tab", tab))
        )
        ns = {
            "threading": SimpleNamespace(Thread=FakeThread),
            "Clock": SimpleNamespace(
                schedule_once=lambda callback, delay: callback(0)
            ),
            "ydlh": SimpleNamespace(
                _channel_url_from_oembed=lambda video: channel_url,
                _validated_youtube_channel_url=lambda value: (
                    value if value == channel_url else ""
                ),
            ),
            "App": SimpleNamespace(
                get_running_app=lambda: SimpleNamespace(root=root)
            ),
            "ma": SimpleNamespace(
                webview_show=lambda url: calls.append(("web", url)),
                log=lambda msg: calls.append(("log", msg)),
            ),
        }
        handler = isolated_definition(
            "audio_screen.py", "open_current_channel", ns
        )
        player = SimpleNamespace(
            _last_video_url="https://www.youtube.com/watch?v=LTsbyX-l06Y",
            _load_gen=7,
            _is_screen_active=lambda: True,
        )
        handler(player)
        handler(player)   # double tapping shouldn't spawn duplicate navigation
        self.assertEqual(len(jobs), 1)
        jobs.pop()()
        self.assertEqual(calls[:2], [
            ("tab", "web"), ("web", channel_url)
        ])

        calls.clear()
        handler(player)
        self.assertEqual(len(jobs), 1)
        player._load_gen += 1  # switched tracks before DNS/oEmbed completed
        jobs.pop()()
        self.assertEqual(calls, [])

    def test_channel_link_rejects_non_youtube_destination(self):
        calls = []
        class FakeThread:
            def __init__(self, *, target, name, daemon):
                self.target = target
            def start(self):
                self.target()
        handler = isolated_definition(
            "audio_screen.py", "open_current_channel",
            {
                "threading": SimpleNamespace(Thread=FakeThread),
                "Clock": SimpleNamespace(schedule_once=lambda fn, dt: fn(0)),
                "ydlh": SimpleNamespace(
                    _channel_url_from_oembed=lambda url:
                        "https://www.youtube.com.evil.example/@Grimwind",
                    _validated_youtube_channel_url=lambda url: "",
                ),
                "App": SimpleNamespace(get_running_app=lambda: None),
                "ma": SimpleNamespace(log=lambda msg: calls.append(msg)),
            },
        )
        player = SimpleNamespace(
            _last_video_url="https://www.youtube.com/watch?v=LTsbyX-l06Y",
            _load_gen=1,
            _is_screen_active=lambda: True,
        )
        handler(player)
        self.assertTrue(any("unavailable" in msg for msg in calls))

    def test_custom_autoplay_png_reflects_on_off_and_no_missing_icons(self):
        kv = (ROOT / "youtube_gui.kv").read_text(encoding="utf-8")
        source = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn(
            "PNGIconButton:\n                            id: autoplay_btn", kv
        )
        self.assertIn("source: 'ico/icoauto_on.png'", kv)
        self.assertNotIn("icon: 'autorenew'", kv)
        self.assertIn('autoplay_button.source = (', source)
        self.assertIn('"ico/icoauto_on.png" if enabled else "ico/icoauto_off.png"', source)
        self.assertIn('for button_id in ("autoskip_btn", "autoplay_btn"):', source)
        for icon in ("icoauto_on.png", "icoauto_off.png"):
            self.assertTrue((ROOT / "ico" / icon).is_file(), icon)


    def test_related_legacy_and_modern_youtube_cards_have_real_metadata(self):
        """Exercise YouTube 2026 lockup and older compact video models."""
        import html
        import re
        from typing import Any

        scope = {"html": html, "re": re, "Any": Any}
        for name in (
            "_text", "_first_text", "_thumb_from_node",
            "_lockup_metadata_rows", "_channel", "_views_and_published",
            "_duration", "_collect_renderers",
        ):
            isolated_definition("related_videos.py", name, scope)
        modern = {
            "contentId": "eUgRBJ2akVw",
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": "Grimwind - Хаос Неделимый"},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [
                                {"metadataParts": [
                                    {"text": {"content": "Grimwind"}}
                                ]},
                                {"metadataParts": [
                                    {"text": {"content": "251 тис."}},
                                    {"text": {"content": "5 р. тому"}},
                                ]},
                            ]
                        }
                    },
                }
            },
            "contentImage": {
                "thumbnailViewModel": {
                    "image": {"sources": [
                        {"url": "https://i.ytimg.com/vi/eUgRBJ2akVw/mqdefault.jpg"}
                    ]},
                    "overlays": [
                        {"thumbnailBottomOverlayViewModel": {
                            "badges": [
                                {"thumbnailBadgeViewModel": {
                                    "text": "29:27", "animatedText": "Зараз відтворюється"
                                }}
                            ]
                        }}
                    ],
                }
            },
        }
        legacy = {
            "videoId": "TBDXLfm-eNE",
            "title": {"simpleText": "Keepers of Death"},
            "shortBylineText": {"runs": [{"text": "Champlloo"}]},
            "shortViewCountText": {"simpleText": "90 тис. переглядів"},
            "publishedTimeText": {"simpleText": "4 роки тому"},
            "lengthText": {"simpleText": "14:36"},
            "thumbnail": {"thumbnails": [
                {"url": "https://i.ytimg.com/vi/TBDXLfm-eNE/mqdefault.jpg"}
            ]},
        }
        result = scope["_collect_renderers"](
            {"items": [
                {"lockupViewModel": modern},
                {"compactVideoRenderer": legacy},
            ]},
            "LTsbyX-l06Y", 8,
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["channel"], "Grimwind")
        self.assertEqual(result[0]["duration"], "29:27")
        self.assertEqual(result[0]["views_text"], "251 тис.")
        self.assertEqual(result[0]["published_text"], "5 р. тому")
        self.assertEqual(result[1]["channel"], "Champlloo")
        self.assertEqual(result[1]["duration"], "14:36")
        self.assertEqual(result[1]["views_text"], "90 тис. переглядів")
        self.assertEqual(result[1]["published_text"], "4 роки тому")

    def test_related_normalization_keeps_duration_and_stats(self):
        import re
        from datetime import date, datetime

        video_id = re.compile(r"([A-Za-z0-9_-]{11})")
        duration = isolated_definition(
            "audio_screen.py", "_related_duration_label", {"re": re}
        )
        age = isolated_definition(
            "audio_screen.py", "_format_published_age",
            {"date": date, "datetime": datetime, "re": re},
        )
        playlist = SimpleNamespace(
            _normalize_video_id=lambda value: (
                video_id.search(str(value)).group(1)
                if video_id.search(str(value)) else ""
            ),
            _video_id_from_thumb_url=lambda value: "",
            _normalize_video_url=lambda value: (
                "https://www.youtube.com/watch?v=" + value
                if len(value) == 11 else value
            ),
            _normalize_img_url=lambda value: value,
        )
        normalizer = isolated_definition(
            "audio_screen.py", "_normalize_related",
            {
                "Playlist": playlist,
                "_verified_channel_name": lambda name, title: name or "",
                "_format_published_age": age,
                "_related_duration_label": duration,
            },
        )
        player = SimpleNamespace(
            _last_video_url="https://www.youtube.com/watch?v=LTsbyX-l06Y",
            _fmt_views=lambda n: str(n) + " переглядів",
        )
        result = normalizer(player, [
            {"video_id": "eUgRBJ2akVw", "title": "Grimwind title",
             "channel": "Grimwind", "duration": "29:27",
             "views_text": "251 тис.", "published_text": "5 р. тому"},
            {"video_id": "TBDXLfm-eNE", "title": "Legacy",
             "uploader": "Champlloo", "duration": 876,
             "view_count": 90000, "upload_date": "bad date"},
            {"video_id": "LTsbyX-l06Y", "title": "Currently playing"},
        ])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["duration"], "29:27")
        self.assertEqual(result[0]["channel"], "Grimwind")
        self.assertEqual(result[0]["views_text"], "251 тис.")
        self.assertEqual(result[0]["published_text"], "5 р. тому")
        self.assertEqual(result[1]["duration"], "14:36")
        self.assertEqual(result[1]["views_text"], "90000 переглядів")
        self.assertEqual(result[1]["published_text"], "")
        self.assertEqual(duration(None), "")
        self.assertEqual(duration(0), "")
        self.assertEqual(duration("not known"), "")

    def test_related_row_renders_duration_and_youtube_statistics(self):
        import re

        duration = isolated_definition(
            "audio_screen.py", "_related_duration_label", {"re": re}
        )
        fields = (
            "similar_title", "similar_channel", "similar_duration",
            "similar_meta", "similar_thumb",
        )
        row = SimpleNamespace(
            ids=SimpleNamespace(**{
                key: SimpleNamespace(text="", source="") for key in fields
            }),
            bind=lambda **_kw: None,
        )
        factory = SimpleNamespace(SimilarItem=lambda: row)
        playlist = SimpleNamespace(
            _normalize_video_id=lambda value: (
                "eUgRBJ2akVw" if "eUgRBJ2akVw" in value else ""
            ),
            _video_id_from_thumb_url=lambda value: "",
        )
        render = isolated_definition(
            "audio_screen.py", "_make_similar_row",
            {
                "Factory": factory,
                "Playlist": playlist,
                "_related_duration_label": duration,
            },
        )
        owner = SimpleNamespace(
            _set_playlist_thumb=lambda widget, thumb: setattr(
                widget, "source", thumb
            ),
            _play_from_related_index=lambda i: i,
        )
        render(owner, 2, {
            "url": "https://www.youtube.com/watch?v=eUgRBJ2akVw",
            "title": "Grimwind - Хаос Неделимый",
            "channel": "Grimwind",
            "duration": "29:27",
            "views_text": "251 тис.",
            "published_text": "5 р. тому",
        })
        self.assertEqual(row.ids.similar_channel.text, "Grimwind")
        self.assertEqual(row.ids.similar_duration.text, "29:27")
        self.assertEqual(row.ids.similar_meta.text, "251 тис. • 5 р. тому")
        self.assertIn("eUgRBJ2akVw", row.ids.similar_thumb.source)

    def test_related_card_duration_badge_is_on_thumbnail_corner(self):
        kv = (ROOT / "youtube_gui.kv").read_text(encoding="utf-8")
        related = kv.split("<SimilarItem@ButtonBehavior+BoxLayout>:", 1)[1].split(
            "<PlaylistTrackItem@ButtonBehavior+BoxLayout>:", 1
        )[0]
        self.assertIn("FloatLayout:", related)
        self.assertIn("id: similar_thumb", related)
        self.assertIn("id: similar_duration", related)
        self.assertIn("id: similar_channel", related)
        self.assertIn("id: similar_meta", related)
        self.assertIn("pos_hint: {'right': 0.96, 'y': 0.06}", related)
        self.assertIn("opacity: 1 if self.text else 0", related)
        source = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn("str(item.get(\"duration\") or \"\")", source)
        self.assertIn('str(item.get("published_text") or "")', source)


    def test_audio_url_cache_is_bounded_expires_and_invalidates(self):
        import re
        import threading
        import time

        url = "https://www.youtube.com/watch?v=LTsbyX-l06Y"
        source = "https://r1---sn-test.googlevideo.com/videoplayback"
        state = {
            "_extract_video_id_from_url": lambda value: (
                "LTsbyX-l06Y" if value == url else ""
            ),
            "_AUDIO_INFO_CACHE": {},
            "_AUDIO_INFO_CACHE_LOCK": threading.RLock(),
            "_AUDIO_INFO_CACHE_MAX": 2,
            "_AUDIO_INFO_CACHE_TTL": 120.0,
            "time": time,
        }
        save = isolated_definition("ytdlp_helpers.py", "_cache_audio_info", state)
        get = isolated_definition("ytdlp_helpers.py", "_get_cached_audio_info", state)
        invalidate = isolated_definition(
            "ytdlp_helpers.py", "invalidate_audio_info_cache", state
        )
        save(url, {"audio_url": source, "title": "Grimwind"})
        first = get(url)
        self.assertIsNotNone(first)
        self.assertEqual(first["audio_url"], source)
        first["title"] = "mutation"
        self.assertEqual(get(url)["title"], "Grimwind")
        invalidate(url)
        self.assertIsNone(get(url))
        save(url, {"audio_url": source, "expire_ts": time.time() + 5})
        self.assertIsNone(get(url))
        save(url, {"audio_url": "http://untrusted.invalid/song"})
        self.assertIsNone(get(url))

    def test_no_duplicate_full_song_download_during_native_streaming(self):
        source = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        flow = source.split("def _extract_and_start(self", 1)[1].split(
            "def _put_cache(self", 1
        )[0]
        self.assertNotIn("self._cache_audio_async(", flow)
        self.assertIn("self._put_cache(video_url,", flow)
        # Explicit favorite caching remains separate and opt-in.
        self.assertIn("self._cache_current_favorite_async(url)", source)

    def test_slow_network_watchdog_keeps_existing_buffer_and_position(self):
        import time
        state = SimpleNamespace(
            _app_in_background=False, _last_bg_resume_ts=0,
            _playback_desired=True, _user_paused=False,
            _last_watch_pos=1000,
            _buffer_watchdog_ts=time.time() - 12,
            _buffering_started_at=0.0,
            _resume_pos_ms=0,
        )
        calls = []
        player = SimpleNamespace(getCurrentPosition=lambda: 1000)
        media = SimpleNamespace(
            android_player=player,
            _is_prepared=lambda: True,
            is_metered_connection=lambda: False,
            is_network_available=lambda: True,
        )
        state._recover_stream = lambda *a, **kw: calls.append((a, kw))
        func = isolated_definition(
            "audio_screen.py", "_buffer_watchdog",
            {"time": time, "ma": media},
        )
        func(state, 0)
        self.assertEqual(calls, [])  # old 8-second watchdog restarted here
        state._buffer_watchdog_ts = time.time() - 46
        func(state, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0], "buffer_watchdog")
        self.assertEqual(calls[0][1]["force_fresh"], True)
        calls.clear()
        state._buffer_watchdog_ts = time.time() - 90
        media.is_network_available = lambda: False
        func(state, 0)
        self.assertEqual(calls, [])  # do not waste traffic while offline

    def test_audio_first_video_start_and_metered_data_saver(self):
        code = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        media = (ROOT / "media_android.py").read_text(encoding="utf-8")
        self.assertIn("def is_metered_connection()", media)
        self.assertIn("_video_enabled = not bool(ma.is_metered_connection())", code)
        self.assertIn("self._launch_video_after_audio_start(gen)", code)
        prepared = code.split("def _start_from_known_stream(", 1)[1].split(
            "def _audio_cache_dir(", 1
        )[0]
        self.assertNotIn(
            "target=lambda: self._auto_video_for_current(gen", prepared
        )
        self.assertIn("time.sleep(delay)", code)
        self.assertIn("4.0 if ma.is_metered_connection() else 1.5", code)

    def test_nonessential_related_fetch_waits_for_audio_and_metered_tap(self):
        media = SimpleNamespace(is_metered_connection=lambda: True)
        func = isolated_definition(
            "audio_screen.py", "_ensure_related_current_v4",
            {"ma": media},
        )
        player = SimpleNamespace(
            _current_ensure_state=lambda: None,
            _load_gen=3,
            _audio_ready_for_extras_gen=3,
        )
        self.assertFalse(func(player))
        media.is_metered_connection = lambda: False
        player._audio_ready_for_extras_gen = -1
        self.assertFalse(func(player))
        code = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        self.assertIn('user_requested=True)', code)
        self.assertIn('self._similar_collapsed = bool(ma.is_metered_connection())', code)
        self.assertIn('5000 < remaining <= 25000', code)

    def test_expiring_cdn_url_does_not_restart_audible_track(self):
        code = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        expiry = code.split("def _schedule_expiry(self):", 1)[1].split(
            "def toggle_play_pause", 1
        )[0]
        self.assertNotIn("_extract_and_start_gen", expiry)
        self.assertIn("return None", expiry)
        extractor = (ROOT / "ytdlp_helpers.py").read_text(encoding="utf-8")
        self.assertIn("_get_cached_audio_info(video_url)", extractor)
        self.assertIn("invalidate_audio_info_cache(", extractor)


    def test_local_audio_cache_rejects_partial_and_external_files(self):
        import hashlib
        import os
        import tempfile

        fn = isolated_definition(
            "audio_screen.py", "_find_cached_audio", {
                "os": os,
                "load_recent": lambda: [],
            }
        )
        url = "https://www.youtube.com/watch?v=LTsbyX-l06Y"
        key = hashlib.md5(url.encode()).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            cache_dir = os.path.join(root, "audio")
            os.mkdir(cache_dir)
            partial = os.path.join(cache_dir, key + ".m4a.part")
            complete = os.path.join(cache_dir, key + ".m4a")
            with open(partial, "wb") as fp:
                fp.write(b"x" * 1024)
            owner = SimpleNamespace(
                _audio_cache_dir=lambda: cache_dir,
                _cache_key=lambda _url: key,
            )
            self.assertIsNone(fn(owner, url))
            with open(complete, "wb") as fp:
                fp.write(b"x" * 1024)
            self.assertEqual(fn(owner, url), complete)
            os.remove(complete)
            foreign = os.path.join(root, key + ".m4a")
            with open(foreign, "wb") as fp:
                fp.write(b"x" * 1024)
            self.assertIsNone(fn(owner, url))

    def test_explicit_favorite_cache_has_tls_size_redirect_and_disk_guards(self):
        source = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        start = source.index("def _download_audio_locally(")
        end = source.index("def _cache_audio_async(", start)
        cache = source[start:end]
        self.assertIn("max_bytes = 24 * 1024 * 1024", cache)
        self.assertIn("total + 24 * 1024 * 1024 > 128 * 1024 * 1024", cache)
        self.assertIn("ssl.create_default_context(cafile=certifi.where())", cache)
        self.assertIn("class RejectRedirect(", cache)
        self.assertIn('"untrusted audio redirect destination"', cache)
        self.assertIn("os.replace(tmp_path, path)", cache)
        self.assertNotIn("_create_unverified_context", cache)


    def test_cached_audio_alternate_skips_reextract_and_bad_urls(self):
        from urllib import parse
        import time

        host = "https://rr1---sn-abc.googlevideo.com/videoplayback"
        aac140 = host + "?itag=140"
        aac139 = host + "?itag=139"
        opus251 = host + "?itag=251"
        formats = [
            {"format_id": "140", "url": aac140, "vcodec": "none",
             "acodec": "mp4a.40.2", "ext": "m4a", "abr": 128},
            {"format_id": "139", "url": aac139, "vcodec": "none",
             "acodec": "mp4a.40.5", "ext": "m4a", "abr": 48},
            {"format_id": "251", "url": opus251, "vcodec": "none",
             "acodec": "opus", "ext": "webm", "abr": 120},
            {"format_id": "18", "url": host + "?itag=18", "vcodec": "avc1",
             "acodec": "mp4a.40.2", "ext": "mp4"},
            {"format_id": "spoof", "url": "https://googlevideo.com.evil.test/v",
             "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a"},
            {"format_id": "local", "url": "http://127.0.0.1:9000/secret",
             "vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a"},
        ]
        ns = {
            "urlparse": parse,
            "time": time,
            "_get_cached_formats": lambda _video: {"formats": formats},
            "_parse_expire_ts": lambda _url: None,
            "_best_effort_headers": lambda source, fallback: (
                dict(source or fallback)
            ),
            "_ANDROID_YT_UA": "AndroidTest",
        }
        fallback = isolated_definition(
            "ytdlp_helpers.py", "get_cached_alternate_audio_source", ns
        )
        v = "https://www.youtube.com/watch?v=eUgRBJ2akVw"
        first = fallback(v, aac140, {aac140})
        self.assertEqual(first["format_id"], "139")
        self.assertEqual(first["audio_url"], aac139)
        self.assertEqual(
            fallback(v, aac140, {aac140, aac139})["format_id"], "251"
        )
        self.assertIsNone(
            fallback(v, aac140, {aac140, aac139, opus251})
        )
        ns["_get_cached_formats"] = lambda _video: None
        self.assertIsNone(fallback(v, aac140, {aac140}))

    def test_native_minus_38_uses_cached_alternate_before_network_retry(self):
        screen = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        start = screen.index("def _start_from_known_stream(")
        end = screen.index("def _audio_cache_dir(", start)
        part = screen[start:end]
        choice = part.index("ydlh.get_cached_alternate_audio_source(")
        retry = part.index('self._recover_stream("media_error"')
        self.assertLess(choice, retry)
        self.assertIn('name="pymusic-audio-alternate-native"', part)
        self.assertIn("getattr(self, \"_audio_source_attempts\"", part)
        self.assertIn("self._stream_error_hits <= 2", part)

    def test_slow_audio_start_does_not_trigger_second_ytdlp_metadata_extract(self):
        runtime = (ROOT / "fast_track_switch_v9.py").read_text(encoding="utf-8")
        section = runtime.split("def metadata_after_audio(", 1)[1].split(
            "def play_audio_fast_switch(", 1
        )[0]
        self.assertIn("deadline = time.monotonic() + 30.0", section)
        self.assertIn('if not _audio_ready(media):', section)
        self.assertLess(
            section.index('if not _audio_ready(media):'),
            section.index("old_metadata(self, expected_url)"),
        )
        self.assertIn("self._metadata_loaded_urls.add(video_url)",
                      (ROOT / "audio_screen.py").read_text(encoding="utf-8"))

    def test_related_images_are_deferred_until_after_native_audio_start(self):
        src = (ROOT / "audio_screen.py").read_text(encoding="utf-8")
        extractor = src.split("def _extract_and_start(self,", 1)[1].split(
            "def _put_cache(", 1
        )[0]
        self.assertIn(
            'if getattr(self, "_audio_ready_for_extras_gen", -1) == gen:',
            extractor,
        )
        self.assertIn("self._render_similar_ui(force=True)", src)
        self.assertIn("[LATENCY] audio URL ready after", src)
        self.assertIn("[LATENCY] native prepared after", src)
        self.assertIn("[LATENCY] audio start queued after", src)


if __name__ == "__main__":
    unittest.main()
