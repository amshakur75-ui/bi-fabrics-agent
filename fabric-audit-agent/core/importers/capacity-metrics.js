// Schema-aware readers for the real Fabric Capacity Metrics CSV exports.
// The generic column-mapper (map.js) is too loose for these specific tables, so we
// recognize them explicitly and read them with the correct semantics. Pure: no I/O.
import { num } from './map.js';

const norm = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9%]/g, '');
const findH = (headers, pred) => headers.find(h => pred(norm(h))) ?? null;
const round1 = (x) => Math.round(x * 10) / 10;

// ───────────────────────── Items table ─────────────────────────
// e.g. Workspace | Item kind | Item name | CU (s) | Duration (s) | Users | Rejected count | Billing type
export function looksLikeItems(headers) {
  const hasName = headers.some(h => { const n = norm(h); return n.includes('itemname') || n === 'item' || (n.includes('item') && n.includes('name')) || n.includes('datasetname'); });
  const hasCu = headers.some(h => { const n = norm(h); return (n === 'cus' || n.includes('cus') || (n.includes('cu') && n.includes('second'))) && !n.includes('%'); });
  return hasName && hasCu;
}

export function mapItems(headers, rows) {
  const ws = findH(headers, n => n.includes('workspace'));
  const kind = findH(headers, n => n.includes('itemkind') || n === 'kind' || n.includes('itemtype'));
  const name = findH(headers, n => n.includes('itemname') || n === 'item' || (n.includes('item') && n.includes('name')) || n.includes('datasetname'));
  const cu = findH(headers, n => (n === 'cus' || n.includes('cus') || (n.includes('cu') && n.includes('second'))) && !n.includes('%'));
  const dur = findH(headers, n => n.includes('duration'));
  const users = findH(headers, n => n === 'users' || n.includes('usercount'));
  const rej = findH(headers, n => n.includes('reject'));

  const items = rows.map(r => ({
    workspace: ws ? String(r[ws] ?? '').trim() : '',
    kind: kind ? String(r[kind] ?? '').trim() : '',
    name: name ? String(r[name] ?? '').trim() : '',
    cuSeconds: cu ? (num(r[cu]) || 0) : 0,
    durationSec: dur ? (num(r[dur]) || 0) : 0,
    users: users ? (num(r[users]) || 0) : 0,
    rejected: rej ? (num(r[rej]) || 0) : 0,
  })).filter(it => it.name);

  const totalCu = items.reduce((s, it) => s + it.cuSeconds, 0);
  const rejectedTotal = items.reduce((s, it) => s + it.rejected, 0);
  for (const it of items) it.sharePct = totalCu ? round1(it.cuSeconds / totalCu * 100) : 0;
  const top = [...items].sort((a, b) => b.cuSeconds - a.cuSeconds).slice(0, 10)
    .map(it => ({ ...it, pctOfTotal: it.sharePct }));
  const rejectedItems = items.filter(it => it.rejected > 0).sort((a, b) => b.rejected - a.rejected);

  return { items, itemCount: items.length, totalCu, rejectedTotal, top, rejectedItems, columns: { ws, kind, name, cu, dur, users, rej } };
}

// ─────────────────────── Timepoint table ───────────────────────
// e.g. ... | 100% in CU(s) | Timepoint | Total CU Usage % | Total CU(s) | CU % Limit | Capacity State Change ...
export function looksLikeTimepoints(headers) {
  const hasTime = headers.some(h => { const n = norm(h); return n.includes('timepoint') || n === 'time' || n === 'datetime'; });
  const hasCu = headers.some(h => { const n = norm(h); return n.includes('totalcu') || n.includes('100%in') || n.includes('cuusage'); });
  return hasTime && hasCu;
}

export function analyzeTimepoints(headers, rows) {
  const usagePct = findH(headers, n => n.includes('totalcuusage') || (n.includes('usage') && n.includes('%')) || n.includes('utiliz'));
  const totalCu = findH(headers, n => n.includes('totalcus') || (n.includes('total') && n.includes('cus')));
  const baseHdr = findH(headers, n => n.includes('100%in'));
  const stateHdr = findH(headers, n => n.includes('state'));
  const time = findH(headers, n => n.includes('timepoint') || n === 'time');

  let reportedPeakPct = null, reportedAt = '';
  if (usagePct) {
    let mx = -Infinity;
    for (const r of rows) { const v = num(r[usagePct]); if (Number.isFinite(v) && v > mx) { mx = v; reportedAt = time ? String(r[time] ?? '').trim() : ''; } }
    reportedPeakPct = mx === -Infinity ? null : round1(mx);
  }

  let baseline = NaN;
  if (baseHdr) for (const r of rows) { const v = num(r[baseHdr]); if (Number.isFinite(v) && v > 0) { baseline = v; break; } }

  let computedPeakPct = null, computedAt = '';
  if (totalCu && Number.isFinite(baseline) && baseline > 0) {
    let mx = -Infinity;
    for (const r of rows) { const v = num(r[totalCu]); if (Number.isFinite(v)) { const p = v / baseline * 100; if (p > mx) { mx = p; computedAt = time ? String(r[time] ?? '').trim() : ''; } } }
    computedPeakPct = mx === -Infinity ? null : round1(mx);
  }

  const states = {};
  if (stateHdr) for (const r of rows) { const v = String(r[stateHdr] ?? '').trim() || '(blank)'; states[v] = (states[v] ?? 0) + 1; }

  return { reportedPeakPct, reportedAt, computedPeakPct, computedAt, baseline: Number.isFinite(baseline) ? baseline : null, states, columns: { usagePct, totalCu, baseHdr, stateHdr } };
}

// ──────────────────── safe column inspector ────────────────────
// Per-column stats for understanding a file WITHOUT exposing sensitive labels.
// Name/workspace/dataset columns show only a distinct-count; categories (state,
// kind, billing type) show their distinct values; numbers show min/median/max/sum.
export function inspectColumns(headers, rows) {
  const labelish = (n) => n.includes('name') || n.includes('workspace') || n.includes('dataset');
  return headers.map(h => {
    const n = norm(h);
    const vals = rows.map(r => String(r[h] ?? '').trim()).filter(v => v !== '');
    const looksTime = n.includes('timepoint') || n === 'time' || n.includes('datetime');
    const nums = vals.map(num).filter(Number.isFinite);
    if (!looksTime && vals.length && nums.length >= vals.length * 0.6) {
      const sorted = [...nums].sort((a, b) => a - b);
      return { column: h, type: 'number', count: nums.length, min: sorted[0], max: sorted[sorted.length - 1], median: sorted[Math.floor(sorted.length / 2)], sum: Math.round(nums.reduce((s, x) => s + x, 0) * 100) / 100 };
    }
    const distinct = [...new Set(vals)];
    if (looksTime) return { column: h, type: 'time', count: vals.length, distinct: distinct.length };
    if (labelish(n)) return { column: h, type: 'label', count: vals.length, distinct: distinct.length };
    if (distinct.length <= 15) return { column: h, type: 'category', distinct: distinct.length, values: distinct.sort() };
    return { column: h, type: 'text', distinct: distinct.length };
  });
}
