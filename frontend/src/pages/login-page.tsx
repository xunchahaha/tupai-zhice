import { zodResolver } from "@hookform/resolvers/zod";
import { ShieldCheck } from "lucide-react";
import { useForm } from "react-hook-form";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { useQueryClient } from "@tanstack/react-query";
import { loginApiV1AuthTokenPost } from "@/api/generated/client";
import { authStore } from "@/api/http";
import { Button } from "@/components/ui/button";
import { errorMessage } from "@/lib/format";
import { ROUTES } from "@/lib/routes";

const schema = z.object({ username: z.string().min(1, "请输入用户名"), password: z.string().min(1, "请输入密码") });
type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: { username: "admin", password: "tupai-demo-admin-2026!" } });
  const submit = async (values: FormValues) => {
    try {
      const response = await loginApiV1AuthTokenPost(values);
      authStore.set(response.access_token);
      await queryClient.invalidateQueries();
      navigate(ROUTES.assistant, { replace: true });
    } catch (error) { toast.error(errorMessage(error)); }
  };
  return <main className="grid min-h-screen place-items-center bg-zinc-50 px-4"><section className="w-full max-w-sm rounded-xl border border-zinc-200/90 bg-white p-7 shadow-sm transition-all duration-200 hover:shadow-md animate-zoom-in"><div className="mb-7 flex items-center gap-2"><span className="grid size-8 place-items-center rounded-md bg-blue-600 text-white"><ShieldCheck className="size-4" /></span><div><h1 className="text-base font-semibold">途排智策</h1><p className="text-xs text-zinc-400">教务排课控制台</p></div></div><form className="space-y-4" onSubmit={form.handleSubmit(submit)}><label className="block text-sm text-zinc-700">用户名<input className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm shadow-2xs outline-none transition-all duration-150 focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20" autoComplete="username" {...form.register("username")} /></label><label className="block text-sm text-zinc-700">密码<input className="mt-1.5 h-9 w-full rounded-md border border-zinc-300 bg-white px-2.5 text-sm shadow-2xs outline-none transition-all duration-150 focus:border-blue-600 focus:ring-2 focus:ring-blue-500/20" type="password" autoComplete="current-password" {...form.register("password")} /></label><div className="min-h-4 text-xs text-red-600">{form.formState.errors.username?.message ?? form.formState.errors.password?.message}</div><Button className="w-full" type="submit" disabled={form.formState.isSubmitting}>{form.formState.isSubmitting ? "登录中" : "登录"}</Button></form></section></main>;
}
