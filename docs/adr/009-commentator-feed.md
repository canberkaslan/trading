# ADR-009: Commentator feed as opinion context

**Status:** Accepted — shipped **off** (`COMMENTATOR_FEED=1` enables it). It stays off until the measurement gate below passes.
**Date:** 2026-09-28
**Related:** ADR-004 (data providers), ADR-008 (US equities only; unchanged by this ADR)

## Context

The operator asked for Bora Özkent's commentary to become an input. He is a Turkish commentator on US equities. His channels:

| Channel | Identity pinned in code | Role |
|---|---|---|
| YouTube | channel id `UCrXj09uA0Nqv65st774NEKw` (@boraozkentlenasdaq) | Primary. About 5 long-form videos and 5–6 Shorts a week, plus live streams around events |
| X | numeric user id of `@BoraOzkentNSDQ`, pinned once by a person | Secondary. The only public channel where he states a view in writing |

Three facts shape the design:

1. **The content is Turkish.** The system reasons in English, the language of its evidence (ADR-008; `llm/translate.py`). Nothing in it reads Turkish input.
2. **The spoken content is out of reach.** A video's view is in what he says. Transcripts are not available through any allowed route: `captions.download` needs the channel owner's authorisation, and transcript scrapers (`youtube-transcript-api`, `yt-dlp`) or audio transcription break the YouTube Terms. What the API does provide is the title, the description and its chapter lines. So most YouTube items carry topics and an `unstated` stance.
3. **Identities can be taken over.** His old X handle `@BoraOzkent` now belongs to someone else. Two of his former YouTube handles return 404, so anyone could register them.

The scope question is smaller than it looked. In a 15-upload sample (09-18 → 09-27), BIST or Turkish-macro content was **0**. US markets and macro: 5. US single names, ETFs and themes: 4. Mindset and career: 6.

## Decision

**Add one labelled opinion block to the sentiment analyst's prompt, and nothing else.**

- **Where it enters.** It is its own `### Commentator feed` section in the sentiment analyst's system message, inserted before `## How to analyze this data` (appended at the end if the vendor moves that heading). The seam is `_build_system_message`, wrapped the way `sentiment_supplement` wraps `fetch_reddit_posts`. No vendor file is edited.
  - It is not in the trader, the portfolio manager or the risk layer, and it has no weight in any formula.
  - It is not in the Reddit block (its header says Reddit) or the StockTwits slot (its guidance is about Bullish/Bearish ratios).
  - It is not a news vendor: the router's first successful vendor replaces the others, and the sentiment analyst also reads `get_news`, so the item would count twice.
- **How the analyst is told to read it.** The analyst's existing rule 4, "distinguish opinion from event", covers it. The block adds four lines:
  - one commentator is not consensus;
  - an `unstated` stance must not move `overall_score`;
  - he runs a paid community, his content carries promotion, and his headline tone skews bullish;
  - report the source on its own `Commentator view:` line, paraphrased and never quoted.
- **Item format.** An item shows only derived fields and an id, like this:
  `- 2026-09-22T09:30Z [YouTube iIVDlDLd9yk] also on: NVDA; topics: Nasdaq rally breadth; stance on META: unstated; paraphrase: …`
  - At most 5 items about the ticker itself.
  - Market-wide items are keyed as SPY, or carry macro topics and no ticker at all. A single-name item with a macro topic (NVDA with "AI capex") is not market-wide. The SPY analyst reads market-wide items as its own. Every other ticker gets at most one market-wide line.
  - With nothing in the window, the block says "No commentary in window" only when a recorded successful read covers it, and names that read (`read from YouTube at …`). A read covers the window when the unbroken run of reads reaches back to its start, it happened no earlier than the cutoff (a live run: at most 6 hours before, the fetch comes first), and nothing it read can have been purged since. Otherwise — no key, a failed or timed-out fetch, a window older than the reads or than retention — the block says the feed is unavailable, not that there was no commentary. An empty table is not an observed absence.
  - Promo-first items and off-topic items (mindset, crypto-only, BIST) are dropped. This is scope option A: ADR-008 stands, and the universe is not widened to his names.
- **Language.** Each item is read once by the same cheap tier `translate.py` uses (Haiku) and reduced to English fields:
  - `tickers[]`
  - `macro_topics[]`
  - `stance` per ticker, one of `bullish` / `bearish` / `neutral` / `unstated`
  - `claim_en`, a paraphrase of at most 200 characters
  - `is_promo`
  - `is_market_content`

  Every field is validated into its closed shape. The source text is treated as untrusted input to the extractor.
- **Fetch once per run, not per ticker.** `scripts/commentator_fetch.py` runs once in `daily_run.sh`, before the ticker loop, and only when `COMMENTATOR_FEED=1`. It fetches, extracts new items, and stores them in `commentator_items`. The sentiment analysts only read that table, so nothing on the decision path touches the network for this feed. An item already extracted is never extracted again.
- **No look-ahead.** The vendor's `in_window` admits the whole trade date. On top of it:
  - **Live:** an item must be published no later than the run's start. `daily_run.sh` exports `COMMENTATOR_LIVE_AS_OF` once, just before its fetch, and every ticker of that run uses it. A run takes 1–2 hours from 22:30 UTC, so later tickers start after midnight; judged by the wall clock they would read the trade date as a past day and lose its items. Without that anchor (on-demand analysis, a manual run), a run that started on its trade date is live, cut off at its own start.
  - **Backtest** (a run that started after its trade date): an item must be published strictly before the trade date's 00:00 UTC.
  - An undated item is refused in both cases. For live streams, the latest timestamp the API reports is used.
- **Record what was read.** `decision_commentator_refs` links each decision to the items its sentiment analyst was shown. The rows are written by `TradeLogRepository.save_decision`. The new tables are created by `create_all()`, and no column is added to `agent_decisions`.
- **Flag off means unchanged.** With `COMMENTATOR_FEED` unset or anything other than `1`, nothing is installed and nothing is fetched. The sentiment prompt is byte-identical to the one before this ADR. The wrapper also checks the flag on every call, so a process that installed it earlier still sends the vendor's prompt when the flag is off. The one thing that runs whatever the flag says is the retention pass (below): it only deletes, and records that it ran.
- **Retention runs every day, on its own.** `ai-trader-commentator-retention.timer` runs `commentator_fetch --retention-only` daily at 11:00 UTC, weekends included, whatever `COMMENTATOR_FEED` or the kill switch says. The trading run cannot carry retention: it is Mon–Fri, flag-gated, and skipped while the kill switch holds. The measurement gate below fills the table with the flag off, and a feed switched off later still holds data; both must leave on time. The pass makes no YouTube call and no extraction. It exits 1 if the store fails, so the unit's `OnFailure` alert fires. The daily fetch also starts with the same pass.

## Compliance

| Rule | How it is met |
|---|---|
| Only official APIs | YouTube Data API v3 (`channels.list` → uploads playlist → `playlistItems.list` → `videos.list`, about 3 quota units a run, 10,000/day free) and X API v2 `GET /2/users/{id}/tweets` with `exclude=replies,retweets` and `since_id`. There is no scraping, no transcript library, no audio download, and no polling of `youtube.com/feeds` (its robots.txt disallows it) |
| YouTube Developer Policies III.E.4: keep API data at most 30 days | `expires_at_utc` = first fetch + 29 days: one day inside the limit, because the daily pass can run up to a day after a deadline. So an item is deleted within 30 days of its fetch. Reads never return an item past `expires_at_utc`, whenever the purge last ran. The routine ingest horizon (14 days) is shorter than retention, so a purged video is not re-ingested the next day |
| X: honour deletions | Every retention pass (daily, weekends included) and every fetch re-reads the stored post ids (`GET /2/tweets?ids=`) and purges any that X no longer serves (deleted, protected, withheld). X items also expire after 8 days (`COMMENTATOR_X_RETENTION_DAYS`), which keeps each check to about 20 billed lookups. Without a token, or when the check fails (revoked token, spend cap, 429, outage), every stored X item is purged, because its deletions can no longer be seen. Reads skip an X post that has not been confirmed live in the last 24 hours, so a pass that never runs cannot keep one in a prompt. The X fetch stores no new post unless the daily pass ran in the last 26 hours |
| No redistribution, no quotation | Titles, descriptions and post text are read at fetch time and never stored. The table holds ids, timestamps and derived fields. Reports carry paraphrases and ids, never verbatim text; this matters because `final_decision_text_tr` reaches the mobile app |
| No profiling (X Developer Agreement, surveillance clause) | Only claims about markets are stored. Nothing about the person is extracted |
| Identity | YouTube is followed by channel id. X is followed by numeric user id, never by handle. The id is resolved once, by a person (`python -m scripts.commentator_fetch --resolve-x-id BoraOzkentNSDQ`), checked against the profile, and pinned in `dataflows/commentator/config.py` or `COMMENTATOR_X_USER_ID`. A rename is logged; nothing is ever re-resolved by handle |
| Secrets | `YOUTUBE_API_KEY` and `X_BEARER_TOKEN` come from the environment (`/opt/ai-trader/secrets.env` on the box). The YouTube key is sent in the `X-Goog-Api-Key` header and the X token as a Bearer header, so neither appears in httpx's request log. Without a key, that source is skipped with a log line |

Retention also applies to the decision link. When an item is purged, its `item_id` in `decision_commentator_refs` is set to NULL. The fact that the decision read commentator input survives; the id of content that must be gone does not.

## Measurement gate: before the flag goes on

**A. Sentiment-node-only diff** (`agent/scripts/commentator_sentiment_diff.py`). The script runs only the sentiment analyst node, twice per point, with and without the block. Both calls get identical inputs: every live fetcher the node calls is memoised, and the model runs at temperature 0. The script compares the score in the report header.

Suggested target: SPY, META, NVDA and AMZN × 25 dates that have feed items.

```bash
# 1. Put items in the table: routine fetch, plus a backfill for history.
python -m scripts.commentator_fetch --ignore-flag --backfill-days 120 --youtube-pages 4
# 2. Free preview: the points and the block each would add.
python -m scripts.commentator_sentiment_diff --tickers SPY META NVDA AMZN --max-dates 25 --dry-run
# 3. The diff itself (costs one sentiment call per point per arm; --noise adds a feed-off repeat).
python -m scripts.commentator_sentiment_diff --tickers SPY META NVDA AMZN --max-dates 25 --noise --out diff.jsonl
```

How to read the result:
- If points whose items are all `unstated` move the score (|Δ| > 0.5), the prompt is leaking tone into the score. Fix the prompt before anything else.
- If Δ ≈ 0 everywhere, the feed adds nothing the analyst uses. **Stop; keep it off.**

**B. Paired ablation** (only if A shows movement). Run `backtest/llm_backtest.py` twice on the same `(ticker, date)` points. The only difference between the two runs is the environment variable.

```bash
COMMENTATOR_FEED=0 python -m backtest.llm_backtest --points META:2026-09-22 NVDA:2026-09-18 ... > off.txt
COMMENTATOR_FEED=1 python -m backtest.llm_backtest --points META:2026-09-22 NVDA:2026-09-18 ... > on.txt
```

- **What to compare:**
  - the rating-flip rate;
  - among the flips, correct against wrong on the 21-day forward return;
  - the mean rating shift (bullish drift);
  - the cost per node.
- **Cost:** about $1–2 a decision, so 4 × 25 × 2 comes to roughly $200–400.
- **What it can establish:** expect 10–30 flips, which is little statistical power. So this is a **no-harm** gate: correct flips ≥ wrong flips, and no systematic bullish drift. If it fails, the feed stays off. No comparison script ships with this ADR; B has not been run.

Both A and B must finish within the 29 days that YouTube items are kept; the daily retention pass deletes them then, flag or no flag. Descriptions can be edited after publication, and deleted videos are missing from a backfill. This is a small look-ahead and survivorship effect: `fetched_at_utc` marks when each item was first seen, and a later edit never replaces the first extraction.

## Enabling X (phase 2) — preconditions

The code is complete and tested, but inert until all of the following are true:

1. **An X pay-per-use account** with a monthly spend limit of $20 (Canberk sets it up and pays). Expected spend is about $6 a month: a few posts a day at $0.005 each, plus the deletion re-check (about 20 posts, twice on weekdays and once on weekend days).
2. **A pinned numeric user id** (see Compliance above). It is `None` today.
3. **The daily deletion check is running.** `ai-trader-commentator-retention.timer` must be enabled. The code enforces this: the X fetch stores nothing new until that pass has run within the last 26 hours, so a post deleted on Friday evening is purged on Saturday, not Monday.

Whether sending X post text to an LLM API for extraction is compatible with the X redistribution clause (III.A(d)) has not been confirmed with a lawyer. The design keeps it to extraction only, with no verbatim text in any report. If the app is ever monetised, re-evaluate this.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Add him as a news vendor | The first successful vendor replaces the others, and the sentiment analyst also reads `get_news`, so the same item counts twice |
| Append to the Reddit block or the StockTwits slot | Wrong label: the analyst would read opinion as crowd data |
| A new news-analyst tool | Needs vendor edits (`news_analyst.py`, `trading_graph.py`) and puts one person's view into a second agent |
| Transcripts (youtube-transcript-api, yt-dlp, Whisper) | Violate the YouTube Terms and API policies |
| Poll `youtube.com/feeds/videos.xml` | robots.txt disallows `/feeds/videos.xml`; the Data API returns the same metadata for free |
| Scrape x.com | Prohibited by the X Terms, which include a liquidated-damages clause |
| Paid channels (X subscription, YouTube membership, Skool) | Subscriber-only posts are not in the X API, and there is no API at all for the others. It would also amount to copying trade alerts |
| Widen the universe to his tickers (CRDO, ANTW, …) | Lets the commentator pick the universe, with selection bias and pump risk. That is a separate universe decision |
| Re-open BIST/TR (reverse ADR-008) | 0 of 15 sampled items were BIST, so there is no case for it |
| Default on | The feed has no measured value yet, and a default-on feed changes every decision's prompt |

## Consequences

**Gained**
- A named, labelled opinion source that the sentiment analyst can weigh. It has no path to an order around the rest of the graph.
- The first per-decision record of raw inputs (`decision_commentator_refs`).
- A reusable once-per-run, per-item cache pattern. Every other social source still re-fetches per ticker.

**Costs**
- About $0.002 of Haiku per new item (an estimate, not measured), a few X posts at $0.005 each when X is enabled, and extra prompt tokens on every sentiment call while the feed is on: about 900 characters (~220 tokens) for an empty or unavailable block and about 2,500 (~600 tokens) for a full one.

**Accepted risks**
- **Stance is mostly `unstated` on YouTube,** because the view is spoken and transcripts are off-limits. The block will often carry topics rather than views. That is accurate: it is all that can be known.
- **Bias.** He runs a paid community, and his headline tone skews bullish. The prompt names this, and gate B checks for drift.
- **Extraction errors.** A misread stance is limited to one labelled line that cannot move the score when it is `unstated`. Items whose extraction fails are retried and never shown half-read.
