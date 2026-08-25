#!/usr/bin/env python3
"""
Builds news.json — the snapshot Wire loads instantly on open.
Runs server-side (GitHub Actions), so there is no CORS proxy in the path.
"""
import json, re, time, html, sys, math
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"

FEEDS = [
    # Every source here was verified to serve full articles with no subscription wall.
    # WSJ (HTTP 401 on all four feeds) and Washington Post (metered) were dropped for that
    # reason; The Verge was dropped after 2 of 3 sampled articles came back schema-gated.
    ("top", "BBC",          "https://feeds.bbci.co.uk/news/world/rss.xml", 10, False),
    ("top", "BBC US",       "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml", 8, False),
    ("top", "PBS",          "https://www.pbs.org/newshour/feeds/rss/headlines", 10, False),
    ("top", "NPR",          "https://feeds.npr.org/1001/rss.xml", 10, False),
    ("top", "NPR National", "https://feeds.npr.org/1003/rss.xml", 8, False),
    ("top", "ABC News",     "https://abcnews.go.com/abcnews/topstories", 10, False),
    ("top", "CBS News",     "https://www.cbsnews.com/latest/rss/main", 10, False),

    ("thunder", "Daily Thunder",   "https://dailythunder.com/feed/", 8, False),
    ("thunder", "ESPN NBA",        "https://www.espn.com/espn/rss/nba/news", 8, True),
    ("thunder", "Thunderous Int.", "https://thunderousintentions.com/feed/", 4, False),
    ("thunder", "CBS NBA",         "https://www.cbssports.com/rss/headlines/nba/", 8, True),
    ("thunder", "Yahoo NBA",       "https://sports.yahoo.com/nba/rss.xml", 8, True),

    ("faith", "RNS",                "https://religionnews.com/feed/", 8, False),
    ("faith", "Christianity Today", "https://www.christianitytoday.com/rss", 7, False),
    ("faith", "Gospel Coalition",   "https://www.thegospelcoalition.org/feed/", 6, False),
    ("faith", "Relevant",           "https://relevantmagazine.com/feed/", 5, False),

    ("tech", "CNBC",         "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114", 8, False),
    ("tech", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml", 7, False),
    ("tech", "NPR Business", "https://feeds.npr.org/1006/rss.xml", 6, False),
    ("tech", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index", 6, False),
    ("tech", "TechCrunch",   "https://techcrunch.com/feed/", 6, False),
    ("tech", "Engadget",     "https://www.engadget.com/rss.xml", 5, False),
]

# Which outlet wins when several cover the same story. Lower number = preferred.
# Ordered toward straight-reporting outlets that publish without a subscription wall.
SOURCE_RANK = {
    "BBC": 1, "BBC US": 1, "PBS": 2, "NPR": 3, "NPR National": 3,
    "ABC News": 4, "CBS News": 5,
    "ESPN": 1, "Daily Thunder": 2, "CBS NBA": 3, "Yahoo NBA": 3, "Thunderous Int.": 4,
    "RNS": 1, "Christianity Today": 2, "Gospel Coalition": 3, "Relevant": 4,
    "CNBC": 1, "BBC Business": 2, "NPR Business": 3,
    "Ars Technica": 4, "TechCrunch": 5, "Engadget": 6,
}

# Never link to a domain that demands a subscription, even if one is syndicated in.
PAYWALL_DOMAINS = (
    "wsj.com", "washingtonpost.com", "nytimes.com", "ft.com", "economist.com",
    "bloomberg.com", "theathletic.com", "newyorker.com", "barrons.com",
    "theinformation.com", "seekingalpha.com", "telegraph.co.uk", "thetimes.co.uk",
)

STOPWORDS = set("""a an the and or but of in on at to for with from by as is are was were be been
being that this these those it its his her their they them he she you your we our us i not no new
say says said after before over under about into out up down off then than who what when where why
how which will would can could may might must should has have had do does did more most some any
all one two amid watch live update updates video photos analysis opinion report reports""".split())

SIM_THRESHOLD = 0.45      # tuned on live data: real dupes scored >=0.51, nothing false below
CLUSTER_WINDOW_MS = 48 * 3600 * 1000

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
    last = None
    # One retry: under concurrency some hosts (ESPN especially) return a truncated
    # or throttled body that fails XML parsing, then serve fine a moment later.
    for attempt in range(2):
        try:
            items = parse_feed(fetch(url), cat, src)
            if okc:
                items = [i for i in items if OKC_RE.search(i["t"])]
            return items[:cap], None
        except Exception as e:
            last = e
            if attempt == 0:
                time.sleep(1.5)
    return [], "%s: %s" % (src, type(last).__name__)


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


def tokens(title):
    """Content words only, lightly stemmed, for comparing two headlines."""
    out = set()
    for w in re.sub(r"[^a-z0-9 ]", " ", title.lower()).split():
        if len(w) < 3 or w in STOPWORDS:
            continue
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def build_idf(items):
    n = len(items) or 1
    df = {}
    for it in items:
        for w in it["_k"]:
            df[w] = df.get(w, 0) + 1
    return {w: math.log(n / (1.0 + c)) + 0.2 for w, c in df.items()}


def similarity(a, b, idf):
    """Overlap coefficient weighted by inverse document frequency.

    Rare words (proper nouns like 'Lockerbie') dominate; common ones barely count.
    Denominator is the smaller headline so a short headline still matches a long one.
    """
    shared = a["_k"] & b["_k"]
    if len(shared) < 2:
        return 0.0
    num = sum(idf.get(w, 0.2) for w in shared)
    wa = sum(idf.get(w, 0.2) for w in a["_k"])
    wb = sum(idf.get(w, 0.2) for w in b["_k"])
    d = min(wa, wb)
    return (num / d) if d > 0 else 0.0


def cluster_stories(items):
    """Group headlines that describe the same story. Returns clusters, newest first.

    Clustering runs per category, so a tech story and a top story never merge.
    """
    for it in items:
        it["_k"] = tokens(it["t"])
    idf = build_idf(items)

    clusters = []
    for cat in ("top", "thunder", "faith", "tech"):
        pool = [i for i in items if i["c"] == cat]
        pool.sort(key=lambda x: x["ts"], reverse=True)
        cat_clusters = []
        for it in pool:
            best, best_sim = None, 0.0
            for c in cat_clusters:
                if abs(it["ts"] - c["ts"]) > CLUSTER_WINDOW_MS:
                    continue
                # single-link: match against every member, not just the representative.
                # Coverage of one story often links transitively (A~C, B~C, but A!~B).
                sm = max(similarity(it, mem, idf) for mem in c["members"])
                if sm > best_sim:
                    best, best_sim = c, sm
            if best is not None and best_sim >= SIM_THRESHOLD:
                best["members"].append(it)
            else:
                cat_clusters.append({"rep": it, "ts": it["ts"], "members": [it]})
        clusters.extend(cat_clusters)

    clusters.sort(key=lambda c: c["ts"], reverse=True)
    return clusters


def is_paywalled(url):
    """True only on positive evidence. Network errors mean 'unknown', never 'drop'."""
    low = url.lower()
    if any(d in low for d in PAYWALL_DOMAINS):
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=15) as r:
            head = r.read(220000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code in (401, 402)      # explicit "payment/auth required"
    except Exception:
        return False                      # bot-block, timeout, TLS -- give it the benefit
    if re.search(r'"isAccessibleForFree"\s*:\s*(false|"false")', head, re.I):
        return True
    if re.search(r"subscribe to (?:read|continue)|this (?:article|content) is for subscribers"
                 r"|you have reached the end of your free", head, re.I):
        return True
    return False


def pick_open_representative(cluster):
    """Best-ranked source in the cluster that isn't paywalled.

    Checks at most 3 candidates so a big cluster can't blow up the build.
    """
    members = sorted(cluster["members"],
                     key=lambda m: (SOURCE_RANK.get(m["s"], 50), -m["ts"]))
    checked = 0
    for m in members:
        if checked >= 3:
            break
        checked += 1
        if not is_paywalled(m["l"]):
            return m, False
    return members[0], True


def main():
    items, errs = [], []
    with ThreadPoolExecutor(max_workers=10) as ex:
        for got, err in ex.map(fetch_one, FEEDS):
            items.extend(got)
            if err:
                errs.append(err)
    items.extend(espn_news())
    games = espn_schedule()
    raw_count = len(items)

    # drop obviously paywalled domains before doing any work on them
    items = [i for i in items if not any(d in i["l"].lower() for d in PAYWALL_DOMAINS)]

    # exact-duplicate pass (same headline syndicated twice)
    items.sort(key=lambda x: x["ts"], reverse=True)
    seen, deduped = set(), []
    for it in items:
        k = norm_key(it["t"])
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(it)

    # group the same story told by different outlets
    clusters = cluster_stories(deduped)
    merged = sum(1 for c in clusters if len(c["members"]) > 1)

    # one article per story: best-ranked source that isn't behind a wall
    final, gated_dropped = [], 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        picks = list(ex.map(pick_open_representative, clusters))
    for cluster, (rep, all_gated) in zip(clusters, picks):
        if all_gated:
            gated_dropped += 1
            continue
        out = {k: v for k, v in rep.items() if not k.startswith("_")}
        also = sorted({m["s"] for m in cluster["members"] if m["s"] != rep["s"]})
        if also:
            out["n"] = len(also)       # number of OTHER outlets carrying this story
            out["also"] = also[:4]
            # Headline keys this cluster absorbed. The client re-fetches those raw
            # headlines live, and uses these to reattach them instead of forming a
            # rival cluster for the same story.
            out["mk"] = [norm_key(m["t"]) for m in cluster["members"] if m is not rep][:12]
        final.append(out)

    final.sort(key=lambda x: x["ts"], reverse=True)

    payload = {"at": int(time.time() * 1000), "items": final[:400], "games": games}
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    by_cat = {}
    for i in final:
        by_cat[i["c"]] = by_cat.get(i["c"], 0) + 1
    print("raw %d -> exact-dedup %d -> clustered %d (%d merged) -> published %d  %s"
          % (raw_count, len(deduped), len(clusters), merged, len(final), by_cat))
    if gated_dropped:
        print("dropped %d story(ies) with no non-paywalled source" % gated_dropped)
    if errs:
        print("feed errors: " + "; ".join(errs))
    if len(final) < 20:
        print("ERROR: too few stories, refusing to publish", file=sys.stderr)
        return 1
    return 0
if __name__ == "__main__":
    sys.exit(main())
