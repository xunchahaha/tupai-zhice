import { Check, Copy } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

/**
 * 表头文字。表格用的是自动列宽，表头默认可换行，中文表头在窄列里会被挤成
 * 「一行一个字」的竖排；这里强制不换行，让列的最小宽度至少容纳完整表头。
 */
export function ColumnHeader({ children }: { children: ReactNode }) {
  return <span className="whitespace-nowrap">{children}</span>;
}

/**
 * 定宽文本单元格。定宽而不是自适应，是为了让列宽不再随「当前页恰好落了哪几行」
 * 变化——排序会换掉当前页的数据，自适应列宽就会在点击表头后突然被拉长。
 * 超长内容截断显示，完整值放在 title 里悬停可见。
 */
export function TableText({ value, className }: { value: string | number | null | undefined; className?: string }) {
  const text = value === null || value === undefined ? "" : String(value);
  return (
    <span className={cn("block truncate", className)} title={text || undefined}>
      {text || <span className="text-zinc-300">—</span>}
    </span>
  );
}

/**
 * 内部业务 ID 单元格：默认只显示首尾片段（中间省略，保留有区分度的尾段），
 * 悬停看全量，点击复制完整 ID——批量操作和排障仍然拿得到原值。
 */
export function CopyableId({ value, className }: { value: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  const copy = useCallback(() => {
    void (async () => {
      try {
        await navigator.clipboard?.writeText(value);
      } catch {
        return;
      }
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    })();
  }, [value]);

  if (!value) return <TableText value="" className={className} />;
  return (
    <span className={cn("inline-flex items-center gap-1", className)}>
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-zinc-500" title={value}>{shortenId(value)}</span>
      <button
        type="button"
        className="shrink-0 text-zinc-400 transition-colors hover:text-zinc-700"
        aria-label={`复制完整 ID ${value}`}
        title={`复制完整 ID：${value}`}
        onClick={copy}
      >
        {copied ? <Check className="size-3 text-emerald-600" /> : <Copy className="size-3" />}
      </button>
    </span>
  );
}

function shortenId(value: string) {
  if (value.length <= 16) return value;
  return `${value.slice(0, 6)}…${value.slice(-6)}`;
}
