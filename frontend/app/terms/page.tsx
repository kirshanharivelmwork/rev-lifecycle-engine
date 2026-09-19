import { LegalPage } from "@/components/legal/legal-page";

export default function TermsRoute() {
  return (
    <LegalPage title="Terms">
      <p>
        This software is a commercial demo and portfolio product. Use of the hosted Command Center is limited to the
        Growth plan quotas published in the product README (customer accounts, prospect leads, and daily CSV/backfill
        runs).
      </p>
      <p>
        You are responsible for the lawfulness of data you ingest (CSV uploads, Stripe history, Apollo prospects). Do
        not upload data you are not authorized to process.
      </p>
      <p>
        Service is provided as-is. Scores, playbooks, and ARR figures are decision-support outputs, not a guarantee of
        retained revenue.
      </p>
    </LegalPage>
  );
}
