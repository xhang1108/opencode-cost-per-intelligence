# Operating guide

## Data flow

CI (`.github/workflows/refresh.yml`, daily 03:23 UTC) runs:

1. `scripts/fetch_aa.py` — Artificial Analysis `cost_per_task` (list price) → `data/aa-models.json`
2. `scripts/fetch_go.py` — Go monthly allowance from `opencode.ai/console/go.md` → `data/go-limits.json`
3. `scripts/fetch_console.py` — Console pay-as-you-go discounts from `opencode.ai/docs/console/models.md` → `data/console-prices.json`
4. `build.py` — merges the three into `data/merged.json`
5. `scripts/save_history.py` — stores a snapshot only when a meaningful fact changes

`data/merged.json` carries two cost stories:

- `go_effective_cost_per_task` — Go subscription value (`AA cost × $10 / monthly limit`).
- `go_effective_cost_per_task_discounted` — the same value with the discount applied (`× (1 − pct/100)`). `console_discount_pct` (e.g. `60`) records the percentage.
- `aa_cost_per_task_used` — Artificial Analysis list price, **never discounted** (Original price).

A 60% lower token price leaves the Go monthly allowance unchanged ($500 GLM-5.3, $100 DeepSeek V4.1 Flash) and makes the same task consume 40% of the allowance, hence the lower effective cost per task. Discount and usage are separate dimensions.

## Console discount parsing

`fetch_console.py` reads only the `## Pricing` table and accepts rows with a `(N% off)` label and struck-through prices (`~~$0.30~~ $0.12`). It cross-checks input/output/cache ratios and refuses to write if fewer than 2 rows parse (format change guard). `build.py` strips AA's reasoning-tier parentheses and trailing release digits before exact normalized matching, so `GLM-5.3-Flash` does not inherit `GLM-5.3`'s discount. Discounts are attached **only to Go models** (`go_model` present); Zen-only models such as Mistral Large 4 are ignored.

The discount is `1 − actual_price / list_price`. `fetch_aa.py` now stores AA's Free-tier `pricing` fields (list price per 1M tokens) and `build.py` prefers that as the baseline; older snapshots without AA pricing fall back to the struck-through list price. Example: DeepSeek V4.1 Flash list `$0.30 / $1.20 / $0.006` → actual `$0.12 / $0.48 / $0.0024` = 60% off.

## History snapshots

`save_history.py` fingerprints `intelligence_index_version`, Go monthly limits, discount percentages, and AA `cost_per_task` per model. AA cost drift below a 1% median threshold is treated as noise; added/removed AA models always count. When something materially changes, it copies `merged.json` to `data/history/<build_date>.json` and appends an entry to `data/history/index.json` with a human-readable change list. Keying uses `build_date` (detection day), not `snapshot_date` (oldest source fetch). A day with no change gets **no node** — unchanged data is reused from the previous snapshot and "Latest" always reads the live `merged.json`.

Current nodes: 2026-11-21 (Hy4 New limit added), 2026-11-22 (Go discounts added). Older no-change snapshots were dropped; they remain recoverable from git.

The earliest node is the exception: `data/history/2026-11-21.json` (entry date 2026-11-21, build_date 2026-11-22) was seeded by hand from the pre-discount snapshot, so its entry date follows `snapshot_date` rather than `build_date`. Not every node is script-generated, and `save_history.py` will never re-emit that one — that is expected.

## Site controls

- **Go price / Original price** — switches the chart's cost axis. Only Go price can be discounted; Original (AA list) price stays untouched.
- **With discount / No discount** — fixed next to the Go price switch in both price modes; it never moves or hides. It is usable only for **Go price + Go/All filter + a snapshot with discounts**. Otherwise it greys out with a reason on hover: `Discount applies to Go price only` (Original price), `AA provider models have no Go discount` (AA filter), or `No discount available for this snapshot` (older snapshot). In the usable state it toggles `go_effective_cost_per_task_discounted` vs `go_effective_cost_per_task`.
- **Snapshot history** — slider defaults to Latest. It updates its label during the drag and lazy-loads `data/history/<date>.json` on release; tick labels are clickable shortcuts. Dates render as `21 NOV 2026`, and the earliest node carries a `DAY 1` badge. **Back to latest** restores the live `merged.json`. Tick tooltips show that day's changes.

## Local verification

```sh
python -m http.server 8899 --bind 127.0.0.1
# open http://127.0.0.1:8899/index.html
python scripts/save_history.py   # expect "no meaningful change" after a rebuild
```
