import useSWR from 'swr';
import { fetcher } from '@/lib/utils';
import { ChatItem } from '@/components/sidebar-history-item';
import type { ChatHistory } from '@/components/sidebar-history';
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
} from '@/components/ui/sidebar';

/**
 * Shared "Alerts" section: system-owned, public Tier-2 alert conversations (via /api/alerts).
 * Hidden when there are none (or when chat history / DB is disabled).
 */
export function SidebarAlerts() {
  const { data } = useSWR<ChatHistory>('/api/alerts?limit=20', fetcher, {
    fallbackData: { chats: [], hasMore: false },
  });

  const chats = data?.chats ?? [];
  if (chats.length === 0) {
    return null;
  }

  return (
    <SidebarGroup>
      <SidebarGroupLabel>Alerts</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {chats.map((chat) => (
            <ChatItem
              key={chat.id}
              chat={chat}
              isActive={false}
              onDelete={() => {}}
              setOpenMobile={() => {}}
            />
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  );
}
