import { Suspense } from "react";

import { CommandCenter } from "@/components/command-center/command-center";

export default function HomePage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted-foreground">Loading live command center…</p>}>
      <CommandCenter />
    </Suspense>
  );
}
