"""Reddit sentiment scraper. PRAW-based.

Targets r/wallstreetbets, r/stocks, r/investing, r/options for cashtag mentions.
Returns post + comment text for downstream LLM sentiment analysis.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

DEFAULT_SUBS = ("wallstreetbets", "stocks", "investing", "options", "ValueInvesting")


@dataclass(frozen=True)
class RedditPost:
    subreddit: str
    post_id: str
    title: str
    body: str
    score: int
    num_comments: int
    created_utc: datetime
    url: str


class RedditClient:
    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        # Lazy import: PRAW is heavy
        import praw  # type: ignore[import-untyped]

        self.reddit = praw.Reddit(
            client_id=client_id or os.environ["REDDIT_CLIENT_ID"],
            client_secret=client_secret or os.environ["REDDIT_CLIENT_SECRET"],
            user_agent=user_agent or os.environ.get("REDDIT_USER_AGENT", "trading-research/0.1"),
        )
        self.reddit.read_only = True

    def search_ticker(
        self,
        ticker: str,
        subs: tuple[str, ...] = DEFAULT_SUBS,
        limit: int = 25,
        time_filter: str = "day",
    ) -> list[RedditPost]:
        """Search recent posts mentioning a ticker across target subs."""
        out: list[RedditPost] = []
        # PRAW supports searching r/sub1+sub2+... in one call
        sub = self.reddit.subreddit("+".join(subs))
        query = f'"{ticker}" OR "${ticker}"'
        for s in sub.search(query, sort="new", time_filter=time_filter, limit=limit):
            out.append(
                RedditPost(
                    subreddit=str(s.subreddit),
                    post_id=s.id,
                    title=s.title,
                    body=(s.selftext or "")[:2000],
                    score=s.score,
                    num_comments=s.num_comments,
                    created_utc=datetime.fromtimestamp(s.created_utc),
                    url=f"https://reddit.com{s.permalink}",
                )
            )
        return out
