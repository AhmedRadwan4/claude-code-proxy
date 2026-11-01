import { OverviewStats } from "@/lib/types";
import { formatCountdown, formatNumber } from "@/lib/format";
import { Card } from "@/components/ui";

function nextReset(resetAt: string | null): string {
    const countdown = formatCountdown(resetAt);
    if (countdown.startsWith("resets in ")) return `Next reset in ${countdown.slice(10)}`;
    return countdown === "resetting…" ? "Resetting…" : "Next reset unavailable";
}

export function UsageLimitsCard({ stats }: { stats: OverviewStats }) {
    const metrics = [
        { label: "Input rate", value: stats.input_rate_pct, decimals: 2 },
        { label: "Cache hit rate", value: stats.cache_hit_rate_pct, decimals: 1 },
        {
            label: "Average 5-hour limit",
            value: stats.five_hour_average_pct,
            decimals: 1,
            reset: nextReset(stats.five_hour_reset_at),
        },
        {
            label: "Average weekly limit",
            value: stats.weekly_average_pct,
            decimals: 1,
            reset: nextReset(stats.weekly_reset_at),
        },
    ];

    return (
        <Card className="@container overflow-hidden">
            <h2 className="text-fog-100 px-5 pt-5 text-sm font-semibold @min-[640px]:px-6">
                Usage &amp; limits
            </h2>
            <dl className="grid grid-cols-2 @min-[640px]:grid-cols-4 @min-[640px]:py-6">
                {metrics.map(({ label, value, decimals, reset }, index) => (
                    <div
                        key={label}
                        className={`border-ink-700 min-w-0 px-4 py-5 @min-[640px]:px-6 @min-[640px]:py-0 ${index % 2 ? "border-l" : ""} ${index >= 2 ? "border-t @min-[640px]:border-t-0" : ""} ${index === 2 ? "@min-[640px]:border-l" : ""}`}
                    >
                        <dt className="text-fog-400 text-xs font-medium">{label}</dt>
                        <dd className="text-fog-100 mt-3 font-mono text-[1.75rem] leading-tight font-semibold tracking-tight whitespace-nowrap tabular-nums @min-[640px]:text-[2rem] @min-[1000px]:text-4xl">
                            {Math.max(0, Math.min(100, value)).toFixed(decimals)}
                            <span className="text-[1.25rem] @min-[1000px]:text-2xl">%</span>
                        </dd>
                        {reset ? <dd className="text-fog-400 mt-2 text-xs">{reset}</dd> : null}
                    </div>
                ))}
            </dl>
            <p className="border-ink-700 text-fog-400 border-t px-5 py-3 text-xs @min-[640px]:px-6">
                Quota averages across{" "}
                <span className="text-fog-100 font-mono tabular-nums">
                    {formatNumber(stats.active_accounts)} / {formatNumber(stats.total_accounts)}
                </span>{" "}
                authenticated and enabled accounts
            </p>
        </Card>
    );
}
