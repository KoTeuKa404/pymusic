from __future__ import annotations

"""Fetch the same related-video feed YouTube returns for an open watch page.

Primary source: ``youtubei/v1/next`` for the current ``videoId``.  This is the
watch-next/secondary-results feed used by YouTube itself, not a title search.
The watch page's ``ytInitialData`` is retained only as a same-page fallback.
"""

import copy
import html
import json
import re
import urllib.parse
from typing import Any

import httpx

_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0.0.0 Safari/537.36"
)

# Fallbacks are only used if the bundled yt-dlp client table cannot be imported.
# At runtime we prefer yt-dlp's current client versions so this does not become
# stale whenever YouTube rolls the web client forward.
_FALLBACK_CLIENTS = {
    "web": {
        "host": "www.youtube.com",
        "number": "1",
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20260708.00.00",
                "hl": "uk",
                "gl": "UA",
            }
        },
    },
    "mweb": {
        "host": "www.youtube.com",
        "number": "2",
        "context": {
            "client": {
                "clientName": "MWEB",
                "clientVersion": "2.20260708.05.00",
                "hl": "uk",
                "gl": "UA",
                "userAgent": (
                    "Mozilla/5.0 (iPad; CPU OS 16_7_10 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                    "Mobile/15E148 Safari/604.1"
                ),
            }
        },
    },
}


def _video_id(value: str) -> str:
    value = html.unescape(str(value or "")).replace("\\/", "/").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    try:
        parsed = urllib.parse.urlparse(value)
        query = urllib.parse.parse_qs(parsed.query or "")
        candidate = str((query.get("v") or [""])[0] or "")
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate
        if "youtu.be" in (parsed.netloc or "").lower():
            candidate = (parsed.path or "").strip("/").split("/")[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate
    except Exception:
        pass
    match = re.search(
        r"(?:v=|youtu\.be/|/(?:shorts|live|embed|vi|vi_webp)/)"
        r"([A-Za-z0-9_-]{11})",
        value,
    )
    return match.group(1) if match else ""


def _json_after(text: str, marker: str) -> Any | None:
    """Read one balanced JSON object immediately following *marker*."""
    try:
        position = text.find(marker)
        if position < 0:
            return None
        start = text.find("{", position + len(marker))
        if start < 0:
            return None
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : index + 1])
    except Exception:
        pass
    return None


def _text(node: Any) -> str:
    if isinstance(node, str):
        return html.unescape(node)
    if not isinstance(node, dict):
        return ""
    for key in ("simpleText", "content", "text"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return html.unescape(value)
    runs = node.get("runs") or []
    if isinstance(runs, list):
        return html.unescape(
            "".join(
                str(run.get("text") or "")
                for run in runs
                if isinstance(run, dict)
            )
        )
    return ""


def _first_text(node: Any) -> str:
    if isinstance(node, dict):
        direct = _text(node)
        if direct:
            return direct
        for key in (
            "title",
            "headline",
            "primaryText",
            "secondaryText",
            "metadata",
            "contentMetadataViewModel",
        ):
            value = node.get(key)
            text = _first_text(value)
            if text:
                return text
        for value in node.values():
            text = _first_text(value)
            if text:
                return text
    elif isinstance(node, list):
        for value in node:
            text = _first_text(value)
            if text:
                return text
    return ""


def _thumb_from_node(node: Any) -> str:
    if isinstance(node, dict):
        value = node.get("url")
        if isinstance(value, str) and (
            "ytimg.com" in value or "ggpht.com" in value
        ):
            value = (
                html.unescape(value)
                .replace("\\u0026", "&")
                .replace("\\/", "/")
            )
            return "https:" + value if value.startswith("//") else value
        for key in (
            "thumbnail",
            "thumbnails",
            "image",
            "sources",
            "contentImage",
        ):
            result = _thumb_from_node(node.get(key))
            if result:
                return result
        for value in node.values():
            result = _thumb_from_node(value)
            if result:
                return result
    elif isinstance(node, list):
        # YouTube generally orders thumbnail arrays from small to large.
        for value in reversed(node):
            result = _thumb_from_node(value)
            if result:
                return result
    return ""


def _channel(renderer: dict[str, Any]) -> str:
    for key in (
        "shortBylineText",
        "longBylineText",
        "ownerText",
        "byline",
        "subtitle",
    ):
        value = _text(renderer.get(key))
        if value:
            return value

    metadata = renderer.get("metadata")
    if isinstance(metadata, dict):
        # Modern lockupViewModel keeps author/metadata deeper in this subtree.
        rows = metadata.get("lockupMetadataViewModel") or metadata
        value = _first_text(rows)
        if value:
            return value
    return ""


def _duration(renderer: dict[str, Any]) -> str:
    value = _text(renderer.get("lengthText"))
    if value:
        return value
    try:
        overlays = renderer.get("thumbnailOverlays") or []
        for overlay in overlays:
            if not isinstance(overlay, dict):
                continue
            value = _text(
                (overlay.get("thumbnailOverlayTimeStatusRenderer") or {}).get("text")
            )
            if value:
                return value
    except Exception:
        pass
    return ""


def _collect_renderers(
    root: Any,
    current_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Collect video cards in their server-provided order from one related root."""
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(video_id: str, renderer: dict[str, Any]) -> None:
        video_id = str(video_id or "").strip()
        if (
            len(output) >= limit
            or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
            or video_id == current_id
            or video_id in seen
        ):
            return

        title = (
            _text(renderer.get("title"))
            or _first_text(renderer.get("metadata"))
            or _first_text(renderer)
        ).strip()
        if not title:
            return

        seen.add(video_id)
        output.append(
            {
                "id": video_id,
                "video_id": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": title,
                "channel": _channel(renderer),
                "thumbnail": (
                    _thumb_from_node(renderer)
                    or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
                ),
                "duration": _duration(renderer),
            }
        )

    def walk(node: Any) -> None:
        if len(output) >= limit:
            return
        if isinstance(node, list):
            for value in node:
                walk(value)
                if len(output) >= limit:
                    return
            return
        if not isinstance(node, dict):
            return

        # These are the card renderers currently used by WEB/MWEB watch-next.
        for key in (
            "compactVideoRenderer",
            "videoWithContextRenderer",
            "videoRenderer",
            "gridVideoRenderer",
        ):
            renderer = node.get(key)
            if isinstance(renderer, dict):
                add(str(renderer.get("videoId") or ""), renderer)

        lockup = node.get("lockupViewModel")
        if isinstance(lockup, dict):
            content_type = str(lockup.get("contentType") or "").upper()
            # Empty contentType occurs on some MWEB responses; contentId is still
            # a video id there. Explicit playlist lockups are ignored.
            if "PLAYLIST" not in content_type:
                add(
                    str(lockup.get("contentId") or lockup.get("videoId") or ""),
                    lockup,
                )

        # Keep walking because shelves/continuation wrappers contain the cards.
        for value in node.values():
            walk(value)
            if len(output) >= limit:
                return

    walk(root)
    return output[:limit]


def _path(data: Any, *parts: str) -> Any | None:
    current = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _related_roots(data: Any) -> list[Any]:
    """Return watch-next/secondary-results containers, never search results."""
    roots: list[Any] = []

    direct_paths = (
        (
            "contents",
            "twoColumnWatchNextResults",
            "secondaryResults",
            "secondaryResults",
            "results",
        ),
        (
            "contents",
            "twoColumnWatchNextResults",
            "secondaryResults",
            "secondaryResults",
        ),
        (
            "contents",
            "singleColumnWatchNextResults",
            "results",
            "results",
            "contents",
        ),
    )
    for parts in direct_paths:
        value = _path(data, *parts)
        if value is not None:
            roots.append(value)

    if roots:
        return roots

    # YouTube occasionally wraps the same secondaryResults object in response
    # commands. Find only nodes explicitly named secondaryResults so we do not
    # accidentally turn comments/primary metadata into recommendations.
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("secondaryResults", "secondaryResultsRenderer"):
                    roots.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)
    return roots


def _items_from_watch_data(
    data: Any,
    current_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    roots = _related_roots(data)
    if not roots:
        return []

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for item in _collect_renderers(root, current_id, limit):
            video_id = str(item.get("video_id") or "")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            output.append(item)
            if len(output) >= limit:
                return output
    return output


def _client_profiles() -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    try:
        from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS

        for name in ("web", "mweb"):
            source = INNERTUBE_CLIENTS.get(name) or {}
            context = copy.deepcopy(source.get("INNERTUBE_CONTEXT") or {})
            client = context.setdefault("client", {})
            client["hl"] = "uk"
            client["gl"] = "UA"
            client.setdefault("timeZone", "Europe/Kyiv")
            client.setdefault("utcOffsetMinutes", 180)
            profiles.append(
                {
                    "name": name,
                    "host": str(source.get("INNERTUBE_HOST") or "www.youtube.com"),
                    "number": str(source.get("INNERTUBE_CONTEXT_CLIENT_NAME") or (1 if name == "web" else 2)),
                    "context": context,
                }
            )
    except Exception as exc:
        print("[RELATED] yt-dlp client table unavailable:", exc)

    if profiles:
        return profiles

    for name in ("web", "mweb"):
        fallback = copy.deepcopy(_FALLBACK_CLIENTS[name])
        fallback["name"] = name
        profiles.append(fallback)
    return profiles


def _headers_for(profile: dict[str, Any]) -> dict[str, str]:
    client = (profile.get("context") or {}).get("client") or {}
    version = str(client.get("clientVersion") or "")
    user_agent = str(client.get("userAgent") or _DESKTOP_UA)
    return {
        "User-Agent": user_agent,
        "Accept": "*/*",
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.7",
        "Content-Type": "application/json",
        "Origin": "https://www.youtube.com",
        "Referer": "https://www.youtube.com/",
        "X-YouTube-Client-Name": str(profile.get("number") or "1"),
        "X-YouTube-Client-Version": version,
        # This is YouTube's anonymous consent cookie, not a user credential.
        "Cookie": "SOCS=CAI; PREF=hl=uk&gl=UA",
    }


def _watch_bootstrap(
    client: httpx.Client,
    video_id: str,
) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    """Return (ytcfg, ytInitialData, raw_text) from the same watch page."""
    url = "https://www.youtube.com/watch?" + urllib.parse.urlencode(
        {
            "v": video_id,
            "hl": "uk",
            "gl": "UA",
            "persist_hl": "1",
        }
    )
    response = client.get(url)
    response.raise_for_status()
    text = response.text

    ytcfg = _json_after(text, "ytcfg.set(") or {}
    initial = None
    for marker in (
        "var ytInitialData =",
        "ytInitialData =",
        'window["ytInitialData"] =',
        "window['ytInitialData'] =",
    ):
        initial = _json_after(text, marker)
        if initial:
            break
    return ytcfg, initial, text


def _merge_page_config(
    profile: dict[str, Any],
    ytcfg: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    context = copy.deepcopy(profile.get("context") or {})
    client = context.setdefault("client", {})

    page_context = ytcfg.get("INNERTUBE_CONTEXT") or {}
    page_client = page_context.get("client") if isinstance(page_context, dict) else None
    if isinstance(page_client, dict):
        for key in (
            "clientName",
            "clientVersion",
            "visitorData",
            "hl",
            "gl",
            "timeZone",
            "utcOffsetMinutes",
        ):
            if page_client.get(key) not in (None, ""):
                client[key] = page_client[key]

    visitor = str(
        ytcfg.get("VISITOR_DATA")
        or client.get("visitorData")
        or ""
    )
    if visitor:
        client["visitorData"] = visitor

    api_key = str(ytcfg.get("INNERTUBE_API_KEY") or "")
    return context, visitor, api_key


def _fetch_next_for_profile(
    profile: dict[str, Any],
    video_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    headers = _headers_for(profile)
    timeout = httpx.Timeout(9.0, connect=7.0)

    with httpx.Client(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    ) as client:
        ytcfg: dict[str, Any] = {}
        initial: dict[str, Any] | None = None
        try:
            ytcfg, initial, _text_raw = _watch_bootstrap(client, video_id)
        except Exception as exc:
            print(
                f"[RELATED] {profile.get('name')} watch bootstrap failed: {exc}"
            )

        context, visitor, api_key = _merge_page_config(profile, ytcfg)
        client_info = context.get("client") or {}
        version = str(client_info.get("clientVersion") or "")

        api_headers = dict(headers)
        api_headers["X-YouTube-Client-Version"] = version
        if visitor:
            api_headers["X-Goog-Visitor-Id"] = visitor

        payload = {
            "context": context,
            "videoId": video_id,
            "contentCheckOk": True,
            "racyCheckOk": True,
        }

        endpoint = (
            f"https://{profile.get('host') or 'www.youtube.com'}"
            "/youtubei/v1/next?prettyPrint=false"
        )
        if api_key:
            endpoint += "&" + urllib.parse.urlencode({"key": api_key})

        try:
            response = client.post(endpoint, headers=api_headers, json=payload)
            response.raise_for_status()
            data = response.json()
            items = _items_from_watch_data(data, video_id, limit)
            print(
                "[RELATED] YouTube watch-next "
                f"client={profile.get('name')} version={version} "
                f"returned={len(items)}"
            )
            if items:
                return items
        except Exception as exc:
            print(
                f"[RELATED] {profile.get('name')} youtubei/next failed: {exc}"
            )

        # Same YouTube watch page, not search. This keeps the semantics exact
        # even when the next endpoint changes temporarily.
        if initial:
            items = _items_from_watch_data(initial, video_id, limit)
            print(
                f"[RELATED] {profile.get('name')} ytInitialData returned={len(items)}"
            )
            if items:
                return items

    return []


def fetch_related_videos(
    video_url: str,
    title: str = "",
    channel: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return YouTube's own watch-page related videos for ``video_url``.

    ``title`` and ``channel`` remain in the signature for compatibility with the
    existing player call sites, but they are intentionally not used for search.
    """
    del title, channel

    limit = max(1, min(int(limit or 8), 16))
    current_id = _video_id(video_url)
    if not current_id:
        print("[RELATED] invalid current YouTube URL:", video_url)
        return []

    for profile in _client_profiles():
        try:
            items = _fetch_next_for_profile(profile, current_id, limit)
            if items:
                return items[:limit]
        except Exception as exc:
            print(
                f"[RELATED] watch-next profile {profile.get('name')} failed: {exc}"
            )

    print(
        f"[RELATED] YouTube watch-next returned no related videos for {current_id}"
    )
    return []
