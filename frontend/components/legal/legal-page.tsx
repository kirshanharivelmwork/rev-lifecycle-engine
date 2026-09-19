import type { ReactNode } from "react";

import { LegalFooter } from "@/components/legal/legal-footer";

export function LegalPage({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-background px-6 py-16">
      <div className="mx-auto flex max-w-3xl flex-col gap-8">
        <header>
          <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">
            Rev Lifecycle Engine
          </p>
          <h1 className="mt-2 text-4xl font-bold tracking-tight text-foreground">{title}</h1>
          <p className="mt-3 rounded-xl border border-border bg-accent px-4 py-3 text-sm text-accent-foreground">
            This page is a product template, not legal advice. It is not a law-firm opinion and does not create
            attorney–client privilege.
          </p>
        </header>
        <article className="space-y-4 text-sm leading-relaxed text-muted-foreground">{children}</article>
        <LegalFooter />
      </div>
    </div>
  );
}
