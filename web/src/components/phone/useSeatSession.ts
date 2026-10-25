// Live session state for one phone seat: initial GET, the seat's event socket, and a slow fallback poll.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../lib/api";
import type { Seat, SessionSummary } from "../../lib/types";
import { EventSocket } from "../../lib/ws";

export type SessionStatus = "loading" | "ok" | "gone" | "error";

export interface SeatSession {
  summary: SessionSummary | null;
  status: SessionStatus;
  error: string | null;
  wiped: boolean; // server sent `wiped` (or the session 404s): everything is deleted server-side
  trailReadyTick: number; // increments on every `trail_ready` event
  refresh: () => Promise<void>;
  markGone: () => void;
}

const FALLBACK_POLL_MS = 5000;

export function useSeatSession(sid: string, seat: Seat): SeatSession {
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const [wiped, setWiped] = useState(false);
  const [trailReadyTick, setTrailReadyTick] = useState(0);
  const goneRef = useRef(false);

  const markGone = useCallback(() => {
    goneRef.current = true;
    setWiped(true);
    setStatus("gone");
  }, []);

  const refresh = useCallback(async () => {
    if (goneRef.current) return;
    try {
      const s = await api.getSession(sid);
      if (goneRef.current) return;
      setSummary(s);
      setStatus("ok");
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        markGone();
        return;
      }
      // Keep showing the last known state; only surface the error before the first load.
      setError(err instanceof Error ? err.message : "Could not reach Breadcrumbs.");
      setStatus((prev) => (prev === "loading" ? "error" : prev));
    }
  }, [sid, markGone]);

  useEffect(() => {
    void refresh();
    const socket = new EventSocket(sid, seat === 1 ? "seat1" : "seat2");
    const offEvent = socket.on((event) => {
      if (goneRef.current) return;
      switch (event.type) {
        case "session":
          setSummary(event.session);
          setStatus("ok");
          break;
        case "trail_ready":
          setTrailReadyTick((n) => n + 1);
          break;
        case "wiped":
          markGone();
          socket.close();
          break;
        default:
          break;
      }
    });
    // A closed socket may mean the session is gone (4404 is not retried), so check over REST.
    const offStatus = socket.onStatus((s) => {
      if (s === "closed") void refresh();
    });
    const poll = window.setInterval(() => void refresh(), FALLBACK_POLL_MS);
    return () => {
      offEvent();
      offStatus();
      window.clearInterval(poll);
      socket.close();
    };
  }, [sid, seat, refresh, markGone]);

  return { summary, status, error, wiped, trailReadyTick, refresh, markGone };
}
