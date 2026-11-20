"""Offline-cache regression tests: no Android/Kivy installation required."""
from __future__ import annotations

import ast
import hashlib
import html
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

from android_src import recent_utils

ROOT = Path(__file__).resolve().parents[1] / "android_src"


def isolated_function(name, variables):
    tree = ast.parse((ROOT / "offline_image_cache.py").read_text(encoding="utf-8"))
    found = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(found) != 1:
        raise AssertionError(f"expected function {name}")
    isolated = ast.Module(body=found, type_ignores=[])
    exec(compile(ast.fix_missing_locations(isolated), "offline_image_cache.py", "exec"),
         variables)
    return variables[name]


class RecentHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_recent = recent_utils.RECENT_PATH
        self.original_favorites = recent_utils.FAVORITES_PATH
        recent_utils.RECENT_PATH = os.path.join(self.tmp.name, "recent.json")
        recent_utils.FAVORITES_PATH = os.path.join(self.tmp.name, "favorites.json")

    def tearDown(self):
        recent_utils.RECENT_PATH = self.original_recent
        recent_utils.FAVORITES_PATH = self.original_favorites
        self.tmp.cleanup()

    def test_preserves_audio_cover_and_avatar_after_replay(self):
        url = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
        recent_utils.upsert_recent(url, "Initial", "Channel", "https://i.ytimg.com/a.jpg")
        recent_utils.update_recent_cache(url, "/private/offline/track.m4a")
        recent_utils.update_recent_art(url, "/private/offline/cover.jpg")
        recent_utils.update_recent_avatar(url, "https://yt3.ggpht.com/avatar", "/private/avatar.jpg")
        recent_utils.upsert_recent(url, "Corrected", "Channel", "")
        items = recent_utils.load_recent()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Corrected")
        self.assertEqual(items[0]["cache_path"], "/private/offline/track.m4a")
        self.assertEqual(items[0]["art_path"], "/private/offline/cover.jpg")
        self.assertEqual(items[0]["avatar_path"], "/private/avatar.jpg")
        self.assertEqual(items[0]["avatar_url"], "https://yt3.ggpht.com/avatar")
        self.assertFalse(os.path.exists(recent_utils.RECENT_PATH + ".tmp"))

    def test_recent_items_remain_bounded_and_favorites_remain_pinned(self):
        for index in range(recent_utils.MAX_RECENT + 5):
            recent_utils.upsert_recent(f"https://youtube.com/watch?v={index:011d}",
                                       f"Title {index}", "Artist", "")
        self.assertEqual(len(recent_utils.load_recent()), recent_utils.MAX_RECENT)
        self.assertIn("Title 84", recent_utils.load_recent()[0]["title"])
        recent_utils.upsert_favorite({"url": "https://youtube.com/watch?v=00000000000",
                                      "cache_path": "/private/saved.m4a"})
        self.assertEqual(len(recent_utils.load_favorites()), 1)
        recent_utils.remove_favorite("https://youtube.com/watch?v=00000000000")
        self.assertEqual(recent_utils.load_favorites(), [])


class ImageValidationTests(unittest.TestCase):
    def setUp(self):
        self.variables = {
            "html": html,
            "urllib": urllib,
            "_IMAGE_HOSTS": frozenset({
                "i.ytimg.com", "img.youtube.com", "i1.ytimg.com",
                "yt3.ggpht.com", "yt3.googleusercontent.com",
            }),
        }
        self.normalize = isolated_function("_url", self.variables)

    def test_accepts_only_verified_https_youtube_image_hosts(self):
        self.assertEqual(
            self.normalize("https://i.ytimg.com/vi/AAAAAAAAAAA/mqdefault.jpg?ts=1"),
            "https://i.ytimg.com/vi/AAAAAAAAAAA/mqdefault.jpg",
        )
        self.assertTrue(self.normalize("https://yt3.ggpht.com/a=s88"))
        for candidate in (
            "http://i.ytimg.com/image.jpg",
            "https://i.ytimg.com.attacker.example/image.jpg",
            "https://evil.example/cover.jpg",
            "https://i.ytimg.com:8443/image.jpg",
            "https://user:pass@i.ytimg.com/image.jpg",
        ):
            self.assertFalse(self.normalize(candidate), candidate)

    def test_cache_rejects_truncated_and_false_images(self):
        magic = isolated_function("_image_type", {})
        self.assertEqual(magic(bytes.fromhex("ffd8ff") + b"x"), "jpg")
        self.assertEqual(magic(bytes.fromhex("89504e470d0a1a0a") + b"x"), "png")
        self.assertEqual(magic(b"RIFF0000WEBP" + b"x"), "webp")
        self.assertEqual(magic(b"<html>not a thumbnail"), "")

    def test_local_image_exists_only_when_complete_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "https://i.ytimg.com/vi/AAAAAAAAAAA/mqdefault.jpg"
            digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
            image_path = os.path.join(tmp, digest + ".jpg")
            variables = {
                "os": os, "time": time, "IMAGE_MAX_BYTES": 2 * 1024 * 1024,
                "_IMAGE_EXTENSIONS": ("jpg", "png", "webp"),
                "_directory": lambda: tmp, "_digest": lambda s: hashlib.sha256(s.encode()).hexdigest(),
                "_url": self.normalize,
                "_image_type": isolated_function("_image_type", {}),
            }
            lookup = isolated_function("get_cached_image_path", variables)
            Path(image_path).write_bytes(b"<html>" + b"x" * 150)
            self.assertIsNone(lookup(url))
            Path(image_path).write_bytes(bytes.fromhex("ffd8ff") + b"x" * 150)
            self.assertEqual(lookup(url), image_path)


if __name__ == "__main__":
    unittest.main()
