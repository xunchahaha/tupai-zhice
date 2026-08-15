import { Check, Copy } from "lucide-react";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";

/**
 * 多值单元格。一个班级会同时有多个班型、多个教师（走班制下这是常态，不是脏数据），
 * 单值显示会直接丢信息。
 *
 * 列宽固定、展开也只在列内换行：展开靠增加行高而不是撑宽表格——表格外层是
 * overflow-x-auto，绝对定位的浮层会被裁掉，所以不用弹层。
 * 未展开时完整清单挂在 title 上，悬停即可看全。
 */
export function TagList({ values, className, visible = 2, empty = "—" }: { values: string[]; className?: string; visible?: number; empty?: ReactNode }) {
  const [expanded, setExpanded] = useState(false);
  if (!values.length) return <span className={cn("block truncate text-zinc-300", className)}>{empty}</span>;
  const shown = expanded ? values : values.slice(0, visible);
  const hidden = values.length - shown.length;
  const full = values.join("、");
  return (
    <div className={cn("flex flex-wrap items-center gap-1 whitespace-normal py-0.5", className)} title={full}>
      {shown.map((value) => (
        <span key={value} className="max-w-full truncate rounded bg-zinc-100 px-1.5 py-0.5 text-[11px] leading-4 text-zinc-700">{value}</span>
      ))}
      {hidden > 0 || expanded ? (
        <button
          type="button"
          className="shrink-0 rounded px-1 text-[11px] leading-4 text-blue-600 hover:bg-blue-50"
          aria-expanded={expanded}
          title={full}
          onClick={() => setExpanded((open) => !open)}
        >
          {expanded ? "收起" : `+${hidden}`}
        </button>
      ) : null}
    </div>
  );
}

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
