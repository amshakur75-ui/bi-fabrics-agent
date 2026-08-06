import { useState } from 'react';
import useSWR from 'swr';
import { useNavigate } from 'react-router-dom';
import { fetcher } from '@/lib/utils';

// Public, shared notification center (bottom-right). Shows the Tier-2 alert TICKETS — specific,
// user-actionable issues (who's driving an item, cross-user load, throttling) — NOT the repetitive
// capacity-status chatter. Open / Resolved tabs. Every authenticated user sees the same shared
// tickets (system-owned by fabric-audit-agent) and can resolve or open a chat about them.

type AlertAck = {
  status: 'acked' | 'snoozed' | 'resolved';
  resolutionNote?: string | null;
  updatedBy?: string | null;
};
type AlertTicket = {
  checkType?: string | null;
  severity?: string | null;
  resource?: string | null;
  workspace?: string | null;
  detail?: string | null;
  firstDetected?: string | null;
  currentlyActive?: boolean | null;
};
type AlertChat = {
  id: string;
  title: string;
  createdAt?: string | null;
  ack?: AlertAck | null;
  ticket?: AlertTicket | null;
};
type AlertsData = { chats: AlertChat[] };

// Actionable issue types only — deliberately EXCLUDES the informational capacity-status signals
// (sustained early-warning, rate-of-change, the daily digest) so the center is a to-do list of real
// problems, not "capacity is N% today" repeated all day.
const ACTIONABLE = new Set([
  'concentration',
  'cross_user',
  'blind_spot',
  'throttle',
  'pressure',
  'overage',
  'manual', // user-flagged tickets created from a chat conversation
]);

async function post(path: string, body?: unknown) {
  await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
}

function dateLabel(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function firstDetectedLabelSafe(c: AlertChat): string | null {
  return dateLabel(c.ticket?.firstDetected ?? c.createdAt);
}

export function NotificationCenter() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'open' | 'resolved'>('open');
  const [detail, setDetail] = useState<AlertChat | null>(null);
  const { data, mutate } = useSWR<AlertsData>('/api/alerts?limit=50', fetcher, {
    fallbackData: { chats: [] },
    refreshInterval: 60000,
  });

  const tickets = (data?.chats ?? []).filter(
    (c) => c.ticket && ACTIONABLE.has(c.ticket.checkType ?? ''),
  );
  const openTickets = tickets.filter((c) => c.ack?.status !== 'resolved');
  const resolvedTickets = tickets.filter((c) => c.ack?.status === 'resolved');
  const shown = tab === 'open' ? openTickets : resolvedTickets;

  const resolve = async (id: string) => {
    const note = window.prompt(
      'Resolve this issue — what changed / what did you find? (required)',
    );
    if (!note || !note.trim()) return;
    await post(`/api/alerts/${id}/resolve`, { note: note.trim() });
    mutate();
  };
  const reopen = async (id: string) => {
    await post(`/api/alerts/${id}/reopen`);
    mutate();
  };
  const chatAbout = (id: string) => {
    setOpen(false);
    setDetail(null);
    navigate(`/chat/${id}`);
  };

  return (
    <>
      {/* Hover-detail card: a quick-glance, centered view of one ticket — what it is, the problem,
          and enough context to act — without opening the full chat investigation. */}
      {detail && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-4"
          onClick={() => setDetail(null)}
        >
          <div
            className="w-full max-w-md overflow-hidden rounded-xl border border-border bg-background shadow-[var(--shadow-db-lg)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">
                  {detail.ticket?.severity === 'warn' ? '⚠️' : 'ℹ️'}
                </span>
                <h3 className="text-sm font-semibold">{detail.title}</h3>
              </div>
              <button
                type="button"
                onClick={() => setDetail(null)}
                aria-label="Close"
                className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                ✕
              </button>
            </div>
            <div className="space-y-3 px-5 py-4 text-sm">
              <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 text-[13px]">
                {detail.ticket?.checkType ? (
                  <>
                    <dt className="text-muted-foreground">Type</dt>
                    <dd className="capitalize">
                      {detail.ticket.checkType.replace('_', ' ')}
                    </dd>
                  </>
                ) : null}
                {detail.ticket?.resource ? (
                  <>
                    <dt className="text-muted-foreground">Item</dt>
                    <dd>{detail.ticket.resource}</dd>
                  </>
                ) : null}
                {detail.ticket?.workspace ? (
                  <>
                    <dt className="text-muted-foreground">Workspace</dt>
                    <dd>{detail.ticket.workspace}</dd>
                  </>
                ) : null}
                <dt className="text-muted-foreground">Status</dt>
                <dd className="capitalize">
                  {detail.ack?.status === 'resolved'
                    ? 'Resolved'
                    : detail.ticket?.currentlyActive === false
                      ? 'Open (inactive now)'
                      : 'Open'}
                </dd>
                {firstDetectedLabelSafe(detail) ? (
                  <>
                    <dt className="text-muted-foreground">Since</dt>
                    <dd>{firstDetectedLabelSafe(detail)}</dd>
                  </>
                ) : null}
              </dl>
              {detail.ticket?.detail ? (
                <p className="rounded-lg bg-muted/50 p-3 text-[13px] leading-relaxed text-foreground/90">
                  {detail.ticket.detail}
                </p>
              ) : null}
              {detail.ack?.status === 'resolved' && detail.ack.resolutionNote ? (
                <p className="text-[12px] text-emerald-600 dark:text-emerald-400">
                  ✓ Resolved — {detail.ack.resolutionNote}
                </p>
              ) : null}
            </div>
            <div className="flex items-center justify-end gap-2 border-t border-border px-5 py-3">
              {detail.ack?.status !== 'resolved' && (
                <button
                  type="button"
                  onClick={() => resolve(detail.id)}
                  className="rounded-md px-3 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  Resolve
                </button>
              )}
              <button
                type="button"
                onClick={() => chatAbout(detail.id)}
                className="rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-background hover:opacity-90"
              >
                Investigate in chat
              </button>
            </div>
          </div>
        </div>
      )}
      {open && (
        <div className="fixed bottom-20 right-4 z-50 flex max-h-[70vh] w-[22rem] flex-col overflow-hidden rounded-xl border border-border bg-background shadow-[var(--shadow-db-lg)]">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <div className="text-sm font-semibold">Issues to review</div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              ✕
            </button>
          </div>

          <div className="flex gap-1 border-b border-border px-2 py-2 text-xs">
            <button
              type="button"
              onClick={() => setTab('open')}
              className={`rounded-full px-3 py-1 font-medium ${tab === 'open' ? 'bg-amber-500/15 text-amber-700 dark:text-amber-300' : 'text-muted-foreground hover:bg-muted'}`}
            >
              Open ({openTickets.length})
            </button>
            <button
              type="button"
              onClick={() => setTab('resolved')}
              className={`rounded-full px-3 py-1 font-medium ${tab === 'resolved' ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300' : 'text-muted-foreground hover:bg-muted'}`}
            >
              Resolved ({resolvedTickets.length})
            </button>
          </div>

          <div className="flex-1 overflow-y-auto">
            {shown.length === 0 ? (
              <div className="px-4 py-10 text-center text-sm text-muted-foreground">
                {tab === 'open'
                  ? 'No open issues right now. 🎉'
                  : 'Nothing resolved yet.'}
              </div>
            ) : (
              shown.map((t) => {
                const tk = t.ticket;
                const where = tk
                  ? [tk.resource, tk.workspace].filter(Boolean).join(' · ')
                  : '';
                const when = dateLabel(tk?.firstDetected ?? t.createdAt);
                return (
                  <div
                    key={t.id}
                    className="flex flex-col gap-1 border-b border-border/60 px-4 py-3 last:border-b-0"
                  >
                    <div className="flex items-start gap-2">
                      <span className="mt-0.5 text-base leading-none">
                        {tk?.severity === 'warn' ? '⚠️' : 'ℹ️'}
                      </span>
                      <button
                        type="button"
                        onClick={() => setDetail(t)}
                        className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:underline"
                        title="Open details"
                      >
                        {t.title}
                      </button>
                    </div>
                    <div className="flex flex-wrap items-center gap-x-2 pl-6 text-[11px] text-muted-foreground">
                      {where ? <span className="truncate">{where}</span> : null}
                      {tk?.currentlyActive === false &&
                      t.ack?.status !== 'resolved' ? (
                        <span>· inactive now</span>
                      ) : null}
                      {when ? <span>· since {when}</span> : null}
                    </div>
                    <div className="flex items-center gap-3 pl-6 pt-1 text-xs">
                      <button
                        type="button"
                        onClick={() => chatAbout(t.id)}
                        className="text-sky-600 hover:underline dark:text-sky-400"
                      >
                        Chat about it
                      </button>
                      {t.ack?.status === 'resolved' ? (
                        <>
                          <span
                            className="min-w-0 flex-1 truncate text-emerald-600 dark:text-emerald-400"
                            title={t.ack.resolutionNote ?? 'resolved'}
                          >
                            ✓ {t.ack.resolutionNote ?? 'resolved'}
                          </span>
                          <button
                            type="button"
                            onClick={() => reopen(t.id)}
                            className="text-muted-foreground hover:text-foreground"
                          >
                            reopen
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => resolve(t.id)}
                          className="font-medium text-foreground hover:underline"
                        >
                          Resolve
                        </button>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label="Open notifications"
        className="fixed bottom-4 right-4 z-50 flex size-12 items-center justify-center rounded-full border border-border bg-background text-foreground shadow-[var(--shadow-db-lg)] transition hover:bg-muted"
      >
        <svg
          viewBox="0 0 24 24"
          className="size-5"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.8}
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" />
        </svg>
        {openTickets.length > 0 && (
          <span className="absolute -right-1 -top-1 flex min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-[11px] font-semibold text-white">
            {openTickets.length > 9 ? '9+' : openTickets.length}
          </span>
        )}
      </button>
    </>
  );
}
