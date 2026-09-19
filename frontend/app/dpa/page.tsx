import { LegalPage } from "@/components/legal/legal-page";

export default function DpaRoute() {
  return (
    <LegalPage title="Data Processing Addendum">
      <p>
        This template DPA describes how a deployment of Rev Lifecycle Engine processes customer personal data on behalf
        of a tenant organization. Replace this page with counsel-reviewed terms before relying on it in a production
        contract.
      </p>
      <p>
        Processing is limited to providing churn scoring, Command Center analytics, ingest (CSV and historical
        backfill), and optional outbound/CRM integrations you enable. Subprocessors typically include the host
        (Railway or equivalent), Clerk (authentication), and Stripe (billing) when those services are configured.
      </p>
      <p>
        Upon written request after subscription end, tenant data can be exported or deleted from the application
        database subject to backup retention on the host.
      </p>
    </LegalPage>
  );
}
