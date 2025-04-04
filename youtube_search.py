from youtubesearchpython import VideosSearch

def fetch_youtube_videos(query):
    videos_search = VideosSearch(query, limit=10)
    results = []
    
    for video in videos_search.result()["result"]:
        url = video["link"]
        title = video["title"]
        channel = video["channel"]["name"]
        thumbnail = video["thumbnails"][0]["url"]
        duration = video["duration"] if video["duration"] else "N/A"
        
        results.append((url, title, channel, thumbnail, duration))

    return results
