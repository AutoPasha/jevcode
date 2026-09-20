import assert from "node:assert/strict";
import test from "node:test";

import { dedupe } from "../dedupe.js";

test("keeps the first of each", () => {
  assert.deepEqual(dedupe([3, 1, 3, 2, 1]), [3, 1, 2]);
});

test("takes a key function", () => {
  const rows = [{ id: 1, n: "a" }, { id: 2, n: "b" }, { id: 1, n: "c" }];
  assert.deepEqual(dedupe(rows, (r) => r.id).map((r) => r.n), ["a", "b"]);
});
