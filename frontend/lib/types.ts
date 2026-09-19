export type TenantSummary = {
  name: string;
  org_id: string;
  plan_tier: string;
  model_version: string;
  hitl_mrr_threshold: number;
  alert_cooldown_days: number;
  subscription_status?: string;
  subscriber_count?: number;
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
  salesforce_instance_url: string | null;
  audit_log: {
    when: string;
    actor: string;
    action: string;
    old: unknown;
    new: unknown;
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
