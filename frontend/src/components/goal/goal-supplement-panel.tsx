import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import {
  getGetGoalApiV1GoalsGoalIdGetQueryKey,
  useListClassGroupsApiV1ClassGroupsGet,
  useListRoomsApiV1RoomsGet,
  useListTeachersApiV1TeachersGet,
  useListTimeSlotsApiV1TimeSlotsGet,
  useReplaceGoalChecklistApiV1GoalsGoalIdChecklistPatch,
} from "@/api/generated/client";
import {
  type ClassGroupResponse,
  type GoalChecklistItem,
  type GoalDetailResponse,
  type RoomResponse,
  type TeacherResponse,
  type TimeSlotResponse,
} from "@/api/generated/models";
import { ForbiddenParamForm } from "@/components/goal/forbidden-param-form";
import { Button } from "@/components/ui/button";
import { asArray, errorMessage } from "@/lib/format";
import { goalKindLabel, goalSubjectTypeLabel, isNeedsParamsItem, type GoalChecklistEntry } from "@/lib/goal";

/** 补参只需要目标的 id 与整份清单；列表行（GoalResponse）与详情都满足。 */
export type GoalSupplementTarget = Pick<GoalDetailResponse, "id" | "checklist">;

function entryKey(entry: GoalChecklistEntry, index = 0): string {
  return String(entry.key ?? `${entry.kind}-${index}`);
}

/**
 * 单个禁排占位项的补参表单（已接好数据）：自己取教师/班级/教室/时段选项，
 * 保存走 PATCH /goals/{id}/checklist 整份替换（服务端复验 key/底线并记版本）。
 */
export function GoalChecklistParamForm({
  goal,
  entry,
  onCancel,
  onSaved,
}: {
  goal: GoalSupplementTarget;
  entry: GoalChecklistEntry;
  onCancel: () => void;
  onSaved?: () => void;
}) {
  const client = useQueryClient();
  const teachers = useListTeachersApiV1TeachersGet();
  const classes = useListClassGroupsApiV1ClassGroupsGet();
  const rooms = useListRoomsApiV1RoomsGet();
  const slots = useListTimeSlotsApiV1TimeSlotsGet();
  const replaceChecklist = useReplaceGoalChecklistApiV1GoalsGoalIdChecklistPatch({
    mutation: {
      onSuccess: () => {
        toast.success("条件已补充");
        void client.invalidateQueries({ queryKey: getGetGoalApiV1GoalsGoalIdGetQueryKey(goal.id) });
        onSaved?.();
      },
      onError: (error) => toast.error(errorMessage(error)),
    },
  });
  /** 量化禁排占位项：重建整份清单再提交，其余项原样保留。 */
  const quantizeForbidden = (subjectType: string, subjectIds: string[], slotBusinessIds: string[]) => {
    const updated: GoalChecklistItem = {
      key: String(entry.key),
      kind: "forbidden_slot_free",
      requirement: `${goalSubjectTypeLabel(subjectType)} ${subjectIds.join("、")} 不占用指定时段（${slotBusinessIds.join("、")}）——独立复核，不信任求解器自报`,
      params: {
        subject_type: subjectType,
        subject_ids: subjectIds,
        slot_business_ids: slotBusinessIds,
      },
    };
    replaceChecklist.mutate({
      goalId: goal.id,
      data: {
        checklist: goal.checklist.map((item) =>
          String(item.key) === String(entry.key) ? updated : (item as unknown as GoalChecklistItem),
        ),
      },
    });
  };
  return (
    <ForbiddenParamForm
      pending={replaceChecklist.isPending}
      teacherOptions={asArray<TeacherResponse>(teachers.data)}
      classOptions={asArray<ClassGroupResponse>(classes.data)}
      roomOptions={asArray<RoomResponse>(rooms.data)}
      slotOptions={asArray<TimeSlotResponse>(slots.data)}
      onCancel={onCancel}
      onSave={quantizeForbidden}
    />
  );
}

/**
 * 「还差一个条件」的行内补充：列出目标清单里参数待补的禁排占位项，逐项给「补齐参数」入口；
 * 参数齐了该项从「恒不通过」恢复参与验收。没有待补项时不渲染任何东西。
 * autoOpen 让表单一进来就展开（例如从「稍后补充」链接落地）；保存成功后回调 onSaved。
 */
export function GoalSupplementPanel({
  goal,
  autoOpen = false,
  onSaved,
}: {
  goal: GoalSupplementTarget;
  autoOpen?: boolean;
  onSaved?: () => void;
}) {
  const entries = (goal.checklist ?? []) as GoalChecklistEntry[];
  const items = entries
    .map((entry, index) => ({ entry, key: entryKey(entry, index) }))
    .filter(({ entry }) => isNeedsParamsItem(entry));
  // 记录与默认状态相反的项：autoOpen 时默认全开、点一下收起，否则默认全收；
  // 绑定 goal.id，换目标后自动回到默认，不必在 effect 里复位。
  const [flipped, setFlipped] = useState<{ goalId: string; keys: string[] }>({ goalId: goal.id, keys: [] });
  const flippedKeys = flipped.goalId === goal.id ? flipped.keys : [];
  const isOpen = (key: string) => autoOpen !== flippedKeys.includes(key);
  // 函数式更新：保存成功的回调可能晚于点击，不能依赖闭包里旧的 flippedKeys。
  const setOpen = (key: string, open: boolean) =>
    setFlipped((prev) => {
      const keys = prev.goalId === goal.id ? prev.keys : [];
      const currentlyOpen = autoOpen !== keys.includes(key);
      if (currentlyOpen === open) return prev;
      return { goalId: goal.id, keys: keys.includes(key) ? keys.filter((item) => item !== key) : [...keys, key] };
    });
  if (!items.length) return null;
  return (
    <ul className="space-y-1.5">
      {items.map(({ entry, key }) => {
        const open = isOpen(key);
        return (
          <li key={key} className="rounded border border-zinc-100 px-3 py-2 text-xs leading-5 text-zinc-700">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium">{goalKindLabel(entry.kind)}</span>
              <span>· {entry.requirement ?? ""}</span>
              <Button size="sm" variant="outline" aria-expanded={open} onClick={() => setOpen(key, !open)}>
                补齐参数
              </Button>
            </div>
            {open ? (
              <GoalChecklistParamForm
                goal={goal}
                entry={entry}
                onCancel={() => setOpen(key, false)}
                onSaved={() => {
                  setOpen(key, false);
                  onSaved?.();
                }}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
