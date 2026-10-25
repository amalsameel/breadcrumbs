// Pure carousel helpers for the Home orb picker. No runtime imports, so Node's test runner can load
// this file directly (phone/flow.test.mjs).

/** Positive modulo: wraps any integer index into 0..count-1. */
export const wrapIndex = (n: number, count: number): number => ((n % count) + count) % count;

/**
 * The unbounded position closest to `pos` that shows item `target`. The carousel keeps an unbounded
 * position so it can wrap around without items jumping across the stage.
 */
export function nearestPos(pos: number, target: number, count: number): number {
  let d = wrapIndex(target - wrapIndex(pos, count), count);
  if (d > count / 2) d -= count;
  return pos + d;
}

/** A horizontal drag of at least this many pixels is a swipe. */
export const SWIPE_MIN_PX = 48;
/** A pointer that moves less than this between down and up is a tap. */
export const TAP_SLOP_PX = 8;

export type Gesture = { kind: "tap" } | { kind: "swipe"; delta: 1 | -1 } | { kind: "drag" };

/**
 * Classifies a pointer gesture by its total movement. A leftward swipe moves to the next item, a
 * rightward one to the previous item. Anything that is not a tap ("swipe" or an aborted "drag") must
 * swallow the click that the browser fires afterwards, or a drag that starts and ends on a neighbor
 * would also select that neighbor.
 */
export function classifyGesture(dx: number, dy: number): Gesture {
  if (Math.abs(dx) > SWIPE_MIN_PX && Math.abs(dx) > Math.abs(dy) * 1.3) return { kind: "swipe", delta: dx < 0 ? 1 : -1 };
  if (Math.hypot(dx, dy) < TAP_SLOP_PX) return { kind: "tap" };
  return { kind: "drag" };
}

/** Slot offsets from the active item that the stage renders (the outer two are invisible). */
export const SLOT_REACH = 3;

/**
 * Whether a slot at `dist` from the active item keeps its orb mounted. Every rendered slot does,
 * including the invisible outermost one, so an orb that leaves the visible range fades and slides out
 * with its wrapper instead of vanishing on the frame it crosses the edge.
 */
export const keepsOrb = (dist: number): boolean => dist <= SLOT_REACH;
