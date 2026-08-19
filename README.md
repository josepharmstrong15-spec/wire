# Wire

A personal headline reader for iPhone. Free sources only, no paywalled feeds.

**Sections:** Top (US & World) · Thunder (with schedule/scores) · Faith · Money & Tech

## How it stays current

1. `.github/workflows/refresh.yml` runs `build_news.py` every 30 minutes on GitHub's
   servers and commits `news.json`. Nothing needs to be running on your Mac.
2. `index.html` loads `news.json` same-origin, so it paints instantly with no CORS proxy.
3. It then refreshes live in the browser through a rotating pool of CORS proxies to pick
   up anything published since the last build.

If every proxy is down, the app still works from the snapshot. If Actions is down, the
live fetch still works. If both fail, it shows the last cached copy from your phone.

## Install on iPhone

Open the Pages URL in **Safari** → Share → **Add to Home Screen**.

## Changing sources

Edit the `FEEDS` list in **both** `index.html` and `build_news.py` — they mirror each
other on purpose. Categories are `top`, `thunder`, `faith`, `tech`. Set `okc:true`
(JS) / `True` (Python) on a general sports feed to keep only Thunder stories.
