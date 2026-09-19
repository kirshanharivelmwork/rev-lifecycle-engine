"use client";

import { useAuth } from "@clerk/nextjs";

const ADMIN_ALIASES = new Set(["admin", "org:admin", "org_admin", "org admin"]);

export function useOrgRole() {
  const { orgRole, isLoaded, isSignedIn } = useAuth();
  const normalized = (orgRole || "").trim().toLowerCase();
  const isAdmin = ADMIN_ALIASES.has(normalized);
  return {
    isLoaded,
    isSignedIn,
    orgRole: orgRole ?? null,
    isAdmin,
    isMember: Boolean(isSignedIn && !isAdmin),
  };
}
