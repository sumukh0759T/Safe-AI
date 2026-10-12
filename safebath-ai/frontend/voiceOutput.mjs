// The mapping intentionally uses only the user's utterance, never Gemini text.
export function approvedPromptForUtterance(text) {
  const value = String(text || "").toLowerCase();
  if (/\b(emergency procedure|what should i do in an emergency)\b/.test(value)) return 3;
  if (/\b(help|emergency|sos|drowning|can't breathe|cannot breathe|not breathing|bleeding|fell|fall|unconscious|chest pain)\b/.test(value)) return 2;
  if (/\b(are you okay|are you ok|check[ -]?in)\b/.test(value)) return 1;
  if (/\b(safety check|check for safety|is it safe)\b/.test(value)) return 4;
  return null;
}
