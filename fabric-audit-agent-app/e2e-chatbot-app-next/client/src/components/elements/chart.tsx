import { cn } from '@/lib/utils';
import { useMemo, type HTMLAttributes } from 'react';
import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts';
import { BarChartIcon } from '../icons/BarChartIcon';
import { ChartLineIcon } from '../icons/ChartLineIcon';
import { PieChartIcon } from '../icons/PieChartIcon';

// ---------- Data contract (mirrors Python render_chart tool output) ----------

export type ChartDataPoint = {
  x: string | number;
  y: number;
};

export type ChartSeries = {
  name: string;
  data: ChartDataPoint[];
};

export type ChartSpec = {
  chartType: 'line' | 'bar' | 'grouped-bar' | 'stacked-bar' | 'pie' | 'donut';
  title: string;
  series: ChartSeries[];
  axisLabels: { x: string; y: string };
  sourceScope: 'capacity' | 'item' | 'user';
  isProxy: boolean;
};

export type RenderChartOutput =
  | { chart: ChartSpec; fallback?: never }
  | { fallback: true; text: string; reason: string; totalPoints: number; chart?: never }
  | { error: string; chart?: never; fallback?: never };

// ---------- Color palette ----------

const COLORS = [
  '#2563eb', // blue-600
  '#16a34a', // green-600
  '#ea580c', // orange-600
  '#9333ea', // purple-600
  '#dc2626', // red-600
  '#0891b2', // cyan-600
  '#ca8a04', // yellow-600
  '#db2777', // pink-600
];

// ---------- Helpers ----------

function chartIcon(chartType: ChartSpec['chartType']) {
  switch (chartType) {
    case 'line':
      return <ChartLineIcon className="size-4 shrink-0 text-muted-foreground" />;
    case 'pie':
    case 'donut':
      return <PieChartIcon className="size-4 shrink-0 text-muted-foreground" />;
    default:
      return <BarChartIcon className="size-4 shrink-0 text-muted-foreground" />;
  }
}

/**
 * Flatten multi-series data into recharts' row-major format:
 * [{x: "Sales", "Series A": 100, "Series B": 200}, ...]
 */
function flattenSeries(series: ChartSeries[]): Record<string, unknown>[] {
  const byX = new Map<string | number, Record<string, unknown>>();
  for (const s of series) {
    for (const pt of s.data) {
      const existing = byX.get(pt.x) || { x: pt.x };
      existing[s.name] = pt.y;
      byX.set(pt.x, existing);
    }
  }
  return Array.from(byX.values());
}

/**
 * Flatten to pie-chart format: [{name: "Sales", value: 1200}, ...]
 * Uses the first series only (pie charts are single-series).
 */
function flattenPie(series: ChartSeries[]): { name: string; value: number }[] {
  const s = series[0];
  if (!s) return [];
  return s.data.map((pt) => ({
    name: String(pt.x),
    value: pt.y,
  }));
}

// ---------- Sub-components ----------

function ProxyBadge() {
  return (
    <div
      className="mt-2 flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
      data-testid="proxy-badge"
    >
      <svg
        className="size-3.5 shrink-0"
        viewBox="0 0 16 16"
        fill="currentColor"
      >
        <path d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1Zm-.75 3.75a.75.75 0 0 1 1.5 0v4a.75.75 0 0 1-1.5 0v-4Zm.75 7a.75.75 0 1 1 0-1.5.75.75 0 0 1 0 1.5Z" />
      </svg>
      <span>
        Proxy-attributed data — per-user CU figures are approximations, not
        authoritative capacity measurements.
      </span>
    </div>
  );
}

function ChartFallback({ text }: { text: string }) {
  return (
    <div className="rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
      {text}
    </div>
  );
}

function BarChartRenderer({
  data,
  series,
  axisLabels,
  stacked,
}: {
  data: Record<string, unknown>[];
  series: ChartSeries[];
  axisLabels: { x: string; y: string };
  stacked?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis
          dataKey="x"
          label={
            axisLabels.x
              ? { value: axisLabels.x, position: 'insideBottom', offset: -2 }
              : undefined
          }
          className="text-xs"
          tick={{ fontSize: 11 }}
        />
        <YAxis
          label={
            axisLabels.y
              ? {
                  value: axisLabels.y,
                  angle: -90,
                  position: 'insideLeft',
                  offset: 0,
                }
              : undefined
          }
          className="text-xs"
          tick={{ fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'var(--color-popover, #fff)',
            border: '1px solid var(--color-border, #e5e7eb)',
            borderRadius: '0.375rem',
            fontSize: '0.75rem',
          }}
        />
        {series.length > 1 && <Legend wrapperStyle={{ fontSize: '0.75rem' }} />}
        {series.map((s, i) => (
          <Bar
            key={s.name}
            dataKey={s.name}
            fill={COLORS[i % COLORS.length]}
            stackId={stacked ? 'stack' : undefined}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function LineChartRenderer({
  data,
  series,
  axisLabels,
}: {
  data: Record<string, unknown>[];
  series: ChartSeries[];
  axisLabels: { x: string; y: string };
}) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <LineChart
        data={data}
        margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
      >
        <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
        <XAxis
          dataKey="x"
          label={
            axisLabels.x
              ? { value: axisLabels.x, position: 'insideBottom', offset: -2 }
              : undefined
          }
          className="text-xs"
          tick={{ fontSize: 11 }}
        />
        <YAxis
          label={
            axisLabels.y
              ? {
                  value: axisLabels.y,
                  angle: -90,
                  position: 'insideLeft',
                  offset: 0,
                }
              : undefined
          }
          className="text-xs"
          tick={{ fontSize: 11 }}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: 'var(--color-popover, #fff)',
            border: '1px solid var(--color-border, #e5e7eb)',
            borderRadius: '0.375rem',
            fontSize: '0.75rem',
          }}
        />
        {series.length > 1 && <Legend wrapperStyle={{ fontSize: '0.75rem' }} />}
        {series.map((s, i) => (
          <Line
            key={s.name}
            type="monotone"
            dataKey={s.name}
            stroke={COLORS[i % COLORS.length]}
            strokeWidth={2}
            dot={{ r: 3 }}
            activeDot={{ r: 5 }}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function PieChartRenderer({
  data,
  donut,
}: {
  data: { name: string; value: number }[];
  donut?: boolean;
}) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          labelLine={true}
          label={({ name, percent }) => {
            // Guard: recharts passes `percent` as 0..1, but it can be undefined or NaN
            // (zero-sum data / a single slice mid-render) -> that produced "NaN%" labels.
            const pct =
              typeof percent === 'number' && Number.isFinite(percent)
                ? Math.round(percent * 100)
                : 0;
            return `${name}: ${pct}%`;
          }}
          innerRadius={donut ? 60 : 0}
          outerRadius={110}
          paddingAngle={donut ? 2 : 0}
          fill="#8884d8"
          dataKey="value"
        >
          {data.map((_entry, index) => (
            <Cell
              key={`cell-${index}`}
              fill={COLORS[index % COLORS.length]}
            />
          ))}
        </Pie>
        <Tooltip
          contentStyle={{
            backgroundColor: 'var(--color-popover, #fff)',
            border: '1px solid var(--color-border, #e5e7eb)',
            borderRadius: '0.375rem',
            fontSize: '0.75rem',
          }}
        />
        <Legend wrapperStyle={{ fontSize: '0.75rem' }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

// ---------- Main component ----------

type ChartProps = HTMLAttributes<HTMLDivElement> & {
  output: RenderChartOutput;
};

/**
 * Renders a chart from the `render_chart` tool output. Handles all five chart
 * types (line, bar, grouped-bar, stacked-bar, pie), the proxy badge when
 * isProxy is true, and the text fallback for empty/thin data.
 */
export const Chart = ({ output, className, ...props }: ChartProps) => {
  // Error state
  if ('error' in output && output.error) {
    return (
      <div
        className={cn(
          'rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive',
          className,
        )}
        {...props}
      >
        Chart error: {output.error}
      </div>
    );
  }

  // Fallback state (empty/thin data)
  if (output.fallback) {
    return (
      <div className={cn('w-full', className)} {...props}>
        <ChartFallback text={output.text} />
      </div>
    );
  }

  // Chart state
  const chart = output.chart;
  if (!chart) return null;

  return <ChartInner chart={chart} className={className} {...props} />;
};

function ChartInner({
  chart,
  className,
  ...props
}: { chart: ChartSpec } & HTMLAttributes<HTMLDivElement>) {
  const flatData = useMemo(() => flattenSeries(chart.series), [chart.series]);
  const pieData = useMemo(() => flattenPie(chart.series), [chart.series]);

  return (
    <div
      className={cn(
        'not-prose w-full rounded-xl border bg-card p-4 shadow-sm',
        className,
      )}
      data-testid="chart-container"
      {...props}
    >
      {/* Header: title + scope pill (true CU vs monitored-activity proxy) */}
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {chartIcon(chart.chartType)}
          <span className="truncate text-sm font-semibold tracking-tight">
            {chart.title}
          </span>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-medium',
            chart.isProxy
              ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200'
              : 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800 dark:bg-emerald-950 dark:text-emerald-200',
          )}
        >
          {chart.isProxy ? 'monitored proxy' : 'true CU'}
        </span>
      </div>

      {/* Chart body */}
      <div className="w-full">
        {chart.chartType === 'line' && (
          <LineChartRenderer
            data={flatData}
            series={chart.series}
            axisLabels={chart.axisLabels}
          />
        )}
        {chart.chartType === 'bar' && (
          <BarChartRenderer
            data={flatData}
            series={chart.series}
            axisLabels={chart.axisLabels}
          />
        )}
        {chart.chartType === 'grouped-bar' && (
          <BarChartRenderer
            data={flatData}
            series={chart.series}
            axisLabels={chart.axisLabels}
          />
        )}
        {chart.chartType === 'stacked-bar' && (
          <BarChartRenderer
            data={flatData}
            series={chart.series}
            axisLabels={chart.axisLabels}
            stacked
          />
        )}
        {chart.chartType === 'pie' && <PieChartRenderer data={pieData} />}
        {chart.chartType === 'donut' && (
          <PieChartRenderer data={pieData} donut />
        )}
      </div>

      {/* Proxy badge */}
      {chart.isProxy && <ProxyBadge />}
    </div>
  );
}
