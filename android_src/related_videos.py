from __future__ import annotations

"""Load a compact list of YouTube recommendations.

The preferred source is the web client's ``next`` endpoint.  Search is used as
an independent fallback so a layout change in the watch page does not leave the
player without recommendations.
"""

import html
import json
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any

_WEB_UA = (
    "Mozilla/5.0 (Linux; Android 12; Mobile) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36"
)


def _video_id(value: str) -> str:
    value = html.unescape(str(value or "")).replace("\\/", "/")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    try:
        parsed = urllib.parse.urlparse(value)
        candidate = str(
            (
                urllib.parse.parse_qs(parsed.query).get("v")
                or [""]
            )[0]
        )
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate
        if "youtu.be" in parsed.netloc.lower():
            candidate = parsed.path.strip("/").split("/")[0]
            if re.fullmatch(
                r"[A-Za-z0-9_-]{11}", candidate
            ):
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
                    return json.loads(
                        text[start : index + 1]
                    )
    except Exception:
        pass
    return None


def _json_string(text: str, key: str) -> str:
    try:
        match = re.search(
            rf'"{re.escape(key)}"\s*:\s*'
            r'"((?:\\.|[^"\\])*)"',
            text,
        )
        if not match:
            return ""
        return str(json.loads('"' + match.group(1) + '"'))
    except Exception:
        return ""


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
            "ytimg.com" in value
            or "ggpht.com" in value
        ):
            value = html.unescape(value).replace(
                "\\u0026", "&"
            ).replace("\\/", "/")
            return (
                "https:" + value
                if value.startswith("//")
                else value
            )
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
        for key in ("metadataRows", "secondaryText"):
            value = _first_text(metadata.get(key))
            if value:
                return value
    return ""


def _collect(
    data: Any, current_id: str, limit: int
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(
        video_id: str,
        renderer: dict[str, Any],
        title: str = "",
    ) -> None:
        video_id = str(video_id or "")
        if (
            not re.fullmatch(
                r"[A-Za-z0-9_-]{11}", video_id
            )
            or video_id == current_id
            or video_id in seen
        ):
            return
        title = (
            title
            or _text(renderer.get("title"))
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
                "url": (
                    "https://www.youtube.com/watch?v="
                    + video_id
                ),
                "title": title,
                "channel": _channel(renderer),
                "thumbnail": (
                    _thumb_from_node(renderer)
                    or "https://i.ytimg.com/vi/"
                    + video_id
                    + "/mqdefault.jpg"
                ),
                "duration": _text(
                    renderer.get("lengthText")
                ),
            }
        )

    def walk(node: Any) -> None:
        if len(output) >= limit:
            return
        if isinstance(node, dict):
            for key in (
                "compactVideoRenderer",
                "videoRenderer",
                "gridVideoRenderer",
                "playlistVideoRenderer",
                "reelItemRenderer",
            ):
                renderer = node.get(key)
                if isinstance(renderer, dict):
                    add(
                        str(renderer.get("videoId") or ""),
                        renderer,
                    )

            lockup = node.get("lockupViewModel")
            if isinstance(lockup, dict):
                add(
                    str(
                        lockup.get("contentId")
                        or lockup.get("videoId")
                        or ""
                    ),
                    lockup,
                )
            if node.get("videoId"):
                add(str(node.get("videoId") or ""), node)
            elif node.get("contentId") and str(
                node.get("contentType") or ""
            ).upper() in ("VIDEO", ""):
                add(str(node.get("contentId") or ""), node)

            for value in node.values():
                walk(value)
                if len(output) >= limit:
                    return
        elif isinstance(node, list):
            for value in node:
                walk(value)
                if len(output) >= limit:
                    return

    walk(data)
    return output[:limit]


def _request(
    url: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    request_headers = {
        "User-Agent": _WEB_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.youtube.com/",
        "Origin": "https://www.youtube.com",
        "Cookie": "CONSENT=YES+1; SOCS=CAI",
        "Connection": "close",
    }
    request_headers.update(headers or {})
    request = urllib.request.Request(
        url, data=data, headers=request_headers
    )
    with urllib.request.urlopen(
        request,
        timeout=6,
        context=ssl._create_unverified_context(),
    ) as response:
        return response.read(3_000_000)


def _search(
    video_id: str,
    title: str,
    channel: str,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        from youtube_search import fetch_youtube_results

        clean_title = re.sub(
            r"\s+", " ", str(title or "")
        ).strip()
        clean_channel = re.sub(
            r"\s+", " ", str(channel or "")
        ).strip()
        queries = []
        if clean_title and clean_channel:
            queries.append(
                f"{clean_title} {clean_channel}"
            )
        if clean_title:
            queries.append(clean_title)
        if clean_channel:
            queries.append(clean_channel)

        output = []
        seen = set()
        for query in queries:
            videos, _playlists, _continuation, _config = (
                fetch_youtube_results(query)
            )
            for entry in videos or []:
                if not isinstance(entry, (tuple, list)):
                    continue
                candidate = _video_id(
                    str(entry[0] or "")
                )
                if (
                    not candidate
                    or candidate == video_id
                    or candidate in seen
                ):
                    continue
                seen.add(candidate)
                output.append(
                    {
                        "id": candidate,
                        "video_id": candidate,
                        "url": (
                            "https://www.youtube.com/watch?v="
                            + candidate
                        ),
                        "title": (
                            str(entry[1] or "")
                            if len(entry) > 1
                            else ""
                        ),
                        "channel": (
                            str(entry[2] or "")
                            if len(entry) > 2
                            else ""
                        ),
                        "thumbnail": (
                            str(entry[3] or "")
                            if len(entry) > 3
                            and entry[3]
                            else "https://i.ytimg.com/vi/"
                            + candidate
                            + "/mqdefault.jpg"
                        ),
                        "duration": (
                            str(entry[4] or "")
                            if len(entry) > 4
                            else ""
                        ),
                    }
                )
                if len(output) >= limit:
                    return output
            if output:
                return output
        return output
    except Exception as exc:
        print("[RELATED] search fallback failed:", exc)
        return []


def fetch_related_videos(
    video_url: str,
    title: str = "",
    channel: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 8), 16))
    current_id = _video_id(video_url)
    if not current_id:
        print("[RELATED] invalid video URL:", video_url)
        return []

    try:
        watch_url = (
            "https://www.youtube.com/watch?"
            + urllib.parse.urlencode(
                {
                    "v": current_id,
                    "hl": "en",
                    "gl": "US",
                    "persist_hl": "1",
                }
            )
        )
        text = _request(watch_url).decode(
            "utf-8", errors="ignore"
        )

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

        items = (
            _collect(initial, current_id, limit)
            if initial
            else []
        )
        if items:
            print(
                f"[RELATED] watch page returned "
                f"{len(items)} items"
            )

        if len(items) < min(4, limit):
            api_key = _json_string(
                text, "INNERTUBE_API_KEY"
            )
            version = _json_string(
                text, "INNERTUBE_CLIENT_VERSION"
            )
            visitor = _json_string(text, "VISITOR_DATA")
            if api_key and version:
                client = {
                    "clientName": "WEB",
                    "clientVersion": version,
                    "hl": "en",
                    "gl": "US",
                }
                if visitor:
                    client["visitorData"] = visitor
                payload = {
                    "context": {"client": client},
                    "videoId": current_id,
                    "contentCheckOk": True,
                    "racyCheckOk": True,
                }
                headers = {
                    "Content-Type": "application/json",
                    "X-Youtube-Client-Name": "1",
                    "X-Youtube-Client-Version": version,
                }
                if visitor:
                    headers["X-Goog-Visitor-Id"] = visitor
                endpoint = (
                    "https://www.youtube.com/youtubei/v1/next?"
                    + urllib.parse.urlencode(
                        {
                            "key": api_key,
                            "prettyPrint": "false",
                        }
                    )
                )
                raw = _request(
                    endpoint,
                    json.dumps(
                        payload, separators=(",", ":")
                    ).encode("utf-8"),
                    headers,
                )
                next_items = _collect(
                    json.loads(
                        raw.decode(
                            "utf-8", errors="ignore"
                        )
                    ),
                    current_id,
                    limit,
                )
                if next_items:
                    items = next_items
                    print(
                        f"[RELATED] next endpoint returned "
                        f"{len(items)} items"
                    )

        if items:
            return items[:limit]
    except Exception as exc:
        print("[RELATED] watch/next failed:", exc)

    items = _search(
        current_id, title, channel, limit
    )
    print(
        f"[RELATED] search returned {len(items)} "
        f"items for {current_id}"
    )
    return items[:limit]
