import { cn } from '@/lib/utils';

// U3: a small, consistent chip marking whether the figures in a reply are the monitored CPU-time
// PROXY vs true capacity CU — shown once per response so the distinction stays visible without a
// full caveat sentence repeated on every number. Hover for the full explanation.
export function ScopeIndicator({ isProxy }: { isProxy: boolean }) {
  if (isProxy) {
    return (
      <span
        title="Monitored CPU-time PROXY — a ranking of who/what to look at, NOT billed capacity CU. True per-user/per-item CU is not exposed by any API; the gap can exceed 10× for XMLA/DirectQuery work."
        className={cn(
          'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium',
          'bg-amber-500/15 text-amber-700 dark:text-amber-300',
        )}
      >
        <span aria-hidden="true">≈</span>
        monitored proxy
      </span>
    );
  }
  return (
    <span
      title="True CU% — capacity ground truth, read from the Real-Time Hub Capacity Events stream."
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium',
        'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
      )}
    >
      <span aria-hidden="true">✓</span>
      true CU
    </span>
  );
}
