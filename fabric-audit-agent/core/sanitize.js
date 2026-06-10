const SAFE_STRING_KEYS = new Set(['sku', 'status', 'time']); // enum-like, not identifying

/** Strip identifiers from one evidence object: keep numbers/booleans + safe enums; arrays -> count; drop other strings. */
export function sanitizeEvidence(evidence = {}) {
  if (evidence.sensitive === true || evidence.sensitivityLabel) return { redacted: true };
  const out = {};
  for (const [k, v] of Object.entries(evidence)) {
    if (typeof v === 'number' || typeof v === 'boolean') out[k] = v;
    else if (Array.isArray(v)) out[`${k}Count`] = v.length;
    else if (typeof v === 'string' && SAFE_STRING_KEYS.has(k)) out[k] = v;
    // else: drop (dataset names, sources, free text, timestamps)
  }
  return out;
}

/**
 * Build the external-safe payload from flags: only an index, the flag type, and
 * sanitized numeric evidence. No resource, no `what`, no names.
 * @param {object[]} flags
 * @returns {Array<{id:number, type:string, evidence:object}>}
 */
export function sanitize(flags) {
  return flags.map((f, i) => ({ id: i, type: f.type, evidence: sanitizeEvidence(f.evidence) }));
}
