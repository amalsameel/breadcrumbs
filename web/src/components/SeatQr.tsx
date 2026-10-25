// One seat's join card in the lobby (PRD 6.2, F1): QR code, seat number and live partner status.
import { motion } from "framer-motion";
import { QRCodeSVG } from "qrcode.react";
import type { Seat, SeatSummary } from "../lib/types";
import { StatusChip, type ChipState } from "./table/primitives";

interface SeatQrProps {
  seat: Seat;
  url: string;
  summary: SeatSummary | undefined;
}

function chipStates(s: SeatSummary | undefined): Record<"joined" | "consented" | "recording" | "ready", ChipState> {
  const joined = Boolean(s?.joined);
  const consented = Boolean(s?.consented);
  const profiled = Boolean(s?.has_profile);
  const ready = Boolean(s?.ready);
  return {
    joined: joined ? "done" : "pending",
    consented: consented ? "done" : joined ? "active" : "pending",
    recording: profiled || ready ? "done" : consented ? "active" : "pending",
    ready: ready ? "done" : profiled ? "active" : "pending",
  };
}

function headline(s: SeatSummary | undefined): string {
  if (!s?.joined) return "Scan to join";
  if (s.ready) return "Ready";
  if (!s.consented) return "Reading the consent";
  if (!s.has_profile) return s.answered > 0 ? `Recording (${s.answered} answered)` : "Recording a quick profile";
  return "Reviewing their profile";
}

export default function SeatQr({ seat, url, summary }: SeatQrProps) {
  const chips = chipStates(summary);
  const name = summary?.name?.trim() || null;
  const ready = Boolean(summary?.ready);

  return (
    <motion.section
      aria-label={`Seat ${seat}`}
      className={`flex w-full max-w-[392px] flex-col items-center gap-5 rounded-[28px] border bg-bg-raised/70 p-6 backdrop-blur transition-colors duration-500 ${
        ready ? "border-ok/50" : "border-line"
      }`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: seat === 1 ? 0 : 0.08 }}
    >
      <header className="flex w-full items-baseline justify-between">
        <span className="font-mono text-xs uppercase tracking-[0.2em] text-ink-faint">Seat {seat}</span>
        <span className="text-sm text-ink-muted" aria-live="polite">
          {headline(summary)}
        </span>
      </header>

      <div className="rounded-2xl bg-ink p-3.5 shadow-[0_0_60px_-18px_rgba(245,165,36,0.6)]">
        <QRCodeSVG
          value={url}
          size={208}
          level="M"
          marginSize={0}
          bgColor="#F6EFE6"
          fgColor="#17120E"
          title={`Join link for seat ${seat}`}
        />
      </div>

      <p className={`min-h-9 font-display text-[28px] leading-none ${name ? "text-ink" : "text-ink-faint"}`}>
        {name ?? (summary?.joined ? "Joined" : "Waiting for someone")}
      </p>

      <ul className="flex flex-wrap justify-center gap-1.5" aria-label={`Seat ${seat} progress`}>
        <li>
          <StatusChip state={chips.joined}>Joined</StatusChip>
        </li>
        <li>
          <StatusChip state={chips.consented}>Consented</StatusChip>
        </li>
        <li>
          <StatusChip state={chips.recording}>Recording</StatusChip>
        </li>
        <li>
          <StatusChip state={chips.ready}>Ready</StatusChip>
        </li>
      </ul>
    </motion.section>
  );
}
