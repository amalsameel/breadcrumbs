// The Table's 10 Hz VAD frame for the server (docs/CONTRACTS.md section 5). Pure, so it is unit tested.
// `level_db` and `quiet_ms` let the server tell the people at the table from voices further away, and
// start the pause where the voice stopped; both are optional (older Tables send only active and rms).
import type { VadReading } from "../audio/capture";
import type { VadMessage } from "./types";

const QUIET_MS_CAP = 60_000;

export function vadMessage(active: boolean, rms: number, reading?: Pick<VadReading, "levelDb" | "quietMs">): VadMessage {
  const msg: VadMessage = { type: "vad", active, rms };
  if (reading && Number.isFinite(reading.levelDb)) msg.level_db = Math.round(reading.levelDb * 10) / 10;
  if (reading && Number.isFinite(reading.quietMs)) {
    msg.quiet_ms = Math.min(QUIET_MS_CAP, Math.max(0, Math.round(reading.quietMs)));
  }
  return msg;
}
