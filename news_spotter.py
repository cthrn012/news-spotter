#!/usr/bin/env python3
"""news-spotter: collect market-moving news from RSS feeds and file it by theme.

Standard library only, so it runs anywhere Python 3.9+ is installed.

Usage:
    python news_spotter.py collect            # fetch feeds, update data/ and digests/
    python news_spotter.py search nvidia      # search everything collected so far
    python news_spotter.py search "rate cut" --category "Economy & Fed" --days 30
    python news_spotter.py search tariffs --good   # only "good news" items
"""

import argparse
import email.utils
import hashlib
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config"
DATA = ROOT / "data"
DIGESTS = ROOT / "digests"
README = ROOT / "README.md"

USER_AGENT = "Mozilla/5.0 (compatible; news-spotter/1.0; personal RSS reader)"
MAX_AGE_DAYS = 3          # ignore feed items older than this
SUMMARY_CHARS = 280
README_DIGEST_COUNT = 14  # how many recent digests to link from the README

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA_NS = "{http://search.yahoo.com/mrss/}"


# --------------------------------------------------------------------------- #
# Keyword matching
# --------------------------------------------------------------------------- #
def compile_keywords(words):
    """Build one regex per keyword list.

    Short ALL-CAPS acronyms (AI, Fed, CPI, SEC, EU...) are matched case-sensitively
    so "AI" doesn't match "said" fragments and "SEC" doesn't match "sec".
    Everything else is case-insensitive and whole-word.
    """
    sensitive, insensitive = [], []
    for w in words:
        is_acronym = len(w) <= 4 and w[:1].isupper() and w.replace("&", "").isalpha() and w.upper() == w
        (sensitive if is_acronym or w in ("Fed", "Xi", "Dow") else insensitive).append(re.escape(w))
    parts = []
    if sensitive:
        parts.append(re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % "|".join(sensitive)))
    if insensitive:
        parts.append(re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % "|".join(insensitive), re.IGNORECASE))
    return parts


def matches(patterns, text):
    hits = set()
    for p in patterns:
        hits.update(m.group(0) for m in p.finditer(text))
    return hits


def load_rules():
    cfg = json.loads((CONFIG / "categories.json").read_text())
    cats = {
        name: {"emoji": c.get("emoji", ""), "patterns": compile_keywords(c["keywords"])}
        for name, c in cfg["categories"].items()
    }
    good = compile_keywords(cfg["good_news"]["keywords"])
    bad = compile_keywords(cfg["good_news"].get("negative_keywords", []))
    blocked = {n.lower() for n in cfg.get("blocked_sources", {}).get("names", [])}
    return cats, good, bad, blocked


# --------------------------------------------------------------------------- #
# Fetching & parsing
# --------------------------------------------------------------------------- #
def clean_text(raw):
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_url(url):
    """Strip tracking params so the same story isn't saved twice."""
    try:
        p = urlparse(url.strip())
        q = {k: v for k, v in parse_qs(p.query).items()
             if not k.lower().startswith(("utm_", "mod", "cmpid", "__source", "ncid", "taid"))}
        return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/"), "", urlencode(q, doseq=True), ""))
    except Exception:
        return url.strip()


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fetch(feed):
    req = urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()
    return parse_feed(body, feed)


def parse_feed(body, feed):
    root = ET.fromstring(body)
    items = []
    if root.tag == ATOM + "feed":
        for e in root.findall(ATOM + "entry"):
            link = e.find(ATOM + "link")
            items.append({
                "title": e.findtext(ATOM + "title"),
                "link": link.get("href") if link is not None else None,
                "summary": e.findtext(ATOM + "summary") or e.findtext(ATOM + "content"),
                "date": e.findtext(ATOM + "published") or e.findtext(ATOM + "updated"),
                "publisher": None,
            })
    else:
        for e in root.iter("item"):
            items.append({
                "title": e.findtext("title"),
                "link": e.findtext("link") or e.findtext("guid"),
                "summary": e.findtext("description") or e.findtext(MEDIA_NS + "description"),
                "date": e.findtext("pubDate") or e.findtext("{http://purl.org/dc/elements/1.1/}date"),
                "publisher": e.findtext("source"),  # Google News puts the real outlet here
            })

    out = []
    for it in items:
        title = clean_text(it["title"])
        link = (it["link"] or "").strip()
        if not title or not link:
            continue
        publisher = clean_text(it["publisher"]) or None
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        summary = clean_text(it["summary"])
        if summary.startswith(title):  # Google News repeats the headline as the summary
            summary = ""
        if len(summary) > SUMMARY_CHARS:
            summary = summary[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
        out.append({
            "title": title,
            "url": link,
            "summary": summary,
            "published": parse_date(it["date"]),
            "source": publisher or feed["source"],
            "feed": f'{feed["source"]} · {feed["name"]}',
        })
    return out[: feed.get("max_items", len(out))]


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def day_file(day):
    return DATA / day[:4] / f"{day}.json"


def load_day(day):
    f = day_file(day)
    if f.exists():
        return {a["id"]: a for a in json.loads(f.read_text())}
    return {}


def save_day(day, articles):
    f = day_file(day)
    f.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(articles.values(), key=lambda a: a["published"], reverse=True)
    f.write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")


def all_days():
    return sorted((p.stem for p in DATA.glob("*/*.json")), reverse=True)


# --------------------------------------------------------------------------- #
# Digest rendering
# --------------------------------------------------------------------------- #
def md_escape(s):
    return s.replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def render_digest(day, articles, cats):
    rows = sorted(articles.values(), key=lambda a: a["published"], reverse=True)
    lines = [
        f"# 📰 Market News Digest — {day}",
        "",
        f"_{len(rows)} market-relevant stories collected · times in UTC · "
        f"generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC_",
        "",
    ]

    counts = {name: sum(1 for a in rows if name in a["categories"]) for name in cats}
    lines.append(" · ".join(f'{cats[n]["emoji"]} [{n}](#{anchor(n)}) ({c})' for n, c in counts.items() if c))
    lines.append("")

    # Stories from the outlets we subscribe to directly (CNBC, WSJ, ...) go first.
    good = sorted((a for a in rows if a["good_news"]), key=lambda a: a["feed"].startswith("Google News"))
    if good:
        lines += ["## ⭐ Good news & talking points", "",
                  "_Positive headlines worth bringing up in conversations or interviews._", ""]
        for a in good[:25]:
            lines.append(bullet(a, cats, show_cats=True))
        lines.append("")

    for name, c in cats.items():
        group = [a for a in rows if name in a["categories"]]
        if not group:
            continue
        lines += [f'## {c["emoji"]} {name}', ""]
        for a in group:
            lines.append(bullet(a, cats))
        lines.append("")

    return "\n".join(lines)


def anchor(name):
    return re.sub(r"[^\w\- ]", "", name.lower()).strip().replace(" ", "-")


def bullet(a, cats, show_cats=False):
    t = a["published"][11:16]
    tag = ""
    if show_cats:
        tag = " " + "".join(cats[c]["emoji"] for c in a["categories"] if c in cats)
    line = f'- **[{md_escape(a["title"])}]({a["url"]})** — {a["source"]}, {t}{tag}'
    if a.get("summary"):
        line += f'  \n  {md_escape(a["summary"])}'
    return line


def update_readme():
    days = all_days()[:README_DIGEST_COUNT]
    block = ["<!-- DIGESTS:START -->"]
    if days:
        block.append(f"**Latest:** [{days[0]}](digests/{days[0]}.md)")
        block.append("")
        for d in days:
            n = len(json.loads(day_file(d).read_text()))
            block.append(f"- [{d}](digests/{d}.md) — {n} stories")
    else:
        block.append("_No digests yet — the first scheduled run will add them._")
    block.append("<!-- DIGESTS:END -->")
    text = README.read_text()
    text = re.sub(r"<!-- DIGESTS:START -->.*?<!-- DIGESTS:END -->", "\n".join(block), text, flags=re.S)
    README.write_text(text)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_collect(args):
    feeds = json.loads((CONFIG / "feeds.json").read_text())
    cats, good_pat, bad_pat, blocked = load_rules()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)

    def safe_fetch(feed):
        try:
            return feed, fetch(feed), None
        except Exception as e:  # one broken feed shouldn't stop the run
            return feed, [], e

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(safe_fetch, feeds))

    fetched = []
    for feed, items, err in results:
        status = f"ERROR {type(err).__name__}: {err}" if err else f"{len(items)} items"
        print(f'  {feed["source"]:<20} {feed["name"]:<22} {status}')
        fetched.extend(items)

    by_day, seen_titles, kept = {}, set(), 0
    for a in fetched:
        if a["source"].lower() in blocked:
            continue
        pub = a["published"] or now
        if pub < cutoff or pub > now + timedelta(hours=6):
            continue
        text = f'{a["title"]} {a["summary"]}'
        a_cats = [name for name, c in cats.items() if matches(c["patterns"], text)]
        if not a_cats:
            continue  # not market-relevant by our keyword rules
        title_key = re.sub(r"\W+", " ", a["title"].lower()).strip()
        if title_key in seen_titles:
            continue  # same headline from two feeds
        seen_titles.add(title_key)

        url = canonical_url(a["url"])
        day = pub.strftime("%Y-%m-%d")
        if day not in by_day:
            by_day[day] = load_day(day)
        aid = hashlib.sha1(url.encode()).hexdigest()[:12]
        store = by_day[day]
        if aid in store or any(re.sub(r"\W+", " ", x["title"].lower()).strip() == title_key for x in store.values()):
            continue
        is_good = bool(matches(good_pat, a["title"])) and not matches(bad_pat, a["title"])
        store[aid] = {
            "id": aid,
            "title": a["title"],
            "url": a["url"],
            "source": a["source"],
            "feed": a["feed"],
            "published": pub.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": a["summary"],
            "categories": a_cats,
            "good_news": is_good,
        }
        kept += 1

    DIGESTS.mkdir(exist_ok=True)
    for day, store in by_day.items():
        save_day(day, store)
        (DIGESTS / f"{day}.md").write_text(render_digest(day, store, cats) + "\n")
    update_readme()
    print(f"\nAdded {kept} new stories across {len(by_day)} day(s).")


def cmd_search(args):
    query = re.compile(re.escape(args.query), re.IGNORECASE)
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d") if args.days else ""
    hits = []
    for day in all_days():
        if day < since:
            break
        for a in json.loads(day_file(day).read_text()):
            if args.category and args.category not in a["categories"]:
                continue
            if args.good and not a["good_news"]:
                continue
            if query.search(a["title"]) or query.search(a.get("summary", "")):
                hits.append(a)
    for a in hits[: args.limit]:
        star = "⭐ " if a["good_news"] else ""
        print(f'{a["published"][:10]}  {star}{a["title"]}\n            {a["source"]} · {", ".join(a["categories"])}\n            {a["url"]}\n')
    print(f"{len(hits)} match(es)" + (f", showing {args.limit}" if len(hits) > args.limit else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect", help="fetch feeds and update data/ + digests/")
    s = sub.add_parser("search", help="search collected stories")
    s.add_argument("query")
    s.add_argument("--category", help='e.g. "AI & Tech", "Geopolitics"')
    s.add_argument("--days", type=int, help="only look back this many days")
    s.add_argument("--good", action="store_true", help="only good-news / talking-point stories")
    s.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    {"collect": cmd_collect, "search": cmd_search}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
