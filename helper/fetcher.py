import feedparser
import re
from config import RSS_FEEDS

def extract_image_from_entry(entry):
    if "media_content" in entry:
        for media in entry.media_content:
            if "url" in media:
                return media["url"]
                
    if "media_thumbnail" in entry:
        for thumb in entry.media_thumbnail:
            if "url" in thumb:
                return thumb["url"]
                
    if "enclosures" in entry:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image/"):
                return enc.get("href")

    content_blob = ""
    if "summary" in entry:
        content_blob += str(entry.summary)
    if "content" in entry:
        for c in entry.content:
            content_blob += str(c.get("value", ""))
            
    if content_blob:
        img_matches = re.findall(r'src="([^"]+)"', content_blob)
        if not img_matches:
            img_matches = re.findall(r"src='([^']+)'", content_blob)
            
        for img_url in img_matches:
            if img_url.lower().startswith("http") and not any(x in img_url.lower() for x in ["icon", "pixel", "avatar", "emoji", "spacer"]):
                return img_url

    return None

def fetch_new_articles(db, limit: int = 5, ignore_db: bool = False):
    new_articles = []
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            parsed_feed = feedparser.parse(feed_url)
            for entry in parsed_feed.entries[:limit]:
                link = entry.get("link")
                title = entry.get("title")
                summary = entry.get("summary", "")
                image_url = extract_image_from_entry(entry)
                
                if link and title:
                    if ignore_db or not db.is_posted(link, title):
                        new_articles.append({
                            "title": title,
                            "link": link,
                            "summary": summary,
                            "source": source_name,
                            "image_url": image_url
                        })
        except Exception as e:
            print(f"Error fetching {source_name}: {e}")
    return new_articles
