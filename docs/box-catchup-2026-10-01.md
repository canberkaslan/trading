# Box catch-up: what `git pull` would ship (2026-10-01)

> **Correction, 2026-10-03 — this note targets the wrong box.** The live paper
> trader is **prod-fusapp01** (behind `trader.fusapp.com`), on **46bca18**
> (2026-09-23), with a **working** broker key: `/readyz` `alpaca:true`, eval
> day 21 on 10-03. `151.115.89.202` (`trader-stg`) is an orphaned copy whose key
> died when the prod deploy regenerated it; it still runs the full council every
> night and then fails at submit.
>
> What changes as a result:
>
> - **Do not rotate the Alpaca key** (step 4 below). It is the live trader's key;
>   rotating it takes the real runner down. Putting the working key on the WAW
>   copy instead would put two runners on one account. The WAW copy's timers
>   should be disabled, not repaired.
> - **There is no "key refusing" safety window on the live box.** A `git pull`
>   there trades the new code at the next 22:30 UTC run.
> - The live box is **28** commits behind, not 77. In the buckets below:
>   A = 3b502d1 (#49), **15bf661 (#52)**, a716efa (#45), 4bf5f32 (#72, DB engine/WAL);
>   B = 3ef1e1c (#56, flag off), 7835bf8 (#58), 68e709a, bf39d11 (#71);
>   C = 94e71f8, e364701, f007e0b, e276d0c, 14d7722; the rest are D.
>   The September stop work is already on it. #52 is still the blocker.
> - Units there run as `User=deploy`: use `sudo -u deploy git ...`.
> - The watchdog now probes `trader.fusapp.com` from both workflows. The
>   `WATCHDOG_HOST` secret still has to be pointed at the live origin by hand.

The trader box (`ubuntu@151.115.89.202`, `/opt/ai-trader`) is on **4ec0472**
(2026-09-09). `main` is **77 commits** ahead. The deploy path is
`git pull --ff-only origin main`, so it is all of them or none of them. This
note sorts the 77 by blast radius so the pull can be a decision, not a reflex.

The broker key has been refusing since 2026-09-14 22:42 UTC (day 18 today).
While it refuses, the daily run dies before it submits anything. **Rotating
the key is the switch that turns the new code on.** Whatever is on the box
when the key comes back is what trades.

## Buckets

Each commit is counted once, in the riskiest bucket it touches.

| Bucket | Commits | What it changes | Gate before it trades |
|---|---|---|---|
| **A. Execution / risk path** | 15bf661 (#52), 3b502d1 (#49), a716efa (#45), 60ad222, 6fabb07, af15dc9, 651809e, 2d890e2, 99004bb, a60b96c, c281ca6, 07f0a92, 839d300, 6e39be3, c72ff35, a8cbc31 | Orders, stops, exits, sizing, circuit breaker, daily_run wiring | #52 must-fix list closed (below), then a supervised paper day |
| **B. Decision quality (LLM / prompts / data)** | 593603f (vendor 0.2.5→0.4.x), 173a7eb, 7543baa, 397534c, 8d46b8d, e80d089, b0326e0, bf39d11 (#71), 7835bf8 (#58), 3ef1e1c (#56, flag off), 68e709a, 3cd8309 | What the council sees and how it answers | None for safety; it resets any performance baseline. Note the day it lands |
| **C. Read-only API / monitoring** | e364701, f007e0b, 94e71f8, 14d7722, e276d0c, 067a620, 014e7f7, 8f0692b, 00f06d3, 7f6f554, 485441c, 263958e, 860040b, 8b20915 | Routes, watchdog, audit log, account delete | Tests green is enough |
| **D. Mobile / web UI, CI, docs, design** | the remaining ~35 | Nothing the box executes | n/a (OTA / CI) |

Bucket A is where the risk is. Most of it is the protective-stop work from
early September: managing positions after entry, backfilling stops, stops in
tick increments. The box has been running **without** that all along, so
pulling it is overdue rather than speculative. The exception is **#52**.

## #52 is the blocker

#52 (15bf661, time exits: cancel → sell → re-arm, an exit budget, a mark band)
was merged on 2026-09-28 before its review converged. Rounds went 3 → 6 → 5
must-fix and the last round was never re-reviewed. The open class is
**ambiguous broker states**:

- the submit times out and we cannot tell whether the order exists;
- the cancel of the protective leg is `pending_cancel`, not `canceled`;
- an OCO sibling is still live after the parent changes.

In each one the lot can be left with **no stop** for some window. Two ways
out: a design where the protective leg is never removed before the
replacement is confirmed, or reverting 15bf661 on main until that design
exists. The revert applies cleanly on today's main (checked in a scratch
worktree). The only later commit that touches a file #52 touched is 14d7722,
in `tests/test_api_trades.py`, so rerun that file after a revert.

## Not in git: what the pull does not do

| Item | Why it matters |
|---|---|
| `deploy/hetzner/install.sh` changed (+65 lines), plus new `ai-trader-commentator-retention.{service,timer}` and an edited `ai-trader-alert.service` | `git pull` does not reinstall units. Re-run install, then `sed` `User=deploy`→`User=ubuntu` again (see the migration notes) |
| `eval-report.timer` is not among the box's active timers | The weekly scorecard push is not running |
| New env keys, all optional: `HEALTHCHECK_URL`, `OPS_ALERT_GITHUB_TOKEN`, `OPS_ALERT_GITHUB_REPO`, `COMMENTATOR_*`, `YOUTUBE_API_KEY` | Unset means degraded, not broken. The commentator feed stays off by default |
| `pyproject.toml` / `uv.lock` | Unchanged, so no dependency install is needed |
| The box has not fetched (`HEAD..origin/main` = 0) | Run `git fetch` before comparing anything on the box |

## Recommended order

1. Close #52's ambiguous-state cases, or revert 15bf661 on main.
2. Pull on the box while the key **still refuses**. Nothing can trade, so this
   is the cheapest moment to check startup, units and `/readyz` against the
   new code.
3. Re-run `install.sh`, re-apply the `User=ubuntu` sed, and enable
   `eval-report.timer`.
4. Rotate the Alpaca paper key (Canberk, from the dashboard), then restart the
   units.
5. Watch the first daily run live: stop coverage, the exit ledger, no naked
   lots. Record that date as the start of the new performance baseline,
   because bucket B changes the decision-maker.

If step 1 has to wait, steps 2–4 can still run on a **deploy branch** made of
4ec0472 plus a few bucket C commits. That fixes the eval `500` and the
snapshot `502` and makes the watchdog page on a refused key, without bringing
any execution code onto the box. Checked by cherry-picking onto 4ec0472 in a
scratch worktree:

| Commit | Cherry-pick onto 4ec0472 |
|---|---|
| e364701 (eval → 503) | clean |
| 94e71f8 (watchdog reads `/readyz`) | clean |
| f007e0b (snapshot/history/concentration → 503) | conflicts in `api/routes/risk.py`, because the stop-coverage route (7f6f554) is not on the box. Keep the box's side of `risk.py` and take the rest |

The watchdog runs from GitHub Actions on `main`, so 94e71f8 is already live.
It only needs to be on the box if the box itself runs it.
