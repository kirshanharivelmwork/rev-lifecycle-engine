"use client";

import { useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { TenantSummary } from "@/lib/types";

/**
 * Admin UI gates (Approve, Settings PATCH, Run Outbound) use GET /api/v1/tenant
 * role / is_admin from FastAPI AuthUser — not Clerk orgRole in the browser.
 */
export function useOrgRole() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [role, setRole] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!isLoaded) {
      return;
    }
    if (!isSignedIn) {
      setRole(null);
      setIsAdmin(false);
      setReady(true);
      return;
    }
    let cancelled = false;
    setReady(false);
    void (async () => {
      try {
        const tenant = await request<TenantSummary>("/api/v1/tenant");
        if (!cancelled) {
          setRole(tenant.role ?? "Member");
          setIsAdmin(Boolean(tenant.is_admin));
        }
      } catch {
        if (!cancelled) {
          setRole("Member");
          setIsAdmin(false);
        }
      } finally {
        if (!cancelled) {
          setReady(true);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, request]);

  return {
    isLoaded: isLoaded && ready,
    isSignedIn,
    orgRole: role,
    isAdmin,
    isMember: Boolean(isSignedIn && !isAdmin),
  };
}
