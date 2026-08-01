/**
 * Per-message text direction.
 *
 * A conversation routinely mixes Arabic prose with Latin URLs and product
 * names, so direction is decided per message from its first *strong* character
 * — the Unicode Bidi "first strong" heuristic — rather than from the UI
 * language. Neutral characters (digits, punctuation, whitespace, and anything
 * inside a URL) are skipped so that a message beginning with "https://…" or
 * "2026 —" is not misread as left-to-right when its prose is Arabic.
 */

// Arabic, Arabic Supplement/Extended, Hebrew, Syriac, Thaana, and the
// Arabic Presentation Forms blocks.
const RTL_CHAR =
  /[֑-߿ࡠ-ࣿיִ-﷽ﹰ-ﻼ\u{10800}-\u{10FFF}\u{1E800}-\u{1EFFF}]/u;

const LTR_CHAR = /[A-Za-zÀ-ɏͰ-֏Ⰰ-⿯]/u;

/** Strip things that carry no direction signal but often lead a message. */
function stripNeutralPrefix(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, " ") // fenced code
    .replace(/`[^`]*`/g, " ") // inline code
    .replace(/https?:\/\/\S+/g, " ") // URLs
    .replace(/[\p{N}\p{P}\p{S}\p{Z}\s]+/gu, " "); // digits, punctuation, symbols
}

export type Direction = "rtl" | "ltr";

export function detectDirection(text: string, fallback: Direction = "ltr"): Direction {
  const cleaned = stripNeutralPrefix(text ?? "");
  for (const ch of cleaned) {
    if (RTL_CHAR.test(ch)) return "rtl";
    if (LTR_CHAR.test(ch)) return "ltr";
  }
  return fallback;
}

/** True when the string contains any RTL script at all. */
export function hasRtl(text: string): boolean {
  return RTL_CHAR.test(text ?? "");
}
