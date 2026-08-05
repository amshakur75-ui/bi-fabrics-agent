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

export function NotificationCenter() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<'open' | 'resolved'>('open');
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
    navigate(`/chat/${id}`);
  };

  return (
    <>
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
                        onClick={() => chatAbout(t.id)}
                        className="min-w-0 flex-1 truncate text-left text-sm font-medium hover:underline"
                        title={t.title}
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
