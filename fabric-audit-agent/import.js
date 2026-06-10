// Import YOUR real Fabric/Power BI exports and run the diagnosis — no hand-typing.
// 100% local: nothing leaves this machine; no network, no API key.
//
//   node import.js <file> [moreFiles...]
//
// Supported: .csv (Capacity Metrics / model / report exports) and .vpax (VertiPaq).
// Excel? do File -> Save As -> CSV first, then pass the .csv.
// Pass several files at once and they merge (e.g. a capacity csv + a model .vpax).
//
// It writes the combined numbers to my-estate.json (gitignored — never pushed), so you
// can open that file, sanity-check/tweak a value, and re-run with:  node mytest.js
import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join, extname, basename } from 'node:path';
import { parseCsv } from './core/importers/csv.js';
import { mapTable, mergeFacts } from './core/importers/map.js';
import { vpaxToModels } from './core/importers/vpax.js';
import { diagnose, formatDiagnosis } from './core/diagnosis.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const files = process.argv.slice(2);

if (!files.length) {
  console.log(`
  Usage:  node import.js <file> [moreFiles...]

  Examples:
     node import.js "Capacity Metrics export.csv"
     node import.js capacity.csv model.vpax        (merges both)

  Supported: .csv and .vpax. For Excel, Save As CSV first.
`);
  process.exit(0);
}

const parts = [];
const report = [];

for (const f of files) {
  const ext = extname(f).toLowerCase();
  const label = basename(f);
  try {
    if (ext === '.csv') {
      const { headers, rows } = parseCsv(await readFile(f, 'utf8'));
      if (!headers.length) { report.push({ label, note: 'empty / unreadable CSV', coverage: [] }); continue; }
      const part = mapTable(headers, rows);
      parts.push(part);
      report.push({ label, headers, rows: rows.length, coverage: part.coverage });
    } else if (ext === '.vpax') {
      const { models, coverage } = vpaxToModels(await readFile(f));
      parts.push({ capacity: null, models, reports: [], coverage });
      report.push({ label, coverage });
    } else if (ext === '.xlsx' || ext === '.xls') {
      report.push({ label, note: 'Excel not parsed directly — open it and do File -> Save As -> CSV, then re-run with the .csv', coverage: [] });
    } else {
      report.push({ label, note: `unsupported type "${ext}" — use .csv or .vpax`, coverage: [] });
    }
  } catch (err) {
    report.push({ label, note: `could not read: ${err.message}`, coverage: [] });
  }
}

const facts = mergeFacts(parts);

// ---- print what was read + how columns mapped ----
console.log('\n================  IMPORT — WHAT I READ  ===================\n');
for (const r of report) {
  console.log(`File: ${r.label}${r.rows != null ? `   (${r.rows} data row(s))` : ''}`);
  if (r.note) console.log(`   ! ${r.note}`);
  if (r.headers) console.log(`   columns: ${r.headers.join(' | ')}`);
  for (const c of r.coverage ?? []) {
    if (c.source) console.log(`   ok  ${c.field}  <-  "${c.source}"  =  ${c.value}`);
    else console.log(`   --  ${c.field}: ${c.note ?? 'not found'}`);
  }
  console.log('');
}

if (!facts.capacity && !(facts.models?.length) && !(facts.reports?.length)) {
  console.log('No usable capacity / model / report columns were recognized.');
  console.log('Paste me the header line of your file and I will widen the column matchers.\n');
  process.exit(0);
}

// ---- persist to my-estate.json (gitignored) so you can tweak + re-run ----
const out = join(__dirname, 'my-estate.json');
await writeFile(out, JSON.stringify(facts, null, 2));
console.log(`Wrote combined numbers to my-estate.json  (gitignored — never pushed).`);
console.log(`Open it to sanity-check a value, then re-run anytime with:  node mytest.js`);

// ---- run the diagnosis ----
console.log(formatDiagnosis(await diagnose(facts)));
