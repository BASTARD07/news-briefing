# News Briefing

A replacement for Google Assistant's old news briefing. It grabs the newest short
newscast from several outlets (NPR, Fox News, ABC, AP, WSJ, Marketplace by default),
adds short spoken intros ("Next, from ABC News Update"), and plays them back to back
in one player you can add to your phone's home screen.

- Refreshes every hour, so you always get the latest updates
- Lock-screen and headphone controls (skip to the next or previous source)
- Playback speed control, a switch per source to skip it on that device, and seeking by tapping the bar
- Audio streams straight from each publisher, so nothing is re-uploaded

## Option A: free hosting on GitHub (recommended, no PC needed)

1. Create a **public** GitHub repo (for example `news-briefing`) and upload everything in this folder,
   including the hidden `.github` folder.
2. In the repo, open **Settings → Pages** and set **Source** to **GitHub Actions**.
3. Open the **Actions** tab, choose **Build briefing**, and click **Run workflow**. The first build takes about a minute.
4. Open `https://<your-username>.github.io/news-briefing/` on your phone.
   - **Android/Chrome:** ⋮ menu → *Add to Home screen*
   - **iPhone/Safari:** Share → *Add to Home Screen*

It rebuilds every hour on its own. To change the lineup, edit `sources.toml` on GitHub; the commit triggers a rebuild.

## Option B: run it on your own PC

Requires Python 3.11 or newer, plus ffmpeg if you want the merged MP3.

```
pip install -r requirements.txt
python build.py --out D:\briefing
```

Serve the output folder with any web server, for example `python -m http.server 8080 -d D:\briefing`.
Run it hourly with Task Scheduler.

**Single-file and podcast mode (PC only):**

```
python build.py --out D:\briefing --merge --base-url http://<your-pc-address>:8080/
```

This also writes `briefing.mp3`, one loudness-matched file with everything stitched together, and
`feed.xml`, a one-episode podcast feed. You can subscribe to that feed in any podcast app
(Pocket Casts, AntennaPod, Apple Podcasts → *Follow a Show by URL*) to get the briefing auto-downloaded.
Keep this mode on your own machine or behind a password, not on a public site, because it
re-hosts the publishers' audio.

## Customizing

Everything is in `sources.toml`:

- reorder the `[[source]]` blocks to change the play order
- `enabled = false` turns a source off; CNN 5 Things, Reuters, Bloomberg and BBC are included but off by default
- `max_age_hours` skips a source when its latest episode is stale
- `[settings]` holds the intro voice (any Edge neural voice, such as `en-US-GuyNeural` or `en-GB-SoniaNeural`) and `intros = false` to remove intros

To add a source, you need its podcast RSS feed. Searching `podnews.net` for the show name lists it.
