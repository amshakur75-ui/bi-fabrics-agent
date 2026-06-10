/**
 * Minimal, dependency-free CSV parser (RFC-4180-ish).
 * Handles quoted fields, embedded commas / quotes / newlines, CRLF or LF,
 * and a leading UTF-8 BOM. Values are trimmed. Header row keys each row object.
 *
 * @param {string} text
 * @returns {{ headers: string[], rows: Record<string,string>[] }}
 */
export function parseCsv(text) {
  if (typeof text !== 'string' || text.length === 0) return { headers: [], rows: [] };
  const s = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text; // strip BOM

  /** @type {string[][]} */
  const records = [];
  let record = [];
  let field = '';
  let inQuotes = false;
  let started = false; // current record has seen any char/field separator
  const n = s.length;

  const endField = () => { record.push(field); field = ''; };
  const endRecord = () => { records.push(record); record = []; started = false; };

  for (let i = 0; i < n; i++) {
    const ch = s[i];
    if (inQuotes) {
      if (ch === '"') {
        if (s[i + 1] === '"') { field += '"'; i++; }   // escaped quote
        else inQuotes = false;
      } else field += ch;
      continue;
    }
    if (ch === '"') { inQuotes = true; started = true; }
    else if (ch === ',') { endField(); started = true; }
    else if (ch === '\r') { /* ignore; LF ends the record */ }
    else if (ch === '\n') { endField(); endRecord(); }
    else { field += ch; started = true; }
  }
  if (started || field.length > 0 || record.length > 0) { endField(); endRecord(); }

  // Drop blank records (single empty cell).
  const real = records.filter(r => !(r.length === 1 && r[0].trim() === ''));
  if (real.length === 0) return { headers: [], rows: [] };

  const headers = real[0].map(h => h.trim());
  const rows = real.slice(1).map(cells => {
    /** @type {Record<string,string>} */
    const obj = {};
    headers.forEach((h, idx) => { obj[h] = (cells[idx] ?? '').trim(); });
    return obj;
  });
  return { headers, rows };
}
