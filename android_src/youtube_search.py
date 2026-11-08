# youtube_search.py
import httpx
import json
import re


def _extract_text(field: dict | None) -> str:
    """
    Акуратно дістає текст із структур YouTube:
    - {"simpleText": "..."}
    - {"runs": [{"text": "..."}, ...]}
    """
    if not field:
        return ""
    if isinstance(field, str):
        return field
    simple = field.get("simpleText")
    if simple:
        return simple
    runs = field.get("runs")
    if isinstance(runs, list):
        return "".join((r.get("text") or "") for r in runs)
    return ""


def _extract_thumbnail(obj: dict | None) -> str:
    """
    Повертає один нормальний URL мініатюри для відео / плейлиста.
    YouTube любить вкладати thumbnails по-різному.
    """
    if not obj:
        return ""
    # найчастіший варіант: {"thumbnail": {"thumbnails": [ ... ]}}
    thumb_block = obj.get("thumbnail")
    if isinstance(thumb_block, dict):
        thumbs = thumb_block.get("thumbnails") or []
        if thumbs:
            return thumbs[-1].get("url", "") or ""

    # інколи є просто "thumbnails": [ ... ]
    thumbs = obj.get("thumbnails")
    if isinstance(thumbs, list) and thumbs:
        # буває вкладений список: [{"thumbnails":[...]}]
        if isinstance(thumbs[0], dict) and "thumbnails" in thumbs[0]:
            inner = thumbs[0].get("thumbnails") or []
            if inner:
                return inner[-1].get("url", "") or ""
        return thumbs[-1].get("url", "") or ""

    return ""


def _is_shorts_video(vr: dict) -> bool:
    """
    Повертає True для окремого відкриття в Shorts-переглядачі.
    """
    # інколи шорти маркуються полями isShort / isShorts
    if vr.get("isShort") or vr.get("isShorts"):
        return True

    # головний варіант - через thumbnailOverlays.style == "SHORTS"
    overlays = vr.get("thumbnailOverlays") or []
    for ov in overlays:
        ts = ov.get("thumbnailOverlayTimeStatusRenderer") or {}
        style = ts.get("style")
        if style == "SHORTS":
            return True

    return False


def _shorts_shelf_items(node, limit=18):
    """Read genuine Shorts renderer IDs rather than guessing by duration.

    Returns the same five-field rows used for other search videos. Supports
    both reelItemRenderer and shortsLockupViewModel variants of YouTube
    shelves. Traversal is bounded to keep malformed responses inexpensive.
    """
    found = []
    visited = 0
    stack = [node]
    seen = set()
    while stack and visited < 900 and len(found) < limit:
        current = stack.pop()
        visited += 1
        if isinstance(current, list):
            stack.extend(reversed(current[:60]))
            continue
        if not isinstance(current, dict):
            continue
        renderer = current.get("reelItemRenderer") or current.get("shortsLockupViewModel")
        if isinstance(renderer, dict):
            on_tap = renderer.get("onTap")
            on_tap = on_tap if isinstance(on_tap, dict) else {}
            endpoint = on_tap.get("innertubeCommand") or {}
            endpoint = endpoint if isinstance(endpoint, dict) else {}
            reel = endpoint.get("reelWatchEndpoint") or {}
            reel = reel if isinstance(reel, dict) else {}
            vid = str(renderer.get("videoId") or reel.get("videoId") or "").strip()
            if not vid:
                # Modern shelves may expose only a namespaced entity ID.
                entity = str(renderer.get("entityId") or "")
                match = re.fullmatch(
                    r"shorts-shelf-item-([A-Za-z0-9_-]{11})", entity
                )
                vid = match.group(1) if match else ""
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) and vid not in seen:
                seen.add(vid)
                overlay = renderer.get("overlayMetadata") or {}
                overlay = overlay if isinstance(overlay, dict) else {}
                primary = overlay.get("primaryText") or {}
                primary = primary if isinstance(primary, dict) else {}
                title = (
                    _extract_text(renderer.get("headline"))
                    or str(primary.get("content") or "")
                    or str(renderer.get("title") or "Shorts")
                )
                thumbnail = renderer.get("thumbnail") or {}
                thumbnail = thumbnail if isinstance(thumbnail, dict) else {}
                sources = thumbnail.get("sources") or []
                thumb = _extract_thumbnail(renderer)
                if not thumb and sources and isinstance(sources[-1], dict):
                    thumb = str(sources[-1].get("url") or "")
                if not thumb:
                    thumb = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
                found.append((
                    f"https://www.youtube.com/shorts/{vid}",
                    str(title).strip(), "", thumb, "Shorts"
                ))
        for key, value in current.items():
            if key in ("trackingParams", "menu", "navigationEndpoint"):
                continue
            if isinstance(value, (dict, list)):
                stack.append(value)
    return found


def _find_continuation(obj):
    if isinstance(obj, dict):
        cmd = obj.get("continuationCommand") or {}
        token = cmd.get("token")
        if token:
            return token
        for v in obj.values():
            token = _find_continuation(v)
            if token:
                return token
    elif isinstance(obj, list):
        for v in obj:
            token = _find_continuation(v)
            if token:
                return token
    return None


def _parse_items(items):
    videos = []
    playlists = []
    seen_videos = set()
    for it in items or []:
        # інколи елементи загорнуті в richItemRenderer
        if "richItemRenderer" in it:
            it = it.get("richItemRenderer", {}).get("content", {}) or it

        # Include genuine Shorts from YouTube shelves and regular results.
        for short in _shorts_shelf_items(it):
            if short[0] not in seen_videos:
                videos.append(short)
                seen_videos.add(short[0])

        # --------- ВІДЕО + SHORTS ---------
        vr = it.get("videoRenderer")
        if vr:
            try:
                title = _extract_text(vr.get("title"))
                vid = vr.get("videoId") or ""
                if not vid:
                    raise ValueError("no videoId")
                thumb = _extract_thumbnail(vr)
                chan = _extract_text(vr.get("ownerText"))
                dur = _extract_text(vr.get("lengthText"))
                is_short = _is_shorts_video(vr)
                url = (
                    f"https://www.youtube.com/shorts/{vid}"
                    if is_short else f"https://www.youtube.com/watch?v={vid}"
                )
                if url not in seen_videos:
                    videos.append((
                        url, title, chan, thumb,
                        "Shorts" if is_short else dur,
                    ))
                    seen_videos.add(url)
            except Exception as e:
                try:
                    print("[SEARCH] skip video item:", e)
                except Exception:
                    pass

        # --------- ПЛЕЙЛИСТИ ---------
        pr = it.get("playlistRenderer") or it.get("compactPlaylistRenderer")
        if pr:
            try:
                pid = pr.get("playlistId") or ""
                if not pid:
                    raise ValueError("no playlistId")

                p_title = _extract_text(pr.get("title"))
                p_channel = _extract_text(pr.get("shortBylineText"))
                p_thumb = _extract_thumbnail(pr)

                # кількість треків (може бути '50+ videos' / '12 videos' тощо)
                count_text = (
                    _extract_text(pr.get("videoCountText"))
                    or _extract_text(pr.get("thumbnailText"))
                )

                playlists.append(
                    (
                        f"https://www.youtube.com/playlist?list={pid}",
                        p_title,
                        p_channel,
                        p_thumb,
                        count_text,
                    )
                )
            except Exception as e:
                try:
                    print("[SEARCH] skip playlist item:", e)
                except Exception:
                    pass

    continuation = _find_continuation(items)
    return videos, playlists, continuation


def _extract_ytcfg(text: str) -> dict:
    cfg = {}
    try:
        m = re.search(r"ytcfg\.set\((\{.*?\})\);", text, re.S)
        if m:
            cfg = json.loads(m.group(1))
    except Exception:
        cfg = {}

    if not cfg:
        try:
            key_m = re.search(r"\"INNERTUBE_API_KEY\":\"([^\"]+)\"", text)
            ver_m = re.search(r"\"INNERTUBE_CLIENT_VERSION\":\"([^\"]+)\"", text)
            cfg = {
                "INNERTUBE_API_KEY": key_m.group(1) if key_m else None,
                "INNERTUBE_CLIENT_VERSION": ver_m.group(1) if ver_m else None,
            }
        except Exception:
            cfg = {}

    return cfg or {}


def _build_context(cfg: dict) -> dict:
    ctx = cfg.get("INNERTUBE_CONTEXT") or {}
    if not ctx:
        ctx = {
            "client": {
                "clientName": "WEB",
                "clientVersion": cfg.get("INNERTUBE_CLIENT_VERSION") or "2.20240201.00.00",
            }
        }
    return ctx


def fetch_youtube_results(query):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
    }

    from urllib.parse import urlencode
    url = "https://www.youtube.com/results?" + urlencode({
        "search_query": str(query), "hl": "en", "persist_gl": "1"
    })

    try:
        resp = httpx.get(url, headers=headers, timeout=6.0)
    except Exception as e:
        print("[SEARCH] HTTP error:", e)
        return [], [], None, {}

    match = re.search(r"var ytInitialData = ({.*?});", resp.text)
    if not match:
        print("[SEARCH] ytInitialData not found")
        return [], [], None, {}

    try:
        data = json.loads(match.group(1))
        cfg = _extract_ytcfg(resp.text)
        sections = (
            data["contents"]["twoColumnSearchResultsRenderer"]
            ["primaryContents"]["sectionListRenderer"]["contents"]
        )

        videos = []
        playlists = []
        continuation = None

        for sec in sections:
            items = sec.get("itemSectionRenderer", {}).get("contents", [])
            v, p, c = _parse_items(items)
            videos.extend(v)
            playlists.extend(p)
            if c and not continuation:
                continuation = c

        return videos, playlists, continuation, cfg

    except Exception as e:
        print("[SEARCH] parse error:", e)
        return [], [], None, {}


def fetch_youtube_continuation(continuation: str, cfg: dict):
    if not continuation:
        return [], [], None
    api_key = cfg.get("INNERTUBE_API_KEY")
    if not api_key:
        return [], [], None

    url = f"https://www.youtube.com/youtubei/v1/search?key={api_key}"
    context = _build_context(cfg)
    payload = {"context": context, "continuation": continuation}

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Content-Type": "application/json",
        "Origin": "https://www.youtube.com",
        "X-Youtube-Client-Name": "1",
        "X-Youtube-Client-Version": context.get("client", {}).get("clientVersion", "2.20240201.00.00"),
    }

    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=6.0)
        data = resp.json()
    except Exception as e:
        print("[SEARCH] continuation HTTP error:", e)
        return [], [], None

    items = []
    try:
        cmds = data.get("onResponseReceivedCommands") or []
        for cmd in cmds:
            append = cmd.get("appendContinuationItemsAction") or {}
            items = append.get("continuationItems") or []
            if items:
                break
    except Exception:
        items = []

    videos, playlists, next_token = _parse_items(items)
    return videos, playlists, next_token
