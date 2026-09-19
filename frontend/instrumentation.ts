export async function register() {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.SENTRY_DSN;
  if (!dsn) {
    return;
  }
  const Sentry = await import("@sentry/nextjs");
  const { sentryBeforeSend } = await import("./lib/sentry");
  Sentry.init({
    dsn,
    sendDefaultPii: false,
    beforeSend(event) {
      sentryBeforeSend(event as unknown as Record<string, unknown>);
      return event;
    },
  });
}
