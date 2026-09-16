# Wire

A personal headline reader for iPhone. Free, non-paywalled sources only.

**Sections:** Top (US & World) · Thunder (with schedule/scores) · Faith · Money & Tech

## One article per story

Several outlets cover the same story with different headlines. Wire groups those and
shows only one — from the most trusted source in the group — with a `+N outlets` badge
so you can see it was deduplicated rather than missing.

Grouping compares headlines by shared content words, weighted by how rare each word is
(inverse document frequency), so distinctive words like "Lockerbie" count far more than
"announces". Two headlines join the same story when they score `SIM_THRESHOLD` (0.45) or
higher, using single-link clustering within a 48-hour window.

That threshold was tuned against live data: real duplicates scored 0.51+, while the band
below it contained a genuine false positive (a `$100 billion` SpaceX story vs a
`$20 billion` Canada tariffs story, which share only "announces" and "billion").
**Lower it and unrelated stories start merging.**

Which outlet wins is set by `SOURCE_RANK` — lower number wins. Reorder it to taste.

## Weather banner

A single tappable banner sits above the search bar showing the current temperature,
conditions, and today's high and low. Tapping it opens the National Weather Service point
forecast, which is free, text based, ad free, and has no video.

Everything comes from `api.weather.gov`: no API key, no CORS proxy, and it is the agency
other forecasts are derived from. Three endpoints are used, each failing independently so
a partial outage still yields a useful banner:

| Data | Endpoint |
|---|---|
| Current conditions | `/stations/KOUN/observations/latest`, falling back to `KOKC` |
| High and low | `/gridpoints/OUN/100,83/forecast` |
| Active alerts | `/alerts/active?point=lat,lon` |

An active alert adds a badge. Severe and Extreme alerts (tornado and severe thunderstorm
warnings) turn the whole banner red. The city label yields to the badge when one is
present, since the alert matters more than the location.

If the weather service cannot be reached the banner hides itself rather than showing a
broken bar.

**Changing the location:** `GET https://api.weather.gov/points/<lat>,<lon>`, then copy
`gridId/gridX/gridY` and the nearest entry from `observationStations` into the `WX` block
in both `index.html` and `build_news.py`. It currently points at Norman, OK.

KOUN (Norman / Max Westheimer) is the nearest station but sometimes reports a blank
condition string or drops out, so `stations` is a list tried in order with KOKC as the
backstop.

## Search

The bar at the top filters every headline currently loaded, across all four sections at
once. Results are newest-first, tagged with the section they came from, with matches
highlighted.

- Multiple words are AND-ed: `iran oil` needs both.
- `"quoted phrases"` match as a unit.
- Matches headline text, source name, and the outlets folded in by clustering.
- Escape, the X, or Clear exits; tapping a section tab also exits.

It searches loaded headlines, not full article text — roughly the last 150 stories held
in memory, which is everything `news.json` carries plus whatever the live refresh added.

## No paywalls

- WSJ was removed: every feed returns **HTTP 401** on the article itself.
- Washington Post was removed: metered paywall, and it blocks automated access.
- The Verge was removed: 2 of 3 sampled articles reported `isAccessibleForFree: false`.

Three defenses remain in place:
1. `PAYWALL_DOMAINS` — never link to a known subscription domain, even if syndicated in.
2. The builder fetches each candidate article and checks for a paywall signal
   (`isAccessibleForFree: false`, HTTP 401/402, "subscribe to continue").
3. If the best-ranked source in a group is gated, it falls through to the next source in
   that group, so you still get the story from someone else.

Detection only ever drops an article on **positive** evidence. A timeout, TLS failure, or
bot-block (Religion News Service returns 403 to scripts) counts as "unknown" and is kept —
otherwise good articles would silently vanish.

## How it stays current

1. `.github/workflows/refresh.yml` runs `build_news.py` every 30 minutes on GitHub's
   servers and commits `news.json`. Nothing needs to run on your Mac.
2. `index.html` loads `news.json` same-origin, so it paints instantly with no CORS proxy.
3. It then refreshes live in the browser through a rotating pool of CORS proxies.

Representatives in `news.json` carry an `mk` list of the headline keys they absorbed. The
client uses it to reattach live copies to the right story instead of forming a rival
cluster for the same event.

## Install on iPhone

Open the Pages URL in **Safari** → Share → **Add to Home Screen**.

## Changing sources

Edit `FEEDS`, `SOURCE_RANK`, and the clustering constants in **both** `index.html` and
`build_news.py` — they mirror each other on purpose.

Categories are `top`, `thunder`, `faith`, `tech`. Set `okc:true` (JS) / `True` (Python) on
a general sports feed to keep only Thunder stories.
