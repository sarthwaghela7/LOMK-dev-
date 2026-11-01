import test from "node:test";
import assert from "node:assert/strict";

import { leadCallMade, leadCallMadeOnNote } from "./leadCallMade.js";

const nextDay = { outbound_call_id: "c1", outbound_call_date: "2026-10-13", outbound_call_same_day: false, same_day_outbound_call_id: "" };
const sameDay = { outbound_call_id: "c2", outbound_call_date: "2026-10-12", outbound_call_same_day: true, same_day_outbound_call_id: "c2" };

test("a lead is green once its call was made, the same day or any later day", () => {
  assert.equal(leadCallMade(sameDay), true);
  assert.equal(leadCallMade(nextDay), true);
  assert.equal(leadCallMade({ outbound_call_id: "", same_day_outbound_call_id: "" }), false);
  assert.equal(leadCallMade({}), false);
  assert.equal(leadCallMade(null), false);
  assert.equal(leadCallMade({ same_day_outbound_call_id: "old-field-only" }), true);        // an older payload still colours the row
});

test("'Call made on' is shown only when the call was made on a different day, as MM/DD/YYYY", () => {
  assert.equal(leadCallMadeOnNote(nextDay), "Call made on 10/13/2026");
  assert.equal(leadCallMadeOnNote({ ...nextDay, outbound_call_date: "2026-01-05" }), "Call made on 01/05/2026");
  assert.equal(leadCallMadeOnNote(sameDay), "");                                             // made on the assigned day: nothing extra
  assert.equal(leadCallMadeOnNote({ outbound_call_id: "", outbound_call_date: "" }), "");   // no call yet: nothing to say
  assert.equal(leadCallMadeOnNote({ outbound_call_id: "c3", outbound_call_same_day: false, outbound_call_date: "" }), "");
  assert.equal(leadCallMadeOnNote(undefined), "");
});

test("the date is a calendar date: no time-zone shift can move it", () => {
  for (const tz of ["America/Chicago", "Asia/Kolkata", "Pacific/Auckland"]) {
    process.env.TZ = tz;
    assert.equal(leadCallMadeOnNote({ outbound_call_id: "c", outbound_call_same_day: false, outbound_call_date: "2026-10-13" }), "Call made on 10/13/2026", tz);
  }
});
