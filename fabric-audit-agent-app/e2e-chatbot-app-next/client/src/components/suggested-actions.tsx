import { motion } from 'framer-motion';
import { memo, useState } from 'react';
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

// "Get a visual" sub-menu: concrete chart requests the agent renders via render_chart. The last
// option is open-ended — it hands off to the agent to ask what the user wants to see.
const VISUAL_OPTIONS: { label: string; prompt: string }[] = [
  { label: '📈 CU% today (line)', prompt: "Chart today's true CU% over time as a line chart." },
  { label: '📊 Top users (bar)', prompt: "Show a bar chart of today's top users by their share of monitored activity." },
  { label: '🍩 Item concentration (donut)', prompt: "Show a donut chart of today's activity concentration by item." },
  { label: '🔥 Biggest operations (bar)', prompt: "Chart today's biggest capacity operations by % of base as a bar chart." },
  { label: '➕ Something else…', prompt: "I'd like a visual on something else — ask me what I'd like to see, then build the chart." },
];

const CHIP_CLASS =
  'rounded-full border-border bg-background text-sm font-normal hover:bg-muted';

function PureSuggestedActions({ chatId, sendMessage }: SuggestedActionsProps) {
  const { chatHistoryEnabled } = useAppConfig();
  const [visualMode, setVisualMode] = useState(false);

  const send = (text: string) => {
    softNavigateToChatId(chatId, chatHistoryEnabled);
    sendMessage({ role: 'user', parts: [{ type: 'text', text }] });
  };

  if (visualMode) {
    return (
      <div className="flex w-full flex-col items-center gap-3">
        <div className="text-sm text-muted-foreground">
          Sure! What would you like a visual on?
        </div>
        <div className="flex w-full flex-wrap justify-center gap-2">
          {VISUAL_OPTIONS.map((opt, index) => (
            <motion.div
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 12 }}
              transition={{ delay: 0.03 * index }}
              key={opt.label}
            >
              <Suggestion
                suggestion={opt.prompt}
                variant="outline"
                onClick={(s) => send(s)}
                className={CHIP_CLASS}
              >
                {opt.label}
              </Suggestion>
            </motion.div>
          ))}
          <Suggestion
            suggestion=""
            variant="tertiary"
            onClick={() => setVisualMode(false)}
            className="rounded-full text-sm font-normal text-muted-foreground hover:bg-muted"
          >
            ← Back
          </Suggestion>
        </div>
      </div>
    );
  }

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
            onClick={(suggestion) => send(suggestion)}
            className={CHIP_CLASS}
          >
            {cap.label}
          </Suggestion>
        </motion.div>
      ))}
      {/* "Get a visual" — opens a card menu of chart options rather than sending immediately. */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 12 }}
        transition={{ delay: 0.03 * CAMP_CAPABILITIES.length }}
        key="get-a-visual"
      >
        <Suggestion
          suggestion=""
          variant="outline"
          onClick={() => setVisualMode(true)}
          className={CHIP_CLASS}
        >
          🎨 Get a visual
        </Suggestion>
      </motion.div>
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
