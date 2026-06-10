/**
 * Column mapper: turn an arbitrary CSV table (headers + rows) into the agent's
 * `facts` shape, tolerating the many ways Fabric Capacity Metrics / VertiPaq
 * exports name their columns. Pure: no I/O. Emits a coverage report so callers
 * can show exactly which column fed which field (and what was NOT found).
 *
 * Field contract (must match the detectors):
 *   capacity: { tenant, capacityId, sku, memoryGB, peakCuPct, peakAt, throttleMinutes,
 *               refreshes:[{workspace,dataset,scheduledAt,durationMin,sizeGB}] }
 *   models:  [{ workspace, name, sizeGB, bidirectionalRels, autoDateTime, refreshFailRatePct }]
 *   reports: [{ workspace, name, visuals, mode, slowestVisualMs }]
 */

/** normalize a header for matching: lowercase, keep only a-z 0-9 % */
const norm = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9%]/g, '');

/** parse the first number out of a messy cell ("1,234 ms", "87%", "4.2 GB") */
export function num(v) {
  if (v == null) return NaN;
  const m = String(v).replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
  return m ? parseFloat(m[0]) : NaN;
}

const truthy = (v) => ['true', 'yes', '1', 'enabled', 'on', 'y'].includes(String(v ?? '').trim().toLowerCase());

const round3 = (x) => Math.round(x * 1000) / 1000;

/** Predicates that recognize a column by its normalized header. */
const MATCHERS = {
  sku:          (h) => h === 'sku' || h.includes('skuname') || (h.includes('sku') && !h.includes('skip')),
  capacityName: (h) => h.includes('capacity') && (h.includes('name') || h.includes('id') || h === 'capacity'),
  tenant:       (h) => h.includes('tenant'),
  memoryGB:     (h) => h.includes('capacitymemory') || h === 'memory' || h === 'memorygb' || h === 'ram' || h === 'ramgb',
  throttle:     (h) => h.includes('throttl') || h.includes('overload') || h.includes('interactivedelay'),
  time:         (h) => h.includes('timepoint') || h.includes('timestamp') || h.includes('datetime') || h === 'time' || h === 'date',
  workspace:    (h) => h.includes('workspace'),
  itemName:     (h) => h.includes('itemname') || h === 'item' || h.includes('datasetname') || h === 'dataset' || h.includes('semanticmodel') || (h.includes('model') && h.includes('name')) || h === 'name' || h.includes('reportname'),
  sizeGB:       (h) => h.includes('sizegb') || h.includes('modelsize') || h.includes('datasetsize') || h === 'size' || h.includes('dynamicmemory') || h.includes('totalsize'),
  durationMin:  (h) => h.includes('duration'),
  scheduledAt:  (h) => h.includes('scheduled') || h.includes('starttime') || h === 'start',
  bidi:         (h) => h.includes('bidirection') || h.includes('bidi') || h.includes('bothdirection'),
  autoDate:     (h) => h.includes('autodate') || h.includes('autodatetime'),
  failRate:     (h) => h.includes('failrate') || (h.includes('refresh') && h.includes('fail')) || h.includes('failurerate') || h.includes('errorrate'),
  visuals:      (h) => h.includes('visual') && !h.includes('ms') && !h.includes('slow'),
  mode:         (h) => h === 'mode' || h.includes('storagemode') || h.includes('connectionmode'),
  slowest:      (h) => h.includes('slowest') || h.includes('renderms') || h.includes('rendertime') || (h.includes('visual') && h.includes('ms')),
};

/** find the first header matching `key`, returns the ORIGINAL header text or null */
function find(headers, key) {
  for (const h of headers) if (MATCHERS[key](norm(h))) return h;
  return null;
}

/**
 * Find the capacity *utilization %* column, in priority order. Capacity Metrics
 * exports contain look-alikes that are NOT utilization: "100% in CU(s)" is the
 * baseline (CU-seconds == 100%), "CU % Limit" is the limit line, and
 * "Background %/Interactive %" are component splits. Prefer the real overall-usage
 * column ("Total CU Usage %", "% of base capacity", "Utilization") over those.
 * @param {string[]} headers
 * @returns {string|null}
 */
export function findCuPct(headers) {
  const tagged = headers.map(h => [h, norm(h)]);
  const tiers = [
    ([, h]) => h.includes('totalcuusage') || (h.includes('usage') && h.includes('cu') && h.includes('%')) || h.includes('utiliz'),
    ([, h]) => h.includes('%ofbase') || h.includes('ofbasecapacity'),
    ([, h]) => h.includes('cu') && (h.includes('%') || h.includes('pct') || h.includes('percent'))
      && !h.includes('limit') && !h.includes('100%in') && !h.includes('nonbillable')
      && !h.startsWith('background') && !h.startsWith('interactive') && !h.includes('autoscale'),
  ];
  for (const pred of tiers) { const hit = tagged.find(pred); if (hit) return hit[0]; }
  return null;
}

/** normalize a storage-mode cell to the detector's vocabulary */
function normMode(v) {
  const h = norm(v);
  if (h.includes('direct')) return 'DirectQuery';
  if (h.includes('import')) return 'Import';
  if (h.includes('dual')) return 'Dual';
  if (h.includes('live')) return 'LiveConnection';
  return String(v ?? '').trim();
}

/**
 * Map one table into a partial facts object + coverage report.
 * @param {string[]} headers
 * @param {Record<string,string>[]} rows
 * @returns {{capacity:object|null, models:object[], reports:object[], coverage:Array<{field:string,source:string|null,value:any,note?:string}>}}
 */
export function mapTable(headers, rows = []) {
  const cov = [];
  const cols = {};
  for (const key of Object.keys(MATCHERS)) cols[key] = find(headers, key);
  cols.cuPct = findCuPct(headers);

  const firstNonEmpty = (col) => {
    if (!col) return '';
    for (const r of rows) if (String(r[col] ?? '').trim() !== '') return String(r[col]).trim();
    return '';
  };
  const note = (looked) => `no column found (looked for: ${looked})`;

  // ---- capacity scalars ----
  let capacity = null;
  const hasCapacitySignal = cols.cuPct || cols.throttle || cols.sku || cols.capacityName || cols.memoryGB;
  if (hasCapacitySignal) {
    let peakCuPct = 0, peakAt = '';
    if (cols.cuPct) {
      for (const r of rows) {
        const v = num(r[cols.cuPct]);
        if (Number.isFinite(v) && v > peakCuPct) { peakCuPct = v; peakAt = cols.time ? String(r[cols.time] ?? '').trim() : ''; }
      }
      cov.push({ field: 'peakCuPct', source: cols.cuPct, value: `${peakCuPct}%`, note: peakCuPct > 1000 ? 'that looks like CU-seconds, not a %, double-check the column' : undefined });
    } else cov.push({ field: 'peakCuPct', source: null, value: 0, note: note('Total CU Usage %, utilization, % of base') });

    let throttleMinutes = 0;
    if (cols.throttle) {
      for (const r of rows) { const v = num(r[cols.throttle]); if (Number.isFinite(v)) throttleMinutes += v; }
      cov.push({ field: 'throttleMinutes', source: cols.throttle, value: throttleMinutes });
    } else cov.push({ field: 'throttleMinutes', source: null, value: 0, note: note('throttling, overloaded, rejected') });

    const sku = firstNonEmpty(cols.sku);
    const capacityId = firstNonEmpty(cols.capacityName) || sku || 'unnamed';
    const memoryGB = num(firstNonEmpty(cols.memoryGB));
    cov.push({ field: 'sku', source: cols.sku, value: sku || '(none)' });
    cov.push({ field: 'capacityId', source: cols.capacityName, value: capacityId });

    capacity = {
      tenant: firstNonEmpty(cols.tenant) || 'tenant',
      capacityId,
      sku: sku || '',
      memoryGB: Number.isFinite(memoryGB) ? memoryGB : 0,
      peakCuPct,
      peakAt,
      throttleMinutes,
      refreshes: [],
    };

    // ---- refreshes (one per item row) ----
    if (cols.itemName && (cols.sizeGB || cols.durationMin || cols.scheduledAt)) {
      for (const r of rows) {
        const dataset = String(r[cols.itemName] ?? '').trim();
        if (!dataset) continue;
        capacity.refreshes.push({
          workspace: cols.workspace ? String(r[cols.workspace] ?? '').trim() : '',
          dataset,
          scheduledAt: cols.scheduledAt ? String(r[cols.scheduledAt] ?? '').trim()
                      : cols.time ? String(r[cols.time] ?? '').trim() : '',
          durationMin: cols.durationMin ? (num(r[cols.durationMin]) || 0) : 0,
          sizeGB: cols.sizeGB ? (num(r[cols.sizeGB]) || 0) : 0,
        });
      }
      cov.push({ field: 'capacity.refreshes', source: cols.itemName, value: `${capacity.refreshes.length} row(s)` });
    }
  }

  // ---- models (only if model-shaped columns exist) ----
  const models = [];
  if (cols.itemName && (cols.bidi || cols.autoDate || cols.failRate)) {
    for (const r of rows) {
      const name = String(r[cols.itemName] ?? '').trim();
      if (!name) continue;
      models.push({
        workspace: cols.workspace ? String(r[cols.workspace] ?? '').trim() : '',
        name,
        sizeGB: cols.sizeGB ? (num(r[cols.sizeGB]) || 0) : 0,
        bidirectionalRels: cols.bidi ? (num(r[cols.bidi]) || 0) : 0,
        autoDateTime: cols.autoDate ? truthy(r[cols.autoDate]) : false,
        refreshFailRatePct: cols.failRate ? (num(r[cols.failRate]) || 0) : 0,
      });
    }
    cov.push({ field: 'models', source: cols.itemName, value: `${models.length} row(s)` });
  }

  // ---- reports (only if report-shaped columns exist) ----
  const reports = [];
  if (cols.itemName && (cols.visuals || cols.mode || cols.slowest)) {
    for (const r of rows) {
      const name = String(r[cols.itemName] ?? '').trim();
      if (!name) continue;
      reports.push({
        workspace: cols.workspace ? String(r[cols.workspace] ?? '').trim() : '',
        name,
        visuals: cols.visuals ? (num(r[cols.visuals]) || 0) : 0,
        mode: cols.mode ? normMode(r[cols.mode]) : 'Import',
        slowestVisualMs: cols.slowest ? (num(r[cols.slowest]) || 0) : 0,
      });
    }
    cov.push({ field: 'reports', source: cols.itemName, value: `${reports.length} row(s)` });
  }

  return { capacity, models, reports, coverage: cov };
}

/**
 * Merge several mapped parts into one facts object.
 * Capacity scalars: peakCuPct=max, throttleMinutes=sum, first non-empty for the rest,
 * refreshes concatenated. models/reports concatenated.
 * @param {Array<{capacity:object|null, models:object[], reports:object[]}>} parts
 */
export function mergeFacts(parts) {
  const facts = {};
  const caps = parts.map(p => p?.capacity).filter(Boolean);
  if (caps.length) {
    const capacity = { tenant: 'tenant', capacityId: '', sku: '', memoryGB: 0, peakCuPct: 0, peakAt: '', throttleMinutes: 0, refreshes: [] };
    for (const c of caps) {
      if (c.tenant && capacity.tenant === 'tenant') capacity.tenant = c.tenant;
      capacity.capacityId ||= c.capacityId;
      capacity.sku ||= c.sku;
      capacity.memoryGB ||= c.memoryGB;
      if ((c.peakCuPct || 0) > capacity.peakCuPct) { capacity.peakCuPct = c.peakCuPct; capacity.peakAt = c.peakAt || capacity.peakAt; }
      capacity.throttleMinutes += c.throttleMinutes || 0;
      capacity.refreshes.push(...(c.refreshes || []));
    }
    capacity.capacityId ||= 'unnamed';
    capacity.peakCuPct = round3(capacity.peakCuPct);
    facts.capacity = capacity;
  }
  const models = parts.flatMap(p => p?.models || []);
  if (models.length) facts.models = models;
  const reports = parts.flatMap(p => p?.reports || []);
  if (reports.length) facts.reports = reports;
  return facts;
}
