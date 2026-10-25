// Auto-reconnecting WebSockets (PRD 12.1). `EventSocket` = JSON events; `AudioSocket` = binary PCM.
import type { ClientEvent, ServerEvent } from "./types";

export type Role = "table" | "seat1" | "seat2";
export type SocketStatus = "connecting" | "open" | "closed";

function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

const BACKOFF_MS = [500, 1000, 2000, 4000];

export class EventSocket {
  private ws: WebSocket | null = null;
  private listeners = new Set<(e: ServerEvent) => void>();
  private statusListeners = new Set<(s: SocketStatus) => void>();
  private attempt = 0;
  private closedByUser = false;
  private queue: ClientEvent[] = [];
  private timer: number | null = null;
  status: SocketStatus = "connecting";

  constructor(private sid: string, private role: Role) {
    this.connect();
  }

  private setStatus(s: SocketStatus) {
    this.status = s;
    this.statusListeners.forEach((l) => l(s));
  }

  private connect() {
    this.setStatus("connecting");
    const ws = new WebSocket(wsUrl(`/ws/events/${this.sid}?role=${this.role}`));
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.setStatus("open");
      const pending = this.queue;
      this.queue = [];
      pending.forEach((e) => this.send(e));
    };
    ws.onmessage = (msg) => {
      if (typeof msg.data !== "string") return;
      try {
        const event = JSON.parse(msg.data) as ServerEvent;
        this.listeners.forEach((l) => l(event));
      } catch {
        /* ignore malformed */
      }
    };
    ws.onclose = (ev) => {
      this.setStatus("closed");
      this.ws = null;
      // 4404 = unknown/wiped session, 4403 = forbidden: do not reconnect.
      if (this.closedByUser || ev.code === 4404 || ev.code === 4403) return;
      const delay = BACKOFF_MS[Math.min(this.attempt++, BACKOFF_MS.length - 1)];
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
  }

  send(event: ClientEvent) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(event));
    } else if (event.type !== "vad") {
      this.queue.push(event); // VAD frames are only useful live; drop them while disconnected
    }
  }

  on(listener: (e: ServerEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStatus(listener: (s: SocketStatus) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  close() {
    this.closedByUser = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.ws?.close();
  }
}

/** Binary PCM uplink for the Table mic (`/ws/audio/{sid}`). Frames sent while disconnected are dropped. */
export class AudioSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private closedByUser = false;
  private timer: number | null = null;
  onStatus: ((s: SocketStatus, code?: number) => void) | null = null;

  constructor(private sid: string) {
    this.connect();
  }

  private connect() {
    this.onStatus?.("connecting");
    const ws = new WebSocket(wsUrl(`/ws/audio/${this.sid}`));
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.onStatus?.("open");
    };
    ws.onclose = (ev) => {
      this.ws = null;
      this.onStatus?.("closed", ev.code);
      if (this.closedByUser || ev.code === 4404 || ev.code === 4403) return;
      const delay = BACKOFF_MS[Math.min(this.attempt++, BACKOFF_MS.length - 1)];
      this.timer = window.setTimeout(() => this.connect(), delay);
    };
  }

  send(frame: ArrayBuffer) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(frame);
  }

  close() {
    this.closedByUser = true;
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.ws?.close();
  }
}
