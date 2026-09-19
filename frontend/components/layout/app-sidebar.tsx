"use client";

import { SignedIn, SignedOut, SignInButton, UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LayoutDashboard, DoorOpen, ShieldCheck, Settings2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useTenant } from "@/hooks/use-tenant";
import { NAV_ITEMS } from "@/lib/command-center-data";
import { cn } from "@/lib/utils";

const ICONS = {
  "/": LayoutDashboard,
  "/acquisition": DoorOpen,
  "/staging": ShieldCheck,
  "/settings": Settings2,
} as const;

export function AppSidebar() {
  const pathname = usePathname();
  const tenant = useTenant();

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-sidebar-border bg-sidebar px-4 py-6">
      <div className="px-2">
        <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">
          Rev Lifecycle Engine
        </p>
        <h1 className="mt-1 text-lg font-bold leading-tight text-foreground">
          Executive Revenue Command Center
        </h1>
      </div>

      <div className="mt-6 rounded-2xl border border-border bg-card p-3 shadow-card">
        <p className="text-xs font-medium text-muted-foreground">Organization</p>
        <SignedOut>
          <p className="mt-1 text-sm font-semibold">Sign in to load your tenant</p>
          <SignInButton mode="redirect">
            <Button className="mt-3 w-full" size="sm">
              Sign in
            </Button>
          </SignInButton>
        </SignedOut>
        <SignedIn>
          <p className="mt-1 text-sm font-semibold">{tenant?.name ?? "Loading…"}</p>
          <p className="mt-1 font-mono text-xs text-muted-foreground">
            {tenant ? `${tenant.org_id} · ${tenant.plan_tier} plan` : "Fetching organization"}
          </p>
          <div className="mt-3 flex items-center justify-between">
            <Badge className="bg-primary/10 text-primary hover:bg-primary/10" variant="secondary">
              Live API
            </Badge>
            <UserButton afterSignOutUrl="/sign-in" />
          </div>
        </SignedIn>
      </div>

      <Separator className="my-6" />

      <nav className="flex flex-1 flex-col gap-1">
        {NAV_ITEMS.map((item) => {
          const Icon = ICONS[item.href];
          const active = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "rounded-xl px-3 py-2.5 transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground hover:bg-muted",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-semibold">
                <Icon className="h-4 w-4 text-primary" />
                {item.label}
              </span>
              <span className="mt-0.5 block pl-6 text-xs text-muted-foreground">
                {item.description}
              </span>
            </Link>
          );
        })}
      </nav>

      <p className="px-2 text-xs leading-relaxed text-muted-foreground">
        Configure Stripe, Slack, Resend, Apollo, Instantly, and HITL in Tenant Settings.
      </p>
    </aside>
  );
}
