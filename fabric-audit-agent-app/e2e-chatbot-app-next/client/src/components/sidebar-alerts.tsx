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
type AlertChat = { id: string; title: string; ack?: AlertAck | null };
type AlertsData = { chats: AlertChat[]; hasMore: boolean };

async function post(path: string, body?: unknown) {
  await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
}

/**
 * Shared "Alerts" section: system-owned Tier-2 alert tickets (via /api/alerts). Each open ticket
 * gets a Resolve control (requires a "what changed" note, Step 8/9); resolved tickets show who
 * resolved them + the note, with Reopen. Hidden when there are none.
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
          {chats.map((chat) => (
            <div key={chat.id} className="flex flex-col">
              <ChatItem
                chat={chat as never}
                isActive={false}
                onDelete={() => {}}
                setOpenMobile={() => {}}
              />
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
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
