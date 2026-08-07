import { cn } from '@/lib/utils';
import { type HTMLAttributes, type ReactNode, useState } from 'react';

// Plan 5.5 — a read-only KQL/DAX display with lightweight syntax highlighting + a copy
// button, for U4 ("show me the query"). The plugin's editor.ts shipped a full Monaco editor;
// our agent IS the query interface, so a standalone offline editor makes no sense — what
// transfers is the read-only, highlighted, copyable view. This is a self-contained highlighter
// (no Monaco, no external grammar dependency): it tokenizes into React spans, so there is no
// dangerouslySetInnerHTML and no new bundle weight. Keyword list ported verbatim from
// editor.ts's KQL_KEYWORDS (kusto.tmLanguage.json origin).

// KQL keywords — ported from servers/kql-mcp/services/editor.ts (KQL_KEYWORDS).
const KQL_KEYWORDS = new Set([
  'where', 'project', 'extend', 'summarize', 'sort', 'order', 'by', 'asc', 'desc',
  'take', 'limit', 'top', 'join', 'union', 'let', 'on', 'kind', 'distinct', 'range',
  'count', 'countif', 'dcount', 'dcountif', 'sum', 'sumif', 'avg', 'avgif', 'min',
  'max', 'bin', 'floor', 'ago', 'now', 'startofday', 'startofweek', 'startofmonth',
  'datetime', 'todatetime', 'totimespan', 'toint', 'tolong', 'todouble', 'toreal',
  'tostring', 'tobool', 'todynamic', 'iff', 'case', 'and', 'or', 'not', 'has', 'has_any',
  'in', 'contains', 'startswith', 'endswith', 'between', 'split', 'parse', 'extract',
  'mv-expand', 'mv-apply', 'materialize', 'toscalar', 'broadcast', 'render', 'timechart',
  'barchart', 'piechart', 'scatterchart', 'make-series', 'evaluate', 'arg_max', 'arg_min',
  'take_any', 'print', 'find', 'search', 'set', 'datatable',
]);

// A minimal DAX keyword set so the same viewer can render a "show me the DAX" answer.
const DAX_KEYWORDS = new Set([
  'var', 'return', 'evaluate', 'define', 'measure', 'calculate', 'calculatetable',
  'filter', 'all', 'allexcept', 'allselected', 'values', 'distinct', 'sumx', 'averagex',
  'countrows', 'divide', 'if', 'switch', 'and', 'or', 'not', 'related', 'relatedtable',
  'earlier', 'rankx', 'topn', 'summarize', 'summarizecolumns', 'addcolumns', 'selectcolumns',
  'format', 'blank', 'hasonevalue', 'selectedvalue', 'treatas', 'union', 'crossjoin',
]);

type Token = { text: string; kind: 'kw' | 'str' | 'comment' | 'num' | 'pipe' | 'plain' };

// Pure tokenizer — deterministic, no regex backtracking traps. Splits one line into typed
// tokens so the renderer can style each span. Comments (// … end of line) and string literals
// ('…' / "…") win over keyword matching, exactly like the KQL grammar.
function tokenizeLine(line: string, keywords: Set<string>): Token[] {
  const tokens: Token[] = [];
  let i = 0;
  while (i < line.length) {
    const rest = line.slice(i);
    // Line comment — consumes to end of line.
    if (rest.startsWith('//')) {
      tokens.push({ text: rest, kind: 'comment' });
      break;
    }
    // String literal (single or double quote).
    const quote = line[i];
    if (quote === "'" || quote === '"') {
      let j = i + 1;
      while (j < line.length && line[j] !== quote) {
        j += 1;
      }
      tokens.push({ text: line.slice(i, Math.min(j + 1, line.length)), kind: 'str' });
      i = Math.min(j + 1, line.length);
      continue;
    }
    // The pipe operator that starts most KQL stages.
    if (quote === '|') {
      tokens.push({ text: '|', kind: 'pipe' });
      i += 1;
      continue;
    }
    // Word (identifier / keyword / number) — letters, digits, _ and - (mv-expand).
    const wordMatch = rest.match(/^[A-Za-z0-9_.-]+/);
    if (wordMatch) {
      const word = wordMatch[0];
      if (/^\d[\d.]*$/.test(word)) {
        tokens.push({ text: word, kind: 'num' });
      } else if (keywords.has(word.toLowerCase())) {
        tokens.push({ text: word, kind: 'kw' });
      } else {
        tokens.push({ text: word, kind: 'plain' });
      }
      i += word.length;
      continue;
    }
    // Any single other char (whitespace, punctuation) — passthrough.
    tokens.push({ text: line[i], kind: 'plain' });
    i += 1;
  }
  return tokens;
}

const KIND_CLASS: Record<Token['kind'], string> = {
  kw: 'text-[#288FC2] font-semibold', // Newell primary blue for keywords (brand parity, 5.6)
  str: 'text-[#16a34a]',
  comment: 'text-muted-foreground italic',
  num: 'text-[#9333ea]',
  pipe: 'text-[#01405C] font-semibold', // Newell navy for the stage pipe
  plain: 'text-foreground',
};

type KqlViewerProps = HTMLAttributes<HTMLDivElement> & {
  code: string;
  language?: 'kql' | 'dax';
  label?: string;
};

export const KqlViewer = ({
  code,
  language = 'kql',
  label,
  className,
  ...props
}: KqlViewerProps): ReactNode => {
  const [copied, setCopied] = useState(false);
  const keywords = language === 'dax' ? DAX_KEYWORDS : KQL_KEYWORDS;
  const lines = (code ?? '').split('\n');

  const onCopy = () => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(code ?? '').then(
        () => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        },
        () => {
          /* clipboard denied — leave the button idle, never throw at the user */
        },
      );
    }
  };

  return (
    <div
      className={cn(
        'relative w-full overflow-hidden rounded-md border bg-background text-foreground',
        className,
      )}
      {...props}
    >
      <div className="flex items-center justify-between border-b bg-muted/40 px-3 py-1.5">
        <span className="font-mono text-muted-foreground text-xs uppercase tracking-wide">
          {label ?? language.toUpperCase()}
        </span>
        <button
          type="button"
          onClick={onCopy}
          className="rounded px-2 py-0.5 text-muted-foreground text-xs hover:bg-muted hover:text-foreground"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
      <pre className="overflow-x-auto p-3 font-mono text-sm leading-relaxed">
        <code>
          {lines.map((line, li) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: read-only static render of a query
            <div key={li}>
              {tokenizeLine(line, keywords).map((tok, ti) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: token order is stable per render
                <span key={ti} className={KIND_CLASS[tok.kind]}>
                  {tok.text}
                </span>
              ))}
              {line.length === 0 ? ' ' : null}
            </div>
          ))}
        </code>
      </pre>
    </div>
  );
};

export default KqlViewer;
