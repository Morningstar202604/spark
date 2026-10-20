// P1：前端 API 合同 codegen（数据源 = 仓库内提交的 web/openapi.json）
//
// 只生成 **TypeScript 类型**，不生成 HTTP 客户端：项目现有调用层是 fetch +
// zustand（web/src/api.ts），引入 axios/react-query 会平白多两个运行时依赖。
// 类型层解决真正的问题——后端改字段时前端在 tsc 阶段就能发现。
//
// 重新生成：python scripts/export_openapi.py && cd web && npm run gen:api
import { defineConfig } from "orval";

export default defineConfig({
  spark: {
    input: "./openapi.json",
    output: {
      target: "./src/api/schema.ts",
      client: "fetch",
      mode: "single",
      clean: true,
      override: {
        // Types only: no hooks, no client wrapper.
        useTypeOverInterfaces: true,
      },
    },
  },
});
