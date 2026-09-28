# ADR-009: Commentator feed as opinion context

**Status:** Accepted — shipped **off** (`COMMENTATOR_FEED=1` enables it). It stays off until the measurement gate below passes. `YOUTUBE_API_KEY` stays unset until the YouTube legal preconditions below are met: the YouTube source, as designed, does what the YouTube Developer Policies prohibit (derived data from API Data), and that is unresolved. The code refuses the key unless `COMMENTATOR_YOUTUBE_CLEARED=1` records that they are.
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
  - report the source on its own `Commentator view:` line, citing items by label, paraphrased and never quoted.
- **Item format.** An item shows only derived fields and a label for this block, never the video or post id, like this:
  `- 2026-09-22T09:30Z [C1, YouTube] also on: NVDA; topics: Nasdaq rally breadth; stance on META: unstated; paraphrase: …`
  - The label (`C1`, `C2`, …) is what the analyst cites. It resolves to an item only through `decision_commentator_refs`: a decision's refs are written in prompt order, so its n-th ref row (by `id`) is `[Cn]`. The platform id lives only there and in `commentator_items`, and retention scrubs both; no log line carries one (see "Logs" under Compliance). The report the analyst writes is stored whole and outlives both (see "Reports keep what the analyst wrote"), so an id in the prompt would outlive a deleted post in every copy of it.
  - At most 5 items about the ticker itself.
  - Market-wide items are keyed as SPY, or carry macro topics and no ticker at all. A single-name item with a macro topic (NVDA with "AI capex") is not market-wide. The SPY analyst reads market-wide items as its own. Every other ticker gets at most one market-wide line.
  - With nothing in the window, the block says "No commentary in window" only when a recorded successful read covers it, and names that read. A live run names it with its time (`read from YouTube at …`). A backtest names the source only (`read from YouTube`): its covering read happened after the trade date, and the same prompt tells the analyst that the trade date is "now", so the time would hand a point-in-time prompt the real present, in the feed-on arm only. A read covers the window when the unbroken run of reads reaches back to its start, it happened no earlier than the cutoff (a live run: at most 6 hours before, the fetch comes first), and nothing it read can have been purged since. A read proves what was fetched, not what was extracted: while an item it fetched in the window is still unextracted (the model overloaded, unparseable output), nothing is known about it, so the block says the feed is unavailable, and beside shown items it says the list may be incomplete. Otherwise — no key, a failed or timed-out fetch, a window older than the reads or than retention — the block also says the feed is unavailable, not that there was no commentary. An empty table is not an observed absence.
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
- **Record what was read.** `decision_commentator_refs` links each decision to the items its sentiment analyst was shown, in the order of their labels. The rows are written by `TradeLogRepository.save_decision`. The new tables are created by `create_all()`, and no column is added to `agent_decisions`.
- **Flag off means unchanged.** With `COMMENTATOR_FEED` unset or anything other than `1`, nothing is installed and nothing is fetched. The sentiment prompt is byte-identical to the one before this ADR. The wrapper also checks the flag on every call, so a process that installed it earlier still sends the vendor's prompt when the flag is off. The one thing that runs whatever the flag says is the retention pass (below): it only deletes, and records that it ran.
- **Retention runs every day, on its own.** `ai-trader-commentator-retention.timer` runs `commentator_fetch --retention-only` daily at 11:00 UTC, weekends included, whatever `COMMENTATOR_FEED` or the kill switch says. The trading run cannot carry retention: it is Mon–Fri, flag-gated, and skipped while the kill switch holds. The measurement gate below fills the table with the flag off, and a feed switched off later still holds data; both must leave on time. The pass makes no YouTube call and no extraction. It exits 1 if the store fails, so the unit's `OnFailure` alert fires. The daily fetch also starts with the same pass.

## Compliance

| Rule | How it is met |
|---|---|
| Only official APIs | YouTube Data API v3 (`channels.list` → uploads playlist → `playlistItems.list` → `videos.list`, about 3 quota units a run, 10,000/day free) and X API v2 `GET /2/users/{id}/tweets` with `exclude=replies,retweets` and `since_id`. There is no scraping, no transcript library, no audio download, and no polling of `youtube.com/feeds` (its robots.txt disallows it) |
| YouTube Developer Policies III.E.4: keep API data at most 30 days (the storage rule only; see the next row) | `expires_at_utc` = first fetch + 29 days: one day inside the limit, because the daily pass can run up to a day after a deadline. So an item is deleted within 30 days of its fetch. Reads never return an item past `expires_at_utc`, whenever the purge last ran. The routine ingest horizon (14 days) is shorter than retention, so a purged video is not re-ingested the next day |
| YouTube Developer Policies III.E.4: no new or derived data from API Data | **Not met. Unresolved legal risk.** The same subsection as the 30-day rule says API Clients must not "access or use API Data to create new or derived data or metrics". The YouTube path exists to do exactly that. The extractor turns a video's title, description and chapters (API Data) into new fields: `tickers`, a `stance` per ticker, `claim_en`, `is_promo` and `is_market_content`. Those fields are stored, shown to the sentiment analyst, and restated in reports that reach the mobile app. The policy's examples are about metrics (likes, engagement scores), and there may be an argument that classifying a video's topic is not what the clause targets. That argument has not been made to, or confirmed by, a lawyer or YouTube. The only route the policies name is III.L: permission for audited developers with analytics use cases, requested through the quota extension form. It has not been applied for. Until this is resolved, the YouTube source must not be keyed, and the code refuses a key without `COMMENTATOR_YOUTUBE_CLEARED=1` (see "Enabling YouTube — preconditions") |
| YouTube Developer Policies III.A.1, III.A.2: YouTube Terms link and a privacy policy | **Not assessed.** An API Client must link to the YouTube Terms of Service and state in its own terms that users are bound by them. It must also require users to accept a privacy policy that says it uses YouTube API Services and links Google's Privacy Policy. Derived YouTube content reaches app users through decision reports, so these may apply to the mobile app. Nothing in this repository, the mobile app included, does either today |
| X: honour deletions | Every retention pass (daily, weekends included) and every fetch re-reads the stored post ids (`GET /2/tweets?ids=`) and purges any that X no longer serves (deleted, protected, withheld). X items also expire after 8 days (`COMMENTATOR_X_RETENTION_DAYS`), which keeps each check to about 20 billed lookups. Without a token, or when the check fails (revoked token, spend cap, 429, outage), every stored X item is purged, because its deletions can no longer be seen. Reads skip an X post that has not been confirmed live in the last 24 hours, so a pass that never runs cannot keep one in a prompt. The X fetch stores no new post unless the daily pass ran in the last 26 hours |
| Backups | `scripts/backup.py` ships a dated copy of `local.db` off the box every day, and those copies are kept for good (git history, dated S3 keys), past every deadline above. So the copy loses the feed tables before it is compressed: `commentator_items` and `commentator_status` are emptied and every `decision_commentator_refs.item_id` is set to NULL. The backup API copies free pages too, which hold the bytes of rows the live database already purged, so the copy is then rebuilt with `VACUUM`. No feed table row, live or purged, reaches a backup, and no video or post id does, because reports cite labels. **Decision reports do reach it, whole**, with whatever the analyst wrote about a claim; that is a recorded decision with an open legal question, not something the scrub covers (see "Reports keep what the analyst wrote"). A restored database has an empty feed, and its prompts say the feed is unavailable until the next fetch |
| Logs | `daily_run.sh` appends the fetch's output to `logs/daily_<DATE>.log`, which nothing rotates, and the retention unit writes to journald. An id in either would outlive the purge of its item, so no line names a post or video. httpx logs each request URL at INFO, and two of this feed's URLs carry ids in the query string: the deletion check (`GET /2/tweets?ids=`) and `videos.list` (`?id=`); the X fetch carries a `since_id`. So `commentator_fetch` holds the `httpx` logger at WARNING. A failure is logged by exception type and HTTP status (`dataflows/commentator/failures.py`), never with an HTTP error's message or traceback, which repeat the URL. The fetch's engine hides SQL parameters from error messages. An edited item is logged as a count, a skipped one without its id, and a dry run prints publish times, not ids |
| No redistribution, no quotation | Titles, descriptions and post text are read at fetch time and never stored. The table holds ids, timestamps and derived fields. The prompt carries per-block labels, publish times and paraphrases, never ids or verbatim text, so reports can carry no more than that; this matters because reports reach the mobile app (`GET /decisions/{id}`, `final_decision_text_tr`). Reports are not purged (below) |
| No profiling (X Developer Agreement, surveillance clause) | Only claims about markets are stored. Nothing about the person is extracted |
| Identity | YouTube is followed by channel id. X is followed by numeric user id, never by handle. The id is resolved once, by a person (`python -m scripts.commentator_fetch --resolve-x-id BoraOzkentNSDQ`), checked against the profile, and pinned in `dataflows/commentator/config.py` or `COMMENTATOR_X_USER_ID`. A rename is logged; nothing is ever re-resolved by handle |
| Secrets | `YOUTUBE_API_KEY` and `X_BEARER_TOKEN` come from the environment (`/opt/ai-trader/secrets.env` on the box). The YouTube key is sent in the `X-Goog-Api-Key` header and the X token as a Bearer header, so neither appears in httpx's request log. Without a key, that source is skipped with a log line |

Retention also applies to the decision link. When an item is purged, its `item_id` in `decision_commentator_refs` is set to NULL. The fact that the decision read commentator input survives; the id of content that must be gone does not.

A purge removes bytes, not only rows. A plain SQLite DELETE unlinks a row and leaves its bytes in the file's free space. So every write to the feed tables turns on `secure_delete` for its connection, and freed space is overwritten with zeros: the space a purge frees, and the old copies of a row that an earlier update or page split freed. While the tables are empty (the feed off, no evaluation data stored) nothing writes to them, so no connection's setting changes.

### Reports keep what the analyst wrote

A purge reaches `commentator_items` and the ids in `decision_commentator_refs`. It does not reach what the agents wrote. The sentiment analyst is told to report this source on a `Commentator view:` line. Its report is read by the bull and bear researchers and the three risk debaters, whose arguments reach the research manager, the trader and the portfolio manager; any of them may restate it. That text is then kept in:

- `agent_decisions.reasoning_json` and `final_decision_text` / `final_decision_text_tr`, served to the mobile app by `GET /decisions/{id}`;
- the daily off-box `local.db` backups, kept for good;
- the vendor's `~/.tradingagents/logs/<ticker>/TradingAgentsStrategy_logs/full_states_log_<date>.json` (on the box) and its memory log `~/.tradingagents/memory/trading_memory.md` (the portfolio manager's decision; backed up off-box as `memory.tar.gz`). Measurement gate B writes both too.

What that text can hold is bounded by the prompt: labels, publish times, stances, and paraphrases of paraphrases. It holds no video or post id and no verbatim title, description or post, because the prompt holds none. It can hold the substance of a claim for as long as the decision is kept: past YouTube's 30 days, and past the deletion of the X post it came from.

**Decision: these reports are not scrubbed.** The restatements are free text written by several agents and translated to Turkish. They cannot be reliably found. A scrub of the `Commentator view:` line alone would leave the rest and make the retention claim false again. The reports are the audit record of each decision, and this system already refuses to cut them (`graph/pipeline.py`).

**Open legal question, not confirmed with a lawyer:** is keeping a derived restatement of a claim, with no id and no quotation, compatible with the X Developer Agreement's deletion rule and YouTube's III.E.4 retention? If the answer is no, the fix is not a scrub. It is to stop the feed from reaching stored reports at all, for example by keeping it out of the persisted sentiment report. This must be answered before `COMMENTATOR_FEED=1` is set for a run that saves decisions, and before X is enabled.

## Measurement gate: before the flag goes on

**A. Sentiment-node-only diff** (`agent/scripts/commentator_sentiment_diff.py`). The script runs only the sentiment analyst node, twice per point, with and without the block. Both calls get identical inputs: every live fetcher the node calls is memoised, and the model runs at temperature 0. The script compares the score in the report header.

Suggested target: SPY, META, NVDA and AMZN × 25 dates that have feed items.

Blocked until the YouTube preconditions below are met: step 1 needs `YOUTUBE_API_KEY`, and that fetch is what creates the derived data. The code holds the block: without `COMMENTATOR_YOUTUBE_CLEARED=1` step 1 skips YouTube and says why, the table stays empty, and step 2 finds no points.

```bash
# 1. Put items in the table: routine fetch, plus a backfill for history.
#    Only once the preconditions are met: it needs YOUTUBE_API_KEY and
#    COMMENTATOR_YOUTUBE_CLEARED=1, and the key alone is refused.
python -m scripts.commentator_fetch --ignore-flag --backfill-days 120 --youtube-pages 4
# 2. Free preview: the points and the block each would add.
python -m scripts.commentator_sentiment_diff --tickers SPY META NVDA AMZN --max-dates 25 --dry-run
# 3. The diff itself (costs one sentiment call per point per arm; --noise adds a feed-off repeat).
python -m scripts.commentator_sentiment_diff --tickers SPY META NVDA AMZN --max-dates 25 --noise --out diff.jsonl
```

How to read the result:
- If points whose prompt lines all show an `unstated` stance move the score (|Δ| > 0.5), the prompt is leaking tone into the score. Fix the prompt before anything else. A point is judged by the stance each line shows, not by every stance its items carry: a META item that also names SPY is shown as `stance on META: …`, and its SPY stance is not in META's prompt.
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

## Enabling YouTube — preconditions

`YOUTUBE_API_KEY` must not be set on any box, and must not be set for the measurement gate's `--ignore-flag` backfill either, until all of the following are true. The key is what creates the derived data. The flag only decides whether a prompt reads it, and the gate fetches with the flag off.

1. **The derived-data clause is resolved in writing.** Either a lawyer confirms that extracting tickers, stances and a paraphrase from titles and descriptions is outside III.E.4's "new or derived data" prohibition, or YouTube grants the III.L permission for this use. Until then the YouTube source is described as non-compliant, not as compliant.
2. **III.A.1/III.A.2 are settled for every surface that shows the result.** Either the mobile app (and trader.fusapp.com, if it shows reports) links the YouTube Terms and carries a conforming privacy policy, or a lawyer confirms these do not apply to a backend client whose users see only derived text.
3. **The report-retention question is answered** (see "Reports keep what the analyst wrote"), because YouTube-derived text in stored reports outlives the 30 days.

The code enforces this. `config.youtube_api_key()` refuses the key, with a warning, unless `COMMENTATOR_YOUTUBE_CLEARED=1` is also set, whatever `COMMENTATOR_FEED` or `--ignore-flag` says; the fetch then skips YouTube and its summary names the missing clearance. That variable is the record that all three conditions above are true in writing. Set it only then, never to make a run work. A key alone never reaches the API, so it never creates derived data. The `.env.example` and `deploy/hetzner/install.sh` templates carry the warning next to the variable.

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
- **Legal, open.** The YouTube source, as designed, runs against the Developer Policies' derived-data clause (III.E.4), and the III.A.1/III.A.2 obligations for what users see are not assessed. This is not accepted; it is why the key stays unset (see "Enabling YouTube — preconditions"). Two more questions are also open: X III.A(d), and keeping derived restatements in stored reports.
- **Stance is mostly `unstated` on YouTube,** because the view is spoken and transcripts are off-limits. The block will often carry topics rather than views. That is accurate: it is all that can be known.
- **Bias.** He runs a paid community, and his headline tone skews bullish. The prompt names this, and gate B checks for drift.
- **Extraction errors.** A misread stance is limited to one labelled line that cannot move the score when it is `unstated`. Items whose extraction fails are retried and never shown half-read; while one sits in a window, that window is reported unavailable, not empty.
