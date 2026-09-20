"use client";

import { FormEvent, createContext, useCallback, useContext, useMemo, useState } from "react";
import { useUser } from "@clerk/nextjs";
import { Lock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import { cn } from "@/lib/utils";

const CSV_SNAPSHOT_KEY = "rle.freeCsvSnapshot";

type WaitlistState = {
  open: boolean;
  headline: string;
  subtitle: string;
};

type ProWaitlistContextValue = {
  openWaitlist: (opts?: { headline?: string; subtitle?: string }) => void;
  closeWaitlist: () => void;
  markCsvSnapshot: () => void;
  csvSnapshotActive: boolean;
  handlePlanLimit: (error: unknown) => boolean;
};

const DEFAULT_HEADLINE = "Unlock Real-Time Revenue Automation";
const DEFAULT_SUBTITLE = "Rev Lifecycle Pro is rolling out to early adopters. Lock in your spot.";
const CSV_LIMIT_COPY = "You've used this month's free diagnostic. Join the Pro waitlist.";

const ProWaitlistContext = createContext<ProWaitlistContextValue | null>(null);

export function useProWaitlist() {
  const value = useContext(ProWaitlistContext);
  if (!value) {
    throw new Error("useProWaitlist must be used within ProWaitlistProvider");
  }
  return value;
}

export function csvSnapshotBannerVisible() {
  if (typeof window === "undefined") {
    return false;
  }
  return window.sessionStorage.getItem(CSV_SNAPSHOT_KEY) === "1";
}

export function ProWaitlistProvider({ children }: { children: React.ReactNode }) {
  const { request } = useAuthedFetch();
  const { user } = useUser();
  const clerkEmail = user?.primaryEmailAddress?.emailAddress || "";
  const [state, setState] = useState<WaitlistState>({
    open: false,
    headline: DEFAULT_HEADLINE,
    subtitle: DEFAULT_SUBTITLE,
  });
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [csvSnapshotActive, setCsvSnapshotActive] = useState(csvSnapshotBannerVisible);

  const openWaitlist = useCallback((opts?: { headline?: string; subtitle?: string }) => {
    setDone(false);
    setError(null);
    setEmail(clerkEmail);
    setState({
      open: true,
      headline: opts?.headline || DEFAULT_HEADLINE,
      subtitle: opts?.subtitle || DEFAULT_SUBTITLE,
    });
  }, [clerkEmail]);

  const closeWaitlist = useCallback(() => {
    setState((current) => ({ ...current, open: false }));
  }, []);

  const markCsvSnapshot = useCallback(() => {
    if (typeof window !== "undefined") {
      window.sessionStorage.setItem(CSV_SNAPSHOT_KEY, "1");
    }
    setCsvSnapshotActive(true);
  }, []);

  const handlePlanLimit = useCallback(
    (error: unknown) => {
      if (error instanceof ApiError && error.status === 403 && error.code === "plan_limit") {
        openWaitlist({ subtitle: CSV_LIMIT_COPY });
        return true;
      }
      return false;
    },
    [openWaitlist],
  );

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await request("/api/v1/waitlist", {
        method: "POST",
        body: JSON.stringify({ email: trimmed }),
      });
      setDone(true);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Unable to join the waitlist");
    } finally {
      setSubmitting(false);
    }
  }

  const value = useMemo(
    () => ({ openWaitlist, closeWaitlist, markCsvSnapshot, csvSnapshotActive, handlePlanLimit }),
    [openWaitlist, closeWaitlist, markCsvSnapshot, csvSnapshotActive, handlePlanLimit],
  );

  return (
    <ProWaitlistContext.Provider value={value}>
      {children}
      {state.open ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-foreground/40 p-4" role="dialog" aria-modal="true">
          <div className="w-full max-w-md rounded-2xl border border-border bg-background p-6 shadow-card">
            <div className="mb-4 flex items-center gap-2 text-primary">
              <Lock className="h-4 w-4" />
              <span className="rounded-md border border-primary/30 bg-primary/10 px-2 py-0.5 text-[0.65rem] font-semibold tracking-[0.14em] text-primary">
                PRO
              </span>
            </div>
            <h3 className="text-xl font-bold text-foreground">{state.headline}</h3>
            <p className="mt-2 text-sm text-muted-foreground">{state.subtitle}</p>
            {done ? (
              <p className="mt-4 text-sm text-primary">You&apos;re on the list. We&apos;ll reach out when Pro access opens.</p>
            ) : (
              <form className="mt-4 flex flex-col gap-3" onSubmit={(event) => void onSubmit(event)}>
                <label className="text-sm font-medium">
                  Work email
                  <input
                    className="mt-1 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    required
                  />
                </label>
                {error ? <p className="text-sm text-destructive">{error}</p> : null}
                <Button type="submit" disabled={submitting || !email.trim()}>
                  {submitting ? "Requesting…" : "Request Priority Access"}
                </Button>
              </form>
            )}
            <button
              type="button"
              className="mt-4 text-sm text-muted-foreground underline-offset-4 hover:underline"
              onClick={closeWaitlist}
            >
              Not now
            </button>
          </div>
        </div>
      ) : null}
    </ProWaitlistContext.Provider>
  );
}

export function ProBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-1.5 py-0.5 text-[0.65rem] font-semibold tracking-[0.14em] text-primary",
        className,
      )}
    >
      <Lock className="h-3 w-3" />
      PRO
    </span>
  );
}

export function ProLockOverlay({
  locked,
  onUnlock,
  className,
}: {
  locked: boolean;
  onUnlock: () => void;
  className?: string;
}) {
  if (!locked) {
    return null;
  }
  return (
    <button
      type="button"
      className={cn("absolute inset-0 z-10 cursor-pointer rounded-lg", className)}
      onClick={onUnlock}
      aria-label="Unlock Real-Time Revenue Automation"
    />
  );
}
