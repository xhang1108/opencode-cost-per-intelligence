# OpenCode Go × Artificial Analysis

Cost per intelligence point for every model on OpenCode Go pricing.

**Live: https://xhang1108.github.io/opencode-cost-per-intelligence/**

![Page](preview.jpg)

## Data

| File | Content |
|---|---|
| `data/aa-models.json` | Intelligence Index, cost per task, output tok/s — Artificial Analysis free API |
| `data/go-limits.json` | Go monthly allowance per model from the public Go model-list API, with `contributor` / `limited_regions` flags; Markdown rows supply names and a fallback |
| `data/merged.json` | Joined rows + `snapshot_date`, from `python build.py` — each row carries `pricing_mode`, `aa_cost_per_task_used` and `go_limited_regions` |

A snapshot, not a live feed. Current numbers: [Intelligence Index](https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index) · [Go allowances](https://opencode.ai/inference/go/v1/models) · [Go pricing](https://opencode.ai/console/go)

## Method

| Metric | Formula |
|---|---|
| Go $/task | AA $/task × $10 ÷ monthly allowance |
| Contributor $/task | AA $/task × 0.0531 (contributor ÷ standard blended price, 7:2:1 cache-hit : input : output) × $10 ÷ monthly allowance |
| Two rows | A model AA has measured and Go sells in both modes is listed twice: Meta standard at AA list price, and the Go Contributor variant (or, for Beam, the Go free tier) |
| Go free tier | Beam Free has no monthly allowance and costs $0/task on Go (limited regions, limited time, daily request cap). $0 rows get their own FREE column left of the paid log scale; in the combined view, Original price shows only AA's list-price row, so the free tier is not listed twice |
| $/point | Go $/task ÷ Intelligence Index |
| Tasks per $10 | monthly allowance ÷ AA $/task |

One model at a time: assumes the whole allowance is spent on that model, and unused allowance does not roll over.

## Refresh

Daily 03:23 UTC (11:23 Taipei) and on `workflow_dispatch`: fetch → build → commit `data/` only when it changed. Needs the `AA_API_KEY` repo secret.

Unofficial; not affiliated with OpenCode / SST.
