"""Fetch OpenCode Console (pay-as-you-go) discounts from the published Markdown page
into data/console-prices.json.

Source: https://opencode.ai/docs/console/models.md  (no API key needed)

The models endpoint (https://opencode.ai/inference/v1/models) only lists the
already-discounted prices, so the docs "## Pricing" table is the machine-readable
source for the original (struck-through) list price and the discount ratio.

Only rows that have BOTH an "(N% off)" label and struck-through prices
"~~$X~~ $Y" are kept. The discount is a Console pay-as-you-go property and must
NOT be mixed into Go subscription numbers (the Go API still shows list price).

Output: {"<normalized name>": {"discount_pct": 60, "name": "GLM 5.3",
         "input": 0.56, "output": 1.76, "cache_read": 0.104,
         "list_input": 1.4, "list_output": 4.4, "list_cache_read": 0.26}}
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
MIN_DISCOUNTED = 2


def today():
    """Calendar date in Asia/Taipei (UTC+8, no DST) so CI and local runs agree."""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


OFF_RE = re.compile(r'\((\d+)%\s*off\)', re.I)
STRIKE_RE = re.compile(r'~~\$\s*([\d,.]+)\s*~~\s*\$\s*([\d,.]+)')


def pricing_rows(text):
    """Yield the pipe-delimited rows of the main '## Pricing' table only."""
    in_section = False
    for line in text.splitlines():
        if line.strip() == '## Pricing':
            in_section = True
            continue
        # Stop at the next level-2 heading (### subsections stay inside).
        if in_section and line.startswith('## '):
            break
        if in_section and line.startswith('|'):
            yield [c.strip() for c in line.strip().strip('|').split('|')]


def parse_strike(cell):
    """'~~$1.40~~ $0.56' -> (1.4, 0.56); None when the cell has no struck price."""
    m = STRIKE_RE.search(cell)
    if not m:
        return None
    return (float(m.group(1).replace(',', '')), float(m.group(2).replace(',', '')))


def main():
    req = urllib.request.Request(URL, headers={'accept': 'text/markdown', 'user-agent': 'opencode-console-cost/1.0'})
    with urllib.request.urlopen(req, timeout=60) as res:
        text = res.read().decode('utf-8')

    prices = {}
    for cols in pricing_rows(text):
        if len(cols) < 4 or cols[0] == 'Model' or set(cols[0]) <= {'-'}:
            continue
        off = OFF_RE.search(cols[0])
        if not off:
            continue
        label_pct = int(off.group(1))

        # Input, output, cached-read are the first three price columns.
        pairs = [parse_strike(c) for c in cols[1:4]]
        if any(p is None for p in pairs):
            sys.exit(f'console: discounted row {cols[0]!r} lacks struck-through prices — format changed')
        (list_in, new_in), (list_out, new_out), (list_cache, new_cache) = pairs
        if not (list_in and list_out and list_cache):
            sys.exit(f'console: zero list price in row {cols[0]!r} — format changed')

        pct = round((1 - new_in / list_in) * 100)
        # Output and cache-read must carry the same ratio as input.
        for old, new in ((list_out, new_out), (list_cache, new_cache)):
            if round((1 - new / old) * 100) != pct:
                sys.exit(f'console: inconsistent discount in row {cols[0]!r} — format changed')
        if pct != label_pct:
            sys.exit(f'console: row {cols[0]!r} label says {label_pct}% but prices say {pct}% — format changed')

        name = OFF_RE.sub('', cols[0]).strip()
        prices[norm(name)] = {
            'discount_pct': pct,
            'name': name,
            'input': new_in,
            'output': new_out,
            'cache_read': new_cache,
            'list_input': list_in,
            'list_output': list_out,
            'list_cache_read': list_cache,
        }

    if len(prices) < MIN_DISCOUNTED:
        sys.exit(f'console: parsed {len(prices)} discounted rows, expected >= {MIN_DISCOUNTED} — page format likely changed')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(prices.items())), f, ensure_ascii=False, indent=2)

    meta = {}
    if os.path.exists(META):
        with open(META, encoding='utf-8') as f:
            meta = json.load(f)
    meta['console'] = today()
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    print(f'console: {len(prices)} discounted models, '
          f'{sum(1 for v in prices.values() if v["discount_pct"] == 60)} at 60% off')


if __name__ == '__main__':
    main()
