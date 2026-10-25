import type { HTMLAttributes } from "react";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: "raised" | "sunken" | "glow";
  as?: "div" | "section" | "article";
}

const TONES = {
  raised: "bg-bg-raised border border-line",
  sunken: "bg-bg-sunken border border-line/70",
  glow: "bg-bg-raised border border-amber/40 shadow-[0_0_40px_-18px_rgba(245,165,36,0.7)]",
} as const;

export function Card({ tone = "raised", as: Tag = "div", className = "", ...rest }: CardProps) {
  return <Tag className={`rounded-3xl p-5 ${TONES[tone]} ${className}`} {...rest} />;
}
