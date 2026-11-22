"""Fetch Artificial Analysis language models (Free tier) into data/aa-models.json.

Endpoint: GET /api/v2/language/models/free  (header: x-api-key)
Free tier already exposes cost_per_task.total_cost, which is all this page needs.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = 'https://artificialanalysis.ai/api/v2/language/models/free'
OUT = os.path.join(BASE, 'data', 'aa-models.json')
META = os.path.join(BASE, 'data', 'fetched_at.json')
MIN_SCORED = 100


def today():
    """Calendar date in Asia/Taipei (UTC+8, no DST) so CI and local runs agree."""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat()


def get(page):
    req = urllib.request.Request(
        f'{URL}?page={page}',
        headers={'x-api-key': os.environ['AA_API_KEY'], 'accept': 'application/json'},
    )
    with urllib.request.urlopen(req, timeout=60) as res:
        return json.load(res)


def main():
    models, version, page = [], None, 1
    while True:
        body = get(page)
        version = version or body.get('intelligence_index_version')
        for m in body.get('data') or []:
            cost = (m.get('artificial_analysis_intelligence_index_cost') or {}).get('cost_per_task') or {}
            models.append({
                'name': m['name'],
                'slug': m['slug'],
                'intelligence_index': (m.get('evaluations') or {}).get('artificial_analysis_intelligence_index'),
                'cost_per_task': cost.get('total_cost'),
                'pricing': m.get('pricing'),  # list price per 1M tokens; baseline for Go discounts
                'median_output_tokens_per_second': (m.get('performance') or {}).get('median_output_tokens_per_second'),
                'release_date': m.get('release_date'),
            })
        paging = body.get('pagination') or {}
        if not paging.get('has_more'):
            break
        page += 1
        if page > 20:
            sys.exit('aa: pagination did not terminate')

    models.sort(key=lambda m: m['slug'])
    scored = [m for m in models if m['intelligence_index'] is not None and m['cost_per_task'] is not None]
    if len({m['slug'] for m in models}) != len(models):
        sys.exit('aa: duplicate slugs in response')
    if version is None:
        sys.exit('aa: intelligence_index_version missing')
    if len(scored) < MIN_SCORED:
        sys.exit(f'aa: only {len(scored)} scored models, expected >= {MIN_SCORED} — refusing to overwrite')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump({'intelligence_index_version': version, 'models': models}, f, ensure_ascii=False, indent=2)

    meta = {}
    if os.path.exists(META):
        with open(META, encoding='utf-8') as f:
            meta = json.load(f)
    meta['aa'] = today()
    with open(META, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=2)

    print(f'aa: {len(models)} models, {len(scored)} scored, II v{version}')


if __name__ == '__main__':
    main()
