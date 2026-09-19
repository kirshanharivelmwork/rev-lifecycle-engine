const SENSITIVE = ["authorization", "cookie", "token", "secret", "password", "api_key", "apikey", "bearer"];

function isSensitiveKey(key: string): boolean {
  const lowered = key.toLowerCase();
  return SENSITIVE.some((token) => lowered.includes(token));
}

function redact(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(redact);
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      out[key] = isSensitiveKey(key) ? "[filtered]" : redact(item);
    }
    return out;
  }
  return value;
}

export function sentryBeforeSend(event: Record<string, unknown>): Record<string, unknown> {
  const request = event.request as Record<string, unknown> | undefined;
  if (request) {
    if (request.headers && typeof request.headers === "object") {
      request.headers = redact(request.headers);
    }
    delete request.cookies;
    if ("data" in request) {
      request.data = redact(request.data);
    }
    event.request = request;
  }
  if (event.extra) {
    event.extra = redact(event.extra);
  }
  if (event.user) {
    event.user = redact(event.user);
  }
  return event;
}

export function sentryDsn(): string | undefined {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN || process.env.SENTRY_DSN;
  return dsn && dsn.trim() ? dsn.trim() : undefined;
}
