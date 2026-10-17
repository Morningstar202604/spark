// P1：前端 API 客户端 codegen 配置（数据源 = 本地服务 /api/openapi.json）
// 用法：先起 web 服务，再 npm run gen:api
import { defineConfig } from "orval";

export default defineConfig({
  spark: {
    input: "http://127.0.0.1:8000/api/openapi.json",
    output: {
      target: "./src/api/generated.ts",
      client: "react-query",
      mode: "tags-split",
      override: {
        query: { useQuery: true, useMutation: true },
      },
    },
  },
});
