import type { RenderChartOutput } from '@/components/elements/chart';

/**
 * A rendered message is a sequence of text runs and charts. The backend appends render_chart specs
 * to the answer text as ```fabric-chart fenced blocks (agent_server/chart_stream.py); we split those
 * out so each renders as the real recharts <Chart> instead of a code block full of JSON.
 */
export type MessageSegment =
  | { type: 'text'; text: string }
  | { type: 'chart'; output: RenderChartOutput };

const FENCE_TAG = 'fabric-chart';
// ```fabric-chart\n<single-line JSON>\n```  — JSON is emitted compact (no newlines) by json.dumps.
const FENCE_RE = /```fabric-chart[ \t]*\r?\n([\s\S]*?)\r?\n```/g;

/**
 * Split answer text into text + chart segments. Text with no chart fence returns a single text
 * segment. A malformed fence is kept as literal text (nothing is silently dropped).
 */
export function splitChartSegments(text: string): MessageSegment[] {
  const src = text ?? '';
  if (!src.includes(FENCE_TAG)) {
    return [{ type: 'text', text: src }];
  }

  const segments: MessageSegment[] = [];
  let lastIndex = 0;
  FENCE_RE.lastIndex = 0;
  let match: RegExpExecArray | null = FENCE_RE.exec(src);
  while (match !== null) {
    const before = src.slice(lastIndex, match.index);
    if (before.trim().length > 0) {
      segments.push({ type: 'text', text: before });
    }
    try {
      const output = JSON.parse(match[1].trim()) as RenderChartOutput;
      segments.push({ type: 'chart', output });
    } catch {
      // malformed JSON — keep the raw block as text rather than dropping it
      segments.push({ type: 'text', text: match[0] });
    }
    lastIndex = match.index + match[0].length;
    match = FENCE_RE.exec(src);
  }

  const after = src.slice(lastIndex);
  if (after.trim().length > 0) {
    segments.push({ type: 'text', text: after });
  }

  return segments.length > 0 ? segments : [{ type: 'text', text: src }];
}
