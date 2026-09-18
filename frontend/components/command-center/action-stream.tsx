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
import { ACTION_STREAM } from "@/lib/command-center-data";

function formatUsd(value: number) {
  return `$${value.toLocaleString("en-US")}`;
}

export function ActionStream() {
  return (
    <Card className="rounded-2xl border-border shadow-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Live action stream</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
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
            {ACTION_STREAM.map((row) => (
              <TableRow key={`${row.customerId}-${row.when}`}>
                <TableCell className="whitespace-nowrap text-muted-foreground">{row.when}</TableCell>
                <TableCell className="font-medium">{row.customerId}</TableCell>
                <TableCell>{row.channel}</TableCell>
                <TableCell>
                  <Badge variant="outline" className="border-primary/30 text-primary">
                    {row.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-right">{formatUsd(row.arrSavedEst)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
