"""A single named market commentator, read as opinion context (ADR-009).

Bora Özkent publishes US-equities commentary in Turkish on YouTube (primary)
and X (secondary). This package turns that into a short, English, labelled
block in the sentiment analyst's prompt — never a buy/sell signal, never an
input to the trader, the portfolio manager, the risk layer or a score formula.

Only official APIs: the YouTube Data API v3 (title, description, chapters) and
the X API v2. No scraping, no transcript libraries, no audio download, and no
polling of youtube.com/feeds (its robots.txt disallows it). The spoken content
of a video is therefore not available, which is why most YouTube items carry
topics and an `unstated` stance rather than a view.

Off unless COMMENTATOR_FEED=1. With the flag off nothing here runs: no fetch,
no extraction, no write, and the sentiment prompt is byte-identical to before.

Modules:
    config          pinned identities, retention and horizon settings
    items           the RawItem both sources hand to extraction
    youtube_source  channels.list -> uploads playlist -> playlistItems -> videos
    x_source        /2/users/{id}/tweets by pinned numeric id; deletion reconcile
    extract         one cheap-tier call per item: Turkish text -> English fields
    ingest          fetch, extract once, store, purge — once per run, not per ticker
"""
