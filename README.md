# 📰 news-spotter

Automatically collects stock-market-moving news from **CNBC** and other outlets (MarketWatch, WSJ, Yahoo Finance, NYT, BBC, the Federal Reserve, Google News) and files every story by theme:

| | Category | What lands here |
|---|---|---|
| 🤖 | **AI & Tech** | AI, chips, Nvidia, big tech, data centers, cloud |
| 📊 | **Economy & Fed** | rates, inflation, jobs, GDP, Treasury yields |
| 🏛️ | **Politics & Policy** | tariffs, taxes, regulation, elections, Congress |
| 🌍 | **Geopolitics** | China/Taiwan, Russia/Ukraine, Middle East, oil, sanctions |
| 👥 | **Social & Consumer** | consumer sentiment, labor, strikes, layoffs, cost of living |
| 📈 | **Markets & Earnings** | S&P 500, Nasdaq, earnings, IPOs, M&A, crypto, gold |
| ⭐ | **Good news & talking points** | positive headlines (record highs, beats, breakthroughs, deals) worth bringing up in conversations and interviews |

A GitHub Action runs **4× every weekday and 1× on weekends**, saves new stories to `data/`, and writes a readable daily digest to `digests/`. Over time this becomes a searchable archive of what moved markets and why.

## 📅 Daily digests

<!-- DIGESTS:START -->
**Latest:** [2026-11-09](digests/2026-11-09.md)

- [2026-11-09](digests/2026-11-09.md) — 180 stories
- [2026-11-08](digests/2026-11-08.md) — 52 stories
- [2026-11-07](digests/2026-11-07.md) — 25 stories
- [2026-11-06](digests/2026-11-06.md) — 44 stories
<!-- DIGESTS:END -->

## 🔎 Searching the archive (great for interview prep)

```bash
git clone https://github.com/cthrn012/news-spotter && cd news-spotter
git pull                                              # get the latest news

python3 news_spotter.py search nvidia
python3 news_spotter.py search "rate cut" --category "Economy & Fed" --days 30
python3 news_spotter.py search tariffs --category Geopolitics
python3 news_spotter.py search AI --good --days 7     # recent positive AI stories
```

You can also just use GitHub's search box on the repo, or open any file in `digests/`.

## ▶️ Running it yourself

No dependencies — just Python 3.9+.

```bash
python3 news_spotter.py collect
```

To trigger the GitHub Action manually: **Actions → Collect news → Run workflow**.

## ⚙️ Customizing

- **Add or remove news sources:** edit [`config/feeds.json`](config/feeds.json) (any RSS/Atom feed works — e.g. a Google News search RSS for a topic you care about).
- **Change what counts as AI / economic / political / etc.:** edit the keyword lists in [`config/categories.json`](config/categories.json). You can add whole new categories too.
- **Tune "good news":** edit `good_news.keywords` / `negative_keywords` in the same file.

## 📁 Layout

```
config/feeds.json        news sources
config/categories.json   category + good-news keyword rules
news_spotter.py          collector, digest writer, search CLI
data/YYYY/YYYY-MM-DD.json   every story (title, link, source, time, summary, categories)
digests/YYYY-MM-DD.md       human-readable daily digest
.github/workflows/collect.yml   the schedule
```

## Notes

Only headlines, short feed summaries and links are stored — click through to read the full article on the publisher's site. Keyword tagging is simple and occasionally mis-files a story; tweak the keyword lists if you see noise.
