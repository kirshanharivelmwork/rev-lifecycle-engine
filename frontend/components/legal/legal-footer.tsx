import Link from "next/link";

import { cn } from "@/lib/utils";

export function LegalFooter({ className }: { className?: string }) {
  return (
    <footer className={cn("mt-10 flex flex-wrap gap-x-4 gap-y-2 text-sm text-muted-foreground", className)}>
      <Link className="hover:text-primary hover:underline" href="/privacy">
        Privacy
      </Link>
      <Link className="hover:text-primary hover:underline" href="/terms">
        Terms
      </Link>
      <Link className="hover:text-primary hover:underline" href="/dpa">
        DPA
      </Link>
    </footer>
  );
}

