"use client";

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RISK_COLORS, RISK_MIX } from "@/lib/command-center-data";

export function BookRiskChart() {
  return (
    <Card className="rounded-2xl border-border shadow-card">
      <CardHeader className="pb-2">
        <CardTitle className="text-lg">Book risk mix</CardTitle>
      </CardHeader>
      <CardContent className="h-[320px] pt-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={RISK_MIX} barGap={2}>
            <CartesianGrid stroke="#E5E7EB" vertical={false} />
            <XAxis dataKey="bin" tick={{ fill: "#4B5563", fontSize: 11 }} tickLine={false} axisLine={false} />
            <YAxis allowDecimals={false} tick={{ fill: "#4B5563", fontSize: 11 }} tickLine={false} axisLine={false} />
            <Tooltip
              contentStyle={{
                borderRadius: 12,
                border: "1px solid #E5E7EB",
                fontSize: 12,
              }}
            />
            <Legend />
            <Bar dataKey="Low" stackId="risk" fill={RISK_COLORS.Low} radius={[0, 0, 0, 0]} />
            <Bar dataKey="Medium" stackId="risk" fill={RISK_COLORS.Medium} />
            <Bar dataKey="Critical" stackId="risk" fill={RISK_COLORS.Critical} radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}
