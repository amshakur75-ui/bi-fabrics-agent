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

// Quick, readable summary of everything Newell can do — shown in the "What can I do?" popup.
const CAPABILITY_SUMMARY: { title: string; desc: string }[] = [
  { title: 'Capacity audit & verdict', desc: 'A full health check with a size-up vs. optimize recommendation.' },
  { title: 'Spikes & unusual activity', desc: 'Find unusual CU spikes and what drove them.' },
  { title: 'User activity', desc: 'What a specific user is running and their share of capacity CU.' },
  { title: 'Query & DAX analysis', desc: 'Inspect a specific query or a DAX measure for anti-patterns.' },
  { title: 'Model & workspace review', desc: 'Inspect a semantic model or review workspace usage.' },
  { title: 'Refresh history', desc: 'Dataset / semantic-model refresh outcomes over time.' },
  { title: 'Top consumers & concentration', desc: 'Who and what is driving the most CU (the noisy-neighbor check).' },
  { title: 'What changed', desc: 'Diff the current state against the last audit.' },
  { title: 'Diagnosis & forecast', desc: 'Root-cause a problem and forecast throttling risk.' },
  { title: 'Audit-log summary', desc: 'Summarize recent activity and anything notable.' },
  { title: 'Direct Fabric lookups', desc: 'List workspaces, items, and capacities directly.' },
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
        className="font-semibold text-lg md:text-xl text-center"
      >
        {greeting}
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
            <AlertDialogTitle>What Newell can help with</AlertDialogTitle>
            <AlertDialogDescription>
              I'm read-only — I investigate, explain, and advise, but I never change,
              refresh, scale, or delete anything.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul className="flex flex-col gap-3 py-2 text-sm">
            {CAPABILITY_SUMMARY.map((cap) => (
              <li key={cap.title}>
                <span className="font-medium text-foreground">{cap.title}</span>
                <span className="text-muted-foreground"> — {cap.desc}</span>
              </li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogCancel>Got it</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
};
