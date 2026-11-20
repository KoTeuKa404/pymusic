"""Android background networking must never mistake a blocked DNS port for offline."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ANDROID = Path(__file__).resolve().parents[1] / "android_src"


def _network_fn(manager):
    src = (ANDROID / "media_android.py").read_text(encoding="utf-8")
    parsed = ast.parse(src)
    node = next(
        n for n in parsed.body
        if isinstance(n, ast.FunctionDef) and n.name == "is_network_available"
    )
    namespace = {
        "activity": SimpleNamespace(getSystemService=lambda _key: manager),
        "Context": SimpleNamespace(CONNECTIVITY_SERVICE="connectivity"),
        "NetworkCapabilities": SimpleNamespace(
            NET_CAPABILITY_VALIDATED=16, NET_CAPABILITY_INTERNET=12
        ),
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])),
                 "media_android.py", "exec"), namespace)
    return namespace["is_network_available"]


class NetworkBackgroundTests(unittest.TestCase):
    def test_android_network_state_permission_is_packaged(self):
        spec = (ANDROID.parent / "buildozer.spec").read_text(encoding="utf-8")
        self.assertIn("ACCESS_NETWORK_STATE", spec)
        self.assertIn("ACCESS_WIFI_STATE", spec)

    def test_internet_network_accepted_even_when_not_validated(self):
        caps = SimpleNamespace(hasCapability=lambda cap: cap == 12)
        cm = SimpleNamespace(
            getActiveNetwork=lambda: object(),
            getNetworkCapabilities=lambda _: caps,
        )
        self.assertTrue(_network_fn(cm)())

    def test_confirmed_offline_returns_false(self):
        cm = SimpleNamespace(
            getActiveNetwork=lambda: None,
            getActiveNetworkInfo=lambda: None,
        )
        self.assertFalse(_network_fn(cm)())

    def test_permission_error_is_unknown_not_offline(self):
        def no_permission():
            raise PermissionError("requires ACCESS_NETWORK_STATE")
        cm = SimpleNamespace(getActiveNetwork=no_permission)
        self.assertTrue(_network_fn(cm)())

    def test_disconnected_legacy_network_reports_offline(self):
        cm = SimpleNamespace(
            getActiveNetwork=lambda: None,
            getActiveNetworkInfo=lambda: SimpleNamespace(isConnected=lambda: False),
        )
        self.assertFalse(_network_fn(cm)())

    def test_no_tcp_53_probe_to_classify_offline(self):
        src = (ANDROID / "media_android.py").read_text(encoding="utf-8")
        fn = src.split("def is_network_available():", 1)[1].split(
            "def is_metered_connection()", 1
        )[0]
        self.assertNotIn("socket.create_connection", fn)
        self.assertNotIn('"1.1.1.1"', fn)

    def test_offline_audio_prefers_completed_cache_before_waiting_for_network(self):
        src = (ANDROID / "player_stability_v6.py").read_text(encoding="utf-8")
        fn = src.split("def extract_gen_network_safe(", 1)[1].split(
            "cls._advance_to_next_track =", 1
        )[0]
        self.assertLess(
            fn.index("self._try_start_cached_audio("),
            fn.index("start_network_wait(self, pos)"),
        )


if __name__ == "__main__":
    unittest.main()
