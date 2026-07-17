import { motion } from 'framer-motion';
import { memo } from 'react';
import type { UseChatHelpers } from '@ai-sdk/react';
import type { VisibilityType } from './visibility-selector';
import type { ChatMessage } from '@chat-template/core';
import { Suggestion } from './elements/suggestion';
import { softNavigateToChatId } from '@/lib/navigation';
import { useAppConfig } from '@/contexts/AppConfigContext';

interface SuggestedActionsProps {
  chatId: string;
  sendMessage: UseChatHelpers<ChatMessage>['sendMessage'];
  selectedVisibilityType: VisibilityType;
}

// Narrowed (2026-07-17) to the focused capability set the user asked for: the capacity-peaks
// lens (spikes / top operations), per-user investigation, query analysis, and issue diagnosis.
// `label` is the chip text; `prompt` is what gets sent to the agent when tapped. Prompts that need
// a specific target (a user, a query, a symptom) send a lead-in so the agent asks rather than
// guessing. The agent still supports every other capability if the user just asks; this only
// shapes the first-click surface.
const CAMP_CAPABILITIES: { label: string; prompt: string }[] = [
  { label: '⚡ Check for activity spikes', prompt: 'Check for activity spikes today — the top capacity operations above 250% of base, plus any refreshes that ran over 100%.' },
  { label: '🏆 Top capacity operations today', prompt: 'Show me the top capacity operations today above 250% of base — user, item, operation, duration, and % of base.' },
  { label: '👤 Look into a user', prompt: 'Look into a specific user — ask me which user, then show their operations, how often they recur, and whether other users hit the same item.' },
  { label: '🔎 Analyze a query', prompt: 'Analyze a specific expensive query — ask me which one, then explain what makes it costly and the fix.' },
  { label: '🩺 Diagnose an issue', prompt: 'Diagnose a capacity issue — ask me the symptom (slowness, refresh failures, or throttling), then run the decision tree.' },
];

function PureSuggestedActions({ chatId, sendMessage }: SuggestedActionsProps) {
  const { chatHistoryEnabled } = useAppConfig();

  return (
    <div
      data-testid="suggested-actions"
      className="flex w-full flex-wrap justify-center gap-2"
    >
      {CAMP_CAPABILITIES.map((cap, index) => (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 12 }}
          transition={{ delay: 0.03 * index }}
          key={cap.label}
        >
          <Suggestion
            suggestion={cap.prompt}
            variant="outline"
            onClick={(suggestion) => {
              softNavigateToChatId(chatId, chatHistoryEnabled);
              sendMessage({
                role: 'user',
                parts: [{ type: 'text', text: suggestion }],
              });
            }}
            className="rounded-full border-border bg-background text-sm font-normal hover:bg-muted"
          >
            {cap.label}
          </Suggestion>
        </motion.div>
      ))}
    </div>
  );
}

export const SuggestedActions = memo(
  PureSuggestedActions,
  (prevProps, nextProps) => {
    if (prevProps.chatId !== nextProps.chatId) return false;
    if (prevProps.selectedVisibilityType !== nextProps.selectedVisibilityType)
      return false;

    return true;
  },
);
