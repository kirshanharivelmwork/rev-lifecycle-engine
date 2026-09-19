"use client";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatUsd, type RiskTier } from "@/lib/command-center-data";
import type { CommandCenterPayload } from "@/lib/types";
import { cn } from "@/lib/utils";

const TIER_STYLES: Record<RiskTier, string> = {
  Low: "border-primary/30 bg-primary/10 text-primary",
  Medium: "border-amber-300 bg-amber-50 text-amber-800",
  Critical: "border-red-200 bg-red-50 text-red-800",
};

export function AtRiskBook({ rows }: { rows: CommandCenterPayload["at_risk_book"] }) {
  return (
    <Card className="rounded-2xl border-border shadow-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">At-risk book</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {rows.length === 0 ? (
          <p className="py-8 text-sm text-muted-foreground">No accounts currently above the at-risk threshold.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Account</TableHead>
                <TableHead>Channel</TableHead>
                <TableHead>Contract</TableHead>
                <TableHead className="text-right">ARR</TableHead>
                <TableHead className="text-right">Churn p</TableHead>
                <TableHead>Tier</TableHead>
                <TableHead>Playbook</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.account_id}>
                  <TableCell className="font-medium">{row.customer_id}</TableCell>
                  <TableCell>{row.channel}</TableCell>
                  <TableCell>{row.contract_type}</TableCell>
                  <TableCell className="text-right">{formatUsd(row.arr)}</TableCell>
                  <TableCell className="text-right">{(row.churn_probability * 100).toFixed(0)}%</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={cn(TIER_STYLES[row.risk_tier])}>
                      {row.risk_tier}
                    </Badge>
                  </TableCell>
                  <TableCell>{row.recommended_action}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
