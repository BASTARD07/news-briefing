#!/usr/bin/env python3
"""
News Briefing builder.

Pulls the newest short newscast from each source in sources.toml and writes a
static site the web player reads:

  site/index.html            the player (copied from web/)
  site/briefing.json         today's lineup: source, headline, audio link, intro clip
  site/intros/*.mp3          short spoken intros ("Next, from NPR News Now")

The audio itself is streamed straight from each publisher, so nothing
copyrighted is re-hosted.

Optional (for running on your own PC, not on a public site):
  --merge                    also stitch everything into site/briefing.mp3
  --base-url URL             public URL of site/, used for site/feed.xml (podcast feed)

Usage:
  python build.py                       # build into ./site
  python build.py --out D:/briefing     # build somewhere else
  python build.py --merge --base-url http://my-pc:8080/
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import feedparser

ROOT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (NewsBriefing; +https://github.com/) feedparser"


# --------------------------------------------------------------------------- feeds

def fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_duration(raw) -> int | None:
    if not raw:
        return None
    try:
        parts = [int(float(p)) for p in str(raw).strip().split(":")]
    except ValueError:
        return None
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs or None


def latest_episode(src: dict) -> dict | None:
    """Return the newest episode of a feed, or None if missing/stale."""
    try:
        feed = feedparser.parse(fetch(src["feed"]))
    except Exception as e:  # network error, bad feed, etc.
        print(f"  ! {src['name']}: could not fetch feed ({e})")
        return None

    best = None
    for e in feed.entries:
        audio = next(
            (l.get("href") for l in e.get("links", [])
             if l.get("rel") == "enclosure" and "audio" in (l.get("type") or "audio")),
            None,
        )
        t = e.get("published_parsed") or e.get("updated_parsed")
        if not audio or not t:
            continue
        ts = calendar.timegm(t)
        if best is None or ts > best["ts"]:
            best = {"ts": ts, "audio": audio, "entry": e}

    if best is None:
        print(f"  ! {src['name']}: no playable episode found")
        return None

    age_h = (time.time() - best["ts"]) / 3600
    if age_h > src.get("max_age_hours", 24):
        print(f"  - {src['name']}: newest episode is {age_h:.0f}h old, skipping")
        return None

    e = best["entry"]
    audio = best["audio"]
    if audio.startswith("http://"):  # avoid mixed-content blocking on https sites
        audio = "https://" + audio[len("http://"):]
    return {
        "source": src["name"],
        "title": (e.get("title") or "").split(" | ")[0].strip(),
        "published": datetime.fromtimestamp(best["ts"], timezone.utc).isoformat(),
        "duration": parse_duration(e.get("itunes_duration")),
        "audio": audio,
    }


# --------------------------------------------------------------------------- intros

def make_tts(jobs: list[tuple[str, Path]], voice: str) -> bool:
    """Render (text, path) pairs with Microsoft Edge neural TTS. Returns False if unavailable."""
    try:
        import edge_tts
        import edge_tts.communicate as comm
    except ImportError:
        print("  ! edge-tts not installed; skipping spoken intros")
        return False

    cafile = os.environ.get("SSL_CERT_FILE")
    if cafile and Path(cafile).exists() and hasattr(comm, "_SSL_CTX"):
        comm._SSL_CTX = ssl.create_default_context(cafile=cafile)

    async def run():
        for text, path in jobs:
            await edge_tts.Communicate(text, voice).save(str(path))

    try:
        asyncio.run(run())
        return True
    except Exception as e:
        print(f"  ! spoken intros failed ({e}); continuing without them")
        return False


def intro_lines(segments: list[dict], now_local: datetime) -> list[str]:
    day = f"{now_local:%A}, {now_local:%B} {now_local.day}"
    lines = []
    for i, s in enumerate(segments):
        name = s["source"]
        if i == 0:
            lines.append(f"Here's your news briefing for {day}. First, from {name}.")
        elif i == len(segments) - 1 and len(segments) > 1:
            lines.append(f"And finally, from {name}.")
        else:
            lines.append(f"Next, from {name}.")
    return lines


# --------------------------------------------------------------------------- merge (optional)

def ffmpeg_norm(src: Path, dst: Path):
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "44100",
         "-b:a", "64k", str(dst)],
        check=True,
    )


def merge_audio(segments: list[dict], site: Path, outro: Path | None) -> float:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for i, s in enumerate(segments):
            if s.get("intro"):
                n = tmp / f"{i:02d}a.mp3"
                ffmpeg_norm(site / s["intro"], n)
                parts.append(n)
            raw = tmp / f"{i:02d}raw"
            print(f"  downloading {s['source']}…")
            raw.write_bytes(fetch(s["audio"], timeout=120))
            n = tmp / f"{i:02d}b.mp3"
            ffmpeg_norm(raw, n)
            parts.append(n)
        if outro:
            n = tmp / "zz.mp3"
            ffmpeg_norm(outro, n)
            parts.append(n)
        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts))
        out = site / "briefing.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(out)], check=True)
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout
    return float(dur or 0)


def write_feed(site: Path, base_url: str, title: str, segments: list[dict], duration: float,
               now: datetime):
    base = base_url.rstrip("/") + "/"
    size = (site / "briefing.mp3").stat().st_size
    guid = now.strftime("briefing-%Y%m%d%H%M")
    desc = "\n".join(f"{s['source']}: {s['title']}" for s in segments)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>{escape(title)}</title>
  <link>{escape(base)}</link>
  <description>Your personal news briefing.</description>
  <language>en-us</language>
  <itunes:image href="{escape(base)}podcast-art.png"/>
  <item>
    <title>{escape(title)} – {now:%a %b} {now.day}, {now.strftime('%I:%M %p').lstrip('0')}</title>
    <guid isPermaLink="false">{guid}</guid>
    <pubDate>{format_datetime(now)}</pubDate>
    <description>{escape(desc)}</description>
    <enclosure url="{escape(base)}briefing.mp3?v={guid}" length="{size}" type="audio/mpeg"/>
    <itunes:duration>{int(duration)}</itunes:duration>
  </item>
</channel>
</rss>
"""
    (site / "feed.xml").write_text(xml, encoding="utf-8")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "sources.toml"))
    ap.add_argument("--out", default=str(ROOT / "site"))
    ap.add_argument("--merge", action="store_true", help="also build one combined briefing.mp3")
    ap.add_argument("--base-url", help="public URL of the output folder (needed for feed.xml)")
    args = ap.parse_args()

    cfg = tomllib.loads(Path(args.config).read_text(encoding="utf-8"))
    settings = cfg.get("settings", {})
    tz = ZoneInfo(settings.get("timezone", "America/New_York"))
    now = datetime.now(tz)

    site = Path(args.out)
    site.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "web").iterdir():
        if f.is_file():
            shutil.copy2(f, site / f.name)

    print("Fetching sources:")
    segments = []
    for src in cfg.get("source", []):
        if not src.get("enabled", True):
            continue
        ep = latest_episode(src)
        if ep:
            print(f"  + {ep['source']}: {ep['title']}")
            segments.append(ep)

    if not segments:
        print("No sources had a fresh episode; leaving previous briefing in place.")
        sys.exit(1)

    # Spoken intros
    intros_dir = site / "intros"
    shutil.rmtree(intros_dir, ignore_errors=True)
    outro_path = None
    if settings.get("intros", True):
        intros_dir.mkdir()
        jobs = []
        for s, line in zip(segments, intro_lines(segments, now)):
            name = hashlib.sha1(line.encode()).hexdigest()[:10] + ".mp3"
            s["intro"] = f"intros/{name}"
            s["intro_text"] = line
            jobs.append((line, intros_dir / name))
        outro_path = intros_dir / "outro.mp3"
        jobs.append(("That's your briefing. Have a good one.", outro_path))
        if not make_tts(jobs, settings.get("voice", "en-US-AriaNeural")):
            for s in segments:
                s.pop("intro", None)
            outro_path = None

    data = {
        "title": settings.get("title", "Your News Briefing"),
        "generated": now.isoformat(),
        "outro": "intros/outro.mp3" if outro_path else None,
        "segments": segments,
    }
    (site / "briefing.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    total = sum(s["duration"] or 0 for s in segments)
    print(f"Wrote {site / 'briefing.json'}: {len(segments)} segments, ~{round(total / 60)} min")

    if args.merge:
        print("Merging audio:")
        dur = merge_audio(segments, site, outro_path)
        print(f"Wrote {site / 'briefing.mp3'} ({dur / 60:.1f} min)")
        if args.base_url:
            write_feed(site, args.base_url, data["title"], segments, dur, now)
            print(f"Wrote {site / 'feed.xml'}")


if __name__ == "__main__":
    main()
