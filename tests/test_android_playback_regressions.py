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
        self.assertEqual(chooser(False), ("android",))
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
            ["95", "94", "93", "18"]
        )
        self.assertEqual(
            [item["protocol"] for item in selected],
            ["hls", "hls", "hls", "mp4"]
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
            "_video_stream_candidates": lambda formats, headers, limit=4: [{
                "video_url": formats[0]["url"], "http_headers": headers,
                "format_id": formats[0]["format_id"], "protocol": "hls",
                "height": 720,
            }],
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


if __name__ == "__main__":
    unittest.main()
