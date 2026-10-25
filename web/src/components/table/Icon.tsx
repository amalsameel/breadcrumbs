// Small inline SVG icon set (Lucide-style strokes) so the Table needs no icon dependency.
import type { SVGProps } from "react";

export type IconName =
  | "spark"
  | "skip"
  | "hold"
  | "play"
  | "pause"
  | "end"
  | "xray"
  | "mic"
  | "micOff"
  | "close"
  | "check"
  | "send"
  | "link"
  | "home"
  | "trash";

const PATHS: Record<IconName, string[]> = {
  spark: [
    "M12 3v3",
    "M12 18v3",
    "M3 12h3",
    "M18 12h3",
    "M12 8.5l1.2 2.3 2.3 1.2-2.3 1.2L12 15.5l-1.2-2.3L8.5 12l2.3-1.2z",
    "M5.6 5.6l1.6 1.6",
    "M16.8 16.8l1.6 1.6",
    "M5.6 18.4l1.6-1.6",
    "M16.8 7.2l1.6-1.6",
  ],
  skip: ["M5 5l10 7-10 7z", "M19 5v14"],
  hold: ["M12 7v5l3 2", "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z"],
  play: ["M7 5l12 7-12 7z"],
  pause: ["M8 5v14", "M16 5v14"],
  end: ["M6 6l12 12", "M18 6L6 18"],
  xray: [
    "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z",
    "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  ],
  mic: [
    "M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3z",
    "M19 11a7 7 0 0 1-14 0",
    "M12 18v3",
  ],
  micOff: ["M3 3l18 18", "M9 9v3a3 3 0 0 0 5.1 2.1", "M15 9.3V6a3 3 0 0 0-5.7-1.3", "M19 11a7 7 0 0 1-1.2 3.9", "M5 11a7 7 0 0 0 10.4 6.1", "M12 18v3"],
  close: ["M6 6l12 12", "M18 6L6 18"],
  check: ["M5 12.5l4.5 4.5L19 7.5"],
  send: ["M4 12l16-8-6 16-2.5-6.5z", "M11.5 13.5L20 4"],
  link: ["M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1", "M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"],
  home: ["M3 11l9-7 9 7", "M5 10v10h14V10"],
  trash: ["M4 7h16", "M9 7V4h6v3", "M6 7l1 13h10l1-13"],
};

interface IconProps extends Omit<SVGProps<SVGSVGElement>, "name"> {
  name: IconName;
  size?: number;
}

export function Icon({ name, size = 20, strokeWidth = 1.8, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
