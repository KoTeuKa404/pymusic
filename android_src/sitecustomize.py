"""Startup hooks for PyMusic Android runtime fixes.

Python imports this file automatically when it is present on sys.path. The
actual patches live in pymusic_runtime_fixes.py so this file stays tiny and safe.
"""

try:
    import pymusic_runtime_fixes

    pymusic_runtime_fixes.install()
except Exception as exc:
    try:
        print("[PYMUSIC_PATCH] sitecustomize failed:", exc)
    except Exception:
        pass
