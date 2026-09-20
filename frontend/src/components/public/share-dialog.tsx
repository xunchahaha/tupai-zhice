import QRCode from "react-qr-code";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { absoluteUrl, icsUrl, webcalUrl } from "@/lib/public-api";
import { errorMessage } from "@/lib/format";

export interface ShareableLink {
  displayName: string;
  /** 完整 H5 链接（含明文 token），只在创建/轮换响应里出现一次。 */
  publicUrl: string;
}

interface ShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  link: ShareableLink | null;
}

function extractPublicToken(publicUrl: string): string | null {
  const match = publicUrl.match(/\/public\/t\/([^/?#]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function copyText(value: string, successMessage: string) {
  try {
    // 非 HTTPS 或旧浏览器下 navigator.clipboard 可能不存在，这里必须退化成提示而不是抛错。
    if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不允许自动复制");
    await navigator.clipboard.writeText(value);
    toast.success(successMessage);
  } catch (error) {
    toast.error(`复制失败，请手动选中复制：${errorMessage(error)}`);
  }
}

/**
 * 分享弹窗（06 §3 B4）：二维码 + H5 链接 + ICS webcal 订阅链接。
 * 必须传入明文链接——token 只有创建/轮换响应返回一次，历史链接无法重新分享。
 */
export function ShareDialog({ open, onOpenChange, link }: ShareDialogProps) {
  if (!link) return null;
  const token = extractPublicToken(link.publicUrl);
  const httpsIcs = token ? absoluteUrl(icsUrl(token)) : null;
  const webcal = token ? webcalUrl(token) : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogTitle className="text-base font-semibold">分享「{link.displayName}」</DialogTitle>
        <DialogDescription className="mt-1 text-sm text-zinc-500">
          扫码或复制链接发给老师 / 家长 / 学生，免登录直接查看课表。
        </DialogDescription>

        <div className="mt-4 flex justify-center rounded-xl border border-zinc-200 bg-white p-4">
          <div className="w-44" data-testid="public-share-qr">
            <QRCode value={link.publicUrl} className="size-full h-auto w-full" />
          </div>
        </div>

        <div className="mt-4 space-y-3">
          <ShareField
            label="H5 页面链接"
            value={link.publicUrl}
            copyLabel="复制 H5 链接"
            onCopy={() => void copyText(link.publicUrl, "H5 链接已复制")}
          />
          {webcal ? (
            <ShareField
              label="日历订阅（webcal）"
              value={webcal}
              copyLabel="复制订阅链接"
              onCopy={() => void copyText(webcal, "订阅链接已复制")}
            />
          ) : null}
          {httpsIcs ? (
            <ShareField
              label="ICS 文件直链（https）"
              value={httpsIcs}
              copyLabel="复制 ICS 链接"
              onCopy={() => void copyText(httpsIcs, "ICS 链接已复制")}
            />
          ) : null}
        </div>

        <p className="mt-4 rounded-md bg-zinc-50 p-2.5 text-xs leading-5 text-zinc-500">
          订阅后由日历应用定期刷新，Google 约 12-24 小时；链接包含凭证，请勿发布到公开网络。
        </p>
        <div className="mt-4 flex justify-end">
          <Button variant="outline" onClick={() => onOpenChange(false)}>关闭</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ShareField({ label, value, copyLabel, onCopy }: { label: string; value: string; copyLabel: string; onCopy: () => void }) {
  return (
    <div>
      <div className="text-xs font-medium text-zinc-500">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        <input
          readOnly
          value={value}
          aria-label={label}
          onFocus={(event) => event.currentTarget.select()}
          className="h-9 min-w-0 flex-1 rounded-md border border-zinc-300 bg-zinc-50 px-2.5 font-mono text-xs text-zinc-600 outline-none"
        />
        <Button size="sm" variant="outline" className="shrink-0" onClick={onCopy}>
          {copyLabel}
        </Button>
      </div>
    </div>
  );
}
