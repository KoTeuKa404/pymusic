"""Opt-in PyMusic crash reports with strict privacy scrubbing.

The diagnostic files are already created by media_android. This module never
sends reports on startup, and never embeds GitHub credentials in Android.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request

# Filled only after the secure Worker is deployed. Do NOT put secrets here.
CRASH_UPLOAD_ENDPOINT = ""
REPORTS_PAGE = "https://koteuka404.github.io/pymusic/crashes/"
CONFIG_URL = REPORTS_PAGE + "config.json"
ISSUE_NEW_PAGE = "https://github.com/KoTeuKa404/pymusic/issues/new?template=crash_report.yml"

_SEND_LOCK = threading.Lock()
_CONFIG_CACHE = {"when": 0.0, "gateway": ""}
_MAX_DIAGNOSTIC_CHARS = 11000
_MAX_UPLOAD_BYTES = 16000

# Explicit opt-in: the user sees "Send crash report (public)" in PyMusic.
# Even after filtering, public GitHub Issues are not appropriate for private logs.
_PATTERNS = [
    (re.compile(r"https?://[^\s<>\]\"']+", re.I), "[URL]"),
    (re.compile(r"(?i)\b(?:authorization|cookie|set-cookie|password|passwd|secret|access_token|refresh_token|client_secret|api_key)\s*[:=]\s*[^\s,;]+"), "[REDACTED]"),
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_.=-]{8,}"), "[AUTH]"),
    (re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    (re.compile(r"(?i)(?:/storage/emulated/\d+|/sdcard|/data/(?:user|data)/\d*|/home/[\w.-]+|/Users/[\w.-]+|C:\\Users\\[^\\\s]+)[^\s\"']*"), "[PRIVATE_PATH]"),
    (re.compile(r"\b[a-fA-F0-9]{32,}\b"), "[HEX_ID]"),
]


def _scrub(text: str) -> str:
    value = str(text or "").replace("\x00", "")
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    # Never include a line containing raw secrets or client-supplied cookies.
    safe_lines = []
    for line in value.splitlines():
        if re.search(r"(?i)\b(?:cookie|password|bearer|authorization|oauth|secret|token|signature)\b", line):
            safe_lines.append("[SENSITIVE_LINE_REMOVED]")
        else:
            safe_lines.append(line[:320])
    return "\n".join(safe_lines)[-_MAX_DIAGNOSTIC_CHARS:]


def _read_tail(path: str, limit: int) -> str:
    if not os.path.isfile(path) or os.path.islink(path):
        return ""
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - limit))
        return handle.read(limit).decode("utf-8", "replace")


def make_report(log_directory: str) -> dict:
    """Read only diagnostic files from the app's own trusted directory."""
    names = []
    try:
        for entry in os.scandir(log_directory):
            if not entry.is_file(follow_symlinks=False):
                continue
            if not entry.name.startswith(("crash_python_", "native_fatal_")):
                continue
            if not entry.name.endswith(".log"):
                continue
            # A native faulthandler file is created at every clean start. It
            # is not a crash unless it contains an actual fault/trace.
            if time.time() - entry.stat(follow_symlinks=False).st_mtime > 14 * 86400:
                continue
            names.append((entry.stat(follow_symlinks=False).st_mtime, entry.path))
    except OSError:
        pass
    names.sort(reverse=True)
    selected = []
    for _mtime, path in names[:4]:
        try:
            body = _read_tail(path, 6000)
            if os.path.basename(path).startswith("native_fatal_"):
                if not re.search(r"Fatal Python error|Current thread|Traceback|SIGABRT|SIGSEGV|JNI DETECTED", body, re.I):
                    continue
            if not body.strip():
                continue
            selected.append(body)
        except OSError:
            continue
        if len(selected) >= 2:
            break

    kind = "crash" if selected else "diagnostic"
    if not selected:
        # No raw recent browser history, stream URLs or cookies are collected.
        for fname in ("pymusic_diag.txt",):
            try:
                latest = _read_tail(os.path.join(log_directory, fname), 10000)
                interesting = (
                    line for line in latest.splitlines()
                    if re.search(r"\[CRASH\]|Exception|Traceback|Error|Fatal|JNI|SIGABRT", line, re.I)
                )
                selected.append("\n".join(interesting)[-6000:])
            except OSError:
                pass
    details = _scrub("\n\n".join(selected)) or "No crash traceback captured by Android."
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    return {
        "kind": kind,
        "title": "PyMusic crash" if kind == "crash" else "PyMusic diagnostic",
        "summary": ("Crash report from the PyMusic Android app" if kind == "crash"
                    else "Manual diagnostics; crash stack may not be captured"),
        "details": details,
        "created_at": stamp,
        "schema": 1,
    }


def _gateway_url() -> str:
    """Read deployment URL from the GitHub Pages config, not from an APK secret.

    The page can be enabled later without rebuilding PyMusic. No log is sent
    to GitHub Pages; config.json contains only the public Worker endpoint.
    """
    value = str(CRASH_UPLOAD_ENDPOINT or "").strip()
    if not value:
        if time.monotonic() - float(_CONFIG_CACHE["when"]) < 1800:
            value = str(_CONFIG_CACHE["gateway"])
        else:
            _CONFIG_CACHE["when"] = time.monotonic()
            try:
                import certifi
                context = ssl.create_default_context(cafile=certifi.where())
                req = urllib.request.Request(CONFIG_URL, headers={
                    "Accept": "application/json",
                    "User-Agent": "PyMusic-Android-Crash-Reporter/1",
                })
                with urllib.request.urlopen(req, timeout=5, context=context) as result:
                    if result.geturl() != CONFIG_URL or result.status != 200:
                        return ""
                    data = json.loads(result.read(2048).decode("utf-8"))
                if isinstance(data, dict):
                    value = str(data.get("gateway") or "").strip()
                    _CONFIG_CACHE["gateway"] = value
            except Exception:
                return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = str(parsed.hostname or "").lower()
        if (parsed.scheme == "https" and host.endswith(".workers.dev")
                and not parsed.username and not parsed.password
                and parsed.port in (None, 443) and not parsed.query and not parsed.fragment):
            return value.rstrip("/")
    except (ValueError, TypeError):
        pass
    return ""


def _send_to_gateway(report: dict) -> str:
    endpoint = _gateway_url()
    if not endpoint:
        raise RuntimeError("report gateway not configured")
    url = endpoint if endpoint.endswith("/report") else endpoint + "/report"
    data = json.dumps(report, ensure_ascii=False).encode("utf-8")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise ValueError("report is too large")
    import certifi
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "PyMusic-Android-Crash-Reporter/1",
    })
    with urllib.request.urlopen(req, timeout=15, context=ssl_context) as response:
        if response.status != 201:
            raise RuntimeError("report server rejected submission")
        result = json.loads(response.read(2048).decode("utf-8"))
    public_issue = str(result.get("issue_url") or "")
    if not re.fullmatch(r"https://github\.com/KoTeuKa404/pymusic/issues/\d+", public_issue):
        raise ValueError("unexpected report link")
    return public_issue


def _open_url(url: str) -> None:
    # Only fixed GitHub destinations or the server's validated issue URL.
    if not (url.startswith("https://github.com/KoTeuKa404/pymusic/")
            or url == REPORTS_PAGE):
        return
    try:
        from jnius import autoclass
        intent_class = autoclass("android.content.Intent")
        uri = autoclass("android.net.Uri")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        intent = intent_class(intent_class.ACTION_VIEW, uri.parse(url))
        activity.startActivity(intent)
    except Exception:
        import webbrowser
        webbrowser.open(url)


def send_crash_report() -> None:
    """Tap to submit an anonymized, public GitHub Issue through the gateway.

    Without a deployed endpoint, the fallback copies only scrubbed diagnostics
    and opens the Issue form. No automatic telemetry runs at application start.
    """
    try:
        from kivy.clock import Clock
        from kivy.core.clipboard import Clipboard
        from kivymd.toast import toast
        import media_android as media
    except Exception:
        return

    if not _SEND_LOCK.acquire(blocking=False):
        return

    def on_ui(action: str, message: str = "", clipboard_text: str | None = None):
        def run(_dt):
            if clipboard_text is not None:
                try:
                    Clipboard.copy(clipboard_text)
                except Exception:
                    pass
            if message:
                try:
                    toast(message)
                except Exception:
                    print("[CRASH-REPORT] " + message)
            if action:
                _open_url(action)
        Clock.schedule_once(run, 0)

    def worker():
        try:
            report = make_report(media.LOG_DIR)
            if _gateway_url():
                try:
                    public_issue = _send_to_gateway(report)
                except Exception as exc:
                    print("[CRASH-REPORT] upload failed:", type(exc).__name__)
                    on_ui("", "Не вдалося надіслати репорт. Перевір з'єднання.")
                    return
                on_ui(public_issue, "Звіт надіслано на GitHub")
            else:
                sanitized_body = (
                    "## " + report["summary"] + "\n\n"
                    + "Time: " + report["created_at"] + "\n\n"
                    + chr(96) * 3 + "text\n"
                    + report["details"] + "\n" + chr(96) * 3
                )
                on_ui(
                    ISSUE_NEW_PAGE,
                    "Лог скопійовано. Встав його у GitHub Issue.",
                    sanitized_body,
                )
        except Exception as exc:
            print("[CRASH-REPORT] preparation failed:", type(exc).__name__)
            on_ui("", "Не вдалося підготувати звіт.")
        finally:
            _SEND_LOCK.release()

    threading.Thread(
        target=worker, name="pymusic-manual-crash-report", daemon=True
    ).start()
