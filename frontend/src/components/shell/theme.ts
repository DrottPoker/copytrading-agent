export type ThemePreference = "system" | "light" | "dark";
export type TextSize = "normal" | "large" | "larger";

export const THEME_STORAGE_KEY = "copyagent.theme";
export const TEXT_SIZE_STORAGE_KEY = "copyagent.textSize";

// Runs before the first paint so the saved theme and text size never flash.
export const THEME_INIT_SCRIPT = `(function(){var r=document.documentElement;try{var p=localStorage.getItem("${THEME_STORAGE_KEY}");var d=p==="dark"||((!p||p==="system")&&window.matchMedia("(prefers-color-scheme: dark)").matches);r.dataset.theme=d?"dark":"light";var t=localStorage.getItem("${TEXT_SIZE_STORAGE_KEY}");if(t==="large"||t==="larger"){r.dataset.textSize=t;}}catch(e){r.dataset.theme="light";}})();`;

export function readThemePreference(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

export function systemPrefersDark() {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyThemePreference(preference: ThemePreference) {
  const dark = preference === "dark" || (preference === "system" && systemPrefersDark());
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try {
    if (preference === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, preference);
    }
  } catch {
    // Storage can be unavailable in private windows; the theme still applies for this page.
  }
}

export function readTextSize(): TextSize {
  try {
    const stored = window.localStorage.getItem(TEXT_SIZE_STORAGE_KEY);
    return stored === "large" || stored === "larger" ? stored : "normal";
  } catch {
    return "normal";
  }
}

export function applyTextSize(size: TextSize) {
  if (size === "normal") {
    delete document.documentElement.dataset.textSize;
  } else {
    document.documentElement.dataset.textSize = size;
  }
  try {
    if (size === "normal") {
      window.localStorage.removeItem(TEXT_SIZE_STORAGE_KEY);
    } else {
      window.localStorage.setItem(TEXT_SIZE_STORAGE_KEY, size);
    }
  } catch {
    // Storage can be unavailable in private windows; the size still applies for this page.
  }
}
