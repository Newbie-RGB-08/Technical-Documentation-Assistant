import { describe, expect, it } from "vitest";

import {
  DEFAULT_PROXY_TARGET,
  PROXY_PREFIX,
  buildProxyConfig,
  resolveProxyTarget,
} from "../../config/proxy";

// 以文本方式读取 vite.config.ts：直接 import 该文件会在 jsdom 下触发 esbuild 运行时，
// 这里只断言接线是否仍然指向可配置的 buildProxyConfig。
const rawConfigs = import.meta.glob("../../vite.config.ts", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("vite 代理目标解析", () => {
  it("默认指向 IPv4 回环 8000（避免 localhost 解析到 ::1）", () => {
    expect(DEFAULT_PROXY_TARGET).toBe("http://127.0.0.1:8000");
    expect(resolveProxyTarget({})).toBe(DEFAULT_PROXY_TARGET);
  });

  it("VITE_PROXY_TARGET 可覆盖（例如后端跑在 8003）", () => {
    expect(resolveProxyTarget({ VITE_PROXY_TARGET: "http://127.0.0.1:8003" })).toBe(
      "http://127.0.0.1:8003",
    );
  });

  it("空白值视为未设置，回落默认", () => {
    expect(resolveProxyTarget({ VITE_PROXY_TARGET: "   " })).toBe(DEFAULT_PROXY_TARGET);
    expect(resolveProxyTarget({ VITE_PROXY_TARGET: "" })).toBe(DEFAULT_PROXY_TARGET);
  });

  it("去掉首尾空白", () => {
    expect(
      resolveProxyTarget({ VITE_PROXY_TARGET: "  http://127.0.0.1:9000  " }),
    ).toBe("http://127.0.0.1:9000");
  });

  it("生成的代理键为 /api 且开启 changeOrigin", () => {
    const proxy = buildProxyConfig({});
    expect(Object.keys(proxy)).toEqual([PROXY_PREFIX]);
    expect(proxy[PROXY_PREFIX]).toEqual({
      target: DEFAULT_PROXY_TARGET,
      changeOrigin: true,
    });
  });

  it("vite.config.ts 实际接线 buildProxyConfig（防止再次写死端口）", () => {
    const cfg = rawConfigs["../../vite.config.ts"];
    expect(cfg).toBeTruthy();
    expect(cfg).toContain('import { buildProxyConfig } from "./config/proxy"');
    expect(cfg).toContain("proxy: buildProxyConfig(process.env)");
    // 不再允许出现硬编码的 http://localhost:8000
    expect(cfg).not.toContain("http://localhost:8000");
  }, 20000);
});
