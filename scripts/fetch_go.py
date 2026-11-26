"""Fetch OpenCode Go allowances into data/go-limits.json.

Two official sources, in order of authority:

1. https://opencode.ai/inference/go/v1/models — the public model-list API (no key;
   documented at https://opencode.ai/docs/console/api/models). Each Go model carries
   `go.monthly_usage`, the live allowance per plan. This is the source of truth.
2. https://opencode.ai/console/go.md — the Markdown price table. It supplies the model
   names and badges, and a fallback for models the API omits. Its allowances can lag
   behind the API (a promotional allowance may be live in the API but not yet in the page),
   so every name matched in the API has its limit and flags replaced by the API values.

The table appends badges to model names. "Contributor" and "Limited regions" are parsed
into flags because they change what Go actually serves: Contributor mode is a
lower-priced copy of the model that may train on your traffic, and regional routing can
disappear without the model changing. "Free" and "New" stay part of the name (they are how
the model is listed); "Limited time" is dropped as noise.

Output: {"<base name>": {"limit": 60.0 | null, "contributor": bool, "limited_regions": bool}}
"""

import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = 'https://opencode.ai/console/go.md'
API_URL = 'https://opencode.ai/inference/go/v1/models'
OUT = os.path.join(BASE, 'data', 'go-limits.json')
META = os.path.join(BASE, 'data', 'fetched_at.json')
MIN_MODELS = 20
MIN_API_MODELS = 20
REQUIRED = 'GLM-5.3'


def today():
    """Calendar date in Asia/Taipei (UTC+8, no DST) so CI and local runs agree."""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()

# Badge -> output flag. "Free" / "New" stay: they are part of the name.
BADGES = {'Contributor': 'contributor', 'Limited regions': 'limited_regions'}
DROP = ('Limited time',)


def split_badges(label):
    """'Muse Spark 1.3 Contributor Limited regions' -> ('Muse Spark 1.3', {'contributor': True, ...})"""
    text = label.replace('·', ' ')
    flags = {key: False for key in BADGES.values()}
    for badge, key in BADGES.items():
        if re.search(r'\b' + re.escape(badge) + r'\b', text):
            flags[key] = True
            text = re.sub(r'\s*\b' + re.escape(badge) + r'\b\s*', ' ', text)
    for badge in DROP:
        text = re.sub(r'\s*\b' + re.escape(badge) + r'\b\s*', ' ', text)
    return re.sub(r'\s+', ' ', text).strip(), flags


def parse_allowance(cell):
    """'Go: $15; Go Plus: $60' -> 15.0 ; 'Go: ∞; Go Plus: ∞' -> None"""
    m = re.search(r'Go:\s*\$?([\d,]+)', cell)
    if not m:
        if '∞' in cell:
            return None
        raise ValueError(f'unreadable monthly limit cell: {cell!r}')
    return float(m.group(1).replace(',', ''))


def key(s):
    """Normalized name for matching Markdown rows to API entries."""
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def fmt_limit(value):
    return 'unlimited' if value is None else f'{value:g}'


def api_models():
    """Live Go allowances/flags from the public model-list API.

    Returns {normalized name: {"limit": float|None, "contributor": bool,
    "limited_regions": bool}}. Models without `go.monthly_usage` are skipped.
    """
    req = urllib.request.Request(
        API_URL,
        headers={'accept': 'application/json', 'user-agent': 'opencode-go-cost/1.0'},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        body = json.load(res)

    out = {}
    for m in body.get('data') or []:
        go = m.get('go') or {}
        if 'monthly_usage' not in go:
            continue
        usage = go['monthly_usage'] or {}
        limit = usage.get('go')
        if limit is not None and not isinstance(limit, (int, float)):
            sys.exit(f'go api: unreadable monthly_usage.go for {m.get("name")!r}: {limit!r}')
        name = m.get('name') or ''
        k = key(name)
        if not k:
            continue
        rec = {
            'limit': float(limit) if limit is not None else None,
            'contributor': bool(go.get('training')),
            'limited_regions': bool(go.get('limited_regions')),
        }
        out[k] = rec
        # The Markdown parser keeps "Free"/"New" in names but strips "Contributor"
        # into a flag; index that spelling too so both sources match.
        plain = key(re.sub(r'\bcontributor\b', '', name, flags=re.I))
        if plain != k:
            out[plain] = rec

    if len(out) < MIN_API_MODELS:
        sys.exit(f'go api: only {len(out)} monthly allowances, expected >= {MIN_API_MODELS} '
                 '— response format likely changed')
    return out


def main():
    req = urllib.request.Request(URL, headers={'accept': 'text/markdown', 'user-agent': 'opencode-go-cost/1.0'})
    with urllib.request.urlopen(req, timeout=60) as res:
        text = res.read().decode('utf-8')

    limits = {}
    for line in text.splitlines():
        if not line.startswith('|'):
            continue
        cols = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cols) != 4 or cols[0] in ('Model', '') or set(cols[0]) <= {'-'}:
            continue
        name, flags = split_badges(cols[0])
        limits[name] = {
            'limit': parse_allowance(cols[3]),
            'contributor': flags['contributor'],
            'limited_regions': flags['limited_regions'],
        }

    if len(limits) < MIN_MODELS:
        sys.exit(f'go: parsed {len(limits)} models, expected >= {MIN_MODELS} — page format likely changed')
    if REQUIRED not in limits:
        sys.exit(f'go: {REQUIRED!r} missing — page format likely changed')

    # The Markdown table is the name list; the API owns the live numbers. A model
    # can be promoted in the API while the page still shows the old allowance.
    api = api_models()
    api_only = set(api)
    overlaid, missing, changed = 0, [], []
    for name, rec in limits.items():
        k = key(name)
        candidates = (k, k[:-3] if k.endswith('new') else k)
        hit = next((api[c] for c in candidates if c in api), None)
        if hit is None:
            missing.append(name)
            continue
        for c in candidates:
            api_only.discard(c)
        if rec['limit'] != hit['limit']:
            changed.append(f'{name}: {fmt_limit(rec["limit"])} -> {fmt_limit(hit["limit"])}')
        rec['limit'] = hit['limit']
        rec['contributor'] = hit['contributor']
        rec['limited_regions'] = hit['limited_regions']
        overlaid += 1
    if REQUIRED in missing:
        sys.exit(f'go api: {REQUIRED!r} missing — response format likely changed')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(limits.items())), f, ensure_ascii=False, indent=2)

    meta = {}
    if os.path.exists(META):
        with open(META, encoding='utf-8') as f:
            meta = json.load(f)
    meta['go'] = today()
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    print(f'go: {len(limits)} models, {sum(1 for v in limits.values() if v["limit"] is None)} unlimited, '
          f'{sum(1 for v in limits.values() if v["contributor"])} contributor, '
          f'{sum(1 for v in limits.values() if v["limited_regions"])} limited regions')
    print(f'go api: {overlaid} overlaid, {len(missing)} page-only ({", ".join(missing) or "none"}), '
          f'{len(api_only)} api-only')
    if changed:
        print('go api overrides: ' + ', '.join(changed))


if __name__ == '__main__':
    main()
