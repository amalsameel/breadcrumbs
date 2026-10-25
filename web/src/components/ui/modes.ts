// User-facing copy per mode (PRD 4.2). Shared by Home, the phone flow and (optionally) the Table.
import type { Mode } from "../../lib/types";

export interface ModeCopy {
  label: string;
  blurb: string;
  detail: string;
  joinLine: string; // "You're joining a ... conversation"
}

export const MODE_COPY: Record<Mode, ModeCopy> = {
  social: {
    label: "Social",
    blurb: "Meeting new people",
    detail: "Warm, curious prompts that get past small talk. Swap contacts and get a trail at the end.",
    joinLine: "a social chat with someone new",
  },
  networking: {
    label: "Networking",
    blurb: "Mixers and coffee chats",
    detail: "Finds what you can offer each other. Professional, specific, with follow-ups to send after.",
    joinLine: "a networking conversation",
  },
  speed_dating: {
    label: "Speed dating",
    blurb: "Six-minute rounds",
    detail: 'Playful prompts that escalate gently. A private "Match?" at the end, revealed only if you both say yes.',
    joinLine: "a speed dating round",
  },
};

export const MODES: Mode[] = ["social", "networking", "speed_dating"];
