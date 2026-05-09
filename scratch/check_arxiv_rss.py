import feedparser
import json

query = "cond-mat"
url = f"https://rss.arxiv.org/atom/{query}"
print(f"Fetching {url}...")
feed = feedparser.parse(url)

print(f"Feed title: {getattr(feed.feed, 'title', 'N/A')}")
print(f"Number of entries: {len(feed.entries)}")

if len(feed.entries) > 0:
    for i, entry in enumerate(feed.entries[:5]):
        print(f"\nEntry {i}:")
        print(f"  ID: {entry.id}")
        print(f"  Title: {entry.title}")
        print(f"  Announce Type: {entry.get('arxiv_announce_type', 'N/A')}")
else:
    print("No entries found.")
