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
    Повертає True, якщо це шорт, який треба проігнорити.
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
    for it in items or []:
        # інколи елементи загорнуті в richItemRenderer
        if "richItemRenderer" in it:
            it = it.get("richItemRenderer", {}).get("content", {}) or it

        # --------- ВІДЕО (без шортів) ---------
        vr = it.get("videoRenderer")
        if vr:
            # фільтр шортів
            if _is_shorts_video(vr):
                continue
            try:
                title = _extract_text(vr.get("title"))
                vid = vr.get("videoId") or ""
                if not vid:
                    raise ValueError("no videoId")
                thumb = _extract_thumbnail(vr)
                chan = _extract_text(vr.get("ownerText"))
                dur = _extract_text(vr.get("lengthText"))
                videos.append(
                    (
                        f"https://www.youtube.com/watch?v={vid}",
                        title,
                        chan,
                        thumb,
                        dur,
                    )
                )
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

    url = f"https://www.youtube.com/results?search_query={query}&hl=en&persist_gl=1"

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
