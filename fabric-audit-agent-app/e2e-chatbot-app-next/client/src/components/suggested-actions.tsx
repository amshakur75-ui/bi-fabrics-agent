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

// Narrowed to Sowmya's approved focus (2026-07-14 meeting): "top CU consumers and pattern
// analysis." The chips lead with those two lanes and their natural follow-ups (per-user
// investigation, throttle root-cause, weekly recurrence). The underlying agent still supports
// every capability -- users can still ask any question freely; this only shapes the FIRST
// nudge so the demo/onboarding matches the narrowed use case.
const CAMP_CAPABILITIES: { label: string; prompt: string }[] = [
  { label: '🏆 Top CU consumers right now', prompt: 'Show me the top CU consumers on the capacity right now — users and items — and what they were doing.' },
  { label: '👤 Investigate a specific user', prompt: "Investigate a specific user's CU consumption in depth — ask me which user, then dig into their queries, timeline, and peers." },
  { label: '📊 What\'s driving the current peak', prompt: 'What\'s driving the current CU peak? Attribute it to the item, then to the user, then to the query pattern.' },
  { label: '📈 Recurring spike patterns', prompt: 'Look for recurring CU spike patterns over the last 14 days — same time of day, same day of week, same user, or same item.' },
  { label: '🔀 What changed since yesterday', prompt: 'What changed on the capacity since yesterday\'s reading? Peak, top consumers, new findings, resolved findings.' },
  { label: '⚡ Investigate throttle event', prompt: 'Investigate the most recent throttle event — confirm it, attribute it, identify the top offenders, and give me the fix.' },
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