export const TENANT = {
  name: "Acme SaaS",
  orgId: "org_acme",
  planTier: "growth",
  modelVersion: "1.0.0",
  hitlFloorUsd: 1000,
  cooldownDays: 14,
  subscriberCount: 50,
} as const;

export const COMMAND_CENTER_KPIS = [
  {
    id: "arr",
    label: "Monitored subscribers & active ARR",
    value: "$416.6K",
    delta: "50 accounts",
  },
  {
    id: "at-risk",
    label: "Prospective ARR at risk",
    value: "$100.7K",
    delta: "p ≥ 0.65 churn",
  },
  {
    id: "verified",
    label: "Verified ARR saved",
    value: "$150.4K",
    delta: "30 / 60 / 90-day audits",
  },
  {
    id: "interventions",
    label: "Dispatched interventions (this month)",
    value: "3",
    delta: "HITL queued",
  },
] as const;

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

export const RISK_MIX: { bin: string; Low: number; Medium: number; Critical: number }[] = [
  { bin: "0.0–0.1", Low: 8, Medium: 0, Critical: 0 },
  { bin: "0.1–0.2", Low: 7, Medium: 1, Critical: 0 },
  { bin: "0.2–0.3", Low: 6, Medium: 2, Critical: 0 },
  { bin: "0.3–0.4", Low: 4, Medium: 3, Critical: 0 },
  { bin: "0.4–0.5", Low: 2, Medium: 3, Critical: 0 },
  { bin: "0.5–0.6", Low: 1, Medium: 3, Critical: 1 },
  { bin: "0.6–0.7", Low: 0, Medium: 2, Critical: 2 },
  { bin: "0.7–0.8", Low: 0, Medium: 1, Critical: 2 },
  { bin: "0.8–0.9", Low: 0, Medium: 0, Critical: 1 },
  { bin: "0.9–1.0", Low: 0, Medium: 0, Critical: 1 },
];

export const RISK_COLORS: Record<RiskTier, string> = {
  Low: "#556B2F",
  Medium: "#CA8A04",
  Critical: "#B91C1C",
};

export const ACTION_STREAM = [
  {
    when: "2026-09-18 14:22",
    customerId: "acme_042",
    channel: "slack",
    status: "queued_hitl",
    arrSavedEst: 18600,
    triggerReason: "Inactivity spike + monthly contract",
  },
  {
    when: "2026-09-16 09:04",
    customerId: "acme_018",
    channel: "resend",
    status: "queued_hitl",
    arrSavedEst: 24000,
    triggerReason: "Feature adoption below cohort median",
  },
  {
    when: "2026-09-12 17:41",
    customerId: "acme_007",
    channel: "slack",
    status: "queued_hitl",
    arrSavedEst: 31200,
    triggerReason: "Support load + days since last login",
  },
] as const;

export const AT_RISK_BOOK = [
  {
    customerId: "acme_007",
    channel: "Outbound Cold Email",
    mrr: 2600,
    arr: 31200,
    contractType: "Monthly",
    churnProbability: 0.91,
    riskTier: "Critical" as RiskTier,
    recommendedAction: "Executive save call",
  },
  {
    customerId: "acme_018",
    channel: "Paid Search",
    mrr: 2000,
    arr: 24000,
    contractType: "Monthly",
    churnProbability: 0.78,
    riskTier: "Critical" as RiskTier,
    recommendedAction: "CS playbook + product tour",
  },
  {
    customerId: "acme_042",
    channel: "Outbound Cold Email",
    mrr: 1550,
    arr: 18600,
    contractType: "Monthly",
    churnProbability: 0.71,
    riskTier: "Critical" as RiskTier,
    recommendedAction: "Usage reactivation sequence",
  },
  {
    customerId: "acme_031",
    channel: "Content / SEO",
    mrr: 900,
    arr: 10800,
    contractType: "Annual",
    churnProbability: 0.66,
    riskTier: "Medium" as RiskTier,
    recommendedAction: "Success check-in",
  },
] as const;
