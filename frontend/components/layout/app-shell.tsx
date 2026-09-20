"use client";

import { usePathname } from "next/navigation";

import { AppSidebar } from "@/components/layout/app-sidebar";
import { ProWaitlistProvider } from "@/components/ui/pro-waitlist-modal";

const PUBLIC_PREFIXES = ["/pricing", "/sign-in", "/sign-up", "/privacy", "/terms", "/dpa"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isPublic = PUBLIC_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
  if (isPublic) {
    return <>{children}</>;
  }
  return (
    <ProWaitlistProvider>
      <div className="flex min-h-screen bg-background">
        <AppSidebar />
        <main className="min-w-0 flex-1 px-8 py-6">{children}</main>
      </div>
    </ProWaitlistProvider>
  );
}
