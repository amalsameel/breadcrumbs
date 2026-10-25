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
      className={`flex w-full max-w-[440px] flex-col items-center gap-4 rounded-[24px] border bg-bg-raised p-5 shadow-card transition-colors duration-500 sm:p-6 ${
        ready ? "border-ok/40" : "border-line"
      }`}
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: seat === 1 ? 0.08 : 0.14, ease: [0.22, 1, 0.36, 1] }}
    >
      <header className="flex w-full items-center justify-between gap-3">
        <span className="inline-flex min-h-7 items-center rounded-full bg-well px-3 font-mono text-[12px] uppercase tracking-[0.14em] text-ink-muted">
          Seat {seat}
        </span>
        <span className="text-right text-[14px] text-ink-muted" aria-live="polite">
          {headline(summary)}
        </span>
      </header>

      <div className="rounded-[20px] border border-line bg-bg-raised p-3">
        <QRCodeSVG
          value={url}
          size={176}
          level="M"
          marginSize={0}
          bgColor="#FFFFFF"
          fgColor="#0C0B0A"
          title={`Join link for seat ${seat}`}
          className="block h-auto w-[min(176px,52vw)]"
        />
      </div>

      <p
        className={`min-h-9 text-center font-display text-[28px] font-normal leading-none tracking-[-0.03em] ${
          name ? "text-ink" : "text-ink-muted"
        }`}
      >
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
