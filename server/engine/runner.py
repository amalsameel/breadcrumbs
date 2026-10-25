"""Per-session live loop (docs/CONTRACTS.md sections 1 and 4, PRD 8.10, 10.2, 12.1).

The Engine is pure; this runner is its async shell. It owns:
- a 10 Hz tick task (`engine.tick(time.monotonic())`) and a 4 Hz `engine` snapshot broadcast,
- the effect executor (every effect type in CONTRACTS section 1),
- the prefetch schedule (1.5 s debounce after a turn, 30 s periodic refresh while live),
- the Muse realtime client, bound to the Table's `/ws/audio` socket.

Every AI call runs as a background task with a timeout, so the tick loop never waits on the network.
Failures are logged with their exception type only (never transcript text) and never stop the loops.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

import config
from ai.classifiers import classify_hold, tag_energy
from ai.crumbs import ConvoCtx, CrumbService, Delivery
from ai.profiles import allowed_items
from ai.elevenlabs import get_elevenlabs
from ai.tts import synthesize
from engine.core import Effect, Engine
from models import Crumb, CrumbKind, EnergyTag, HoldDecision, OverlapSet
from muse.realtime import MockMuseRealtime, MuseRealtimeClient, make_realtime_client
from session_store import Session

log = logging.getLogger("breadcrumbs.runner")

Clock = Callable[[], float]
MuseClient = MuseRealtimeClient | MockMuseRealtime

TICK_S = 1.0 / config.TICK_HZ
BROADCAST_S = 1.0 / config.ENGINE_BROADCAST_HZ
DELIVER_TIMEOUT_S = 12.0  # CrumbService has its own 6 s text + 4 s TTS budgets; this is the hard stop
SAY_TIMEOUT_S = 5.0
MAX_BOT_SPEECH_S = 30.0  # longer than any crumb or system line; an interval still open after this was lost
CLASSIFY_TIMEOUT_S = 6.0
ENERGY_TIMEOUT_S = 6.0
PREFETCH_TIMEOUT_S = 40.0
FULL_TRANSCRIPT_S = 1e9
ROUND_WARNING_INSTRUCTION = "one minute left in the round"
DEV_TURN_ID_BASE = 100_000  # dev turns never collide with Muse turnIds
DEV_SECONDS_PER_WORD = 0.3
DEV_PARTIAL_EVERY_S = 0.5
DEV_COMPLETE_DELAY_S = 0.2
LABELS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]
TURN_EVENT_FIELDS = (
    "turn_id", "seat", "label", "text", "words", "dropped_as_bot", "is_question", "is_backchannel", "hold",
)
KEYWORD_MIN_CHARS = 5
KEYWORD_STOP = frozenset(
    "about after again always around because being could every first friends going great having "
    "learn learning looking love really right something someone started still there their these "
    "thing things think those through trying whole would years".split()
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]+")


def profile_keywords(names: dict[int, str], session: Session, limit: int = 40) -> list[str]:
    """Muse `keywords`: both names, then distinctive words from each person's allowed profile items."""
    words: list[str] = [n for n in names.values() if n]
    for profile in session.profiles.values():
        for item in allowed_items(profile):
            for w in _WORD_RE.findall(item.text):
                if len(w) >= KEYWORD_MIN_CHARS and w.lower() not in KEYWORD_STOP:
                    words.append(w)
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out[:limit]


def turn_event(turn: dict) -> dict:
    return {k: turn.get(k) for k in TURN_EVENT_FIELDS}


class SessionRunner:
    def __init__(
        self,
        session: Session,
        *,
        overlaps: OverlapSet,
        on_round_end: Callable[[], Awaitable[None]],
        clock: Clock = time.monotonic,
        crumbs: CrumbService | None = None,
    ) -> None:
        self.session = session
        self.clock = clock
        self.names = session.names
        self.engine = Engine(session.mode, self.names, session.params)
        self.crumbs = crumbs or CrumbService(
            mode=session.mode,
            names=self.names,
            profiles=session.profiles,
            overlaps=overlaps,
            location=config.EVENT_LOCATION,
        )
        self._on_round_end = on_round_end
        self._tasks: set[asyncio.Task] = set()
        self._loops: list[asyncio.Task] = []
        self._stopped = False
        self._calibration_started = False

        self._muse: MuseClient | None = None
        self._muse_task: asyncio.Task | None = None
        self._audio_owner: object | None = None
        self._audio_lock = asyncio.Lock()  # attach/detach never interleave (one Muse client at a time)

        # Bot speech the Table reported as playing and has not yet reported as finished (id -> start).
        self._bot_open: dict[str, float] = {}
        self._bot_force_closed: set[str] = set()

        self._prefetch_due: float | None = None
        self._last_prefetch_at: float | None = None
        self._prefetch_task: asyncio.Task | None = None

        self._dev_labels: dict[int, str] = {}
        self._dev_turn_seq = 0

    # ------------------------------------------------------------------ lifecycle

    @property
    def stopped(self) -> bool:
        return self._stopped

    def start(self) -> None:
        self._loops = [
            asyncio.create_task(self._tick_loop(), name="engine-tick"),
            asyncio.create_task(self._broadcast_loop(), name="engine-broadcast"),
        ]
        # Open the ElevenLabs TLS connection now, so the first live line (calibration, a 1 s miss) skips
        # the handshake. A no-op without a key.
        self._spawn(self._warm_voice())

    async def stop(self) -> None:
        """Cancel every task and close Muse. Safe to call twice, and from inside a runner task."""
        if self._stopped:
            return
        self._stopped = True
        current = asyncio.current_task()
        pending = [t for t in (*self._loops, *self._tasks) if t is not current and not t.done()]
        for task in pending:
            task.cancel()
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._close_muse()
        self._tasks.clear()
        self._loops = []

    def end(self) -> None:
        """Session End: freeze the engine and send one last snapshot to the Table."""
        now = self.clock()
        self._apply(self.engine.end(now))
        self._broadcast_snapshot(now)

    def full_transcript(self) -> str:
        return self.engine.recent_transcript(self.clock(), seconds=FULL_TRANSCRIPT_S)

    def snapshot(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        return {**self.engine.snapshot(now), "cached": self.crumbs.cached_summary(now)}

    # ------------------------------------------------------------------ loops

    async def _tick_loop(self) -> None:
        next_at = self.clock()
        while True:
            try:
                now = self.clock()
                self._expire_bot_speech(now)
                self._apply(self.engine.tick(now))
                self._maybe_prefetch(now)
            except Exception:  # noqa: BLE001 - a bad tick must not kill the session
                log.exception("engine tick failed")
            next_at += TICK_S
            delay = next_at - self.clock()
            if delay < -1.0:  # fell far behind (laptop slept): resync instead of bursting ticks
                next_at = self.clock()
                delay = 0.0
            await asyncio.sleep(max(0.0, delay))

    async def _broadcast_loop(self) -> None:
        while True:
            try:
                if self.session.has_role("table"):
                    self._broadcast_snapshot(self.clock())
            except Exception:  # noqa: BLE001
                log.exception("engine snapshot broadcast failed")
            await asyncio.sleep(BROADCAST_S)

    def _broadcast_snapshot(self, now: float) -> None:
        self.session.broadcast({"type": "engine", "engine": self.snapshot(now)}, roles=("table",))

    # ------------------------------------------------------------------ inputs from the Table

    def on_vad(self, active: bool, rms: float) -> None:
        self._apply(self.engine.on_vad(bool(active), float(rms), self.clock()))

    def on_tts_state(self, crumb_id: str, playing: bool) -> None:
        crumb_id = str(crumb_id)
        now = self.clock()
        if playing:
            self._bot_open.setdefault(crumb_id, now)
            self._bot_force_closed.discard(crumb_id)
        elif crumb_id in self._bot_force_closed and crumb_id not in self._bot_open:
            return  # a late `playing:false` for speech we already closed: nothing left to do
        else:
            self._bot_open.pop(crumb_id, None)
        self._apply(self.engine.on_tts_state(crumb_id, bool(playing), now))

    def on_table_gone(self) -> None:
        """The last Table connection closed: whatever it was playing stopped with it (PRD 8.3, 8.7).

        A reload mid-crumb never sends `tts_state playing:false`, and an open bot interval keeps
        `tts_playing` true forever, which blocks every automatic crumb and queues every Drop a crumb.
        """
        self._close_bot_speech(list(self._bot_open), self.clock(), "table gone")

    def _expire_bot_speech(self, now: float) -> None:
        stale = [i for i, started in self._bot_open.items() if now - started >= MAX_BOT_SPEECH_S]
        self._close_bot_speech(stale, now, "no tts end reported")

    def _close_bot_speech(self, ids: list[str], now: float, reason: str) -> None:
        for speech_id in ids:
            self._bot_open.pop(speech_id, None)
            self._bot_force_closed.add(speech_id)
            log.info("closed bot speech %s (%s)", speech_id, reason)
            self._apply(self.engine.on_tts_state(speech_id, False, now))

    def on_control(self, action: str) -> None:
        if action == "xray":
            return  # the drawer is local to the Table; nothing to do server-side
        self._apply(self.engine.on_control(action, self.clock()))

    def update_params(self, changes: dict) -> dict:
        """The Engine holds the same Params object, so changes apply on the next tick."""
        self.session.params.update(changes)
        params = self.session.params.to_dict()
        self.session.broadcast({"type": "params", "params": params}, roles=("table",))
        return params

    def ensure_calibration(self) -> None:
        """Calibration starts when the Table's audio arrives (or the first dev turn, without a mic)."""
        if self._calibration_started or self.engine.phase != "calibrating" or self._stopped:
            return
        self._calibration_started = True
        self._apply(self.engine.start_calibration(self.clock()))

    # ------------------------------------------------------------------ Muse lifecycle (/ws/audio)

    @property
    def audio_owner(self) -> object | None:
        return self._audio_owner

    async def attach_audio(self, owner: object, *, only_if_free: bool = False) -> None:
        """A Table audio socket started streaming: open a fresh Muse session for it.

        `only_if_free` reclaims Muse for a socket that was displaced by another one which has since
        closed, without stealing it back from a socket that is still streaming.
        """
        async with self._audio_lock:
            if self._stopped or self._audio_owner is owner:
                return
            if only_if_free and self._audio_owner is not None:
                return
            await self._close_muse()
            if not self._stopped:  # stop() may have run while the old client was closing
                self._open_muse(owner)

    def _open_muse(self, owner: object) -> None:
        self._audio_owner = owner
        self._dev_labels.clear()
        client = make_realtime_client(self._on_muse_event, profile_keywords(self.names, self.session), self._on_muse_status)
        self._muse = client
        self._muse_task = asyncio.create_task(self._run_muse(client), name="muse")
        self._emit_muse_status("connecting", "mock" if isinstance(client, MockMuseRealtime) else "")
        if self._calibration_started:
            # A new Muse session means new diarization labels: re-map the voices.
            self._apply(self.engine.on_muse_reconnect(self.clock()))
        else:
            self.ensure_calibration()

    def push_audio(self, owner: object, frame: bytes) -> None:
        if self._muse is not None and self._audio_owner is owner:
            self._muse.push_audio(frame)

    async def detach_audio(self, owner: object) -> None:
        async with self._audio_lock:
            if self._audio_owner is owner:
                await self._close_muse()

    async def _run_muse(self, client: MuseClient) -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("muse client stopped (%s)", type(exc).__name__)
            self._emit_muse_status("error", type(exc).__name__)

    async def _close_muse(self) -> None:
        client, task = self._muse, self._muse_task
        self._muse = self._muse_task = None
        self._audio_owner = None
        if client is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(client.close(), timeout=6.0)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _on_muse_event(self, evt: dict) -> None:
        if self._stopped:
            return
        self._apply(self.engine.on_muse_event(evt, self.clock()))

    async def _on_muse_status(self, status: str, detail: str) -> None:
        self._emit_muse_status(status, detail)
        if status == "resyncing" and not self._stopped:
            self._dev_labels.clear()
            self._apply(self.engine.on_muse_reconnect(self.clock()))

    def _emit_muse_status(self, status: str, detail: str = "") -> None:
        self.session.broadcast({"type": "muse_status", "status": status, "detail": detail}, roles=("table",))

    # ------------------------------------------------------------------ dev turns (DEV_TOOLS)

    def dev_turn(self, seat: int, text: str, duration_s: float | None = None) -> None:
        """Inject a Muse-shaped turn for `seat`, paced in real time, as if the mic had heard it."""
        words = text.split()
        if seat not in (1, 2) or not words:
            return
        self.ensure_calibration()
        duration = float(duration_s) if duration_s and duration_s > 0 else max(0.6, DEV_SECONDS_PER_WORD * len(words))
        self._dev_turn_seq += 1
        self._spawn(self._play_dev_turn(seat, " ".join(words), min(duration, 30.0), DEV_TURN_ID_BASE + self._dev_turn_seq))

    def _dev_label(self, seat: int) -> str:
        """The label the engine maps to `seat`, else the one this seat used before, else the next unused."""
        for label, mapped in self.engine.label_map.items():
            if mapped == seat:
                return label
        taken = set(self.engine.label_map)
        own = self._dev_labels.get(seat)
        if own is not None and own not in taken:
            return own
        taken |= set(self._dev_labels.values())
        label = next(lab for lab in LABELS if lab not in taken)
        self._dev_labels[seat] = label
        return label

    async def _play_dev_turn(self, seat: int, text: str, duration: float, turn_id: int) -> None:
        words = text.split()
        self._feed({"type": "speechStart", "turnId": turn_id})
        steps = max(1, int(duration / DEV_PARTIAL_EVERY_S))
        for i in range(1, steps + 1):
            await asyncio.sleep(duration / steps)
            partial = " ".join(words[: max(1, round(len(words) * i / steps))])
            self._feed({"type": "transcript", "transcript": partial, "final": False})
        self._feed({"type": "speaker", "label": self._dev_label(seat)})
        self._feed({"type": "speechEnd", "turnId": turn_id})
        await asyncio.sleep(DEV_COMPLETE_DELAY_S)
        self._feed({"type": "speechComplete", "turnId": turn_id, "transcript": text})

    def _feed(self, evt: dict) -> None:
        if not self._stopped:
            self._apply(self.engine.on_muse_event(evt, self.clock()))

    # ------------------------------------------------------------------ effect executor

    def _apply(self, effects: list[Effect]) -> None:
        for effect in effects:
            try:
                self._execute(effect)
            except Exception:  # noqa: BLE001 - one bad effect must not drop the rest
                log.exception("effect %s failed", effect.get("type"))

    def _execute(self, effect: Effect) -> None:
        kind = effect.get("type")
        session = self.session
        if kind == "partial":
            session.broadcast({"type": "partial", "text": effect.get("text", ""), "label": effect.get("label")}, roles=("table",))
        elif kind == "turn":
            session.broadcast({"type": "turn", "turn": turn_event(effect["turn"])}, roles=("table",))
        elif kind == "calibration":
            session.broadcast({"type": "calibration", "step": effect["step"], "label_map": effect.get("label_map", {})}, roles=("table",))
        elif kind == "decision":
            session.broadcast({"type": "decision", "decision": effect["decision"]}, roles=("table",))
        elif kind == "say":
            self._spawn(self._say(str(effect["id"]), str(effect["text"])))
        elif kind == "deliver":
            self._spawn(self._deliver(effect["kind"]))
        elif kind == "round_warning":
            self._spawn(self._deliver("switch", instruction=ROUND_WARNING_INSTRUCTION))
        elif kind in ("barge_in", "skip"):
            if effect.get("crumb_id"):
                session.broadcast({"type": "crumb_interrupted", "crumb_id": effect["crumb_id"]}, roles=("table",))
        elif kind == "classify_hold":
            self._spawn(self._classify(int(effect["turn_id"]), str(effect.get("context", ""))))
        elif kind == "tag_energy":
            self._spawn(self._tag_energy(str(effect.get("context", ""))))
        elif kind == "prefetch":
            # PRD 8.10 debounces turn-driven refreshes; warming-band and topic-change refreshes are urgent.
            now = self.clock()
            if effect.get("reason") == "turn committed":
                self._prefetch_due = now + config.PREFETCH_DEBOUNCE_S
            else:
                self._prefetch_due = now if self._prefetch_due is None else min(self._prefetch_due, now)
        elif kind == "live":
            if session.phase == "calibrating":
                session.phase = "live"
                session.send_session()
        elif kind == "round_end":
            asyncio.create_task(self._on_round_end(), name="round-end")  # outside our task set: it stops us
        else:
            log.warning("unknown effect type %s", kind)

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        if self._stopped:
            coro.close()
            return
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("runner task failed (%s)", type(task.exception()).__name__)

    # ------------------------------------------------------------------ effect bodies

    def _convo(self, now: float, instruction: str | None = None) -> ConvoCtx:
        return ConvoCtx(
            recent_transcript=self.engine.recent_transcript(now),
            current_topic=self.engine.current_topic,
            depth_max=self.engine.depth_max(now),
            minutes_live=self.engine.minutes_live(now),
            instruction=instruction,
        )

    async def _deliver(self, kind: CrumbKind, *, instruction: str | None = None) -> None:
        started = self.clock()
        convo = self._convo(started, instruction)
        try:
            delivery: Delivery = await asyncio.wait_for(
                self.crumbs.opener(convo) if kind == "opener" else self.crumbs.get(kind, convo, started),
                timeout=DELIVER_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("crumb generation failed (kind=%s, %s)", kind, type(exc).__name__)
            self.engine.crumb_failed(self.clock())
            if kind == "manual":  # someone tapped Drop a crumb and is waiting for it
                self.session.broadcast({"type": "error", "message": "Breadcrumbs could not come up with a crumb just now. Try again."}, roles=("table",))
            return
        now = self.clock()
        if self._stopped or self.engine.phase == "ended":
            self.engine.crumb_failed(now)
            return
        if self.engine.pending_cancelled:
            # PRD 8.1: someone spoke (or tapped Skip) while it was generated. Never send it to the Table and
            # never mark it delivered; a still-wanted request is already re-queued by the engine.
            log.info("crumb dropped (kind=%s, cancelled while generating)", kind)
            self._apply(self.engine.crumb_cancelled(now))
            return
        self._dispatch(delivery, (now - started) * 1000.0, now)

    def _dispatch(self, delivery: Delivery, latency_ms: float, now: float) -> None:
        crumb: Crumb = delivery.crumb
        audio = base64.b64encode(delivery.audio_wav).decode("ascii") if delivery.audio_wav else ""
        self.session.broadcast({
            "type": "crumb",
            "crumb": crumb.model_dump(),
            "audio_b64": audio,
            "cache_hit": delivery.cache_hit,
            "latency_ms": round(latency_ms, 1),
        }, roles=("table",))
        self.engine.crumb_dispatched(crumb, now, latency_ms, delivery.cache_hit)
        self.crumbs.mark_delivered(crumb)
        # F14: a ready steer and switch are always cached during LIVE. A hit just emptied a slot (and a
        # miss means it was empty), so refill it now rather than at the next turn or the 30 s refresh.
        self._prefetch_due = now if self._prefetch_due is None else min(self._prefetch_due, now)
        log.info("crumb dispatched (kind=%s, source=%s, cache_hit=%s, %.0f ms)", crumb.kind, crumb.source, delivery.cache_hit, latency_ms)

    async def _warm_voice(self) -> None:
        try:
            await get_elevenlabs().warm()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - warming is best effort
            log.info("voice warm-up failed (%s)", type(exc).__name__)

    async def _say(self, speech_id: str, text: str) -> None:
        audio: bytes | None = None
        try:
            audio = await asyncio.wait_for(synthesize(text, budget_s=SAY_TIMEOUT_S), timeout=SAY_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the Table falls back to speechSynthesis
            log.warning("system line tts failed (%s)", type(exc).__name__)
        payload = base64.b64encode(audio).decode("ascii") if audio else ""
        self.session.broadcast({"type": "system_say", "id": speech_id, "text": text, "audio_b64": payload}, roles=("table",))

    async def _classify(self, turn_id: int, context: str) -> None:
        try:
            decision: HoldDecision = await asyncio.wait_for(classify_hold(context), timeout=CLASSIFY_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the lexical hold stays in effect
            log.warning("hold classifier failed (%s)", type(exc).__name__)
            return
        if not self._stopped:
            self._apply(self.engine.apply_hold_decision(turn_id, decision, self.clock()))

    async def _tag_energy(self, context: str) -> None:
        try:
            tag: EnergyTag = await asyncio.wait_for(tag_energy(context), timeout=ENERGY_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("energy tagger failed (%s)", type(exc).__name__)
            return
        if not self._stopped:
            self._apply(self.engine.apply_energy_tag(tag, self.clock()))

    # ------------------------------------------------------------------ prefetch (PRD 8.10)

    def _maybe_prefetch(self, now: float) -> None:
        if self.engine.phase != "live":
            return
        if self._prefetch_task is not None and not self._prefetch_task.done():
            return
        debounced = self._prefetch_due is not None and now >= self._prefetch_due
        periodic = self._last_prefetch_at is None or now - self._last_prefetch_at >= config.PREFETCH_REFRESH_S
        if not (debounced or periodic):
            return
        self._prefetch_due = None
        self._last_prefetch_at = now
        if self._stopped:
            return
        task = asyncio.create_task(self._prefetch(now), name="prefetch")
        self._prefetch_task = task
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    async def _prefetch(self, now: float) -> None:
        try:
            await asyncio.wait_for(self.crumbs.prefetch(self._convo(now), now), timeout=PREFETCH_TIMEOUT_S)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a miss falls back to live generation
            log.warning("prefetch failed (%s)", type(exc).__name__)
