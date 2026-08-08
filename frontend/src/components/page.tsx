import { AlertCircle, LoaderCircle } from "lucide-react";
import { type ReactNode } from "react";

import { Button } from "@/components/ui/button";

export function PageHeader({ title, actions, children }: { title: string; actions?: ReactNode; children?: ReactNode }) {
  return <header className="flex flex-col gap-3 border-b border-zinc-200 pb-4 sm:flex-row sm:items-center sm:justify-between"><div><h1 className="text-xl font-semibold text-zinc-950">{title}</h1>{children}</div>{actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}</header>;
}

export function LoadingState() { return <div className="flex min-h-40 items-center justify-center text-sm text-zinc-500"><LoaderCircle className="mr-2 size-4 animate-spin" />加载中</div>; }

export function ErrorState({ error, retry }: { error?: unknown; retry?: () => void }) { const message = error instanceof Error ? error.message : "数据请求未完成"; return <div className="flex min-h-40 flex-col items-center justify-center gap-3 text-sm text-zinc-600"><AlertCircle className="size-5 text-red-500" />{message}{retry ? <Button variant="outline" size="sm" onClick={retry}>重试</Button> : null}</div>; }
