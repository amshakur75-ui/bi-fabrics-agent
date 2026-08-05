import { type ComponentProps, memo } from 'react';
import { DatabricksMessageCitationStreamdownIntegration } from '../databricks-message-citation';
import { Streamdown } from 'streamdown';
import { cn } from '@/lib/utils';

type ResponseProps = ComponentProps<typeof Streamdown>;

// Claude-style prose: colored/weighted headings, prominent bold, accent links, bordered zebra
// tables (this agent emits hourly CU% tables constantly), callout blockquotes, and tidy inline code.
// Applied via descendant utilities on the root so we never override Streamdown's components — links
// (citations) and fenced code blocks keep their own behavior.
const PROSE = cn(
  'flex flex-col gap-3 text-[15px] leading-relaxed',
  // headings
  '[&_h1]:mt-4 [&_h1]:mb-1 [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:tracking-tight',
  '[&_h2]:mt-4 [&_h2]:mb-1 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:tracking-tight',
  '[&_h3]:mt-3 [&_h3]:mb-0.5 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-[#3557c7]',
  // emphasis + links
  '[&_strong]:font-semibold [&_strong]:text-foreground',
  '[&_a]:font-medium [&_a]:text-[#4a6ad6] [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:text-[#2272b4]',
  // lists
  '[&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-1 [&_li]:marker:text-muted-foreground',
  // callouts
  '[&_blockquote]:my-2 [&_blockquote]:border-l-2 [&_blockquote]:border-[#5b7be8] [&_blockquote]:bg-[#5b7be8]/5 [&_blockquote]:py-1 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
  '[&_hr]:my-4 [&_hr]:border-border',
  // inline code (not fenced blocks)
  "[&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1.5 [&_:not(pre)>code]:py-0.5 [&_:not(pre)>code]:text-[0.85em] [&_:not(pre)>code]:font-mono",
  // tables — bordered, rounded, zebra, sticky-ish header tint
  '[&_table]:my-3 [&_table]:w-full [&_table]:border-collapse [&_table]:overflow-hidden [&_table]:rounded-lg [&_table]:border [&_table]:border-border [&_table]:text-sm',
  '[&_thead]:bg-muted/60 [&_th]:border-b [&_th]:border-border [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold',
  '[&_td]:border-b [&_td]:border-border/50 [&_td]:px-3 [&_td]:py-1.5 [&_td]:align-top',
  '[&_tbody_tr]:transition-colors hover:[&_tbody_tr]:bg-muted/30 [&_tbody_tr:last-child_td]:border-b-0',
);

export const Response = memo(
  ({ className, ...props }: ResponseProps) => {
    return (
      <Streamdown
        components={{
          a: DatabricksMessageCitationStreamdownIntegration,
        }}
        className={cn(PROSE, className)}
        {...props}
      />
    );
  },
  (prevProps, nextProps) => prevProps.children === nextProps.children,
);

Response.displayName = 'Response';
