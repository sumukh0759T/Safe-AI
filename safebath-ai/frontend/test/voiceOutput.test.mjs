import test from "node:test";
import assert from "node:assert/strict";
import { approvedPromptForUtterance } from "../voiceOutput.mjs";

test("maps only explicit user intents to approved prerecorded prompt ids", () => {
  assert.equal(approvedPromptForUtterance("Help me, I fell"), 2);
  assert.equal(approvedPromptForUtterance("Read the emergency procedure"), 3);
  assert.equal(approvedPromptForUtterance("Are you okay?"), 1);
  assert.equal(approvedPromptForUtterance("Please do a safety check"), 4);
  assert.equal(approvedPromptForUtterance("Tell me a joke"), null);
  assert.equal(approvedPromptForUtterance("Gemini said I need help"), 2);
});
