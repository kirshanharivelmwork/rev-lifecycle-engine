"use client";

import Link from "next/link";

import { Button } from "@/components/ui/button";

export function PaymentCta({ message }: { message?: string }) {
  return (
    <div className="mx-auto flex max-w-xl flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        {message || "This workspace needs an active subscription before product APIs will load."}
      </p>
      <div className="flex flex-wrap gap-3">
        <Button asChild>
          <Link href="/billing">Update payment</Link>
        </Button>
        <Link className="self-center text-sm text-primary underline-offset-4 hover:underline" href="/pricing">
          Open pricing
        </Link>
      </div>
    </div>
  );
}
