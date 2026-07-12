import { defineConfig } from "vitest/config";

// test/handlers.node.test.ts runs under `node --test` (see package.json), not vitest: envio's
// HandlerLoader needs its tsx/esm module hooks, which work under plain node (the same path
// `envio start` uses) but not inside vitest's module pipeline.
export default defineConfig({
  test: {
    exclude: ["**/node_modules/**", "test/*.node.test.ts"],
  },
});
