import { motion } from 'framer-motion';
import { useState } from 'react';
import { useAppConfig } from '@/contexts/AppConfigContext';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

// Quick, readable summary of what Newell focuses on -- shown in the "What can I do?" popup.
// Narrowed 2026-07-14 to the two lanes Sowmya approved: (1) top CU consumers, (2) pattern
// analysis. The "Beyond the focus" section makes it explicit the agent still handles the wider
// capability set on demand -- the narrowing is about what we lead with, not what we can do.
const CAPABILITY_SUMMARY: { title: string; desc: string }[] = [
  { title: '🏆 Top CU consumers', desc: 'Who and what is driving the most capacity right now -- users, items, and the queries behind them.' },
  { title: '📈 Pattern analysis', desc: 'Recurring spikes, day-of-week rhythms, and workloads that keep hitting the capacity the same way.' },
  { title: '👤 Per-user investigation', desc: 'Deep dive on one user -- their timeline, query anatomy, and how they compare to peers on the same item.' },
  { title: '⚡ Throttle root-cause', desc: 'When the capacity throttles, confirm it, attribute it to an item/user, and name the specific fix.' },
  { title: '🔀 What changed', desc: 'Diff today\'s reading against yesterday -- new findings, resolved findings, and shifts in the top consumers.' },
];

const BEYOND_FOCUS: { title: string; desc: string }[] = [
  { title: 'Full capacity audit', desc: 'Estate-wide health check with a size-up vs. optimize recommendation.' },
  { title: 'Query & DAX analysis', desc: 'Inspect a specific query or DAX measure for anti-patterns.' },
  { title: 'Refresh history & schedules', desc: 'Dataset refresh outcomes, schedules, and contention checks.' },
  { title: 'Direct Fabric lookups', desc: 'List workspaces, items, capacities, and dataset schedules directly.' },
  { title: 'Ad-hoc read-only KQL', desc: 'Run bespoke read-only Kusto queries against the capacity events store.' },
];

export const Greeting = () => {
  const { greeting } = useAppConfig();
  const [open, setOpen] = useState(false);

  return (
    <div
      key="overview"
      className="mx-auto flex size-full max-w-3xl flex-col items-center justify-center gap-2 px-4 mb-6"
    >
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 10 }}
        className="flex flex-col items-center gap-3"
      >
        <span
          aria-hidden="true"
          className="flex size-12 items-center justify-center rounded-2xl text-white shadow-sm"
          style={{ background: 'linear-gradient(135deg, #5b7be8, #2272b4)' }}
        >
          <svg
            viewBox="0 0 24 24"
            className="size-7"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.7}
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 3.4v2.2" />
            <circle cx="12" cy="2.4" r="1.1" fill="currentColor" stroke="none" />
            <rect x="5" y="5.6" width="14" height="12" rx="3.4" />
            <path d="M5 10.6H3.4M19 10.6h1.6" />
            <circle cx="9.6" cy="11.6" r="1.25" fill="currentColor" stroke="none" />
            <circle cx="14.4" cy="11.6" r="1.25" fill="currentColor" stroke="none" />
            <path d="M9.8 14.6h4.4" />
          </svg>
        </span>
        <div className="text-center text-lg font-semibold md:text-xl">
          {greeting}
        </div>
        <div className="max-w-md text-center text-sm text-muted-foreground">
          Read-only Fabric &amp; Power BI capacity investigator — ask about throttling,
          top consumers, spikes, refresh contention, or a specific user.
        </div>
      </motion.div>

      <Button
        variant="link"
        size="sm"
        className="text-muted-foreground"
        onClick={() => setOpen(true)}
      >
        ℹ️ What can I do?
      </Button>

      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>What Newell focuses on</AlertDialogTitle>
            <AlertDialogDescription>
              I'm read-only — I investigate, explain, and advise, but I never change,
              refresh, scale, or delete anything.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="py-2 text-sm">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Primary focus
            </p>
            <ul className="flex flex-col gap-3">
              {CAPABILITY_SUMMARY.map((cap) => (
                <li key={cap.title}>
                  <span className="font-medium text-foreground">{cap.title}</span>
                  <span className="text-muted-foreground"> — {cap.desc}</span>
                </li>
              ))}
            </ul>
            <p className="mt-5 mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Beyond the focus (just ask)
            </p>
            <ul className="flex flex-col gap-3">
              {BEYOND_FOCUS.map((cap) => (
                <li key={cap.title}>
                  <span className="font-medium text-foreground">{cap.title}</span>
                  <span className="text-muted-foreground"> — {cap.desc}</span>
                </li>
              ))}
            </ul>
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Got it</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};
