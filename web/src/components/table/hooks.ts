// Small Table-only hooks: a coarse clock and the keyboard shortcuts.
import { useEffect, useRef, useState } from "react";

/** `performance.now()` refreshed every `intervalMs` (drives caption linger and fades). */
export function useNow(intervalMs = 500): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(performance.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export interface ShortcutHandlers {
  spark?: () => void;
  xray?: () => void;
  hold?: () => void;
  pause?: () => void;
  escape?: () => void;
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "TEXTAREA" || tag === "SELECT") return true;
  if (tag !== "INPUT") return false;
  const type = (target as HTMLInputElement).type;
  return type !== "checkbox" && type !== "radio" && type !== "range" && type !== "button";
}

/** Space = drop a crumb, X = X-ray, H = hold, P = pause. Ignored while typing or with modifiers. */
export function useShortcuts(handlers: ShortcutHandlers): void {
  const ref = useRef(handlers);
  ref.current = handlers;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || e.metaKey || e.ctrlKey || e.altKey || isTypingTarget(e.target)) return;
      const h = ref.current;
      // Let a focused button keep its own Space / Enter activation.
      const onButton = e.target instanceof HTMLButtonElement;
      let handler: (() => void) | undefined;
      if (e.code === "Space" && !onButton) handler = h.spark;
      else if (e.key === "x" || e.key === "X") handler = h.xray;
      else if (e.key === "h" || e.key === "H") handler = h.hold;
      else if (e.key === "p" || e.key === "P") handler = h.pause;
      else if (e.key === "Escape") handler = h.escape;
      if (!handler) return;
      e.preventDefault();
      handler();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
