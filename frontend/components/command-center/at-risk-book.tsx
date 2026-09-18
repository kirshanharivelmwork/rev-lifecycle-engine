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
import { AT_RISK_BOOK, type RiskTier } from "@/lib/command-center-data";
import { cn } from "@/lib/utils";

const TIER_STYLES: Record<RiskTier, string> = {
  Low: "border-primary/30 bg-primary/10 text-primary",
  Medium: "border-amber-300 bg-amber-50 text-amber-800",
  Critical: "border-red-200 bg-red-50 text-red-800",
};

export function AtRiskBook() {
  return (
    <Card className="rounded-2xl border-border shadow-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">At-risk book</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
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
            {AT_RISK_BOOK.map((row) => (
              <TableRow key={row.customerId}>
                <TableCell className="font-medium">{row.customerId}</TableCell>
                <TableCell>{row.channel}</TableCell>
                <TableCell>{row.contractType}</TableCell>
                <TableCell className="text-right">${row.arr.toLocaleString("en-US")}</TableCell>
                <TableCell className="text-right">{(row.churnProbability * 100).toFixed(0)}%</TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn(TIER_STYLES[row.riskTier])}>
                    {row.riskTier}
                  </Badge>
                </TableCell>
                <TableCell>{row.recommendedAction}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
