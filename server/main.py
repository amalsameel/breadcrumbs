"""Breadcrumbs server: REST (PRD 13), WebSockets (PRD 12.1), static web app (docs/CONTRACTS.md section 4).

Run from `server/`: `uvicorn main:app --port 8000`. Everything lives in memory (session_store.py); the
live loop for each session is an engine/runner.py SessionRunner created when both seats are ready.

Two setups (`setup` on create): "quick" ("Just talk", the default: no phones, both people consent on the
Table with POST /start, the conversation goes live at once and the trail is shown on the Table) and
"profiles" (phones, interview, profiles, calibration, overlap opener, opt-in trail on the phones).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import AsyncIterator

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

import config
from ai.gemini import Gemini
from ai.profiles import extract_profile, find_overlaps
from ai.trail import generate_trail
from audio.convert import ConversionError, ffmpeg_available, to_wav_24k
from engine.runner import SessionRunner
from models import (
    Consent,
    ConsentBody,
    CreateSessionBody,
    InterviewQuestion,
    Mode,
    OverlapSet,
    Profile,
    ProfilePatchBody,
    StartBody,
    Trail,
    TrailOptInBody,
    TrailResponse,
)
from muse.transcribe import TranscribeError, transcribe_wav
from session_store import EVENT_ROLES, Connection, SeatState, Session, SessionStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("breadcrumbs.api")

WEB_DIST = config.ROOT / "web" / "dist"
MAX_UPLOAD_BYTES = 32 * 1024 * 1024  # Muse REST body limit
PROFILE_TIMEOUT_S = 30.0
OVERLAP_TIMEOUT_S = 30.0
TRAIL_TIMEOUT_S = 25.0
MAX_OFF_LIMITS = 12
OFF_LIMITS_MAX_CHARS = 60
CONTACT_MAX_CHARS = 200
CONTROL_ACTIONS = frozenset({"spark", "skip", "hold", "resume", "pause_listening", "resume_listening", "xray"})
OPEN_PHASES = ("lobby", "profiles")  # profile setup is still allowed
SEAT_KEY_BYTES = 24
NOT_YOUR_SEAT = "This seat is already in use on another phone. Scan the code for your own seat."
NO_PHONES = "This table doesn't need phones: everything happens on the laptop."
NAME_MAX_CHARS = 60
# "Just talk": the Table shows its trail when the conversation ends; the session is wiped by its Done button
# (DELETE), this long after the end, or the TTL, whichever comes first.
QUICK_WIPE_AFTER_END_S = 600.0

# The AI helpers fall back to their deterministic heuristics when the model fails, but a caller's
# outer timeout can cancel them before they get there (slow model + retries). On a timeout we run the
# same helper again with this always-mock client, which goes straight to the heuristic (PRD 9.7, 11.3).
_HEURISTIC = Gemini(mock=True)

# PRD 6.3: three questions per mode; Quick profile asks only Q1.
INTERVIEW: dict[str, list[str]] = {
    "social": [
        "What's something you could talk about for an hour?",
        "What's a story your friends always make you tell?",
        "What's something you're looking forward to right now?",
    ],
    "networking": [
        "What are you working on that you're excited about?",
        "What's something you're trying to learn or figure out right now?",
        "What could you help someone with, and what kind of person are you hoping to meet?",
    ],
    "speed_dating": [
        "What does a perfect Saturday look like for you?",
        "What's a small thing that makes you unreasonably happy?",
        "What's something people are usually surprised to learn about you?",
    ],
}


def interview_questions(mode: Mode, quick: bool) -> list[InterviewQuestion]:
    texts = INTERVIEW[mode][:1] if quick else INTERVIEW[mode]
    return [InterviewQuestion(id=f"q{i}", text=t) for i, t in enumerate(texts, start=1)]


def app_config() -> dict:
    """AppConfig (web/src/lib/types.ts)."""
    return {
        "mock": {"muse": config.MUSE_MOCK, "gemini": config.AI_MOCK},
        "ai_provider": config.AI_PROVIDER,
        "dev_tools": config.DEV_TOOLS,
        "public_base_url": config.PUBLIC_BASE_URL,
        "ffmpeg": ffmpeg_available(),
    }


def seat_cookie(seat: int) -> str:
    return f"bc_seat{seat}"


def seat_cookie_path(sid: str, seat: int) -> str:
    return f"/api/sessions/{sid}/seats/{seat}"


def _clean_terms(terms: list[str]) -> list[str]:
    out: list[str] = []
    for term in terms:
        t = " ".join(str(term).split())[:OFF_LIMITS_MAX_CHARS]
        if t and t.lower() not in {o.lower() for o in out}:
            out.append(t)
    return out[:MAX_OFF_LIMITS]


def create_app(store: SessionStore | None = None) -> FastAPI:
    store = store if store is not None else SessionStore()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        sweeper = asyncio.create_task(store.run_sweeper(), name="ttl-sweeper")
        log.info("breadcrumbs up (muse_mock=%s, ai_provider=%s, ffmpeg=%s)", config.MUSE_MOCK, config.AI_PROVIDER, ffmpeg_available())
        try:
            yield
        finally:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            await store.wipe_all("server shutdown")

    app = FastAPI(title="Breadcrumbs", lifespan=lifespan)
    app.state.store = store

    # ------------------------------------------------------------------ helpers

    async def require_session(sid: str) -> Session:
        session = await store.get(sid)
        if session is None:
            raise HTTPException(404, "This session doesn't exist or was already deleted.")
        return session

    def require_seat(session: Session, seat: int) -> SeatState:
        if seat not in session.seats:
            raise HTTPException(404, "Unknown seat.")
        return session.seat(seat)

    def require_consent(st: SeatState) -> None:
        if not st.consented:
            raise HTTPException(403, "Consent is required first.")

    def require_owner(request: Request, st: SeatState) -> None:
        """A seat belongs to the phone that consented for it (PRD 5.6, F2, F4, F17).

        The first consent mints a secret that goes back as an HttpOnly cookie scoped to that seat's
        path, so the browser sends it on every later call for its own seat and never for the other
        one. Without it, the other phone (or anyone with the sid) could consent or opt in for the
        partner, read their off-limits list, or undo their removed items.
        """
        if st.key is None:
            return
        presented = request.cookies.get(seat_cookie(st.seat)) or ""
        if not secrets.compare_digest(presented.encode(), st.key.encode()):
            raise HTTPException(403, NOT_YOUR_SEAT)

    def require_open(session: Session) -> None:
        if session.phase not in OPEN_PHASES:
            raise HTTPException(409, "Profiles are locked once the conversation has started.")

    def require_profiles(session: Session) -> None:
        """Seat routes belong to the profiles setup; a "Just talk" table has no phones."""
        if session.setup != "profiles":
            raise HTTPException(409, NO_PHONES)

    def send_table_state(session: Session, conn: Connection | None = None) -> None:
        """The Table needs params and (once live) the latest engine snapshot right away."""
        events: list[dict] = [{"type": "params", "params": session.params.to_dict()}]
        if session.runner is not None:
            events.append({"type": "engine", "engine": session.runner.snapshot()})
        for event in events:
            if conn is not None:
                conn.send_text(json.dumps(event, separators=(",", ":")))
            else:
                session.broadcast(event, roles=("table",))

    async def start_live(session: Session) -> None:
        """Both seats ready: overlaps, Engine + CrumbService + runner, phase calibrating."""
        async with session.lock:
            if (session.setup != "profiles" or session.runner is not None or session.phase not in OPEN_PHASES
                    or not session.both("ready")):
                return
            p1, p2 = session.seat(1).profile, session.seat(2).profile
            if p1 is None or p2 is None:
                return
            try:
                overlaps = await asyncio.wait_for(find_overlaps(p1, p2, session.mode), timeout=OVERLAP_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - heuristic overlaps keep the opener overlap-based (F11)
                log.warning("overlap finder failed (%s); using heuristic overlaps", type(exc).__name__)
                try:
                    overlaps = await find_overlaps(p1, p2, session.mode, gemini=_HEURISTIC)
                except Exception as exc2:  # noqa: BLE001 - the opener falls back to a generated switch
                    log.warning("heuristic overlaps failed (%s)", type(exc2).__name__)
                    overlaps = OverlapSet(overlaps=[])
            if session.wiped or session.phase not in OPEN_PHASES:
                return
            session.overlaps = overlaps
            runner = SessionRunner(session, overlaps=overlaps, on_round_end=lambda: end_session(session))
            session.runner = runner
            session.phase = "calibrating"
            runner.start()
            log.info("session calibrating (overlaps=%d)", len(overlaps.overlaps))
        session.send_session()
        send_table_state(session)

    async def start_quick(session: Session, names: dict[str, str] | None) -> None:
        """"Just talk": both people agreed on the Table. Consent for both seats (source "table"), names,
        ready, then the engine with no profiles; live as soon as the Table's audio arrives (no prompts)."""
        async with session.lock:
            if session.setup != "quick":
                raise HTTPException(409, "This table uses phones: scan the codes to start.")
            if session.runner is not None or session.phase != "lobby":
                raise HTTPException(409, "This conversation has already started.")
            at = time.time()
            for seat in (1, 2):
                st = session.seat(seat)
                typed = " ".join(str((names or {}).get(str(seat)) or "").split())[:NAME_MAX_CHARS]
                st.anonymous = not typed
                st.consent = Consent(typed_name=typed or f"Person {seat}", agreed=True, at=at, source="table")
                st.joined = st.ready = True
            runner = SessionRunner(session, overlaps=OverlapSet(overlaps=[]), on_round_end=lambda: end_session(session))
            session.runner = runner
            session.phase = "calibrating"
            runner.start()
            log.info("quick session started (named=%d)", sum(1 for s in (1, 2) if not session.seat(s).anonymous))
        session.send_session()
        send_table_state(session)

    async def end_session(session: Session) -> None:
        if session.phase == "ended" or session.wiped:
            return
        session.phase = "ended"
        runner = session.runner
        if runner is not None:
            runner.end()
            session.transcript = runner.full_transcript()
            if session.setup == "quick":
                session.memory = runner.memory.to_dict()  # the runner drops it when it stops
            await runner.stop()
        session.send_session()
        log.info("session ended")
        if session.setup == "quick" and not session.wiped:
            session.trail_task = asyncio.create_task(build_table_trail(session), name="table-trail")
            session.wipe_task = asyncio.create_task(wipe_later(session), name="quick-wipe")

    async def build_table_trail(session: Session) -> None:
        """"Just talk": threads to pick up, from the live memory and the transcript, shown on the Table."""
        runner = session.runner
        args = (session.mode, session.names, session.transcript, {})
        kwargs = {
            "memory": session.memory, "anonymous": session.anonymous, "table": True,
            "safety_paused": bool(runner is not None and runner.engine.safety_paused),
        }
        trail = Trail(items=[])
        try:
            trail = await asyncio.wait_for(generate_trail(*args, **kwargs), timeout=TRAIL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - fall back to the heuristic trail (PRD 9.7)
            log.warning("table trail generation failed (%s); using heuristic trail", type(exc).__name__)
            try:
                trail = await generate_trail(*args, gemini=_HEURISTIC, **kwargs)
            except Exception as exc2:  # noqa: BLE001 - the Table then shows no threads
                log.warning("heuristic table trail failed (%s)", type(exc2).__name__)
        if session.wiped:
            return
        session.trail = trail
        session.trail_ready = True
        session.transcript = ""  # no longer needed by anything
        session.memory = None
        session.broadcast({"type": "table_trail", "trail": trail.model_dump()}, roles=("table",))
        log.info("table trail ready (items=%d)", len(trail.items))

    async def wipe_later(session: Session) -> None:
        await asyncio.sleep(QUICK_WIPE_AFTER_END_S)
        await store.wipe(session.sid, "ended a while ago")

    async def build_trail(session: Session) -> None:
        both_share = all(session.seat(s).opted_in is True for s in (1, 2))
        trail = Trail(items=[])
        if both_share:
            args = (session.mode, session.names, session.transcript, session.profiles)
            try:
                trail = await asyncio.wait_for(generate_trail(*args), timeout=TRAIL_TIMEOUT_S)
            except Exception as exc:  # noqa: BLE001 - fall back to the heuristic trail (PRD 9.7)
                log.warning("trail generation failed (%s); using heuristic trail", type(exc).__name__)
                try:
                    trail = await generate_trail(*args, gemini=_HEURISTIC)
                except Exception as exc2:  # noqa: BLE001 - contacts are still swapped without threads
                    log.warning("heuristic trail failed (%s)", type(exc2).__name__)
        if session.wiped:
            return
        session.trail = trail
        session.trail_ready = True
        session.transcript = ""  # no longer needed by anything
        session.broadcast({"type": "trail_ready"}, roles=("seat1", "seat2"))
        log.info("trail ready (matched=%s, items=%d)", both_share, len(trail.items))

    # ------------------------------------------------------------------ meta

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "sessions": len(store), **app_config()}

    @app.get("/api/config")
    async def get_config() -> dict:
        return app_config()

    @app.get("/api/interview")
    async def get_interview(mode: Mode = "social", quick: bool = False) -> dict:
        return {"questions": [q.model_dump() for q in interview_questions(mode, quick)]}

    # ------------------------------------------------------------------ sessions

    @app.post("/api/sessions")
    async def create_session(body: CreateSessionBody | None = None) -> dict:
        body = body or CreateSessionBody()
        quick_profile = body.quick and body.setup == "profiles"  # Quick profile only means something with phones
        session = store.create(body.mode, quick_profile, body.setup)
        base = config.PUBLIC_BASE_URL
        join_urls = {} if body.setup == "quick" else {
            "1": f"{base}/join/{session.sid}/1", "2": f"{base}/join/{session.sid}/2",
        }
        return {"session_id": session.sid, "join_urls": join_urls, "setup": session.setup}

    @app.get("/api/sessions/{sid}")
    async def get_session(sid: str) -> dict:
        return (await require_session(sid)).summary()

    @app.delete("/api/sessions/{sid}")
    async def delete_session(sid: str) -> dict:
        await require_session(sid)
        await store.wipe(sid, "deleted")
        return {"ok": True}

    @app.post("/api/sessions/{sid}/start")
    async def start(sid: str, body: StartBody | None = None) -> dict:
        """"Just talk": both people agreed on the Table ("We're both in"); names are optional."""
        session = await require_session(sid)
        await start_quick(session, dict(body.names) if body and body.names else None)
        return {"ok": True}

    @app.post("/api/sessions/{sid}/end")
    async def end(sid: str) -> dict:
        await end_session(await require_session(sid))
        return {"ok": True}

    @app.get("/api/sessions/{sid}/trail")
    async def get_table_trail(sid: str) -> dict:
        """"Just talk": the Table's trail once the conversation ended (null while it is being written)."""
        session = await require_session(sid)
        if session.setup != "quick":
            raise HTTPException(409, "This table's trail is shown on the phones.")
        if session.phase != "ended":
            raise HTTPException(409, "The conversation hasn't ended yet.")
        trail = session.trail if session.trail_ready else None
        return {"trail": trail.model_dump() if trail is not None else None}

    @app.patch("/api/sessions/{sid}/params")
    async def patch_params(sid: str, changes: dict = Body(...)) -> dict:
        session = await require_session(sid)
        try:
            if session.runner is not None:
                params = session.runner.update_params(changes)
            else:
                session.params.update(changes)
                params = session.params.to_dict()
                session.broadcast({"type": "params", "params": params}, roles=("table",))
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"Invalid parameter value: {exc}") from exc
        return {"params": params}

    @app.get("/api/sessions/{sid}/metrics")
    async def get_metrics(sid: str) -> dict:
        """Aggregate counts and latencies only, never text (PRD 15.1, 16.3)."""
        session = await require_session(sid)
        runner = session.runner
        return {
            "phase": session.phase,
            "turns": len(runner.engine.turns) if runner else 0,
            "metrics": runner.engine.metrics() if runner else None,
        }

    # ------------------------------------------------------------------ seats: consent, interview, profile

    @app.post("/api/sessions/{sid}/seats/{seat}/consent")
    async def consent(sid: str, seat: int, body: ConsentBody, request: Request, response: Response) -> dict:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)
        require_open(session)
        name = " ".join(body.typed_name.split())
        if not body.agreed:
            raise HTTPException(400, "Please check the box to agree before continuing.")
        if not name:
            raise HTTPException(422, "Please type your name.")
        st.consent = Consent(typed_name=name, agreed=True, at=time.time())
        st.joined = True
        if st.key is None:
            st.key = secrets.token_urlsafe(SEAT_KEY_BYTES)
        response.set_cookie(
            seat_cookie(seat), st.key, path=seat_cookie_path(sid, seat),
            max_age=max(60, round(session.expires_in())), httponly=True, samesite="lax",
        )
        if session.phase == "lobby":
            session.phase = "profiles"
        session.send_session()
        return {"ok": True}

    @app.post("/api/sessions/{sid}/seats/{seat}/answers")
    async def upload_answer(
        sid: str, seat: int, request: Request, question_id: str = Form(...), audio: UploadFile = File(...)
    ) -> dict:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)
        require_consent(st)
        require_open(session)
        qids = [q.id for q in interview_questions(session.mode, session.quick)]
        if question_id not in qids:
            raise HTTPException(422, "Unknown question.")
        try:
            data = await audio.read(MAX_UPLOAD_BYTES + 1)
        finally:
            await audio.close()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That recording is too large.")
        if not data:
            raise HTTPException(422, "The recording is empty. Please record again.")
        wav = await _to_wav(data, audio.filename or "answer.webm")
        mock_key = f"{session.mode}:{question_id}:seat{seat}"
        try:
            transcript = await transcribe_wav(wav, mock_key=mock_key)
        except TranscribeError as exc:
            raise HTTPException(502, f"Transcription failed ({exc}). Please try again.") from exc
        if session.wiped:
            raise HTTPException(404, "This session was deleted.")
        st.answers[question_id] = transcript
        session.send_session()
        log.info("answer stored (seat=%d, %s, %d words)", seat, question_id, len(transcript.split()))
        return {"transcript": transcript}

    @app.post("/api/sessions/{sid}/seats/{seat}/profile")
    async def build_profile(sid: str, seat: int, request: Request) -> Profile:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)
        require_consent(st)
        require_open(session)
        if not st.answers:
            raise HTTPException(409, "Record at least one answer first.")
        order = [q.id for q in interview_questions(session.mode, session.quick)]
        answers = [st.answers[q] for q in order if q in st.answers]
        args = (seat, session.mode, st.name or f"Seat {seat}", answers)
        try:
            profile = await asyncio.wait_for(extract_profile(*args), timeout=PROFILE_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("profile extraction timed out; using heuristic extraction")
            profile = await extract_profile(*args, gemini=_HEURISTIC)  # F4: the heuristic profile, not a 504
        if session.wiped:
            raise HTTPException(404, "This session was deleted.")
        st.profile = profile
        session.send_session()
        return profile

    @app.get("/api/sessions/{sid}/seats/{seat}/profile")
    async def get_profile(sid: str, seat: int, request: Request) -> Profile:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)  # off-limits and removed items are private to their seat
        if st.profile is None:
            raise HTTPException(404, "No profile yet.")
        return st.profile

    @app.patch("/api/sessions/{sid}/seats/{seat}/profile")
    async def patch_profile(sid: str, seat: int, body: ProfilePatchBody, request: Request) -> Profile:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)
        require_consent(st)
        require_open(session)
        if st.profile is None:
            raise HTTPException(409, "Build your profile first.")
        known = {i.id for i in st.profile.items}
        removed = list(dict.fromkeys(i for i in body.removed_item_ids if i in known))
        st.profile = st.profile.model_copy(update={"removed_item_ids": removed, "off_limits": _clean_terms(body.off_limits)})
        return st.profile

    @app.post("/api/sessions/{sid}/seats/{seat}/ready")
    async def ready(sid: str, seat: int, request: Request) -> dict:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)
        require_consent(st)
        if st.ready:
            return {"ok": True}
        require_open(session)
        if st.profile is None:
            raise HTTPException(409, "Build and review your profile first.")
        st.ready = True
        session.send_session()
        await start_live(session)
        return {"ok": True}

    # ------------------------------------------------------------------ end of session: opt-in + trail

    @app.post("/api/sessions/{sid}/seats/{seat}/trail-optin")
    async def trail_optin(sid: str, seat: int, body: TrailOptInBody, request: Request) -> dict:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)  # only this seat's phone can answer (or change) its own yes/no
        if session.phase != "ended":
            raise HTTPException(409, "The conversation hasn't ended yet.")
        if session.trail_task is not None:
            raise HTTPException(409, "Both answers are in already.")
        contact = " ".join((body.contact or "").split())[:CONTACT_MAX_CHARS]
        st.opted_in = body.share
        st.contact = (contact or None) if body.share else None
        session.send_session()
        if all(session.seat(s).opted_in is not None for s in (1, 2)):
            session.trail_task = asyncio.create_task(build_trail(session), name="trail")
        return {"ok": True}

    @app.get("/api/sessions/{sid}/seats/{seat}/trail")
    async def get_trail(sid: str, seat: int, request: Request) -> TrailResponse:
        session = await require_session(sid)
        require_profiles(session)
        st = require_seat(session, seat)
        require_owner(request, st)  # fetching marks trail_fetched, which wipes the session
        if not session.trail_ready or session.trail is None:
            raise HTTPException(409, "The trail isn't ready yet.")
        partner = session.seat(2 if seat == 1 else 1)
        matched = st.opted_in is True and partner.opted_in is True
        response = TrailResponse(
            matched=matched,
            partner_name=partner.name if matched else None,
            partner_contact=partner.contact if matched else None,
            trail=[i for i in session.trail.items if i.for_seat == seat] if matched else [],
        )
        st.trail_fetched = True
        if session.both("trail_fetched"):
            await store.wipe(sid, "trail delivered")
        return response

    # ------------------------------------------------------------------ WebSockets

    async def handle_table_event(session: Session, conn: Connection, raw: str) -> None:
        try:
            evt = json.loads(raw)
        except ValueError:
            return
        if not isinstance(evt, dict):
            return
        kind = evt.get("type")
        runner = session.runner
        live = runner is not None and not runner.stopped
        try:
            if kind == "control":
                action = evt.get("action")
                if action == "end":
                    await end_session(session)
                elif action in CONTROL_ACTIONS and live:
                    runner.on_control(action)
            elif kind == "vad" and live:
                runner.on_vad(bool(evt.get("active")), float(evt.get("rms") or 0.0),
                              _opt_float(evt.get("level_db")), _opt_int(evt.get("quiet_ms")))
            elif kind == "tts_state" and live:
                runner.on_tts_state(str(evt.get("crumb_id") or ""), bool(evt.get("playing")))
            elif kind == "params" and isinstance(evt.get("changes"), dict):
                if runner is not None:
                    runner.update_params(evt["changes"])
                else:
                    session.params.update(evt["changes"])
                    session.broadcast({"type": "params", "params": session.params.to_dict()}, roles=("table",))
            elif kind == "dev_turn":
                if not config.DEV_TOOLS:
                    return
                if not live:
                    conn.send_text(json.dumps({"type": "error", "message": "Dev turns work once both seats are ready."}))
                    return
                duration = evt.get("duration_s")
                runner.dev_turn(int(evt.get("seat") or 0), str(evt.get("text") or ""), float(duration) if duration else None)
        except (TypeError, ValueError):
            log.warning("ignored a malformed %s event", kind)

    @app.websocket("/ws/events/{sid}")
    async def ws_events(ws: WebSocket, sid: str, role: str = "table") -> None:
        await ws.accept()
        session = await store.get(sid)
        if session is None:
            await ws.close(4404)
            return
        if role not in EVENT_ROLES:
            await ws.close(4403)
            return
        conn = Connection(ws, role)  # type: ignore[arg-type]
        conn.start()
        session.register(conn)
        try:
            if role == "table":
                conn.send_text(json.dumps({"type": "session", "session": session.summary()}))
                send_table_state(session, conn)
                if session.setup == "quick" and session.trail_ready and session.trail is not None:
                    conn.send_text(json.dumps({"type": "table_trail", "trail": session.trail.model_dump()}))
            elif session.setup != "profiles":
                # A phone link of a "Just talk" table: it only needs the summary (setup) to say so.
                conn.send_text(json.dumps({"type": "session", "session": session.summary()}))
            else:
                st = session.seat(1 if role == "seat1" else 2)
                if not st.joined:
                    st.joined = True
                    session.send_session()
                else:
                    conn.send_text(json.dumps({"type": "session", "session": session.summary()}))
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                text = message.get("text")
                if text and role == "table":
                    await handle_table_event(session, conn, text)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            session.unregister(conn)
            runner = session.runner
            if role == "table" and runner is not None and not session.has_role("table"):
                # No Table is left to report `tts_state playing:false` for what it was playing (PRD 8.3).
                runner.on_table_gone()
            await conn.stop()

    @app.websocket("/ws/audio/{sid}")
    async def ws_audio(ws: WebSocket, sid: str) -> None:
        await ws.accept()
        session = await store.get(sid)
        if session is None:
            await ws.close(4404)
            return
        if not session.both("consented") or session.phase == "ended":
            await ws.close(4403)
            return
        conn = Connection(ws, "audio")
        conn.start()
        session.register(conn)
        attached: SessionRunner | None = None
        try:
            while True:
                message = await ws.receive()
                if message["type"] == "websocket.disconnect":
                    break
                frame = message.get("bytes")
                runner = session.runner
                if not frame or runner is None or runner.stopped:
                    continue  # profiles phase or ended: nothing listens yet
                if attached is not runner:
                    await runner.attach_audio(conn)
                    attached = runner
                elif runner.audio_owner is None:
                    # Another Table's socket took Muse over and has since gone away: take it back.
                    await runner.attach_audio(conn, only_if_free=True)
                runner.push_audio(conn, frame)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            session.unregister(conn)
            if attached is not None:
                await attached.detach_audio(conn)
            await conn.stop()

    # ------------------------------------------------------------------ static web app (SPA fallback)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.split("/", 1)[0] in ("api", "ws"):
            raise HTTPException(404, "Not found.")
        dist = WEB_DIST.resolve()
        index = dist / "index.html"
        if not index.is_file():
            raise HTTPException(404, "The web app is not built. Run `npm run build` in web/, or use the Vite dev server.")
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and dist in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


async def _to_wav(data: bytes, filename: str) -> bytes:
    """24 kHz mono WAV for Muse. Without ffmpeg, mock mode skips conversion (the mock ignores audio)."""
    try:
        return await to_wav_24k(data, filename)
    except ConversionError as exc:
        if not ffmpeg_available():
            if config.MUSE_MOCK:
                return b""
            raise HTTPException(
                503,
                "ffmpeg is not installed on the server, so phone recordings can't be converted. "
                "Ask the operator to run: brew install ffmpeg",
            ) from exc
        raise HTTPException(422, f"We couldn't read that recording ({exc}). Please record again.") from exc


def _opt_float(value: object) -> float | None:
    """An optional numeric field of a client event (older Tables omit it); anything else is None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value == value and abs(value) != float("inf") else None


def _opt_int(value: object) -> int | None:
    f = _opt_float(value)
    return None if f is None else max(0, min(60_000, int(f)))


app = create_app()
