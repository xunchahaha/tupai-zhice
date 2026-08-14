import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/button";

interface State {
  error: Error | null;
}

/** 渲染期异常此前会让整页变空白，没有任何提示也没有恢复入口。 */
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
        <div className="w-full max-w-lg border border-zinc-200 bg-white p-6">
          <h1 className="text-lg font-semibold text-zinc-900">页面出错了</h1>
          <p className="mt-2 text-sm text-zinc-600">
            这一页在渲染时抛出了异常，其余功能不受影响。可以重试，或回到总览重新进入。
          </p>
          <pre className="mt-4 max-h-40 overflow-auto border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-600">
            {error.message}
          </pre>
          <div className="mt-5 flex gap-2">
            <Button onClick={() => this.setState({ error: null })}>重试</Button>
            <Button variant="outline" onClick={() => window.location.assign("/")}>
              回到总览
            </Button>
          </div>
        </div>
      </div>
    );
  }
}
