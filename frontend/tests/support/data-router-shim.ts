// jsdom 提供的 AbortSignal 不被 Node 内置 Request 认可，react-router 的 data router 每次导航
// （包括 <Navigate> 与点击链接）都会在 new Request(..., { signal }) 处抛错。
// 这些测试不需要可中断的请求，丢掉 signal 让 Request 自带的即可。
const NativeRequest = globalThis.Request;

globalThis.Request = class extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    super(input, init ? { ...init, signal: undefined } : init);
  }
} as typeof Request;
