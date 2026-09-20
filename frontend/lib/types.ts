export type QuotaMeter = {
  used: number;
  max: number;
  remaining: number;
};

export type PlanQuotas = {
  customer_accounts: QuotaMeter;
  prospect_leads: QuotaMeter;
  ingest_runs_per_utc_day: QuotaMeter;
  csv_ingests_per_calendar_month?: QuotaMeter;
};

export type TenantSummary = {
  name: string;
  org_id: string;
  plan_tier: string;
  pro?: boolean;
  reverse_trial?: boolean;
  reverse_trial_days_remaining?: number;
  model_version: string;
  hitl_mrr_threshold: number;
  alert_cooldown_days: number;
  subscription_status?: string;
  subscriber_count?: number;
  role?: string;
  is_admin?: boolean;
};

export type CommandCenterPayload = {
  tenant: TenantSummary;
  kpis: {
    active_arr: number;
    at_risk_arr: number;
    verified_arr_saved: number;
    dispatched_count: number;
  };
  action_stream: {
    when: string;
    customer_id: string;
    channel: string;
    status: string;
    arr_saved_est: number;
    trigger_reason: string;
  }[];
  risk_mix: { bin: string; Low: number; Medium: number; Critical: number }[];
  at_risk_book: {
    account_id: string;
    customer_id: string;
    mrr: number;
    arr: number;
    channel: string;
    contract_type: string;
    churn_probability: number;
    risk_tier: "Low" | "Medium" | "Critical";
    recommended_action: string;
  }[];
};

export type StagingPayload = {
  tenant: TenantSummary;
  verified_arr_saved: number;
  at_risk_arr: number;
  pending: {
    account_id: string;
    customer_id: string;
    mrr: number;
    arr: number;
    risk_tier: string;
    recommended_action: string;
    churn_probability: number;
  }[];
};

export type SettingsPayload = {
  plan_tier?: string;
  pro?: boolean;
  reverse_trial?: boolean;
  reverse_trial_days_remaining?: number;
  tenant: TenantSummary;
  integrations: {
    stripe: boolean;
    slack: boolean;
    resend: boolean;
    hubspot: boolean;
    salesforce: boolean;
    apollo: boolean;
    instantly: boolean;
  };
  webhook_urls?: {
    stripe: string;
    telemetry: string;
  };
  api_base?: string;
  salesforce_instance_url: string | null;
  quotas?: PlanQuotas;
  audit_log: {
    when: string;
    actor: string;
    action: string;
    old: unknown;
    new: unknown;
  }[];
};

export type BillingPayload = {
  org_id: string;
  name?: string;
  plan_tier: string;
  pro?: boolean;
  reverse_trial?: boolean;
  reverse_trial_days_remaining?: number;
  subscription_status: string;
  stripe_customer_id: string | null;
  portal_available: boolean;
  needs_payment: boolean;
  quotas?: PlanQuotas;
};

export type JobsPayload = {
  org_id: string;
  dead_letter: {
    id: string;
    task_name: string;
    error_message: string | null;
    retry_count: number;
    failed_at: string | null;
    resolved: boolean;
  }[];
  ingestion_failures: {
    id: string;
    source: string;
    status: string;
    attempts: number;
    last_error: string | null;
    created_at: string | null;
    completed_at: string | null;
  }[];
};

export type AcquisitionProspect = {
  id: string;
  company_name: string;
  decision_maker_name: string;
  email: string;
  linkedin_url: string | null;
  conversion_score: number;
  high_intent: boolean;
  status: string;
  sequence_status: string;
  campaign_id: string | null;
  vendor: string | null;
};

export type AcquisitionPayload = {
  tenant: TenantSummary;
  high_intent_count: number;
  prospect_count: number;
  sequence_status: Record<string, number>;
  prospects: AcquisitionProspect[];
};

export type CsvIngestResponse = {
  ok: boolean;
  accepted?: boolean;
  status: string;
  reason?: string;
  org_id: string;
  event_id?: string;
  accounts_upserted?: number;
  assessments_written?: number;
  account_count?: number;
  assessment_count?: number;
};

export type BackfillResponse = {
  accepted: boolean;
  org_id: string;
  status: string;
  source?: string;
};

export type CheckoutConfirmResponse = {
  org_id: string;
  session_id: string;
  subscription_status: string;
  activated: boolean;
};
