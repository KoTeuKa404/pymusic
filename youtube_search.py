import httpx, json, re

def fetch_youtube_videos(query):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    url = f"https://www.youtube.com/results?search_query={query}"
    resp = httpx.get(url, headers=headers, timeout=10)
    if resp.status_code != 200:
        return []

    match = re.search(r"var ytInitialData = ({.*?});", resp.text)
    if not match:
        return []

    data = json.loads(match.group(1))
    try:
        sections = data["contents"]["twoColumnSearchResultsRenderer"]\
            ["primaryContents"]["sectionListRenderer"]["contents"]
        videos = []
        for sec in sections:
            items = sec.get("itemSectionRenderer",{}).get("contents",[])
            for it in items:
                vr = it.get("videoRenderer")
                if not vr: continue
                title   = "".join([r["text"] for r in vr["title"]["runs"]])
                vid     = vr["videoId"]
                thumb   = vr["thumbnail"]["thumbnails"][-1]["url"]
                chan    = vr["ownerText"]["runs"][0]["text"]
                dur     = vr.get("lengthText",{}).get("simpleText","")
                videos.append((
                    f"https://www.youtube.com/watch?v={vid}",
                    title, chan, thumb, dur
                ))
        return videos
    except Exception:
        return []
