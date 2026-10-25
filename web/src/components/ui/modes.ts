// User-facing copy per mode (PRD 4.2). Shared by Home, the phone flow and (optionally) the Table.
import type { OrbPalette } from "../CrumbOrb";
import type { Mode } from "../../lib/types";

export interface ModeCopy {
  label: string;
  blurb: string;
  detail: string;
  tagline: string; // two short lines under the orb on Home
  joinLine: string; // "You're joining a ... conversation"
  palette: OrbPalette; // the mode's orb on Home and its tiny dot
}

export const MODE_COPY: Record<Mode, ModeCopy> = {
  social: {
    label: "Social",
    blurb: "Meeting new people",
    detail: "Warm, curious prompts that get past small talk. Swap contacts and get a trail at the end.",
    tagline: "Warm, curious prompts that get past small talk.",
    joinLine: "a social chat with someone new",
    palette: "ember",
  },
  networking: {
    label: "Networking",
    blurb: "Mixers and coffee chats",
    detail: "Finds what you can offer each other. Professional, specific, with follow-ups to send after.",
    tagline: "Finds what you can offer each other, with follow-ups to send.",
    joinLine: "a networking conversation",
    palette: "cocoa",
  },
  speed_dating: {
    label: "Speed dating",
    blurb: "Six-minute rounds",
    detail: 'Playful prompts that escalate gently. A private "Match?" at the end, revealed only if you both say yes.',
    tagline: 'Six-minute rounds, playful prompts and a private "Match?"',
    joinLine: "a speed dating round",
    palette: "lilac",
  },
};

export const MODES: Mode[] = ["social", "networking", "speed_dating"];
