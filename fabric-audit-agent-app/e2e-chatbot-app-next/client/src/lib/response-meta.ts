import type { ConfidenceLevel } from '@/components/elements/confidence-badge';

// Detect the investigation's stated confidence level so it can render as a badge (U2).
const CONF_RE = /\bconfidence:?\s*(validated|likely|inconclusive)\b/i;

export function confidenceLevel(text: string): ConfidenceLevel | null {
  const m = (text || '').match(CONF_RE);
  return m ? (m[1].toLowerCase() as ConfidenceLevel) : null;
}

// Remove a STANDALONE "Confidence: X" line (its own line, possibly bulleted/bolded) once we show
// the badge, so it isn't duplicated. Inline prose mentions are left untouched.
const CONF_LINE_RE =
  /^[-*>\s]*\*{0,2}\s*confidence\*{0,2}:?\s*(validated|likely|inconclusive)\b.*$/i;

export function stripConfidenceLine(text: string): string {
  return (text || '')
    .split('\n')
    .filter((l) => !CONF_LINE_RE.test(l.trim()))
    .join('\n');
}

// Detect that a reply leans on the monitored CPU-time proxy, so a single scope indicator (U3) can
// stand in for the repeated per-figure caveat.
const PROXY_RE =
  /(cpu-?time)\s+(proxy|ranking)|monitored[- ]?cu|not\s+billed\s+cu|proxy\s+(for|ranking)/i;

export function mentionsProxy(text: string): boolean {
  return PROXY_RE.test(text || '');
}
