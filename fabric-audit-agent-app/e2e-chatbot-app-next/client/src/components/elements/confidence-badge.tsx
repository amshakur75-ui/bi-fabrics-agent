import { cn } from '@/lib/utils';

// U2: render the investigation's confidence level as a small colored badge instead of plain text.
export type ConfidenceLevel = 'validated' | 'likely' | 'inconclusive';

const STYLES: Record<ConfidenceLevel, string> = {
  validated: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  likely: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  inconclusive: 'bg-muted text-muted-foreground',
};
const LABELS: Record<ConfidenceLevel, string> = {
  validated: 'Validated',
  likely: 'Likely',
  inconclusive: 'Inconclusive',
};
const TITLES: Record<ConfidenceLevel, string> = {
  validated: 'Validated — directly confirmed by tool data and a verified formula/gate result.',
  likely: 'Likely — consistent with the evidence but not uniquely determined.',
  inconclusive: 'Inconclusive — insufficient evidence to favour any single cause.',
};

export function ConfidenceBadge({ level }: { level: ConfidenceLevel }) {
  return (
    <span
      title={TITLES[level]}
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium',
        STYLES[level],
      )}
    >
      <span aria-hidden="true">●</span>
      {LABELS[level]}
    </span>
  );
}
