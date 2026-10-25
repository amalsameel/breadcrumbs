import type { HTMLAttributes } from "react";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: "raised" | "sunken" | "glow"; // glow = raised with a soft lift, for the one card that matters
  as?: "div" | "section" | "article";
}

const TONES = {
  raised: "bg-bg-raised border border-line",
  sunken: "bg-bg-sunken/60 border border-line",
  glow: "bg-bg-raised border border-line shadow-card",
} as const;

export function Card({ tone = "raised", as: Tag = "div", className = "", ...rest }: CardProps) {
  return <Tag className={`rounded-3xl p-5 ${TONES[tone]} ${className}`} {...rest} />;
}
