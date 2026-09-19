export const NAV_ITEMS = [
  { href: "/", label: "Command Center", description: "Book health & interventions" },
  {
    href: "/acquisition",
    label: "Front Door: Acquisition",
    description: "ICP ingest & outbound",
  },
  {
    href: "/staging",
    label: "Staging & Approval Queue",
    description: "HITL playbook review",
  },
  {
    href: "/settings",
    label: "Tenant Settings",
    description: "Integrations & policy",
  },
] as const;

export type RiskTier = "Low" | "Medium" | "Critical";

export const RISK_COLORS: Record<RiskTier, string> = {
  Low: "#556B2F",
  Medium: "#CA8A04",
  Critical: "#B91C1C",
};

export function formatUsd(value: number): string {
  if (Math.abs(value) >= 1_000_000) {
    return `$${(value / 1_000_000).toFixed(2)}M`;
  }
  if (Math.abs(value) >= 1_000) {
    return `$${(value / 1_000).toFixed(1)}K`;
  }
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

export function formatWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}
