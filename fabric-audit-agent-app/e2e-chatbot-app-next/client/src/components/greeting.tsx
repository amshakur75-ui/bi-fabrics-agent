import { motion } from 'framer-motion';

export const Greeting = () => {
  return (
    <div
      key="overview"
      className="mx-auto mb-6 flex size-full max-w-3xl flex-col items-center justify-center gap-3 px-4"
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
          👋 Hey! How can I help you today?
        </div>
        <div className="max-w-md text-center text-sm text-muted-foreground">
          Fabric &amp; Power BI capacity investigator — ask about throttling, top
          consumers, spikes, refresh contention, or a specific user.
        </div>
      </motion.div>
    </div>
  );
};
