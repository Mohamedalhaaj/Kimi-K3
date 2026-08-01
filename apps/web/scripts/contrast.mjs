#!/usr/bin/env node
/**
 * Fails if any token pair used for text drops below WCAG AA.
 *
 * The palette is asserted here rather than eyeballed: the brand blue passes on
 * white (4.76:1) but only reaches 3.81:1 on the dark surface, which is why dark
 * mode lifts the accent used for text. Run via `npm run check:contrast`.
 */

const LIGHT = {
  bg: "#ffffff",
  bgSunken: "#f6f8fb",
  fg: "#0b1220",
  fgMuted: "#5b6577",
  fgSubtle: "#6a7280",
  accent: "#0171dd",
  accentFg: "#0171dd",
  danger: "#b42318",
  dangerSoft: "#fef3f2",
  warnFg: "#93500b",
  warnSoft: "#fffaeb",
};

const DARK = {
  bg: "#0f1622",
  bgSunken: "#0b111b",
  fg: "#e8ecf3",
  fgMuted: "#98a2b3",
  fgSubtle: "#7d8798",
  accent: "#0171dd",
  accentFg: "#4da3f5",
  danger: "#fda29b",
  dangerSoft: "#2a1512",
  warnFg: "#fcd34d",
  warnSoft: "#2a2110",
};

function luminance(hex) {
  const h = hex.replace("#", "");
  const c = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
  const [r, g, b] = c.map((x) =>
    x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a, b) {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

function pairs(t) {
  return [
    ["body text on content", t.fg, t.bg, 4.5],
    ["body text on sidebar", t.fg, t.bgSunken, 4.5],
    ["muted text on content", t.fgMuted, t.bg, 4.5],
    ["muted text on sidebar", t.fgMuted, t.bgSunken, 4.5],
    // Subtle ink is used for hints and placeholders, which are still text.
    ["subtle text on content", t.fgSubtle, t.bg, 4.5],
    ["subtle text on sidebar", t.fgSubtle, t.bgSunken, 4.5],
    ["accent text on content", t.accentFg, t.bg, 4.5],
    ["white label on accent button", "#ffffff", t.accent, 4.5],
    ["error text on error surface", t.danger, t.dangerSoft, 4.5],
    ["warning text on warning surface", t.warnFg, t.warnSoft, 4.5],
    ["focus ring on content", t.accentFg, t.bg, 3.0],
  ];
}

let failed = 0;
for (const [name, theme] of [
  ["light", LIGHT],
  ["dark", DARK],
]) {
  console.log(`\n${name.toUpperCase()}`);
  for (const [label, fg, bg, need] of pairs(theme)) {
    const r = ratio(fg, bg);
    const ok = r >= need;
    if (!ok) failed++;
    console.log(
      `  ${ok ? "PASS" : "FAIL"}  ${r.toFixed(2)}:1  (need ${need})  ${label}`,
    );
  }
}

if (failed) {
  console.error(`\n${failed} contrast pair(s) below AA.`);
  process.exit(1);
}
console.log("\nAll contrast pairs meet WCAG AA.");
