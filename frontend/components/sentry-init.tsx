"use client";

import { useEffect } from "react";

export function SentryInit() {
  useEffect(() => {
    const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
    if (!dsn) {
      return;
    }
    void import("@sentry/nextjs").then((Sentry) => {
      void import("@/lib/sentry").then(({ sentryBeforeSend }) => {
        Sentry.init({
          dsn,
          sendDefaultPii: false,
          beforeSend(event) {
            sentryBeforeSend(event as unknown as Record<string, unknown>);
            return event;
          },
        });
      });
    });
  }, []);
  return null;
}
