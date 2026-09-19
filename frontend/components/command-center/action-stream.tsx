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
import { formatUsd, formatWhen } from "@/lib/command-center-data";
import type { CommandCenterPayload } from "@/lib/types";

export function ActionStream({ rows }: { rows: CommandCenterPayload["action_stream"] }) {
  return (
    <Card className="rounded-2xl border-border shadow-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Live action stream</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {rows.length === 0 ? (
          <p className="py-8 text-sm text-muted-foreground">No dispatched actions this month for this tenant.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Execution</TableHead>
                <TableHead>Customer</TableHead>
                <TableHead>Channel</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Est. ARR saved</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.customer_id}-${row.when}-${row.channel}`}>
                  <TableCell className="whitespace-nowrap text-muted-foreground">{formatWhen(row.when)}</TableCell>
                  <TableCell className="font-medium">{row.customer_id}</TableCell>
                  <TableCell>{row.channel}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className="border-primary/30 text-primary">
                      {row.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right">{formatUsd(row.arr_saved_est)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
