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
  // Part-7 read-path: chat-less tickets (alert_ticket.chat_id IS NULL — chat creation failed
  // upstream, but the finding still needs to surface). `id` is the incident_key for these, not a
  // navigable chat id.
  hasChat?: boolean;
  incidentKey?: string | null;
};
type AlertsData = { chats: AlertChat[] };

// Actionable issue types only — deliberately EXCLUDES the informational capacity-status signals
// (sustained early-warning, rate-of-change, the daily digest) so the center is a to-do list of real
// problems, not "capacity is N% today" repeated all day.
const ACTIONABLE = new Set([
  // Tier-2 real-time capacity + attribution
  'concentration',
  'cross_user',
  'blind_spot',
  'throttle',
  'pressure',
  'overage',
  // Hourly estate-wide sweep families (checkType = the finding family)
  'model',
  'report',
  'refresh',
  'security',
  'pipeline',
  'cost',
  'blast_radius',
  'pattern', // cross-workspace systemic patterns (B4)
  'sweep', // generic fallback
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

  // Chat-backed tickets keep hitting /api/alerts/:chatId/*; chat-less tickets (Part-7 read-path —
  // alert_ticket.chat_id IS NULL) have no chat to key off, so they use the incident-key-keyed
  // routes instead. `t.id` is the incident_key for those rows (see server GET /api/alerts).
  const resolve = async (t: AlertChat) => {
    const note = window.prompt(
      'Resolve this issue — what changed / what did you find? (required)',
    );
    if (!note || !note.trim()) return;
    const path =
      t.hasChat === false
        ? `/api/alerts/by-incident/${t.id}/resolve`
        : `/api/alerts/${t.id}/resolve`;
    await post(path, { note: note.trim() });
    mutate();
  };
  const reopen = async (t: AlertChat) => {
    const path =
      t.hasChat === false
        ? `/api/alerts/by-incident/${t.id}/reopen`
        : `/api/alerts/${t.id}/reopen`;
    await post(path);
    mutate();
  };
  // Builds the same kind of auto-investigation prompt the Python side's Teams deep-link uses
  // (fabric_audit_agent/automation/sweep_delivery.py:_investigate_query), anchored to when the
  // finding was first detected so the agent investigates the right time window.
  const investigateQuery = (t: AlertChat): string => {
    const what = t.ticket?.detail || t.title;
    const when = t.ticket?.firstDetected ?? t.createdAt;
    const anchor = when
      ? ` This was detected around ${when} — investigate the capacity and activity in that time window as your primary anchor, not the current moment.`
      : '';
    return `Investigate this finding and give me the root cause and the specific fix: ${what}.${anchor}`;
  };
  const chatAbout = (t: AlertChat) => {
    setOpen(false);
    setDetail(null);
    if (t.hasChat === false) {
      // No chat exists for this ticket — open a fresh chat pre-loaded with the investigation
      // prompt instead of navigating to /chat/:id (which would 404 for a null chat id).
      navigate(`/?query=${encodeURIComponent(investigateQuery(t))}`);
      return;
    }
    navigate(`/chat/${t.id}`);
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
            <div className="flex items-start justify-between gap-3 border-border border-b px-5 py-4">
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">
                  {detail.ticket?.severity === 'warn' ? '⚠️' : 'ℹ️'}
                </span>
                <h3 className="font-semibold text-sm">{detail.title}</h3>
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
                <p className="rounded-lg bg-muted/50 p-3 text-[13px] text-foreground/90 leading-relaxed">
                  {detail.ticket.detail}
                </p>
              ) : null}
              {detail.ack?.status === 'resolved' &&
              detail.ack.resolutionNote ? (
                <p className="text-[12px] text-emerald-600 dark:text-emerald-400">
                  ✓ Resolved — {detail.ack.resolutionNote}
                </p>
              ) : null}
            </div>
            <div className="flex items-center justify-end gap-2 border-border border-t px-5 py-3">
              {detail.ack?.status !== 'resolved' && (
                <button
                  type="button"
                  onClick={() => resolve(detail)}
                  className="rounded-md px-3 py-1.5 text-muted-foreground text-sm hover:bg-muted hover:text-foreground"
                >
                  Resolve
                </button>
              )}
              <button
                type="button"
                onClick={() => chatAbout(detail)}
                className="rounded-md bg-primary px-3 py-1.5 font-medium text-background text-sm hover:opacity-90"
              >
                Investigate in chat
              </button>
            </div>
          </div>
        </div>
      )}
      {open && (
        <div className="fixed right-4 bottom-20 z-50 flex max-h-[70vh] w-[22rem] flex-col overflow-hidden rounded-xl border border-border bg-background shadow-[var(--shadow-db-lg)]">
          <div className="flex items-center justify-between border-border border-b px-4 py-3">
            <div className="font-semibold text-sm">Issues to review</div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Close"
              className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            >
              ✕
            </button>
          </div>

          <div className="flex gap-1 border-border border-b px-2 py-2 text-xs">
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
              <div className="px-4 py-10 text-center text-muted-foreground text-sm">
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
                    className="flex flex-col gap-1 border-border/60 border-b px-4 py-3 last:border-b-0"
                  >
                    <div className="flex items-start gap-2">
                      <span className="mt-0.5 text-base leading-none">
                        {tk?.severity === 'warn' ? '⚠️' : 'ℹ️'}
                      </span>
                      <button
                        type="button"
                        onClick={() => setDetail(t)}
                        className="min-w-0 flex-1 truncate text-left font-medium text-sm hover:underline"
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
                    <div className="flex items-center gap-3 pt-1 pl-6 text-xs">
                      <button
                        type="button"
                        onClick={() => chatAbout(t)}
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
                            onClick={() => reopen(t)}
                            className="text-muted-foreground hover:text-foreground"
                          >
                            reopen
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => resolve(t)}
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
        className="fixed right-4 bottom-4 z-50 flex size-12 items-center justify-center rounded-full border border-border bg-background text-foreground shadow-[var(--shadow-db-lg)] transition hover:bg-muted"
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
          <span className="-right-1 -top-1 absolute flex min-w-5 items-center justify-center rounded-full bg-red-500 px-1 font-semibold text-[11px] text-white">
            {openTickets.length > 9 ? '9+' : openTickets.length}
          </span>
        )}
      </button>
    </>
  );
}
