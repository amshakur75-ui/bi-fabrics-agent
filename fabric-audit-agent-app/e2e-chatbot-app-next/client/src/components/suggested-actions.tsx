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

// CAMP capability bubbles. `label` is the chip text; `prompt` is what gets sent to the agent when
// tapped. Prompts that need a specific (a user, a model, a query) send a lead-in so the agent asks
// for it rather than guessing.
const CAMP_CAPABILITIES: { label: string; prompt: string }[] = [
  { label: '📊 Run a capacity audit', prompt: 'Run a Fabric capacity audit and give me the verdict.' },
  { label: '⚡ Check for activity spikes', prompt: 'Check for unusual activity spikes on the capacity.' },
  { label: '👤 Look into a user', prompt: "Look into a specific user's activity — ask me which user." },
  { label: '🔎 Analyze a query', prompt: 'Analyze a specific query — ask me which query to look at.' },
  { label: '🧩 Inspect a model', prompt: 'Inspect a specific semantic model — ask me which model.' },
  { label: '🗂️ Review workspace usage', prompt: 'Review workspace usage across the capacity.' },
  { label: '🔄 Dataset refresh history', prompt: 'Check dataset refresh history — ask me which dataset.' },
  { label: '🏆 Top resource consumers', prompt: 'Identify the top resource consumers on the capacity.' },
  { label: '📝 Summarize audit logs', prompt: 'Summarize the recent audit logs and anything notable.' },
  { label: '🔀 What changed since last run', prompt: 'What changed on the capacity since the last audit?' },
  { label: '🩺 Diagnose an issue', prompt: 'Diagnose a capacity issue — ask me for the symptom if you need it.' },
  { label: '📈 Forecast throttling risk', prompt: 'Forecast whether and when the capacity is likely to throttle.' },
  { label: '🧮 Analyze a DAX measure', prompt: 'Analyze a DAX measure for anti-patterns — ask me to paste it.' },
  { label: '🗺️ List capacities & workspaces', prompt: 'List the Fabric capacities and workspaces you can see.' },
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