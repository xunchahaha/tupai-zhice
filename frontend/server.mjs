import http from "node:http";
import https from "node:https";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const dist = path.join(root, "dist");
const indexFile = path.join(dist, "index.html");
const host = process.env.HOST || "0.0.0.0";
const port = Number(process.env.PORT || 5173);
const apiTarget = new URL(process.env.API_PROXY_TARGET || "http://127.0.0.1:8000");

const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".webp", "image/webp"],
  [".woff", "font/woff"],
  [".woff2", "font/woff2"],
]);

function proxyApi(request, response) {
  const transport = apiTarget.protocol === "https:" ? https : http;
  const headers = {
    ...request.headers,
    host: apiTarget.host,
    "x-forwarded-host": request.headers.host || "",
    "x-forwarded-proto": request.headers["x-forwarded-proto"] || "http",
  };
  const upstream = transport.request(
    {
      protocol: apiTarget.protocol,
      hostname: apiTarget.hostname,
      port: apiTarget.port || undefined,
      method: request.method,
      path: request.url,
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );
  upstream.on("error", (error) => {
    if (!response.headersSent) response.writeHead(502, { "content-type": "application/json" });
    response.end(JSON.stringify({ detail: `后端连接失败：${error.message}` }));
  });
  request.on("aborted", () => upstream.destroy());
  request.pipe(upstream);
}

async function existingFile(pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const candidate = path.resolve(dist, `.${decoded}`);
  if (candidate !== dist && !candidate.startsWith(`${dist}${path.sep}`)) return null;
  try {
    const info = await stat(candidate);
    return info.isFile() ? candidate : null;
  } catch {
    return null;
  }
}

function sendFile(request, response, file) {
  const extension = path.extname(file).toLowerCase();
  response.setHeader("content-type", contentTypes.get(extension) || "application/octet-stream");
  response.setHeader(
    "cache-control",
    file.includes(`${path.sep}assets${path.sep}`)
      ? "public, max-age=31536000, immutable"
      : "no-cache",
  );
  if (request.method === "HEAD") return response.end();
  const stream = createReadStream(file);
  stream.on("error", () => {
    if (!response.headersSent) response.writeHead(500);
    response.end("Internal Server Error");
  });
  stream.pipe(response);
}

const server = http.createServer(async (request, response) => {
  const pathname = new URL(request.url || "/", "http://localhost").pathname;
  if (pathname === "/api" || pathname.startsWith("/api/")) {
    proxyApi(request, response);
    return;
  }
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { allow: "GET, HEAD" });
    response.end("Method Not Allowed");
    return;
  }
  const file = await existingFile(pathname);
  sendFile(request, response, file || indexFile);
});

server.listen(port, host, () => {
  console.log(`Frontend listening on http://${host}:${port}; /api -> ${apiTarget.origin}`);
});
