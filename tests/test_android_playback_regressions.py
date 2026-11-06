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


    def test_healthy_tracks_keep_android_client_and_only_recover_on_failure(self):
        from typing import Tuple
        chooser = isolated_definition(
            "ytdlp_helpers.py", "_audio_client_order",
            {"Tuple": Tuple, "_env_bool": lambda *_args: False},
        )
        self.assertEqual(chooser(False), ("android", "tv_embedded"))
        self.assertEqual(
            chooser(True), ("web_embedded", "tv_embedded", "android", "ios")
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


if __name__ == "__main__":
    unittest.main()
