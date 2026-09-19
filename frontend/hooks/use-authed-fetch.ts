"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

import { apiFetch } from "@/lib/api";

export function useAuthedFetch() {
  const { getToken, isLoaded, isSignedIn } = useAuth();

  const request = useCallback(
    async <T,>(path: string, init?: RequestInit): Promise<T> => {
      const token = await getToken();
      if (!token) {
        throw new Error("Missing Clerk JWT");
      }
      return apiFetch<T>(path, token, init);
    },
    [getToken],
  );

  return { request, isLoaded, isSignedIn };
}
