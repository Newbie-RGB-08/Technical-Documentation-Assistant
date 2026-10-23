/**
 * 开发服务器代理配置（避免端口写死导致 404）。
 *
 * 背景：后端曾在 8003 端口运行，而 vite 代理固定指向 8000，或 8000 上残留
 * 旧版进程（未加载新路由）时，前端会收到 FastAPI 的 {"detail":"Not Found"}。
 * 这里把目标改为可配置，默认对齐后端默认端口 8000 并强制 IPv4 回环，
 * 避免 Windows 下 localhost 优先解析到 ::1 造成偶发拒连。
 */

export const PROXY_PREFIX = "/api";

export const DEFAULT_PROXY_TARGET = "http://127.0.0.1:8000";

/**
 * 解析代理目标：优先 VITE_PROXY_TARGET，去空白；空串视为未设置。
 *
 * 环境变量由调用方（vite.config.ts）显式传入，避免本模块直接依赖 Node 的
 * `process`（该模块也会被前端 tsc 检查，而 tsconfig 未引入 node 类型）。
 */
export function resolveProxyTarget(
  env: Record<string, string | undefined> = {},
): string {
  const raw = env.VITE_PROXY_TARGET;
  const trimmed = typeof raw === "string" ? raw.trim() : "";
  return trimmed || DEFAULT_PROXY_TARGET;
}

/** 生成 vite `server.proxy` 配置。 */
export function buildProxyConfig(env: Record<string, string | undefined> = {}) {
  return {
    [PROXY_PREFIX]: {
      target: resolveProxyTarget(env),
      changeOrigin: true,
    },
  };
}
