import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

type PagePlaceholderProps = {
  kicker: string;
  title: string;
  description: string;
};

export function PagePlaceholder({ kicker, title, description }: PagePlaceholderProps) {
  return (
    <div className="mx-auto max-w-[1400px]">
      <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">{kicker}</p>
      <h2 className="mt-1 text-[2.05rem] font-bold text-foreground">{title}</h2>
      <Card className="mt-6 rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Static shell</CardTitle>
          <CardDescription>{description}</CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          Navigation and theming are in place. API wiring comes later.
        </CardContent>
      </Card>
    </div>
  );
}
