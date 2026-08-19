#!/usr/bin/env python3
"""
Builds news.json — the snapshot Wire loads instantly on open.
Runs server-side (GitHub Actions), so there is no CORS proxy in the path.
"""
import json, re, time, html, sys
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"

FEEDS = [
    ("top", "BBC",             "https://feeds.bbci.co.uk/news/world/rss.xml", 7, False),
    ("top", "BBC US",          "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", 5, False),
    ("top", "NPR",             "https://feeds.npr.org/1001/rss.xml", 7, False),
    ("top", "WaPo",            "https://feeds.washingtonpost.com/rss/national", 6, False),
    ("top", "WaPo World",      "https://feeds.washingtonpost.com/rss/world", 5, False),
    ("top", "WSJ",             "https://feeds.content.dowjones.io/public/rss/RSSWorldNews", 7, False),
    ("top", "PBS",             "https://www.pbs.org/newshour/feeds/rss/headlines", 5, False),

    ("thunder", "Daily Thunder",   "https://dailythunder.com/feed/", 8, False),
    ("thunder", "ESPN NBA",        "https://www.espn.com/espn/rss/nba/news", 8, True),
    ("thunder", "Thunderous Int.", "https://thunderousintentions.com/feed/", 4, False),
    ("thunder", "CBS NBA",         "https://www.cbssports.com/rss/headlines/nba/", 8, True),
    ("thunder", "Yahoo NBA",       "https://sports.yahoo.com/nba/rss.xml", 8, True),

    ("faith", "Christianity Today", "https://www.christianitytoday.com/rss", 7, False),
    ("faith", "RNS",                "https://religionnews.com/feed/", 7, False),
    ("faith", "Gospel Coalition",   "https://www.thegospelcoalition.org/feed/", 6, False),
    ("faith", "Relevant",           "https://relevantmagazine.com/feed/", 5, False),

    ("tech", "WSJ Markets", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", 6, False),
    ("tech", "WSJ Biz",     "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness", 6, False),
    ("tech", "WSJ Tech",    "https://feeds.content.dowjones.io/public/rss/RSSWSJD", 6, False),
    ("tech", "CNBC",        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", 6, False),
    ("tech", "WaPo Biz",    "https://feeds.washingtonpost.com/rss/business", 5, False),
    ("tech", "Ars Technica","https://feeds.arstechnica.com/arstechnica/index", 5, False),
    ("tech", "The Verge",   "https://www.theverge.com/rss/index.xml", 5, False),
]

OKC_RE = re.compile(
    r"(\bthunder\b|\bokc\b|oklahoma city|gilgeous|\bshai\b|holmgren|jalen williams|"
    r"lu dort|caruso|daigneault|hartenstein|\bpresti\b|aaron wiggins|nikola topi|"
    r"isaiah joe|cason wallace|ajay mitchell|kenrich williams)", re.I)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, application/json, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_espn(url, timeout=20):
    """ESPN's API 403s on a spoofed browser UA but serves urllib's default fine."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s)
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return WS_RE.sub(" ", s).strip()


def to_ms(s):
    """Feeds are inconsistent: RFC-822, full ISO-8601, and ISO without seconds all appear."""
    if not s:
        return 0
    s = s.strip()
    try:
        return int(parsedate_to_datetime(s).timestamp() * 1000)
    except Exception:
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp() * 1000)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return int(d.timestamp() * 1000)
        except Exception:
            continue
    return 0


def parse_feed(xml_bytes, cat, src):
    import xml.etree.ElementTree as ET
    txt = xml_bytes.decode("utf-8", "replace")
    txt = re.sub(r"^\s*<\?xml[^>]*\?>", "", txt).strip()
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        txt2 = "".join(ch for ch in txt if ch >= " " or ch in "\t\n\r")
        root = ET.fromstring(txt2)

    def local(t):
        return t.split("}", 1)[-1]

    out = []
    nodes = [e for e in root.iter() if local(e.tag) in ("item", "entry")]
    for n in nodes:
        title = link = date = ""
        for c in n:
            lt = local(c.tag)
            if lt == "title" and not title:
                title = clean(c.text or "".join(c.itertext()))
            elif lt == "link" and not link:
                link = (c.get("href") or "").strip() or clean(c.text)
            elif lt == "guid" and not link and (c.text or "").startswith("http"):
                link = clean(c.text)
            elif lt in ("pubDate", "published", "updated", "date") and not date:
                date = (c.text or "").strip()
        if title and link:
            out.append({"t": title, "l": link, "ts": to_ms(date), "s": src, "c": cat})
    return out


def fetch_one(spec):
    cat, src, url, cap, okc = spec
    fetch = get_espn if "espn.com" in url else get   # ESPN rejects spoofed browser UAs
    try:
        items = parse_feed(fetch(url), cat, src)
        if okc:
            items = [i for i in items if OKC_RE.search(i["t"])]
        return items[:cap], None
    except Exception as e:
        return [], "%s: %s" % (src, type(e).__name__)


def espn_news():
    try:
        d = json.loads(get_espn("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news?team=okc&limit=30"))
        out = []
        for a in d.get("articles", []):
            links = a.get("links") or {}
            href = (links.get("web") or {}).get("href") or (links.get("mobile") or {}).get("href") or ""
            t = clean(a.get("headline"))
            if t and href:
                out.append({"t": t, "l": href, "ts": to_ms(a.get("published") or a.get("lastModified")),
                            "s": "ESPN", "c": "thunder"})
        return out[:14]
    except Exception:
        return []


def espn_schedule():
    try:
        d = json.loads(get_espn("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/okc/schedule"))
    except Exception:
        return None
    evs = []
    for e in d.get("events", []):
        comps = e.get("competitions") or [{}]
        comp = comps[0]
        st = (comp.get("status") or {}).get("type") or {}
        us = them = None
        for c in comp.get("competitors", []):
            sc = c.get("score")
            if isinstance(sc, dict):
                sc = sc.get("displayValue", sc.get("value"))
            try:
                sc = int(sc) if sc not in (None, "") else None
            except (TypeError, ValueError):
                sc = None
            team = c.get("team") or {}
            o = {"name": team.get("shortDisplayName") or team.get("displayName") or "",
                 "abbr": team.get("abbreviation") or "", "score": sc,
                 "home": c.get("homeAway") == "home", "winner": bool(c.get("winner"))}
            if o["abbr"] == "OKC":
                us = o
            else:
                them = o
        if us and them:
            evs.append({"date": to_ms(e.get("date")), "state": st.get("state") or "pre",
                        "done": bool(st.get("completed")),
                        "detail": st.get("shortDetail") or st.get("detail") or "",
                        "seasonType": (e.get("seasonType") or {}).get("abbreviation") or "",
                        "us": us, "them": them})
    now = int(time.time() * 1000)
    live = last = nxt = None
    for e in evs:
        if e["state"] == "in":
            live = e
        elif e["done"]:
            if not last or e["date"] > last["date"]:
                last = e
        elif e["date"] >= now - 3 * 3600 * 1000:
            if not nxt or e["date"] < nxt["date"]:
                nxt = e
    return {"live": live, "last": last, "next": nxt,
            "rec": (d.get("team") or {}).get("recordSummary") or ""}


def norm_key(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower())[:72].strip()


def main():
    items, errs = [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for got, err in ex.map(fetch_one, FEEDS):
            items.extend(got)
            if err:
                errs.append(err)
    items.extend(espn_news())
    games = espn_schedule()

    items.sort(key=lambda x: x["ts"], reverse=True)
    seen, uniq = set(), []
    for it in items:
        k = norm_key(it["t"])
        if not k or k in seen:
            continue
        seen.add(k)
        uniq.append(it)

    payload = {"at": int(time.time() * 1000), "items": uniq[:400], "games": games}
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    by_cat = {}
    for i in uniq:
        by_cat[i["c"]] = by_cat.get(i["c"], 0) + 1
    print("stories: %d  %s" % (len(uniq), by_cat))
    if errs:
        print("feed errors: " + "; ".join(errs))
    if len(uniq) < 20:
        print("ERROR: too few stories, refusing to publish", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
