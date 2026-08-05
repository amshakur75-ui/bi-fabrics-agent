import useSWR from 'swr';
import { fetcher } from '@/lib/utils';
import { ChatItem } from '@/components/sidebar-history-item';
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
} from '@/components/ui/sidebar';

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
  ack?: AlertAck | null;
  ticket?: AlertTicket | null;
};
type AlertsData = { chats: AlertChat[]; hasMore: boolean };

async function post(path: string, body?: unknown) {
  await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
}

// Ticket lifecycle is DERIVED, not stored: resolved (a human resolution note) > investigating
// (someone acked / snoozed it — they're on it) > open (freshly alerted, untouched).
function lifecycle(ack?: AlertAck | null): 'resolved' | 'investigating' | 'open' {
  if (ack?.status === 'resolved') return 'resolved';
  if (ack?.status === 'acked' || ack?.status === 'snoozed') return 'investigating';
  return 'open';
}

const STATUS_STYLE: Record<string, string> = {
  open: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  investigating: 'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  resolved: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
};

function firstDetectedLabel(iso?: string | null): string | null {
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

/**
 * Shared "Alerts" section: system-owned Tier-2 alert tickets (via /api/alerts). Each ticket shows
 * its derived status (open / investigating / resolved), severity, what/where, since-when, and
 * whether the condition is still firing. Open tickets get a Resolve control (requires a
 * "what changed" note, Step 8/9); resolved tickets show who resolved them + the note, with Reopen.
 * Hidden when there are none.
 */
export function SidebarAlerts() {
  const { data, mutate } = useSWR<AlertsData>('/api/alerts?limit=20', fetcher, {
    fallbackData: { chats: [], hasMore: false },
  });

  const chats = data?.chats ?? [];
  if (chats.length === 0) {
    return null;
  }

  const resolve = async (id: string) => {
    const note = window.prompt(
      'Resolve this alert — what changed / what did you find? (required)',
    );
    if (!note || !note.trim()) return;
    await post(`/api/alerts/${id}/resolve`, { note: note.trim() });
    mutate();
  };
  const reopen = async (id: string) => {
    await post(`/api/alerts/${id}/reopen`);
    mutate();
  };

  return (
    <SidebarGroup>
      <SidebarGroupLabel>Alerts</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {chats.map((chat) => {
            const status = lifecycle(chat.ack);
            const t = chat.ticket;
            const detected = firstDetectedLabel(t?.firstDetected);
            const where = t
              ? [t.resource, t.workspace].filter(Boolean).join(' · ')
              : '';
            return (
              <div key={chat.id} className="flex flex-col">
                <ChatItem
                  chat={chat as never}
                  isActive={false}
                  onDelete={() => {}}
                  setOpenMobile={() => {}}
                />
                {/* ticket meta row */}
                <div className="flex flex-wrap items-center gap-1 px-2 pl-8 text-[10px] text-muted-foreground">
                  <span
                    className={`rounded px-1 py-0.5 font-medium capitalize ${STATUS_STYLE[status]}`}
                  >
                    {status}
                  </span>
                  {t?.severity ? (
                    <span className="uppercase tracking-wide">
                      {t.severity === 'warn' ? '⚠️ warning' : t.severity}
                    </span>
                  ) : null}
                  {t?.currentlyActive === false && status !== 'resolved' ? (
                    <span className="text-muted-foreground/70">· inactive now</span>
                  ) : null}
                  {where ? <span className="truncate">· {where}</span> : null}
                  {detected ? (
                    <span className="text-muted-foreground/70">
                      · since {detected}
                    </span>
                  ) : null}
                </div>
                {/* resolve / reopen controls */}
                <div className="flex items-center gap-1 px-2 pb-1 pl-8 text-[11px] text-muted-foreground">
                  {chat.ack?.status === 'resolved' ? (
                    <>
                      <span
                        className="min-w-0 flex-1 truncate text-emerald-600 dark:text-emerald-400"
                        title={
                          chat.ack.resolutionNote
                            ? `${chat.ack.resolutionNote}${chat.ack.updatedBy ? ` — ${chat.ack.updatedBy}` : ''}`
                            : 'resolved'
                        }
                      >
                        ✓ resolved
                        {chat.ack.resolutionNote
                          ? ` — ${chat.ack.resolutionNote}`
                          : ''}
                      </span>
                      <button
                        type="button"
                        onClick={() => reopen(chat.id)}
                        className="shrink-0 rounded px-1 hover:bg-sidebar-accent hover:text-foreground"
                      >
                        reopen
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => resolve(chat.id)}
                      className="rounded px-1.5 py-0.5 hover:bg-sidebar-accent hover:text-foreground"
                    >
                      Resolve
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
