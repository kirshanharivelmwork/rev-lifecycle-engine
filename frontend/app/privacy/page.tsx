import { LegalPage } from "@/components/legal/legal-page";

export default function PrivacyRoute() {
  return (
    <LegalPage title="Privacy">
      <p>
        Rev Lifecycle Engine processes workspace data you choose to upload or connect (billing extracts, product
        telemetry, and integration secrets) so we can score churn risk and show the Command Center for your Clerk
        organization.
      </p>
      <p>
        Tenant secrets are stored encrypted at rest. We do not sell personal data. Operators of a self-hosted or
        Railway deployment are the data controller for their production instance.
      </p>
      <p>
        To request deletion of a customer record in your tenant, use Settings → delete customer (admin). Contact the
        workspace owner for any other privacy request.
      </p>
    </LegalPage>
  );
}
