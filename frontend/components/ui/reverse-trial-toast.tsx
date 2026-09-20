"use client";

import { useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";

const TOAST_DELAY_MS = 900;
const TOAST_STORAGE_PREFIX = "rle.reverseTrialToast.v1:";

export function ReverseTrialToast({
  orgId,
  active,
}: {
  orgId?: string;
  active: boolean;
}) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!active || !orgId || typeof window === "undefined") {
      return;
    }
    const key = `${TOAST_STORAGE_PREFIX}${orgId}`;
    if (window.localStorage.getItem(key) === "1") {
      return;
    }
    const timer = window.setTimeout(() => {
      setVisible(true);
      window.localStorage.setItem(key, "1");
    }, TOAST_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [active, orgId]);

  if (!visible) {
    return null;
  }

  return (
    <div className="pointer-events-none fixed bottom-6 right-6 z-50 flex max-w-sm justify-end">
      <div
        role="status"
        className="pointer-events-auto flex gap-3 rounded-2xl border border-white/10 bg-zinc-950 px-4 py-3 text-white shadow-[0_18px_40px_rgba(0,0,0,0.45)]"
      >
        <span className="mt-0.5 text-primary">
          <Sparkles className="h-4 w-4" />
        </span>
        <p className="text-sm leading-relaxed text-zinc-100">
          Welcome to the Rev Lifecycle Engine. Your 21-day all-access Pro pass is active.
        </p>
        <button
          type="button"
          className="mt-0.5 text-zinc-400 hover:text-white"
          onClick={() => setVisible(false)}
          aria-label="Dismiss trial welcome"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}
