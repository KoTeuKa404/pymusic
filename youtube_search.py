import httpx, json, re

def fetch_youtube_results(query):
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
        return [], []

    match = re.search(r"var ytInitialData = ({.*?});", resp.text)
    if not match:
        return [], []

    data = json.loads(match.group(1))
    try:
        sections = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
        videos = []
        playlists = []
        for sec in sections:
            items = sec.get("itemSectionRenderer", {}).get("contents", [])
            for it in items:
                # Відео
                vr = it.get("videoRenderer")
                if vr:
                    title   = "".join([r["text"] for r in vr["title"]["runs"]])
                    vid     = vr["videoId"]
                    thumb   = vr["thumbnail"]["thumbnails"][-1]["url"]
                    chan    = vr["ownerText"]["runs"][0]["text"]
                    dur     = vr.get("lengthText",{}).get("simpleText","")
                    videos.append((
                        f"https://www.youtube.com/watch?v={vid}",
                        title, chan, thumb, dur
                    ))
                # Плейлист
                pl = it.get("playlistRenderer")
                if pl:
                    title = "".join([r["text"] for r in pl["title"]["runs"]])
                    pid   = pl["playlistId"]
                    thumb = pl["thumbnails"]["thumbnails"][-1]["url"]
                    chan  = pl["shortBylineText"]["runs"][0]["text"]
                    count = pl.get("videoCount", "N/A")
                    playlists.append((
                        f"https://www.youtube.com/playlist?list={pid}",
                        title, chan, thumb, count
                    ))
        return videos, playlists
    except Exception:
        return [], []
