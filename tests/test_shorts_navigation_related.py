"""Offline checks for native Shorts, swipe navigation and recommendation origin."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android_src"
JAVA = ROOT / "java_src/org/koteuka404/pymusic/WebViewClientBridge.java"


def isolate(file: str, name: str, context: dict):
    tree = ast.parse((ANDROID / file).read_text(encoding="utf-8"))
    found = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(found) == 1, (file, name, len(found))
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=found, type_ignores=[])),
                file, "exec"),
        context,
    )
    return context[name]


class ShortsAndNavigationTests(unittest.TestCase):
    def test_short_video_search_result_no_longer_discarded(self):
        scope = {"re": re}
        for name in ("_extract_text", "_extract_thumbnail", "_is_shorts_video",
                     "_shorts_shelf_items", "_find_continuation", "_parse_items"):
            isolate("youtube_search.py", name, scope)
        entries = [
            {"videoRenderer": {
                "videoId": "abc123def45",
                "title": {"simpleText": "A real Short"},
                "ownerText": {"simpleText": "Creator"},
                "thumbnailOverlays": [
                    {"thumbnailOverlayTimeStatusRenderer": {"style": "SHORTS"}}
                ],
            }},
            {"reelShelfRenderer": {
                "items": [
                    {"reelItemRenderer": {
                        "videoId": "sss111aaa22",
                        "headline": {"simpleText": "Short from reel shelf"}
                    }},
                ]
            }},
            {"videoRenderer": {
                "videoId": "vvv111bbb22",
                "title": {"simpleText": "Full length video"},
            }},
        ]
        videos, playlists, token = scope["_parse_items"](entries)
        self.assertEqual(len(videos), 3)
        self.assertEqual(playlists, [])
        self.assertIsNone(token)
        self.assertEqual(videos[0][0], "https://www.youtube.com/shorts/abc123def45")
        self.assertEqual(videos[1][0], "https://www.youtube.com/shorts/sss111aaa22")
        self.assertEqual(videos[2][0], "https://www.youtube.com/watch?v=vvv111bbb22")
        self.assertEqual(videos[0][4], "Shorts")

    def test_malformed_shorts_do_not_create_arbitrary_urls(self):
        scope = {"re": re}
        for name in ("_extract_text", "_extract_thumbnail", "_shorts_shelf_items"):
            isolate("youtube_search.py", name, scope)
        cards = scope["_shorts_shelf_items"]({
            "items": [
                {"reelItemRenderer": {"videoId": "../not-video"}},
                {"reelItemRenderer": {"videoId": "abc123def45"}},
                {"reelItemRenderer": {"videoId": "abc123def45"}},
            ]
        })
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0][0], "https://www.youtube.com/shorts/abc123def45")

    def test_watch_next_is_not_a_search_feed(self):
        scope = {"Any": object}
        isolate("related_videos.py", "_path", scope)
        isolate("related_videos.py", "_related_roots", scope)
        fake = {
            "contents": {
                "twoColumnWatchNextResults": {
                    "secondaryResults": {
                        "secondaryResults": {
                            "results": [{"compactVideoRenderer": {"videoId": "abc123def45"}}]
                        }
                    }
                }
            },
            "searchResults": {"items": [{"videoRenderer": {"videoId": "other"}}]},
        }
        self.assertEqual(
            scope["_related_roots"](fake)[0],
            fake["contents"]["twoColumnWatchNextResults"]["secondaryResults"]
                ["secondaryResults"]["results"],
        )
        self.assertEqual(
            scope["_related_roots"]({"searchResults": fake["searchResults"]}), []
        )
        runtime = (ANDROID / "pymusic_runtime_patch.py").read_text(encoding="utf-8")
        self.assertIn('source = "playlist"', runtime)
        self.assertIn('"Наступні з плейлиста"', runtime)
        self.assertIn('"source": self._related_source', runtime)

    def test_swipe_navigation_is_bounded_and_directional(self):
        scope = {}
        switch = isolate("main.py", "_switch_relative", scope)
        holder = SimpleNamespace(
            sm=SimpleNamespace(current="web"),
            _swipe_order=("search", "web", "shorts", "audio"),
        )
        holder.set_screen = lambda name: setattr(holder.sm, "current", name)
        self.assertTrue(switch(holder, 1))
        self.assertEqual(holder.sm.current, "shorts")
        self.assertTrue(switch(holder, 1))
        self.assertEqual(holder.sm.current, "audio")
        self.assertFalse(switch(holder, 1))
        self.assertEqual(holder.sm.current, "audio")
        self.assertTrue(switch(holder, -1))
        self.assertEqual(holder.sm.current, "shorts")

    def test_pasted_shorts_link_is_routed_before_audio_extraction(self):
        import re
        from urllib.parse import urlparse
        scheduled = []
        player = SimpleNamespace(open_shorts=lambda url: scheduled.append(url))
        scope = {
            "re": re,
            "urlparse": urlparse,
            "Clock": SimpleNamespace(
                schedule_once=lambda fn, delay: fn(0)
            ),
            "_pymusic_mix_video_url": lambda value: (
                (_ for _ in ()).throw(
                    AssertionError("Shorts URL must not become watch audio")
                )
            ),
        }
        fetch = isolate("main.py", "_fetch_results_thread", scope)
        fetch(player, "https://www.youtube.com/shorts/abc123def45")
        self.assertEqual(
            scheduled, ["https://www.youtube.com/shorts/abc123def45"]
        )

    def test_webview_to_shorts_never_hides_shared_native_browser(self):
        events = []
        class Manager:
            current = "web"
            screen_names = ("search", "web", "shorts", "audio")
            def __init__(self):
                self.screens = {
                    name: SimpleNamespace(name=name, _keep_webview_on_leave=False)
                    for name in self.screen_names
                }
            def get_screen(self, name):
                return self.screens[name]
        manager = Manager()
        root = SimpleNamespace(
            sm=manager,
            _active_tab="web",
            _switch_relative=lambda delta: None,
        )
        go = isolate("main.py", "set_screen", {
            "ma": SimpleNamespace(
                apply_light_system_bars=lambda: None
            )
        })
        module = ast.parse((ANDROID / "main.py").read_text(encoding="utf-8"))
        web_class = next(
            cls for cls in module.body
            if isinstance(cls, ast.ClassDef) and cls.name == "YoutubeWebScreen"
        )
        web_method = next(
            node for node in web_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "on_pre_leave"
        )
        web_method.decorator_list = []
        scope = {
            "ma": SimpleNamespace(webview_hide=lambda: events.append("hide"))
        }
        exec(compile(ast.fix_missing_locations(
            ast.Module(body=[web_method], type_ignores=[])
        ), "main.py", "exec"), scope)
        web_leave = scope["on_pre_leave"]
        # Both screen classes have their own on_pre_leave override. Inspect
        # its body so a future refactor cannot accidentally reintroduce a
        # hide() between screens sharing the one Android WebView.
        source = (ANDROID / "main.py").read_text(encoding="utf-8")
        shorts = source.split("class YoutubeShortsScreen(", 1)[1].split(
            "class RootLayout(", 1
        )[0]
        self.assertIn('if getattr(self, "_keep_webview_on_leave", False):', shorts)

        go(root, "shorts")
        self.assertEqual(manager.current, "shorts")
        self.assertTrue(manager.get_screen("web")._keep_webview_on_leave)
        web_leave(manager.get_screen("web"))
        self.assertEqual(events, [])
        self.assertFalse(manager.get_screen("web")._keep_webview_on_leave)

        go(root, "audio")
        self.assertEqual(manager.current, "audio")
        self.assertFalse(manager.get_screen("shorts")._keep_webview_on_leave)

        go(root, "web")
        self.assertEqual(manager.current, "web")
        web_leave(manager.get_screen("web"))
        self.assertEqual(events, ["hide"])

    def test_no_black_nav_bar_in_kivy_or_native_youtube(self):
        main = (ANDROID / "main.py").read_text(encoding="utf-8")
        java = JAVA.read_text(encoding="utf-8")
        self.assertNotIn("def _build_bottom_bar(", main)
        self.assertNotIn("self._build_bottom_bar()", main)
        self.assertNotIn("class BottomNavButton(", main)
        self.assertNotIn("document.createElement('div');bar.id='pymusic-bottom'", java)
        self.assertIn("old.remove()", java)
        self.assertIn('dispatchMode(dx < 0 ? "swipe_left" : "swipe_right")', java)
        self.assertIn("return false;", java)
        self.assertIn("Math.abs(dx) > Math.abs(dy) * 1.8f", java)
        self.assertIn("on_touch_down(self, touch)", main)
        self.assertIn("on_touch_up(self, touch)", main)

    def test_shorts_not_redirected_to_audio_and_still_scroll_vertically(self):
        main = (ANDROID / "main.py").read_text(encoding="utf-8")
        java = JAVA.read_text(encoding="utf-8")
        self.assertIn("class YoutubeShortsScreen(YoutubeWebScreen):", main)
        self.assertIn('"https://m.youtube.com/shorts"', main)
        self.assertIn('root.open_shorts(url)', main)
        self.assertIn('"Shorts" if is_short else dur', (ANDROID / "youtube_search.py").read_text(encoding="utf-8"))
        is_watch = java.split("private static boolean isWatchUrl(", 1)[1].split(
            "private static String forceMobileYoutube", 1
        )[0]
        self.assertNotIn('u.contains("youtube.com/shorts/")', is_watch)
        self.assertIn("installEdgeSwipeRouter", java)
        self.assertIn("MotionEvent.ACTION_UP", java)
        self.assertIn("Math.abs(dx) > Math.abs(dy) * 1.8f", java)


if __name__ == "__main__":
    unittest.main()
