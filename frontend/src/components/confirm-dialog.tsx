import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  pending?: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认",
  cancelLabel = "取消",
  danger = false,
  pending = false,
  onOpenChange,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={(next) => { if (!pending) onOpenChange(next); }}>
      <DialogContent className="max-w-md">
        <div className="flex items-start gap-3 pr-6">
          <div className={danger ? "mt-0.5 rounded-full bg-red-50 p-2 text-red-600" : "mt-0.5 rounded-full bg-amber-50 p-2 text-amber-600"}>
            <AlertTriangle className="size-4" />
          </div>
          <div className="min-w-0">
            <DialogTitle className="text-base font-semibold text-zinc-950">{title}</DialogTitle>
            <DialogDescription className="mt-2 text-sm leading-6 text-zinc-500">{description}</DialogDescription>
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2 border-t border-zinc-100 pt-4">
          <Button variant="outline" disabled={pending} onClick={() => onOpenChange(false)}>{cancelLabel}</Button>
          <Button variant={danger ? "danger" : "primary"} disabled={pending} onClick={onConfirm}>{pending ? "处理中" : confirmLabel}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
