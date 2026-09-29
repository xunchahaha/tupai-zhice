import { useState } from "react";

import { type ClassGroupResponse, type RoomResponse, type TeacherResponse, type TimeSlotResponse } from "@/api/generated/models";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import { formatSlot } from "@/lib/format";
import { GOAL_SUBJECT_TYPE_OPTIONS, goalSubjectTypeLabel } from "@/lib/goal";

/**
 * 禁排占位项补参表单（MEM-D3/A4）：主体类型+主体下拉+时段多选，保存走
 * PATCH /goals/{id}/checklist 整体替换；参数齐了该项从「恒不通过」恢复参与验收。
 */
export function ForbiddenParamForm({
  pending,
  teacherOptions,
  classOptions,
  roomOptions,
  slotOptions,
  onCancel,
  onSave,
}: {
  pending?: boolean;
  teacherOptions: TeacherResponse[];
  classOptions: ClassGroupResponse[];
  roomOptions: RoomResponse[];
  slotOptions: TimeSlotResponse[];
  onCancel: () => void;
  onSave: (subjectType: string, subjectIds: string[], slotBusinessIds: string[]) => void;
}) {
  const [subjectType, setSubjectType] = useState("teacher");
  const [subjectId, setSubjectId] = useState("");
  const [slotIds, setSlotIds] = useState<string[]>([]);
  const subjectOptions = subjectType === "teacher" ? teacherOptions : subjectType === "cohort" ? classOptions : roomOptions;
  const slotLabel = (id: string): string => {
    const slot = slotOptions.find((item) => item.business_id === id);
    return slot ? `${slot.weekday} ${slot.start_time}-${slot.end_time}` : formatSlot(id);
  };
  const valid = Boolean(subjectId) && slotIds.length > 0;
  return (
    <form
      className="mt-2 rounded-md border border-zinc-200 bg-zinc-50/60 p-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onSave(subjectType, [subjectId], slotIds);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-sm text-zinc-700">
          主体类型
          <Select
            aria-label="禁排主体类型"
            selectSize="sm"
            containerClassName="mt-1"
            value={subjectType}
            onChange={(event) => {
              setSubjectType(event.target.value);
              setSubjectId("");
            }}
          >
            {GOAL_SUBJECT_TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </Select>
        </label>
        <label className="block text-sm text-zinc-700">
          主体
          <Select
            aria-label="禁排主体"
            selectSize="sm"
            containerClassName="mt-1"
            value={subjectId}
            onChange={(event) => setSubjectId(event.target.value)}
          >
            <option value="">选择{goalSubjectTypeLabel(subjectType)}…</option>
            {subjectOptions.map((item) => (
              <option key={item.business_id} value={item.business_id}>{item.name}</option>
            ))}
          </Select>
        </label>
      </div>
      <div className="mt-3 text-sm text-zinc-700">
        禁排时段（多选）
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          {slotIds.map((id) => (
            <span key={id} className="inline-flex items-center gap-1 rounded bg-zinc-100 px-1.5 py-0.5 text-xs text-zinc-700">
              {slotLabel(id)}
              <button
                type="button"
                aria-label={`移除 ${slotLabel(id)}`}
                className="text-zinc-400 hover:text-zinc-700"
                onClick={() => setSlotIds((ids) => ids.filter((item) => item !== id))}
              >
                ×
              </button>
            </span>
          ))}
          <Select
            aria-label="添加禁排时段"
            selectSize="sm"
            containerClassName="w-44"
            value=""
            onChange={(event) => {
              if (event.target.value && !slotIds.includes(event.target.value)) {
                setSlotIds((ids) => [...ids, event.target.value]);
              }
            }}
          >
            <option value="">添加时段…</option>
            {slotOptions.filter((item) => !slotIds.includes(item.business_id)).map((item) => (
              <option key={item.business_id} value={item.business_id}>
                {item.weekday} {item.start_time}-{item.end_time}
              </option>
            ))}
          </Select>
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <Button type="submit" size="sm" disabled={pending || !valid}>
          保存参数
        </Button>
        <Button type="button" size="sm" variant="outline" onClick={onCancel} disabled={pending}>
          取消
        </Button>
      </div>
    </form>
  );
}
