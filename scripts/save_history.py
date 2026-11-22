"""Snapshot merged.json into data/history/ only when pricing facts change meaningfully.

Triggers: OpenCode console discount appears/changes/disappears, OpenCode Go monthly
limit changes, an AA intelligence_index_version change, or a material AA
cost_per_task update. Small daily cost drift is ignored via a threshold, so
unchanged data is reused instead of producing a node every day.

The default view is always data/merged.json; history entries are only for sliding back.
"""

import json
import os
import re
import shutil
import statistics
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MERGED = os.path.join(BASE, 'data', 'merged.json')
HISTORY = os.path.join(BASE, 'data', 'history')
INDEX = os.path.join(HISTORY, 'index.json')
# Ignore day-to-day AA cost noise; only a median move above this opens a node.
AA_DRIFT_THRESHOLD = 0.01


def load_json(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def signature(merged):
    """Meaningful-change fingerprint of one merged.json (missing fields -> None)."""
    limits, discounts, aa_costs = {}, {}, {}
    for row in merged.get('rows') or []:
        # Use the Go name when present; otherwise strip AA's reasoning-tier
        # suffix so every tier of a model collapses to one key.
        name = row.get('go_model') or re.sub(r'\s*\([^)]*\)\s*$', '', row.get('name') or '')
        if not name:
            continue
        limit = row.get('go_monthly_limit')
        pct = row.get('console_discount_pct')
        if limit is not None:
            limits[name] = limit
        if pct is not None:
            discounts[name] = pct
        cost = row.get('aa_cost_per_task')
        if isinstance(cost, (int, float)) and cost > 0:
            aa_costs[row['slug']] = cost
    return {
        'ii_version': merged.get('intelligence_index_version'),
        'go_limits': sorted([name, value] for name, value in limits.items()),
        'discounts': sorted([name, value] for name, value in discounts.items()),
        'aa_costs': aa_costs,
    }


def fmt(value, suffix=''):
    if value is None:
        return 'none'
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f'{value}{suffix}'


def fmt_version(value):
    return f'v{value}' if value is not None else 'none'


def diff_signatures(old, new):
    """Human-readable diff lines from the previous signature to the new one."""
    if old is None:
        return []
    lines = []
    if old.get('ii_version') != new.get('ii_version'):
        lines.append(f'AA version: {fmt_version(old.get("ii_version"))} → {fmt_version(new.get("ii_version"))}')
    for label, key, suffix in (('limit', 'go_limits', ''), ('discount', 'discounts', '%')):
        before = {name: value for name, value in old.get(key) or []}
        after = {name: value for name, value in new.get(key) or []}
        for name in sorted(set(before) | set(after)):
            was, now = before.get(name), after.get(name)
            if was != now:
                lines.append(f'{name} {label}: {fmt(was, suffix)} → {fmt(now, suffix)}')
    # AA cost/task: report only material moves so a refresh doesn't churn nodes.
    old_costs, new_costs = old.get('aa_costs') or {}, new.get('aa_costs') or {}
    added, removed = len(set(new_costs) - set(old_costs)), len(set(old_costs) - set(new_costs))
    if added:
        lines.append(f'AA models added: {added}')
    if removed:
        lines.append(f'AA models removed: {removed}')
    rels = []
    for slug in set(old_costs) & set(new_costs):
        was, now = old_costs[slug], new_costs[slug]
        if was and now:
            rels.append(abs(now - was) / abs(was))
    if rels:
        median = statistics.median(rels)
        if median > AA_DRIFT_THRESHOLD:
            lines.append(f'AA cost/task: median {median*100:.1f}% move across {len(rels)} models')
    return lines


def main():
    if not os.path.exists(MERGED):
        sys.exit(f'history: {MERGED} missing')
    with open(MERGED, encoding='utf-8') as f:
        merged = json.load(f)
    if not merged.get('rows'):
        sys.exit('history: merged.json has no rows')
    # Key history by the day the change was detected (build_date), not by
    # snapshot_date: the latter stays at the oldest source fetch and would
    # overwrite an earlier snapshot when a discount appears a day later.
    date = merged.get('build_date') or merged.get('snapshot_date')
    snapshot = merged.get('snapshot_date')
    if not date:
        sys.exit('history: merged.json has no date')

    index = load_json(INDEX) or {}
    # A day with no meaningful change gets no node on the timeline.
    entries = [e for e in (index.get('entries') or []) if e.get('changes')]
    old = None
    if entries:
        last = entries[-1]
        prev_path = os.path.join(HISTORY, f'{last.get("date") or last.get("snapshot_date")}.json')
        prev = load_json(prev_path)
        if prev is not None:
            old = signature(prev)

    changes = diff_signatures(old, signature(merged))
    if not changes:
        print('no meaningful change')
        return

    os.makedirs(HISTORY, exist_ok=True)
    shutil.copyfile(MERGED, os.path.join(HISTORY, f'{date}.json'))  # exact copy, overwrite same date

    entry = {
        'date': date,
        'snapshot_date': snapshot,
        'build_date': merged.get('build_date'),
        'changes': changes,
    }
    entries = [e for e in entries if (e.get('date') or e.get('snapshot_date')) != date]
    entries.append(entry)
    entries.sort(key=lambda e: e.get('date') or e.get('snapshot_date') or '')
    with open(INDEX, 'w', encoding='utf-8') as f:
        json.dump({'entries': entries}, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(f'history: {date} saved ({len(changes)} meaningful change(s))')


if __name__ == '__main__':
    main()
