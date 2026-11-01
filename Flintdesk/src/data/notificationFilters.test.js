import test from "node:test";
import assert from "node:assert/strict";
import { emptyNotificationMessage, filterNotifications } from "./notificationFilters.js";

const items = [{ id: "1", read_at: null }, { id: "2", read_at: "2026-10-01T00:00:00Z" }, { id: "3", read_at: null }];

test("filters by read state without mutating the list", () => {
  assert.deepEqual(filterNotifications(items, "all").map((i) => i.id), ["1", "2", "3"]);
  assert.deepEqual(filterNotifications(items, "unread").map((i) => i.id), ["1", "3"]);
  assert.deepEqual(filterNotifications(items, "read").map((i) => i.id), ["2"]);
  assert.equal(items.length, 3);
  assert.deepEqual(filterNotifications(null, "unread"), []);
});

test("empty-state wording per filter", () => {
  assert.equal(emptyNotificationMessage("unread"), "No unread notifications.");
  assert.equal(emptyNotificationMessage("read"), "No read notifications.");
  assert.equal(emptyNotificationMessage("all"), "No notifications.");
});
