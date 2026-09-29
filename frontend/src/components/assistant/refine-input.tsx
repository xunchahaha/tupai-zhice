import { Sparkles } from "lucide-react";
import { type ReactNode } from "react";

import { Button } from "@/components/ui/button";

/**
 * 行内的「还要改什么？」输入：在当前结果/理解的基础上补一句话再解析。
 * 受控（文本放在任务状态里），因为「填入指令框」也要往这里写建议指令。
 */
export function RefineInput({
  label,
  placeholder,
  submitLabel = "重新解析",
  value,
  onChange,
  onSubmit,
  onCancel,
  disabled,
  disabledReason,
}: {
  label: string;
  placeholder: string;
  submitLabel?: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  disabled: boolean;
  disabledReason?: ReactNode;
}) {
  return (
    <div className="mt-4 border-t border-zinc-100 pt-4">
      <label className="block text-xs font-medium text-zinc-600">
        {label}
        <textarea
          aria-label={label}
          className="mt-1.5 min-h-20 w-full rounded-md border border-zinc-300 bg-white p-3 text-sm font-normal text-zinc-900 outline-none focus:border-blue-500"
          placeholder={placeholder}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
      {disabledReason ? <div className="mt-1 text-xs text-amber-800">{disabledReason}</div> : null}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" onClick={onSubmit} disabled={disabled || value.trim().length < 2}>
          <Sparkles className="size-3.5" />{submitLabel}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>收起</Button>
      </div>
    </div>
  );
}
