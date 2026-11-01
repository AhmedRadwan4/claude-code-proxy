// ---------------------------------------------------------------------------
// Number / date formatting helpers used across the dashboard.
// ---------------------------------------------------------------------------

// "1,234,567" — plain integer with thousands separators.
export function formatNumber(value: number): string {
    return new Intl.NumberFormat("en-US").format(value);
}

// Compact token counts: 950 -> "950", 12_300 -> "12.3k", 1_200_000 -> "1.2M".
export function formatTokens(value: number): string {
    if (value < 1_000) {
        return String(value);
    }
    if (value < 1_000_000) {
        return `${trim(value / 1_000)}k`;
    }
    if (value < 1_000_000_000) {
        return `${trim(value / 1_000_000)}M`;
    }
    return `${trim(value / 1_000_000_000)}B`;
}

// Compact token counts for dense tables (e.g. 17,000 -> "17K").
export function formatCompactNumber(value: unknown): string {
    if (value === null || value === undefined || value === "") return "—";
    const n = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(n)) return "—";
    return new Intl.NumberFormat("en-US", {
        notation: "compact",
        maximumFractionDigits: 1,
    })
        .format(n)
        .replace(/k/g, "K");
}

function trim(n: number): string {
    return n.toFixed(2).replace(/\.00$/, "");
}

// Percent from a 0..1 fraction: 0.42 -> "42%".
export function formatPct(fraction: number): string {
    return `${Math.round(fraction * 100)}%`;
}

// USD money with thousands separators: 1234.5 -> "$1,234.56".
export function formatUsd(value: number): string {
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
    }).format(value);
}

// Per-request cost with extra precision for sub-dollar amounts:
// 0 -> "—", 0.0123 -> "$0.0123", 12.5 -> "$12.50".
export function formatCostUsd(value: number): string {
    if (!Number.isFinite(value) || value <= 0) return "—";
    if (value < 1) return `$${value.toFixed(4)}`;
    return formatUsd(value);
}

// Friendly subscription tier from the raw OAuth value, e.g. "default_claude_max_5x" -> "Max 5×".
export function tierLabel(raw: string | null): string {
    if (!raw) return "Unknown";
    const r = raw.toLowerCase();
    if (r.includes("ultra")) return "Ultra";
    if (r.includes("max_20") || r.includes("max20")) return "Max 20×";
    if (r.includes("max_5") || r.includes("max5")) return "Max 5×";
    if (r.includes("max")) return "Max";
    if (r.includes("team")) return "Team";
    if (r.includes("enterprise")) return "Enterprise";
    if (r.includes("pro")) return "Pro";
    if (r.includes("free")) return "Free";
    return raw.replace(/^default_/, "").replace(/_/g, " ");
}

// Absolute timestamp, e.g. "Jun 21, 2026, 19:07".
export function formatDateTime(iso: string | null): string {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    return new Intl.DateTimeFormat("en-US", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
    }).format(d);
}

// Browser timezone abbreviation, e.g. "IST", "EST", "UTC".
export function localTzAbbr(): string {
    return (
        new Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
            .formatToParts(new Date())
            .find((p) => p.type === "timeZoneName")?.value ?? "local"
    );
}

// Countdown to a future ISO time: "resets in 3h 12m" / "resets in 45s" / "resetting…".
export function formatCountdown(iso: string | null): string {
    if (!iso) return "unknown";

    const target = new Date(iso).getTime();
    if (Number.isNaN(target)) return "unknown";

    const diffMs = target - Date.now();
    if (diffMs <= 0) return "resetting…";

    const totalSec = Math.floor(diffMs / 1000);
    const days = Math.floor(totalSec / 86_400);
    const hours = Math.floor((totalSec % 86_400) / 3_600);
    const minutes = Math.floor((totalSec % 3_600) / 60);
    const seconds = totalSec % 60;

    let span: string;
    if (days > 0) {
        span = `${days}d ${hours}h`;
    } else if (hours > 0) {
        span = `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        span = `${minutes}m`;
    } else {
        span = `${seconds}s`;
    }

    return `resets in ${span}`;
}
