"""Fetch the published OpenCode Console (pay-as-you-go) price table from the docs page
into data/console-prices.json.

Source: https://opencode.ai/docs/console/models.md  (no API key needed)

The models endpoint (https://opencode.ai/inference/v1/models) only lists the
already-discounted price, so the docs "## Pricing" table is the machine-readable
source for the actual price and the original (struck-through) list price. Every
row of that table is parsed, discounted or not, so build.py can derive a discount
from the prices themselves instead of trusting the "(N% off)" label.

Cell forms: "$0.10", "~~$1.40~~ $0.56", "Free", "-".
Each price column yields an actual price plus an optional struck-through list
price. "(N% off)" on the model name is optional and only kept as label_pct; when
a row has both a label and struck prices, the two must describe the same
discount (guarded below). The discount is a Console pay-as-you-go property and
must NOT be mixed into Go subscription numbers (the Go API still shows list
price).

Output: {"<normalized name>": {"name": "GLM 5.3", "input": 0.56, "output": 1.76,
         "cache_read": 0.104, "list_input": 1.4, "list_output": 4.4,
         "list_cache_read": 0.26, "free": false, "label_pct": 60}}
  - input/output/cache_read: actual price per 1M tokens, null when "-"/unparsable
  - list_*: struck-through price, null when the cell has none
  - free: true when the input cell says "Free" (all its numeric prices are then 0)
  - label_pct: int from "(N% off)" on the name, else null
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = 'https://opencode.ai/docs/console/models.md'
OUT = os.path.join(BASE, 'data', 'console-prices.json')
META = os.path.join(BASE, 'data', 'fetched_at.json')

# The live table has ~100 priced rows; anything far below means the layout changed.
MIN_PRICED = 10
# Allowed drift between a row's label and its struck-through ratios, in points.
PCT_TOLERANCE = 1


def today():
    """Calendar date in Asia/Taipei (UTC+8, no DST) so CI and local runs agree."""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


OFF_RE = re.compile(r'\((\d+)%\s*off\)', re.I)
MONEY = r'\$\s*([\d,.]+)'
STRIKE_RE = re.compile(r'~~\s*' + MONEY + r'\s*~~\s*' + MONEY)
PRICE_RE = re.compile(MONEY)


def num(s):
    return float(s.replace(',', ''))


def pricing_table(text):
    """Return (rows, header) of the main '## Pricing' table only.

    rows is None when the section is missing; header is None when no column
    header row ("Model | Input | ...") was found.
    """
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == '## Pricing'), None)
    if start is None:
        return None, None
    rows = []
    for line in lines[start + 1:]:
        # Stop at the next level-2 heading (### subsections stay inside).
        if line.startswith('## '):
            break
        if line.startswith('|'):
            rows.append([c.strip() for c in line.strip().strip('|').split('|')])
    header = next(([c.lower() for c in cols] for cols in rows if cols and cols[0].lower() == 'model'), None)
    return rows, header


def parse_cell(cell):
    """Return (actual, list_price) for one price cell.

    '~~$1.40~~ $0.56' -> (0.56, 1.4); '$0.10' -> (0.10, None);
    'Free' -> (0.0, None); '-' or unparsable -> (None, None).
    """
    m = STRIKE_RE.search(cell)
    if m:
        return num(m.group(2)), num(m.group(1))
    if cell.strip().lower() == 'free':
        return 0.0, None
    m = PRICE_RE.search(cell)
    if m:
        return num(m.group(1)), None
    return None, None


def check_label(name, label_pct, cells):
    """Fail when a row carries both a label and struck prices that disagree."""
    if label_pct is None:
        return
    for field, actual, listed in cells:
        if listed is None:
            continue  # label alone, or only some columns struck: nothing to cross-check
        if actual is None:
            sys.exit(f'console: row {name!r} has struck-through {field} but no actual price — format changed')
        if not listed:
            sys.exit(f'console: zero list {field} price in row {name!r} — format changed')
        pct = (1 - actual / listed) * 100
        if abs(pct - label_pct) > PCT_TOLERANCE:
            sys.exit(f'console: row {name!r} label says {label_pct}% off but {field} prices '
                     f'say {pct:.1f}% — format changed')


def main():
    req = urllib.request.Request(URL, headers={'accept': 'text/markdown', 'user-agent': 'opencode-console-cost/1.0'})
    with urllib.request.urlopen(req, timeout=60) as res:
        text = res.read().decode('utf-8')

    rows, header = pricing_table(text)
    if rows is None:
        sys.exit('console: no "## Pricing" section — page format changed')
    if header is None:
        sys.exit('console: no "Model | Input | ..." header row in ## Pricing — page format changed')
    for needed in ('input', 'output'):
        if not any(needed in col for col in header):
            sys.exit(f'console: ## Pricing header has no {needed!r} column — page format changed')

    prices = {}
    priced = 0
    collapsed = 0
    for cols in rows:
        if len(cols) < 4 or cols[0].lower() == 'model' or set(cols[0]) <= {'-'}:
            continue

        in_actual, in_list = parse_cell(cols[1])
        out_actual, out_list = parse_cell(cols[2])
        cache_actual, cache_list = parse_cell(cols[3])
        label = OFF_RE.search(cols[0])
        label_pct = int(label.group(1)) if label else None
        name = re.sub(r'\s{2,}', ' ', OFF_RE.sub('', cols[0])).strip()
        free = cols[1].strip().lower() == 'free'

        check_label(name, label_pct, (
            ('input', in_actual, in_list),
            ('output', out_actual, out_list),
            ('cache-read', cache_actual, cache_list),
        ))

        if in_actual is not None:
            priced += 1
        key = norm(name)
        if not key:
            sys.exit(f'console: row {cols[0]!r} normalizes to an empty key — format changed')
        if key in prices:
            # Only the tiered "(<= N tokens)"/"(> N tokens)" pairs collapse here:
            # norm() drops the marker, so they share a key. Keep the first (base)
            # tier and leave the expensive overflow tier out.
            collapsed += 1
            continue
        prices[key] = {
            'name': name,
            'input': in_actual,
            'output': out_actual,
            'cache_read': cache_actual,
            'list_input': in_list,
            'list_output': out_list,
            'list_cache_read': cache_list,
            'free': free,
            'label_pct': label_pct,
        }

    if priced < MIN_PRICED:
        sys.exit(f'console: only {priced} rows with a numeric input price, expected >= {MIN_PRICED} — page format likely changed')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(prices.items())), f, ensure_ascii=False, indent=2)

    meta = {}
    if os.path.exists(META):
        with open(META, encoding='utf-8') as f:
            meta = json.load(f)
    meta['console'] = today()
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    print(f'console: {len(prices)} models from {priced} priced rows, '
          f'{sum(1 for v in prices.values() if v["label_pct"] is not None)} labelled, '
          f'{sum(1 for v in prices.values() if v["free"])} free'
          + (f', {collapsed} tiered duplicates collapsed' if collapsed else ''))


if __name__ == '__main__':
    main()
