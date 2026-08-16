import { AlertCircle, LoaderCircle } from "lucide-react";
import { type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";

export function PageHeader({ title, actions, children }: { title: string; actions?: ReactNode; children?: ReactNode }) {
  return (
    <header className="flex flex-col gap-3 border-b border-zinc-200/80 pb-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-zinc-950">{title}</h1>
        {children}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={cn("animate-pulse rounded bg-zinc-200/70", className)} />
  );
}

export function LoadingState({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 py-6 animate-fade-in">
      <div className="flex items-center justify-center gap-2 py-4 text-sm text-zinc-500">
        <LoaderCircle className="size-4 animate-spin text-blue-600" />
        <span>加载数据中…</span>
      </div>
      <div className="space-y-2.5 max-w-full">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex gap-3">
            <Skeleton className="h-9 w-24 shrink-0" />
            <Skeleton className="h-9 flex-1" />
            <Skeleton className="h-9 w-32 shrink-0" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function ErrorState({ error, retry }: { error?: unknown; retry?: () => void }) {
  const message = error instanceof Error ? error.message : "数据请求未完成";
  return (
    <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-red-100 bg-red-50/40 p-6 text-sm text-zinc-600 animate-fade-in">
      <div className="grid size-10 place-items-center rounded-full bg-red-100/80 text-red-600">
        <AlertCircle className="size-5" />
      </div>
      <div className="text-center">
        <p className="font-medium text-zinc-800">{message}</p>
        <p className="mt-0.5 text-xs text-zinc-400">请检查网络或后端服务状态后重试</p>
      </div>
      {retry ? (
        <Button variant="outline" size="sm" onClick={retry} className="mt-1">
          重试
        </Button>
      ) : null}
    </div>
  );
}
