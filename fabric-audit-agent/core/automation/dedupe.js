/**
 * Remove duplicate findings by key. Findings without a key are always kept
 * (so keyless test fakes are unaffected).
 * @param {object[]} findings
 * @returns {object[]}
 */
export function dedupe(findings) {
  const seen = new Set();
  const out = [];
  for (const f of findings) {
    if (f.key == null) { out.push(f); continue; }
    if (seen.has(f.key)) continue;
    seen.add(f.key);
    out.push(f);
  }
  return out;
}
