// Typed REST client (PRD Section 13 + docs/CONTRACTS.md section 4). Same origin as the page.
import type {
  AppConfig,
  CreateSessionResponse,
  InterviewQuestion,
  Mode,
  Profile,
  Seat,
  SessionSummary,
  TrailResponse,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method, headers: {} };
  if (body instanceof FormData) {
    init.body = body;
  } else if (body !== undefined) {
    (init.headers as Record<string, string>)["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (typeof data?.detail === "string") message = data.detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return (await res.json()) as T;
}

const seatPath = (sid: string, seat: Seat) => `/api/sessions/${sid}/seats/${seat}`;

export const api = {
  config: () => request<AppConfig>("GET", "/api/config"),
  createSession: (mode: Mode, quick: boolean) =>
    request<CreateSessionResponse>("POST", "/api/sessions", { mode, quick }),
  getSession: (sid: string) => request<SessionSummary>("GET", `/api/sessions/${sid}`),
  consent: (sid: string, seat: Seat, typed_name: string, agreed: boolean) =>
    request<{ ok: boolean }>("POST", `${seatPath(sid, seat)}/consent`, { typed_name, agreed }),
  interview: (mode: Mode, quick: boolean) =>
    request<{ questions: InterviewQuestion[] }>(
      "GET",
      `/api/interview?mode=${encodeURIComponent(mode)}&quick=${quick ? "true" : "false"}`,
    ),
  uploadAnswer: (sid: string, seat: Seat, questionId: string, audio: Blob, filename: string) => {
    const form = new FormData();
    form.append("question_id", questionId);
    form.append("audio", audio, filename);
    return request<{ transcript: string }>("POST", `${seatPath(sid, seat)}/answers`, form);
  },
  buildProfile: (sid: string, seat: Seat) => request<Profile>("POST", `${seatPath(sid, seat)}/profile`),
  getProfile: (sid: string, seat: Seat) => request<Profile>("GET", `${seatPath(sid, seat)}/profile`),
  patchProfile: (sid: string, seat: Seat, removed_item_ids: string[], off_limits: string[]) =>
    request<Profile>("PATCH", `${seatPath(sid, seat)}/profile`, { removed_item_ids, off_limits }),
  ready: (sid: string, seat: Seat) => request<{ ok: boolean }>("POST", `${seatPath(sid, seat)}/ready`),
  end: (sid: string) => request<{ ok: boolean }>("POST", `/api/sessions/${sid}/end`),
  trailOptIn: (sid: string, seat: Seat, share: boolean, contact: string | null) =>
    request<{ ok: boolean }>("POST", `${seatPath(sid, seat)}/trail-optin`, { share, contact }),
  trail: (sid: string, seat: Seat) => request<TrailResponse>("GET", `${seatPath(sid, seat)}/trail`),
  wipe: (sid: string) => request<{ ok: boolean }>("DELETE", `/api/sessions/${sid}`),
  patchParams: (sid: string, changes: Record<string, unknown>) =>
    request<{ params: Record<string, unknown> }>("PATCH", `/api/sessions/${sid}/params`, changes),
};
