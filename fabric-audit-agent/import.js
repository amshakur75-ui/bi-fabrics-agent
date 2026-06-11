// Import YOUR real Fabric/Power BI exports and run the diagnosis — no hand-typing.
// 100% local: nothing leaves this machine; no network, no API key.
//
//   node import.js <file> [moreFiles...]      run the importer + diagnosis
//   node import.js --inspect <file.csv>       print safe per-column stats (no sensitive values)
//
// Supported: .csv (Capacity Metrics items / timepoint / model / report exports) and .vpax.
// Excel? File -> Save As -> CSV first. Pass several files at once and they merge.
//
// Writes combined numbers to my-estate.json (gitignored — never pushed).
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, extname, basename } from 'node:path';
import { parseCsv } from './core/importers/csv.js';
import { mapTable, mergeFacts } from './core/importers/map.js';
import { vpaxToModels } from './core/importers/vpax.js';
import { looksLikeItems, mapItems, looksLikeTimepoints, analyzeTimepoints, inspectColumns } from './core/importers/capacity-metrics.js';
import { diagnose, formatDiagnosis } from './core/diagnosis.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const inspect = argv.includes('--inspect') || argv.includes('-i');
const files = argv.filter(a => !a.startsWith('-'));
const n0 = (x) => Math.round(x).toLocaleString();

if (!files.length) {
  console.log(`
  Usage:  node import.js <file> [moreFiles...]
          node import.js --inspect <file.csv>     (safe column stats; no sensitive values)

  Examples:
     node import.js "Capacity Metrics export.csv"
     node import.js data.csv Items.csv            (merges both)

  Supported: .csv and .vpax. For Excel, Save As CSV first.
`);
  process.exit(0);
}

// ── inspect mode: understand a file's columns without exposing labels ──
if (inspect) {
  for (const f of files) {
    if (extname(f).toLowerCase() !== '.csv') { console.log(`(skipping ${basename(f)} — inspect is for .csv)`); continue; }
    let parsed;
    try { parsed = parseCsv(await readFile(f, 'utf8')); } catch (e) { console.log(`could not read ${basename(f)}: ${e.message}`); continue; }
    console.log(`\n===== INSPECT: ${basename(f)}  (${parsed.rows.length} rows) =====`);
    for (const s of inspectColumns(parsed.headers, parsed.rows)) {
      if (s.type === 'number') console.log(`  [num]   ${s.column}: min=${s.min}  median=${s.median}  max=${s.max}  sum=${s.sum}`);
      else if (s.type === 'category') console.log(`  [cat]   ${s.column}: ${s.values.join(', ')}`);
      else console.log(`  [${s.type}] ${s.column}: ${s.distinct} distinct (values hidden)`);
    }
  }
  console.log('');
  process.exit(0);
}

const parts = [];
const report = [];
const itemsAnalyses = [];
const tpAnalyses = [];

for (const f of files) {
  const ext = extname(f).toLowerCase();
  const label = basename(f);
  try {
    if (ext === '.csv') {
      const { headers, rows } = parseCsv(await readFile(f, 'utf8'));
      if (!headers.length) { report.push({ label, note: 'empty / unreadable CSV' }); continue; }
      if (looksLikeItems(headers)) {
        itemsAnalyses.push({ label, ...mapItems(headers, rows) });
        report.push({ label, headers, rows: rows.length, kind: 'Capacity Metrics items table' });
      } else {
        const part = mapTable(headers, rows);
        parts.push(part);
        report.push({ label, headers, rows: rows.length, coverage: part.coverage });
        if (looksLikeTimepoints(headers)) tpAnalyses.push({ label, ...analyzeTimepoints(headers, rows) });
      }
    } else if (ext === '.vpax') {
      const { models, coverage } = vpaxToModels(await readFile(f));
      parts.push({ capacity: null, models, reports: [], coverage });
      report.push({ label, coverage });
    } else if (ext === '.xlsx' || ext === '.xls') {
      report.push({ label, note: 'Excel not parsed directly — File -> Save As -> CSV, then re-run' });
    } else {
      report.push({ label, note: `unsupported type "${ext}" — use .csv or .vpax` });
    }
  } catch (err) {
    report.push({ label, note: `could not read: ${err.message}` });
  }
}

const facts = mergeFacts(parts);
if (itemsAnalyses.length) facts.items = itemsAnalyses.flatMap(a => a.items);
const peak = facts.capacity?.peakCuPct ?? 0;
const utilizationUnreadable = peak > 1000;

// ── WHAT I READ ──
console.log('\n================  IMPORT — WHAT I READ  ===================\n');
for (const r of report) {
  console.log(`File: ${r.label}${r.rows != null ? `   (${r.rows} data row(s))` : ''}${r.kind ? `  [${r.kind}]` : ''}`);
  if (r.note) console.log(`   ! ${r.note}`);
  if (r.headers) console.log(`   columns: ${r.headers.join(' | ')}`);
  for (const c of r.coverage ?? []) {
    if (c.source) { console.log(`   ok  ${c.field}  <-  "${c.source}"  =  ${c.value}`); if (c.note) console.log(`       ! ${c.note}`); }
    else console.log(`   --  ${c.field}: ${c.note ?? 'not found'}`);
  }
  console.log('');
}

// ── utilization over time ──
for (const t of tpAnalyses) {
  console.log(`---- Capacity utilization over time (${t.label}) ----`);
  if (t.reportedPeakPct != null) console.log(`   "Total CU Usage %" peak (raw):       ${t.reportedPeakPct}%${t.reportedPeakPct > 1000 ? '   <- raw pre-smoothing spike, NOT the throttling number' : ''}`);
  if (t.computedPeakPct != null) console.log(`   Total CU(s) / 100%-baseline peak:    ${t.computedPeakPct}%   (baseline ${n0(t.baseline)} CU-s = 100%)`);
  if (Object.keys(t.states).length) console.log(`   capacity states:  ${Object.entries(t.states).map(([k, v]) => `${k}=${v}`).join('   ')}`);
  console.log('');
}

// ── items: the optimize targets ──
for (const a of itemsAnalyses) {
  console.log(`---- Top CU consumers (${a.label}) — your optimize targets ----`);
  console.log(`   ${a.itemCount} items, total ${n0(a.totalCu)} CU-seconds`);
  a.top.forEach((it, i) => console.log(`   ${String(i + 1).padStart(2)}. ${String(it.pctOfTotal).padStart(4)}%  ${it.name}${it.kind ? ` [${it.kind}]` : ''}${it.workspace ? `  (${it.workspace})` : ''}  — ${n0(it.cuSeconds)} CU-s`));
  const top5 = Math.round(a.top.slice(0, 5).reduce((s, it) => s + it.pctOfTotal, 0));
  console.log(`   -> top 5 = ${top5}% of all CU.`);
  if (a.rejectedTotal > 0) {
    console.log(`   THROTTLING CONFIRMED: ${n0(a.rejectedTotal)} operation(s) rejected. Worst:`);
    a.rejectedItems.slice(0, 5).forEach(it => console.log(`        ${n0(it.rejected)} rejected   ${it.name}`));
  } else {
    console.log(`   No operations rejected in this window (no hard-throttling rejections recorded).`);
  }
  console.log('');
}

if (!facts.capacity && !(facts.models?.length) && !(facts.reports?.length) && !itemsAnalyses.length) {
  console.log('No usable capacity / item / model / report data recognized.');
  console.log('Run  node import.js --inspect yourfile.csv  and paste me the output.\n');
  process.exit(0);
}

// ── persist + diagnose (sanitize an unreadable raw-% so it can't drive a bogus verdict) ──
const factsForDiag = JSON.parse(JSON.stringify(facts));
if (utilizationUnreadable && factsForDiag.capacity) factsForDiag.capacity.peakCuPct = 0;

if (facts.capacity || facts.models?.length || facts.reports?.length || facts.items?.length) {
  await writeFile(join(__dirname, 'my-estate.json'), JSON.stringify(factsForDiag, null, 2));
  console.log('Wrote combined numbers to my-estate.json (gitignored — never pushed). Tweak + re-run: node mytest.js');
  const diag = await diagnose(factsForDiag);
  if (diag.findings.length) console.log(formatDiagnosis(diag));
}

// ── preliminary, honest read ──
console.log('\n================  PRELIMINARY READ  =======================\n');
const ai = itemsAnalyses[0];
if (ai) {
  if (ai.rejectedTotal > 0) console.log(`* Throttling IS happening: ${n0(ai.rejectedTotal)} rejected operation(s) — capacity is hitting its ceiling.`);
  else console.log('* No rejected operations recorded — no hard throttling in this window.');
  const top5 = Math.round(ai.top.slice(0, 5).reduce((s, it) => s + it.pctOfTotal, 0));
  if (top5 >= 50) console.log(`* CU is concentrated: top 5 items = ${top5}% of all CU  ->  OPTIMIZE those first before paying for a bigger SKU.`);
  else console.log(`* CU is spread across many items (top 5 = ${top5}%)  ->  less easy headroom; if utilization stays high, sizing up may be justified.`);
}
if (utilizationUnreadable) console.log(`* Overall utilization: NOT readable from this file (the "%" column held raw spikes, peak ${n0(peak)}%). Need the smoothed % — see --inspect.`);
else if (facts.capacity) console.log(`* Peak utilization read: ${peak}%.`);

console.log(`
NEXT — to finish the verdict:
  1) node import.js --inspect ${report.find(r => r.headers)?.label ?? 'data.csv'} ${itemsAnalyses[0]?.label ?? ''}
     (paste me the stats — numbers + categories only, no item names)
  2) tell me your capacity SKU  (F2 / F4 / F8 / F16 / F32 / F64 / F128 / F256 ...)
  3) include a throttling/overload export if you have one
`);
