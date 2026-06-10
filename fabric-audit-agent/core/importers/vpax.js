import { readZipEntries } from './zip.js';

/** read the first present key from an object */
const pick = (obj, keys) => { for (const k of keys) if (obj?.[k] != null) return obj[k]; return undefined; };
const round3 = (x) => Math.round(x * 1000) / 1000;

/**
 * Turn a `.vpax` file (VertiPaq Analyzer export — a ZIP of JSON) into a single
 * model fact. Defensive about schema variants (DAX Studio / Tabular Editor / Bravo).
 *
 * @param {Buffer} buf  raw .vpax bytes
 * @returns {{ models: object[], coverage: Array<{field:string,source:string|null,value:any,note?:string}> }}
 */
export function vpaxToModels(buf) {
  const entries = readZipEntries(buf);
  let jsonName = null;
  for (const k of entries.keys()) {
    const low = k.toLowerCase();
    if (low.endsWith('daxmodel.json') || low.endsWith('daxvpaview.json')) { jsonName = k; if (low.endsWith('daxmodel.json')) break; }
  }
  if (!jsonName) throw new Error('no DaxModel.json / DaxVpaView.json inside the .vpax');

  const data = JSON.parse(entries.get(jsonName).toString('utf8'));
  const cov = [];

  const modelName = pick(data, ['ModelName', 'Name']) || pick(data.Model ?? {}, ['Name', 'ModelName']) || 'model';
  const tables = pick(data, ['Tables', 'tables']) ?? pick(data.Model ?? {}, ['Tables']) ?? [];
  const rels = pick(data, ['Relationships', 'relationships']) ?? pick(data.Model ?? {}, ['Relationships']) ?? [];

  // --- size (bytes -> GB): prefer explicit table size, else sum column sizes ---
  let bytes = 0;
  for (const t of tables) {
    const tSize = pick(t, ['TableSize', 'TotalSize']);
    if (Number.isFinite(tSize)) { bytes += tSize; continue; }
    for (const c of pick(t, ['Columns', 'columns']) ?? []) {
      const cSize = pick(c, ['TotalSize', 'ColumnSize', 'DataSize']) ?? 0;
      if (Number.isFinite(cSize)) bytes += cSize;
    }
  }
  const sizeGB = round3(bytes / 1e9);
  cov.push(bytes > 0
    ? { field: 'models[].sizeGB', source: jsonName, value: `${sizeGB} GB (${tables.length} tables)` }
    : { field: 'models[].sizeGB', source: jsonName, value: 0, note: 'no per-table/column sizes found in this .vpax schema' });

  // --- bidirectional relationships ---
  const bidi = rels.filter(r => {
    const b = pick(r, ['CrossFilteringBehavior', 'crossFilteringBehavior', 'FilterDirection']);
    return r?.Bidirectional === true || b === 2 || /both/i.test(String(b ?? ''));
  }).length;
  cov.push({ field: 'models[].bidirectionalRels', source: jsonName, value: bidi });

  // --- auto date/time: Power BI generates LocalDateTable_/DateTableTemplate_ tables ---
  const autoDateTime = tables.some(t => /^(LocalDateTable_|DateTableTemplate_)/.test(String(pick(t, ['TableName', 'Name', 'name']) ?? '')));
  cov.push({ field: 'models[].autoDateTime', source: jsonName, value: autoDateTime });

  const model = {
    workspace: '(vpax import)',
    name: modelName,
    sizeGB,
    bidirectionalRels: bidi,
    autoDateTime,
    refreshFailRatePct: 0, // not available in a .vpax — comes from refresh history
  };
  return { models: [model], coverage: cov };
}
