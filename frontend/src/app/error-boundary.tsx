import { Component, type ErrorInfo, type ReactNode } from "react";
import { isRouteErrorResponse, useRouteError } from "react-router-dom";

import { Button } from "@/components/ui/button";

interface State {
  error: Error | null;
}

/** React Router 路由层级异常捕获组件，避免抛错时直接白屏 */
export function RouteErrorElement() {
  const error = useRouteError();
  let message = "未知渲染异常";
  if (isRouteErrorResponse(error)) {
    message = `${error.status} ${error.statusText}: ${typeof error.data === "string" ? error.data : JSON.stringify(error.data)}`;
  } else if (error instanceof Error) {
    message = error.message;
  } else if (typeof error === "string") {
    message = error;
  }

  return (
    <div className="grid min-h-dvh place-items-center bg-zinc-50 p-6">
      <div className="w-full max-w-lg rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">
        <h1 className="text-lg font-semibold text-zinc-900">页面加载异常</h1>
        <p className="mt-2 text-sm text-zinc-600">
          这一页在渲染时发生了异常，其余模块不受影响。可以重试，或回到总览重新进入。
        </p>
        <pre className="mt-4 max-h-40 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-600 font-mono">
          {message}
        </pre>
        <div className="mt-5 flex gap-2">
          <Button onClick={() => window.location.reload()}>刷新页面</Button>
          <Button variant="outline" onClick={() => window.location.assign("/overview")}>
            回到总览
          </Button>
        </div>
      </div>
    </div>
  );
}

/** 渲染期组件树异常捕获 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("页面渲染失败", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="grid min-h-dvh place-items-center bg-zinc-50 p-6">
        <div className="w-full max-w-lg rounded-xl border border-zinc-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold text-zinc-900">页面出错了</h1>
          <p className="mt-2 text-sm text-zinc-600">
            这一页在渲染时抛出了异常，其余功能不受影响。可以重试，或回到总览重新进入。
          </p>
          <pre className="mt-4 max-h-40 overflow-auto rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-600 font-mono">
            {error.message}
          </pre>
          <div className="mt-5 flex gap-2">
            <Button onClick={() => this.setState({ error: null })}>重试</Button>
            <Button variant="outline" onClick={() => window.location.assign("/overview")}>
              回到总览
            </Button>
          </div>
        </div>
      </div>
    );
  }
}
