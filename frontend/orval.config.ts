import { defineConfig } from "orval";

export default defineConfig({
  tupai: {
    input: "../backend/openapi.json",
    output: {
      mode: "single",
      target: "./src/api/generated/client.ts",
      schemas: "./src/api/generated/models",
      client: "react-query",
      httpClient: "axios",
      prettier: true,
      override: {
        mutator: { path: "./src/api/http.ts", name: "customInstance" }
      }
    }
  }
});
