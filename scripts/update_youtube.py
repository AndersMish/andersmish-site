#!/usr/bin/env python3
"""Henter nyeste video fra YouTube-kanalens offentlige RSS-feed og skriver youtube.json.

Kilde (ingen nøkler): https://www.youtube.com/feeds/videos.xml?channel_id=<id>
Skriver bare filen når nyeste video (id/tittel) har endret seg. Ved feil avsluttes
skriptet med feilkode og youtube.json røres ikke.
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "UCE7BAcHCF5EEcRT1sVykkHQ")  # @andersmishtv
OUT = os.environ.get("YOUTUBE_FILE", "youtube.json")
UA = "andersmish-site-youtube-updater/1.0 (+https://andersmish.com)"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def fetch_latest():
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        root = ET.fromstring(r.read())
    entry = root.find("a:entry", NS)
    if entry is None:
        raise RuntimeError("Ingen videoer i feeden")
    vid = entry.findtext("yt:videoId", namespaces=NS) or ""
    if not re.fullmatch(r"[\w-]{11}", vid):
        raise RuntimeError(f"Ugyldig video-ID: {vid!r}")
    title = " ".join((entry.findtext("a:title", namespaces=NS) or "").split())
    link = entry.find("a:link[@rel='alternate']", NS)
    href = link.get("href") if link is not None else f"https://www.youtube.com/watch?v={vid}"
    if not href.startswith("https://www.youtube.com/"):
        href = f"https://www.youtube.com/watch?v={vid}"
    return {
        "id": vid,
        "title": title,
        "url": href,
        "is_short": "/shorts/" in href,
        "published": entry.findtext("a:published", namespaces=NS),
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
    }


def main():
    latest = fetch_latest()
    old = {}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                old = json.load(f)
        except Exception:  # noqa: BLE001
            old = {}
    o = old.get("latest") or {}
    if (o.get("id"), o.get("title")) == (latest["id"], latest["title"]):
        print(f"Ingen endring (nyeste: {latest['id']}).")
        return
    data = {
        "channel_id": CHANNEL_ID,
        "channel_url": "https://www.youtube.com/@andersmishtv",
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "YouTube RSS",
        "latest": latest,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"Skrev {OUT}: {latest['id']} – {latest['title']}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"Feil: {e}", file=sys.stderr)
        sys.exit(1)
