"""In-memory sessions (PRD 10.1, 15.1; docs/CONTRACTS.md section 4).

Everything a session knows lives here and nowhere else: consent records, answer transcripts, profiles,
overlaps, the live runner, the trail. `SessionStore.wipe()` drops every reference. Nothing is written
to disk. A sweeper wipes sessions older than the TTL (2 h by default).

Each WebSocket gets a `Connection` with its own outbound queue and writer task, so broadcasting is a
non-blocking enqueue, a slow client cannot stall the engine, and two tasks never write to one socket
at the same time.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Literal

from fastapi import WebSocket

import config
from config import Params
from models import Consent, Mode, OverlapSet, Phase, Profile, Trail

if TYPE_CHECKING:
    from engine.runner import SessionRunner

log = logging.getLogger("breadcrumbs.sessions")

Role = Literal["table", "seat1", "seat2", "audio"]
EVENT_ROLES: tuple[Role, ...] = ("table", "seat1", "seat2")
SEATS: tuple[int, int] = (1, 2)
OUTBOX_MAX = 512  # about 2 minutes of 4 Hz engine snapshots; a client this far behind is dead
SWEEP_INTERVAL_S = 60.0


# --- Connections -----------------------------------------------------------------------------


class _Close:
    def __init__(self, code: int) -> None:
        self.code = code


class Connection:
    """One accepted WebSocket plus its outbound queue. `send()` never blocks."""

    def __init__(self, ws: WebSocket, role: Role) -> None:
        self.ws = ws
        self.role = role
        self.closed = False
        self._outbox: asyncio.Queue[str | _Close] = asyncio.Queue(maxsize=OUTBOX_MAX)
        self._writer: asyncio.Task | None = None

    def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop(), name=f"ws-writer-{self.role}")

    def send_text(self, text: str) -> None:
        if self.closed:
            return
        try:
            self._outbox.put_nowait(text)
        except asyncio.QueueFull:
            log.warning("dropping a slow %s client (outbox full)", self.role)
            self.close(1013)

    def close(self, code: int = 1000) -> None:
        """Queue a close after everything already queued (so `wiped` arrives before the 4404)."""
        if self.closed:
            return
        self.closed = True
        try:
            self._outbox.put_nowait(_Close(code))
        except asyncio.QueueFull:
            if self._writer is not None:
                self._writer.cancel()
            asyncio.create_task(self._close_now(code))

    async def stop(self) -> None:
        """The socket is gone: stop the writer."""
        self.closed = True
        if self._writer is not None and self._writer is not asyncio.current_task():
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._writer

    async def _write_loop(self) -> None:
        try:
            while True:
                item = await self._outbox.get()
                if isinstance(item, _Close):
                    await self._close_now(item.code)
                    return
                await self.ws.send_text(item)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the client went away mid-send
            self.closed = True

    async def _close_now(self, code: int) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close(code)


# --- Session state ------------------------------------------------------------------------------


@dataclass
class SeatState:
    seat: int
    joined: bool = False
    consent: Consent | None = None
    answers: dict[str, str] = field(default_factory=dict)  # question id -> transcript (re-record replaces)
    profile: Profile | None = None
    ready: bool = False
    opted_in: bool | None = None
    contact: str | None = None
    trail_fetched: bool = False

    @property
    def consented(self) -> bool:
        return self.consent is not None and self.consent.agreed

    @property
    def name(self) -> str | None:
        return self.consent.typed_name if self.consent else None

    def summary(self) -> dict:
        # opted_in is an answered-only flag: the real yes/no never leaves the server (F17), because the
        # summary is broadcast to both phones.
        return {
            "joined": self.joined,
            "consented": self.consented,
            "ready": self.ready,
            "name": self.name,
            "has_profile": self.profile is not None,
            "answered": len(self.answers),
            "opted_in": True if self.opted_in is not None else None,
        }

    def clear(self) -> None:
        self.consent = None
        self.answers.clear()
        self.profile = None
        self.contact = None
        self.opted_in = None


@dataclass
class Session:
    sid: str
    mode: Mode
    quick: bool
    ttl_s: float
    created_at: float = field(default_factory=time.time)  # unix seconds (for the UI)
    created_mono: float = field(default_factory=time.monotonic)
    phase: Phase = "lobby"
    seats: dict[int, SeatState] = field(default_factory=lambda: {s: SeatState(seat=s) for s in SEATS})
    params: Params = field(default_factory=Params)
    overlaps: OverlapSet | None = None
    runner: SessionRunner | None = None
    transcript: str = ""  # captured at End, only for the trail; wiped with the session
    trail: Trail | None = None
    trail_ready: bool = False
    trail_task: asyncio.Task | None = None
    wiped: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    connections: dict[str, set[Connection]] = field(default_factory=lambda: {r: set() for r in (*EVENT_ROLES, "audio")})

    # -- views --------------------------------------------------------------------------------

    def seat(self, seat: int) -> SeatState:
        return self.seats[seat]

    @property
    def names(self) -> dict[int, str]:
        return {s: (self.seats[s].name or f"Seat {s}") for s in SEATS}

    @property
    def profiles(self) -> dict[int, Profile]:
        return {s: st.profile for s, st in self.seats.items() if st.profile is not None}

    def both(self, attr: str) -> bool:
        return all(getattr(self.seats[s], attr) for s in SEATS)

    def expires_in(self, now_mono: float | None = None) -> float:
        now_mono = time.monotonic() if now_mono is None else now_mono
        return max(0.0, self.created_mono + self.ttl_s - now_mono)

    def expired(self, now_mono: float | None = None) -> bool:
        return self.expires_in(now_mono) <= 0.0

    def summary(self) -> dict:
        """SessionSummary (web/src/lib/types.ts). Never includes transcript or profile text."""
        return {
            "session_id": self.sid,
            "phase": self.phase,
            "mode": self.mode,
            "quick": self.quick,
            "seats": {str(s): self.seats[s].summary() for s in SEATS},
            "created_at": self.created_at,
            "expires_in_s": round(self.expires_in()),
            "dev_tools": config.DEV_TOOLS,
            "mock": {"muse": config.MUSE_MOCK, "gemini": config.GEMINI_MOCK},
        }

    # -- connections ----------------------------------------------------------------------------

    def register(self, conn: Connection) -> None:
        self.connections[conn.role].add(conn)

    def unregister(self, conn: Connection) -> None:
        self.connections[conn.role].discard(conn)

    def has_role(self, role: Role) -> bool:
        return any(not c.closed for c in self.connections[role])

    def broadcast(self, event: dict, roles: Iterable[Role] | None = None) -> int:
        """Queue `event` for every live connection of `roles` (default: table + both seats)."""
        targets = [c for r in (roles or EVENT_ROLES) for c in self.connections[r] if not c.closed]
        if not targets:
            return 0
        text = json.dumps(event, separators=(",", ":"))
        for conn in targets:
            conn.send_text(text)
        return len(targets)

    def send_session(self) -> None:
        self.broadcast({"type": "session", "session": self.summary()})

    def close_all(self, code: int) -> None:
        for conns in self.connections.values():
            for conn in list(conns):
                conn.close(code)

    # -- teardown -------------------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop background work (runner loops, Muse, trail generation)."""
        if self.trail_task is not None and not self.trail_task.done() and self.trail_task is not asyncio.current_task():
            self.trail_task.cancel()
        if self.runner is not None:
            await self.runner.stop()

    def clear(self) -> None:
        """Drop every reference to personal data."""
        for st in self.seats.values():
            st.clear()
        self.overlaps = None
        self.runner = None
        self.transcript = ""
        self.trail = None
        self.trail_task = None
        for conns in self.connections.values():
            conns.clear()


# --- Store ----------------------------------------------------------------------------------


def new_session_id() -> str:
    return secrets.token_urlsafe(9)  # 12 URL-safe chars, unguessable


class SessionStore:
    def __init__(self, ttl_s: float = config.SESSION_TTL_S, sweep_interval_s: float = SWEEP_INTERVAL_S) -> None:
        self.ttl_s = ttl_s
        self.sweep_interval_s = sweep_interval_s
        self._sessions: dict[str, Session] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def __bool__(self) -> bool:
        return True  # an empty store is still a store (`store or default` must not replace it)

    def __contains__(self, sid: object) -> bool:
        return sid in self._sessions

    def create(self, mode: Mode, quick: bool) -> Session:
        sid = new_session_id()
        while sid in self._sessions:
            sid = new_session_id()
        session = Session(sid=sid, mode=mode, quick=quick, ttl_s=self.ttl_s)
        self._sessions[sid] = session
        log.info("session created (mode=%s, quick=%s, open=%d)", mode, quick, len(self._sessions))
        return session

    def peek(self, sid: str) -> Session | None:
        """The stored session without the expiry check (tests and diagnostics)."""
        return self._sessions.get(sid)

    async def get(self, sid: str) -> Session | None:
        """The live session, or None. An expired session is wiped on access instead of served."""
        session = self._sessions.get(sid)
        if session is None:
            return None
        if session.expired():
            await self.wipe(sid, "ttl")
            return None
        return session

    async def wipe(self, sid: str, reason: str) -> bool:
        session = self._sessions.pop(sid, None)
        if session is None:
            return False
        session.wiped = True
        try:
            await session.shutdown()
        except Exception:  # noqa: BLE001 - teardown must always finish
            log.exception("session shutdown failed during wipe")
        session.broadcast({"type": "wiped"})
        session.close_all(4404)
        session.clear()
        log.info("session wiped (%s, open=%d)", reason, len(self._sessions))
        return True

    async def sweep(self) -> int:
        now = time.monotonic()
        expired = [sid for sid, s in self._sessions.items() if s.expired(now)]
        for sid in expired:
            await self.wipe(sid, "ttl")
        return len(expired)

    async def run_sweeper(self) -> None:
        while True:
            await asyncio.sleep(self.sweep_interval_s)
            try:
                await self.sweep()
            except Exception:  # noqa: BLE001 - the sweeper must survive anything
                log.exception("ttl sweep failed")

    async def wipe_all(self, reason: str) -> None:
        for sid in list(self._sessions):
            await self.wipe(sid, reason)

