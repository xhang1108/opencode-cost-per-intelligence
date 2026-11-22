import json, re, os
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
SRC = ('aa-models.json', 'go-limits.json')
AA = json.load(open(os.path.join(BASE,'data','aa-models.json'),encoding='utf-8'))
GO = json.load(open(os.path.join(BASE,'data','go-limits.json'),encoding='utf-8'))
# Console pay-as-you-go discounts are a separate property from Go subscription pricing.
CONSOLE_PATH = os.path.join(BASE,'data','console-prices.json')
CONSOLE = json.load(open(CONSOLE_PATH,encoding='utf-8')) if os.path.exists(CONSOLE_PATH) else {}
PLAN_PRICE = 10.0

# Snapshot date = when the source snapshots were fetched, not when this ran.
def snapshot_date():
    meta_path = os.path.join(BASE, 'data', 'fetched_at.json')
    if os.path.exists(meta_path):
        with open(meta_path, encoding='utf-8') as f:
            stamps = [v for v in json.load(f).values() if v]
        if stamps:
            return min(stamps)  # oldest half of the snapshot
    stamps = [os.path.getmtime(os.path.join(BASE,'data',f)) for f in SRC]
    return datetime.fromtimestamp(max(stamps)).strftime('%Y-%m-%d')

# Go serves Meta's Contributor mode for Muse Spark: same tokens, published at list price
# so the model's data may be used for training. AA measures cost/task at standard prices,
# so convert with the published price ratio instead of hard-coding a multiplier.
# Meta list prices, USD per 1M tokens, blended 7:2:1 cache-hit : input : output.
BLEND = (0.7, 0.2, 0.1)
STANDARD = (0.15, 1.25, 4.25)      # cached / input / output, standard mode
CONTRIBUTOR = (0.002, 0.10, 0.20)  # cached / input / output, contributor mode

def blended(price):
    return sum(w * p for w, p in zip(BLEND, price))

CONTRIBUTOR_RATIO = blended(CONTRIBUTOR) / blended(STANDARD)  # 0.0414 / 0.78 ≈ 0.053077

def go_value(v):
    """go-limits.json entry: new dict, or the old bare float/None. -> (limit, contributor, limited_regions)"""
    if isinstance(v, dict):
        return v.get('limit'), bool(v.get('contributor')), v.get('limited_regions')
    return v, False, None

def norm(s):
    return re.sub(r'[^a-z0-9]+','', (s or '').lower())

go_norm = {norm(k): (k, go_value(v)) for k, v in GO.items()}

# AA and Go list the same model under different product names. Verified against
# Go canonical_id reflection/beam-1.0-501b-a23b and AA slug reflection-beam.
GO_ALIASES = {
    'beam10': 'Beam Free',        # AA Beam 1.0 / Go Beam Free — canonical reflection/beam-1.0-501b-a23b
    'tencenthy4': 'Hy4 New',      # AA Tencent Hy4 / Go Hy4 — canonical tencent/hy4 (release 2026-11-03/04)
}

def match(name):
    n = norm(name)
    if n in GO_ALIASES:
        go_name = GO_ALIASES[n]
        return (norm(go_name), go_name, go_norm[norm(go_name)][1])
    # exact
    if n in go_norm:
        orig, rec = go_norm[n]
        return (n, orig, rec)
    best = None
    # Go base is prefix of AA variant
    for gn, (orig, rec) in go_norm.items():
        if len(gn) < 3: continue
        if n.startswith(gn):
            if best is None or len(gn) > len(best[0]):
                best = (gn, orig, rec)
    if best:
        return best
    # fallback: AA prefix of Go (specific Go variant)
    for gn, (orig, rec) in go_norm.items():
        if len(gn) < 3: continue
        if gn.startswith(n):
            if best is None or len(gn) > len(best[0]):
                best = (gn, orig, rec)
    return best

# Console discount lookup. Keys in console-prices.json are already normalized names.
console_norm = {norm(k): v for k, v in CONSOLE.items()}
# AA Free API also carries list prices per 1M tokens (stored by fetch_aa.py since
# 2026-11-22). Prefer that as the baseline; fall back to the strikethrough list
# price in console-prices.json for older snapshots that lack AA pricing.
AA_PRICE = {m['slug']: (m.get('pricing') or {}) for m in AA['models'] if m.get('pricing')}

def match_console(name):
    """Match a model to its Console discount by normalized name.

    AA appends a reasoning tier in parentheses and sometimes a release suffix
    (e.g. 'DeepSeek V4.1 Flash 1017 (Max)'). Strip those, then require an exact
    normalized match. A plain prefix match would incorrectly give GLM-5.3's
    discount to the separate, un-discounted GLM-5.3-Flash model."""
    base = norm(re.sub(r'\s*\([^)]*\)\s*$', '', name))
    for key in (norm(name), base, re.sub(r'\d+$', '', base)):
        if key in console_norm:
            return console_norm[key]
    return None

def make_row(m, slug, used, go_name, limit, limited, source, pricing_mode, label=None, eff_override=None):
    ii = m['intelligence_index']
    cpt = m['cost_per_task']
    if eff_override is not None:
        eff = eff_override
    elif limit and limit not in (None, float('inf')):
        eff = used * (PLAN_PRICE / limit)
    else:
        eff = used
    return {
        'name': m['name'],
        'label': label or m['name'],          # chart/tooltip name; tells the two tiers apart
        'slug': slug,
        'intelligence_index': ii,
        'aa_cost_per_task': cpt,
        'aa_cost_per_task_standard': cpt,
        'aa_cost_per_task_used': used,
        'go_model': go_name,
        'go_monthly_limit': limit,
        'go_limited_regions': limited,
        'go_effective_cost_per_task': eff,
        'cost_per_intelligence_point': (eff / ii) if (eff is not None and ii) else None,
        'intelligence_per_dollar': (ii / eff) if (eff is not None and ii and eff) else None,
        'monthly_task_capacity': (PLAN_PRICE / eff) if (limit and eff) else None,
        'cost_source': source,
        'pricing_mode': pricing_mode,
        'median_output_tokens_per_second': m.get('median_output_tokens_per_second'),
        'release_date': m.get('release_date'),
    }

rows = []
for m in AA['models']:
    if m.get('intelligence_index') is None or m.get('cost_per_task') is None:
        continue
    hit = match(m['name'])
    if not hit:
        rows.append(make_row(m, m['slug'], m['cost_per_task'], None, None, None, 'AA provider', 'aa-standard'))
        continue
    go_name = hit[1]
    limit, contributor, limited = hit[2]
    if limit is None:
        # Go free tier: no monthly allowance and $0/task. Keep the AA list-price
        # row for reference, and add a Go row at $0 — same two-row pattern as
        # Contributor, but the cheaper side is free rather than discounted.
        rows.append(make_row(m, m['slug'], m['cost_per_task'], None, None, None, 'AA provider', 'aa-standard'))
        rows.append(make_row(m, m['slug'] + '-go-free', m['cost_per_task'], go_name, None, limited,
                             'Go (free)', 'go-free', label=go_name, eff_override=0))
        continue
    source = 'Go' if (limit and limit not in (None, float('inf'))) else 'Go (unlimited)'
    if contributor:
        # Two tiers, two rows: Meta still sells standard (what AA measured) at list price,
        # while Go sells the Contributor variant at the discounted card. Showing only one
        # hides a ~19x gap; showing both lets the reader pick.
        rows.append(make_row(m, m['slug'], m['cost_per_task'], None, None, None, 'AA provider', 'aa-standard'))
        rows.append(make_row(m, m['slug'] + '-contributor', m['cost_per_task'] * CONTRIBUTOR_RATIO,
                             go_name, limit, limited, source, 'go-contributor',
                             label=m['name'] + ' · Contributor'))
    else:
        rows.append(make_row(m, m['slug'], m['cost_per_task'], go_name, limit, limited, source, 'go-standard'))

# The 60% off lowers the price per token while the Go monthly allowance stays
# the same ($500 GLM-5.3, $100 DeepSeek V4.1 Flash), so the same task consumes
# 40% of the allowance. Apply the ratio to the Go effective cost only; the AA
# list price (Original price) is never discounted. Pre-existing fields stay.
for r in rows:
    # Only Go models are in scope; Zen/pay-as-you-go-only models are ignored.
    entry = match_console(r['name']) if r['go_model'] else None
    if entry:
        base_slug = r['slug']
        for suffix in ('-contributor', '-go-free'):
            if base_slug.endswith(suffix):
                base_slug = base_slug[:-len(suffix)]
        aa_in = (AA_PRICE.get(base_slug) or {}).get('price_1m_input_tokens')
        console_in = entry.get('input')
        if aa_in and console_in and 0 < console_in <= aa_in:
            pct = round((1 - console_in / aa_in) * 100)
        else:
            pct = entry['discount_pct']
        eff = r['go_effective_cost_per_task']
        r['console_discount_pct'] = pct
        r['go_effective_cost_per_task_discounted'] = eff * (1 - pct / 100) if eff is not None else None
    else:
        r['console_discount_pct'] = None
        r['go_effective_cost_per_task_discounted'] = None

rows.sort(key=lambda r: r['cost_per_intelligence_point'])

with open(os.path.join(BASE,'data','merged.json'),'w',encoding='utf-8') as f:
    json.dump({'intelligence_index_version': AA.get('intelligence_index_version'), 'plan_price': PLAN_PRICE,
               'snapshot_date': snapshot_date(), 'build_date': datetime.now().strftime('%Y-%m-%d'), 'rows': rows}, f, ensure_ascii=False, indent=2)

print('total scored:', len(rows))
print('Go:', sum(1 for r in rows if r['cost_source'].startswith('Go')))
print('AA provider:', sum(1 for r in rows if r['cost_source']=='AA provider'))
print('snapshot:', snapshot_date())
