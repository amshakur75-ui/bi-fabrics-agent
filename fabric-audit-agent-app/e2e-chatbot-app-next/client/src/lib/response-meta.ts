import type { ConfidenceLevel } from '@/components/elements/confidence-badge';

// Detect the investigation's stated confidence level so it can render as a badge (U2).
const CONF_RE = /\bconfidence:?\s*(validated|likely|inconclusive)\b/gi;

// Weakest first. An answer routinely states one level per claim ("validated" for the CU peak,
// "inconclusive" for the attribution); taking the FIRST match meant the reply could be badged
// "Validated" on the strength of a figure that wasn't the point of the question. One badge has to
// speak for the whole segment, so it speaks for the weakest claim in it.
const CONF_ORDER: ConfidenceLevel[] = ['inconclusive', 'likely', 'validated'];

function confidenceLevels(text: string): ConfidenceLevel[] {
  return [...(text || '').matchAll(CONF_RE)].map(
    (m) => m[1].toLowerCase() as ConfidenceLevel,
  );
}

export function confidenceLevel(text: string): ConfidenceLevel | null {
  const found = confidenceLevels(text);
  if (found.length === 0) return null;
  return CONF_ORDER.find((lvl) => found.includes(lvl)) ?? found[0];
}

// Remove a STANDALONE "Confidence: X" line (its own line, possibly bulleted/bolded) once we show
// the badge, so it isn't duplicated. Inline prose mentions are left untouched.
const CONF_LINE_RE =
  /^[-*>\s]*\*{0,2}\s*confidence\*{0,2}:?\s*(validated|likely|inconclusive)\b.*$/i;

export function stripConfidenceLine(text: string): string {
  const lines = (text || '').split('\n');
  const matches = lines.filter((l) => CONF_LINE_RE.test(l.trim()));
  // `.*$` eats the justification that follows the level on the line, so stripping several lines
  // deleted the reasoning for every claim while the badge could only report one of them. One line
  // is safe to hide (the badge says exactly what it said); two or more are not — keep them all in
  // the text and let the badge be a summary rather than a replacement.
  if (matches.length !== 1) return text || '';
  return lines.filter((l) => !CONF_LINE_RE.test(l.trim())).join('\n');
}

// Detect that a reply leans on the monitored CPU-time proxy, so a single scope indicator (U3) can
// stand in for the repeated per-figure caveat.
const PROXY_RE =
  /(cpu-?time)\s+(proxy|ranking)|monitored[- ]?cu|not\s+billed\s+cu|proxy\s+(for|ranking)/i;

// "Peak true CU% was 90.4% … not monitored CU" DENIES the proxy, and the bare `monitored cu`
// alternative above matched it anyway — stamping the amber "this figure is a proxy" chip on a
// true-CU answer. Negated mentions are blanked before the test rather than excluded with a
// lookbehind (Safari < 16.4 rejects lookbehind at parse time, which would take the whole bundle
// down). Only a directly adjacent negator counts: anything less clear-cut still raises the chip,
// because failing to flag a proxy figure is worse than flagging a true one.
const NEGATED_PROXY_RE = /\b(?:not|than|versus|vs\.?)\s+monitored[- ]?cu/gi;

export function mentionsProxy(text: string): boolean {
  return PROXY_RE.test((text || '').replace(NEGATED_PROXY_RE, ' '));
}
