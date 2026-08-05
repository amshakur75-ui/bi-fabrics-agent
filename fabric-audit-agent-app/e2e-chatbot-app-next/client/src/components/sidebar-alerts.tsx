import useSWR from 'swr';
import { fetcher } from '@/lib/utils';
import { ChatItem } from '@/components/sidebar-history-item';
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
} from '@/components/ui/sidebar';

type AlertAck = { status: 'acked' | 'snoozed'; snoozeUntil: string | null };
type AlertChat = { id: string; title: string; ack?: AlertAck | null };
type AlertsData = { chats: AlertChat[]; hasMore: boolean };

async function post(path: string, method: 'POST' | 'DELETE', body?: unknown) {
  await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
}

/**
 * Shared "Alerts" section: system-owned, public Tier-2 alert conversations (via /api/alerts), each
 * with Ack / Snooze controls (6c) that suppress the 48h reminders. Hidden when there are none.
 */
export function SidebarAlerts() {
  const { data, mutate } = useSWR<AlertsData>('/api/alerts?limit=20', fetcher, {
    fallbackData: { chats: [], hasMore: false },
  });

  const chats = data?.chats ?? [];
  if (chats.length === 0) {
    return null;
  }

  const ack = async (id: string) => {
    await post(`/api/alerts/${id}/ack`, 'POST');
    mutate();
  };
  const snooze = async (id: string) => {
    await post(`/api/alerts/${id}/snooze`, 'POST', { days: 7 });
    mutate();
  };
  const clear = async (id: string) => {
    await post(`/api/alerts/${id}/ack`, 'DELETE');
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
                {chat.ack?.status === 'acked' ? (
                  <>
                    <span className="text-emerald-600 dark:text-emerald-400">
                      ✓ acked
                    </span>
                    <button
                      type="button"
                      onClick={() => clear(chat.id)}
                      className="ml-auto rounded px-1 hover:bg-sidebar-accent hover:text-foreground"
                    >
                      undo
                    </button>
                  </>
                ) : chat.ack?.status === 'snoozed' ? (
                  <>
                    <span className="text-amber-600 dark:text-amber-400">
                      💤 snoozed
                    </span>
                    <button
                      type="button"
                      onClick={() => clear(chat.id)}
                      className="ml-auto rounded px-1 hover:bg-sidebar-accent hover:text-foreground"
                    >
                      undo
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => ack(chat.id)}
                      className="rounded px-1.5 py-0.5 hover:bg-sidebar-accent hover:text-foreground"
                    >
                      Ack
                    </button>
                    <button
                      type="button"
                      onClick={() => snooze(chat.id)}
                      className="rounded px-1.5 py-0.5 hover:bg-sidebar-accent hover:text-foreground"
                    >
                      Snooze 7d
                    </button>
                  </>
                )}
              </div>
            </div>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
