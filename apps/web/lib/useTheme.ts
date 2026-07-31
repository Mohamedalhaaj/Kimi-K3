"use client";

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";

const KEY = "kimi-theme";
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  // Keep other tabs and OS-level changes in sync.
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  window.addEventListener("storage", cb);
  mq.addEventListener("change", cb);
  return () => {
    listeners.delete(cb);
    window.removeEventListener("storage", cb);
    mq.removeEventListener("change", cb);
  };
}

function getSnapshot(): Theme {
  try {
    return (localStorage.getItem(KEY) as Theme) ?? "system";
  } catch {
    return "system";
  }
}

/** The server cannot know the preference; the inline script fixes it pre-paint. */
const getServerSnapshot = (): Theme => "system";

function apply(theme: Theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

/**
 * Theme as an external store rather than effect-synced state.
 *
 * localStorage and the OS colour-scheme query are genuinely external sources,
 * so `useSyncExternalStore` is the correct primitive — it avoids the cascading
 * render that reading them in an effect would cause, and it stays consistent
 * with the pre-paint inline script in layout.tsx.
 */
export function useTheme(): [Theme, (t: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setTheme = useCallback((next: Theme) => {
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* private mode — the class still applies for this session */
    }
    apply(next);
    emit();
  }, []);

  return [theme, setTheme];
}
