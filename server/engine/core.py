"""The Engine facade (docs/CONTRACTS.md section 1, PRD Section 8).

One instance per session. Pure and deterministic: no time, no asyncio, no I/O. Every method takes `now`
(monotonic seconds) and returns a list of effect dicts that the runner executes. Decision effects carry
numeric inputs only, never transcript text.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Literal

from config import (
    CALIBRATION_TIMEOUT_S,
    MANUAL_HOLD_S,
    MAX_DEPTH,
    RAMP_MINUTES,
    ROUND_TIMER_S,
    ROUND_WARNING_S,
    WILDCARD_MIN_GAP_S,
    Params,
)
from models import Crumb, CrumbKind, EnergyTag, HoldDecision, HoldKind, Mode, Turn
from engine import staleness as stale_mod
from engine.hold import (
    HOLD_CONFIDENCE_MIN,
    Hold,
    hold_vetoed,
    is_ambiguous_fragment,
    is_distress,
    match_hold,
    max_duration,
    start_hold,
)
from engine.policy import (
    EPS,
    RATE_WINDOW_S,
    CrumbLedger,
    CrumbRecord,
    TickInputs,
    band,
    effective_thresholds,
    evaluate,
    next_action,
    thresholds,
)
from engine.state import (
    BOT_SPEECH_MAX_S,
    COMPLETE_TIMEOUT_S,
    ECHO_MIN_WORDS,
    ECHO_REPLY_WORDS,
    ECHO_WINDOW_S,
    STUCK_TURN_S,
    VAD_GRACE_S,
    BotInterval,
    BotSpeech,
    Calibration,
    PauseTimer,
    TurnRecord,
    TurnTracker,
    count_words,
    echo_overlap,
    echo_tail_words,
    is_backchannel,
    is_question,
)

Effect = dict[str, Any]
EnginePhase = Literal["calibrating", "live", "ended"]

BOT_OVERLAP_DROP = 0.5  # PRD 8.11: a turn overlapping bot speech by more than 50% is not human
ENERGY_CONTEXT_TURNS = 8
HOLD_CONTEXT_TURNS = 4
AWAIT_PLAYBACK_S = 8.0  # dispatched crumb not reported playing by then (chime + decode is ~1 s): stop waiting
ROUND_WARNING_GAP_S = 5.0  # the 1-minute warning leaves room to answer the previous crumb (no back to back)
ROUND_WARNING_REASON = "One minute left in the round: time check crumb"
NOISE_LATCH_CLEAR_S = 4.0  # VAD quiet this long: the next onset gets its own confirmation window again
BOT_LINES_KEPT = 8  # texts of the latest bot lines, for the echo check
# Usage: the energy tagger is skipped while automatic crumbs stay blocked for at least this long (safety
# pause, or the 10-minute rate cap): its topic and energy only steer automatic crumbs. A skipped tag is not
# lost: the first committed turn once the block is shorter tags again, before the next automatic crumb.
ENERGY_SKIP_BLOCKED_S = 60.0


@dataclass
class _Pending:
    kind: CrumbKind
    auto: bool
    decided_at: float
    reason: str = ""
    round_warning: bool = False
    cancelled: bool = False  # PRD 8.1: speech or Skip arrived while generating; the runner must not send it
    requeued: bool = False  # cancelled by speech but still wanted (manual, opener, round warning)


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def _percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[idx]


class Engine:
    def __init__(self, mode: Mode, names: dict[int, str], params: Params):
        self.mode: Mode = mode
        self.names: dict[int, str] = {int(k): v for k, v in names.items()}
        self.params = params

        self._phase: EnginePhase = "calibrating"
        self._now = 0.0
        self._t0: float | None = None
        self._live_at: float | None = None
        self._ended_at: float | None = None

        self._tracker = TurnTracker()
        self._bot = BotSpeech()
        self._timer = PauseTimer()
        self._calib = Calibration()
        self._ledger = CrumbLedger()

        self._hold: Hold | None = None
        self._staleness = stale_mod.Staleness(gap_ref=params.DEFAULT_GAP_S)
        self._energy: EnergyTag | None = None
        self._live_turns: list[Turn] = []
        self._last_live_human_end: float | None = None

        self._vad_active = False
        self._vad_human = False  # current VAD stretch already counted as human speech
        self._last_human_activity_at = -math.inf
        # Background noise (PRD 8.3): local VAD only counts as speech while Muse confirms it, or for
        # VAD_UNCONFIRMED_S after its onset. Once a stretch outlives that unconfirmed, the room is noisy:
        # later onsets need Muse too, until Muse hears someone or the VAD rests for NOISE_LATCH_CLEAR_S.
        self._vad_since: float | None = None
        self._vad_quiet_since = -math.inf
        self._muse_heard_at = -math.inf
        self._noise_latched = False
        self._bot_lines: dict[str, str] = {}  # speech id -> text of recent crumbs and sys_ lines (echo check)

        self._listening_paused = False
        self._auto_paused = False
        self._crumbs_this_silence = 0
        self._quiet_announced = False
        self._pending: _Pending | None = None
        self._queued: tuple[CrumbKind, str] | None = None
        self._awaiting: tuple[str, float] | None = None  # (crumb_id, dispatched_at) until the Table plays it
        self._force_closed: set[str] = set()  # bot speech the engine closed itself (lost playing:false)
        self._barged: set[str] = set()
        self._warmup_ref: float | None = None
        self._last_wildcard_at: float | None = None
        self._warming_prefetch_anchor: float | None = None
        self._hold_check: int | None = None  # turn id whose hold classifier call waits for silence (PRD 8.6)
        self._hold_asked: int | None = None  # turn id of that deferred call while its answer is out
        self._tagged_turns = 0  # live turns, and their words, when the energy tagger last ran
        self._tagged_words = 0
        self._round_warned = False
        self._round_ended = False
        self._false_interruptions = 0
        self._last_decision: dict | None = None

    # ------------------------------------------------------------------ properties

    @property
    def phase(self) -> EnginePhase:
        return self._phase

    @property
    def round_over(self) -> bool:
        """The round timer ran out (PRD 15.3): no crumb follows, so nothing is worth preparing."""
        return self._round_ended

    def round_left_s(self, now: float) -> float | None:
        """Seconds left in the round (None: the mode has no round timer or the session is not live)."""
        return self._round_remaining(now)

    @property
    def turns(self) -> list[Turn]:
        return self._tracker.turns()

    @property
    def current_topic(self) -> str | None:
        return self._energy.topic if self._energy else None

    @property
    def label_map(self) -> dict[str, int]:
        return dict(self._calib.label_map)

    def minutes_live(self, now: float) -> float:
        if self._live_at is None:
            return 0.0
        end = self._ended_at if self._ended_at is not None else now
        return max(0.0, end - self._live_at) / 60.0

    def depth_max(self, now: float) -> int:
        ramp = RAMP_MINUTES.get(self.mode, 5)
        return int(min(MAX_DEPTH.get(self.mode, 3), 1 + math.floor(self.minutes_live(now) / ramp)))

    # ------------------------------------------------------------------ lifecycle

    def start_calibration(self, now: float) -> list[Effect]:
        self._touch(now)
        if self._phase == "ended":
            return []
        return self._begin_calibration(now, resync=False)

    def on_muse_reconnect(self, now: float) -> list[Effect]:
        """New realtime session: labels and turnIds reset. Re-map voices before any automatic crumb."""
        self._touch(now)
        if self._phase == "ended":
            return []
        effects: list[Effect] = []
        for rec in self._tracker.new_epoch():
            self._tracker.mark_closed(rec)
            rec.turn.ended_at = now
            if rec.best_text:
                effects += self._commit(rec, now)
            else:
                self._tracker.mark_committed(rec)
        return effects + self._begin_calibration(now, resync=self._phase == "live")

    def end(self, now: float) -> list[Effect]:
        self._touch(now)
        if self._phase != "ended":
            self._phase = "ended"
            self._ended_at = now
            self._pending = None
            self._queued = None
            self._awaiting = None
        return []

    # ------------------------------------------------------------------ inputs: Muse

    def on_muse_event(self, evt: dict, now: float) -> list[Effect]:
        self._touch(now)
        if self._phase == "ended":
            return []
        kind = evt.get("type")
        if kind == "speechStart":
            return self._on_speech_start(self._turn_id(evt), now)
        if kind == "transcript":
            return self._on_transcript(str(evt.get("transcript") or ""), now)
        if kind == "speaker":
            label = evt.get("label")
            return self._on_speaker(str(label), now) if label is not None else []
        if kind == "speechEnd":
            return self._on_speech_end(self._turn_id(evt), now)
        if kind == "speechComplete":
            return self._on_speech_complete(self._turn_id(evt), str(evt.get("transcript") or ""), now)
        return []  # audioProgress, error, handshake ack: not engine inputs

    @staticmethod
    def _turn_id(evt: dict) -> int:
        try:
            return int(evt.get("turnId", -1))
        except (TypeError, ValueError):
            return -1

    def _on_speech_start(self, muse_id: int, now: float) -> list[Effect]:
        rec, created = self._tracker.start(muse_id, now)
        if not created:
            return []
        rec.turn.gap_before = self._gap_before(now)
        effects: list[Effect] = []
        if self._echo_lines(rec, now):
            # Muse transcribes our own voice (PRD 8.11): a turn that starts during a bot line, or within
            # ECHO_WINDOW_S after it, waits for its first words. If they repeat the line it is echo (no
            # barge-in, dropped as bot); otherwise it is handled like any other turn (_echo_verdict).
            rec.echo_check = True
            rec.provisional = True
            rec.suspect_at_start = self._bot.suspect(now, self._tail_s)
            return effects
        crumb_iv = self._bot.crumb_playing()
        if crumb_iv is not None and now - crumb_iv.start + EPS >= VAD_GRACE_S and self._vad_active:
            # Local VAD hears the room too: a real voice, so fade the crumb now. A Muse speechStart alone
            # during a crumb is usually the crumb's own echo (Muse reports it a few hundred ms late), so it
            # stays provisional and local VAD decides barge-in (PRD 8.11, F19).
            effects += self._barge_in(now, source="speechStart")
        elif self._bot.suspect(now, self._tail_s):
            rec.provisional = True  # may be our own voice: decided at speechEnd (PRD 8.11)
        if not rec.provisional:
            self._muse_heard(now)
            effects += self._human_activity(now)
        return effects

    def _echo_lines(self, rec: TurnRecord, now: float) -> list[str]:
        """Texts of the bot lines that may be sounding in the room since this turn started."""
        sources = self._bot.echo_sources(rec.turn.started_at, now, ECHO_WINDOW_S)
        return [self._bot_lines[iv.speech_id] for iv in sources if iv.speech_id in self._bot_lines]

    def _echo_verdict(self, rec: TurnRecord, now: float, *, closing: bool = False) -> list[Effect]:
        """Decide an echo-checked turn from its words: the bot's own line, or a person."""
        text = rec.best_text
        if count_words(text) < ECHO_MIN_WORDS:
            return []  # not enough words yet (at speechEnd: decided by time overlap instead)
        lines = self._echo_lines(rec, now)
        overlap = max((echo_overlap(text, line) for line in lines), default=0.0)
        rec.echo_check = False
        if lines and overlap + EPS >= self.params.ECHO_TEXT_OVERLAP:
            rec.echo = True
            return []
        rec.echo = False
        effects: list[Effect] = []
        crumb_iv = self._bot.crumb_playing()
        if (not closing and crumb_iv is not None and now - crumb_iv.start + EPS >= VAD_GRACE_S
                and self._vad_active):
            # Words that are not the line, and the room is loud too: a real voice (barge-in as before).
            rec.provisional = False
            self._muse_heard(now)
            return self._barge_in(now, source="speechStart")
        if not rec.suspect_at_start:
            rec.provisional = False
            self._muse_heard(now)
            effects += self._human_activity(now)
        return effects

    def _echo_reply_check(self, rec: TurnRecord, now: float) -> list[Effect]:
        """An open echo-marked turn whose transcript now ends in words the line never said: Muse kept one
        turn open while our echo ran straight into a person's answer. From here on the turn is theirs
        (captions, a held pause timer, a reset unanswered count). Without this the whole answer was
        dropped as echo and a crumb could fire over the person while they talked."""
        if echo_tail_words(rec.best_text, self._echo_lines(rec, now)) < ECHO_REPLY_WORDS:
            return []
        rec.echo = False
        rec.provisional = False
        rec.reply_after_echo = True
        self._muse_heard(now)
        crumb_iv = self._bot.crumb_playing()
        if crumb_iv is not None and now - crumb_iv.start + EPS >= VAD_GRACE_S and self._vad_active:
            return self._barge_in(now, source="speechStart")  # cutting in while the line still plays
        return self._human_activity(now)

    def _note_bot_line(self, speech_id: str, text: str) -> None:
        self._bot_lines.pop(speech_id, None)
        self._bot_lines[speech_id] = text
        while len(self._bot_lines) > BOT_LINES_KEPT:
            self._bot_lines.pop(next(iter(self._bot_lines)))

    def _gap_before(self, now: float) -> float | None:
        others_open = sum(1 for r in self._tracker.open_records()) > 1
        if others_open:
            return 0.0
        if self._phase == "live":
            prev = self._last_live_human_end
        else:
            prev = self._tracker.last_human_end
        return None if prev is None else max(0.0, now - prev)

    def _on_transcript(self, text: str, now: float) -> list[Effect]:
        rec = self._tracker.current_open() or self._tracker.latest_uncommitted()
        if rec is None:
            return [{"type": "partial", "text": text, "label": None}]
        rec.partial = text
        if rec.final is None:
            rec.turn.text = text
        rec.pre_hold = match_hold(text, self.params.HOLD_TAIL_WORDS)
        effects: list[Effect] = []
        if rec.open and rec.echo_check:
            effects += self._echo_verdict(rec, now)
        elif rec.open and rec.echo:
            effects += self._echo_reply_check(rec, now)
        elif rec.open and not rec.provisional:
            self._muse_heard(now)  # Muse hears a person: local VAD activity right now is speech
        if rec.echo:
            return effects  # our own line coming back: not a caption for the people at the table
        return [{"type": "partial", "text": text, "label": rec.turn.label}] + effects

    def _on_speaker(self, label: str, now: float) -> list[Effect]:
        rec = self._tracker.latest_unlabeled()
        if rec is None:
            return []
        rec.turn.label = label
        rec.turn.seat = self._calib.label_map.get(label)  # type: ignore[assignment]
        if rec.is_human:
            return self._observe_label(rec, now)
        return []

    def _on_speech_end(self, muse_id: int, now: float) -> list[Effect]:
        rec = self._tracker.by_muse(muse_id)
        if rec is None:
            rec, _ = self._tracker.start(muse_id, now)
            rec.provisional = self._bot.suspect(now, self._tail_s)
        if not rec.open:
            return []
        return self._close(rec, now)

    def _on_speech_complete(self, muse_id: int, text: str, now: float) -> list[Effect]:
        rec = self._tracker.by_muse(muse_id)
        if rec is None:  # completion for a turn we never saw start (e.g. right after a reconnect)
            rec, _ = self._tracker.start(muse_id, now)
            rec.final = text
            rec.provisional = self._bot.suspect(now, self._tail_s)
            return self._close(rec, now)
        if rec.committed:
            return []
        rec.final = text
        rec.turn.text = text
        if rec.open:
            return []  # committed at speechEnd (speechComplete can arrive out of order)
        return self._commit(rec, now)

    def _close(self, rec: TurnRecord, now: float) -> list[Effect]:
        """speechEnd: decide human vs bot, restart the pause timer, arm a lexical hold, map the label."""
        self._tracker.mark_closed(rec)
        rec.turn.ended_at = now
        effects: list[Effect] = []
        if rec.echo_check or rec.echo:
            rec.echo_check = True  # re-checked on the whole turn: an echo start with a reply after it is human
            effects += self._echo_verdict(rec, now, closing=True)
        ratio = self._bot.overlap_ratio(rec.turn.started_at, now, now, self._tail_s)
        if rec.echo:
            rec.turn.dropped_as_bot = True
            effects.append(self._decision(
                "dropped_bot_turn", "Turn repeated the line Breadcrumbs was saying, not counted as human speech", now,
                overlap_ratio=round(ratio, 3), duration_s=round(now - rec.turn.started_at, 2), turn_id=rec.turn.turn_id,
                trigger="echo_text",
            ))
        elif ratio > BOT_OVERLAP_DROP and not rec.reply_after_echo:  # a reply after the echo is a person
            rec.turn.dropped_as_bot = True
            effects.append(self._decision(
                "dropped_bot_turn", "Turn overlapped Breadcrumbs' own voice, not counted as human speech", now,
                overlap_ratio=round(ratio, 3), duration_s=round(now - rec.turn.started_at, 2), turn_id=rec.turn.turn_id,
            ))
        else:
            if rec.provisional:
                rec.provisional = False
                effects += self._human_activity(now)
            self._muse_heard(now)
            self._timer.reset(now)
            self._tracker.last_human_end = now
            if self._is_live_turn(rec.turn):
                self._last_live_human_end = now
            effects += self._arm_lexical_hold(rec, now, rec.pre_hold if rec.final is None else match_hold(rec.final, self.params.HOLD_TAIL_WORDS))
            if rec.turn.label is not None:
                effects += self._observe_label(rec, now)
        if rec.final is not None:
            effects += self._commit(rec, now)
        return effects

    def _commit(self, rec: TurnRecord, now: float) -> list[Effect]:
        """speechComplete (or timeout): final text, turn features, staleness, holds, prefetch, energy."""
        if rec.committed:
            return []
        self._tracker.mark_committed(rec)
        text = rec.best_text
        t = rec.turn
        t.text = text
        t.words = count_words(text)
        t.is_question = is_question(text)
        t.is_backchannel = is_backchannel(text)
        if t.dropped_as_bot:
            return [self._turn_effect(rec)]

        effects: list[Effect] = []
        if not self._auto_paused and is_distress(text):
            self._auto_paused = True
            effects.append(self._decision(
                "safety_pause",
                "Possible distress detected: automatic crumbs paused for this session (manual Drop still works)",
                now, turn_id=t.turn_id,
            ))
            effects += self._drop_opener(now)
        counted = self._is_live_turn(t)
        if counted:
            self._live_turns.append(t)
            self._ledger.note_human_turn(t.started_at)
            self._recompute_staleness(now)
        hold_effects = self._final_hold_check(rec, now) if counted else []
        effects = [self._turn_effect(rec)] + effects + hold_effects
        if counted:
            effects.append({"type": "prefetch", "reason": "turn committed"})
            effects += self._maybe_tag_energy(now)
        return effects

    def _maybe_tag_energy(self, now: float) -> list[Effect]:
        """Every ENERGY_EVERY_N_TURNS live turns, but only with new words since the last tag and while
        an automatic crumb could still use the answer."""
        n = self.params.ENERGY_EVERY_N_TURNS
        if n <= 0 or len(self._live_turns) - self._tagged_turns < n:
            return []
        words = sum(t.words for t in self._live_turns)
        if words <= self._tagged_words or self.auto_blocked_for(now) >= ENERGY_SKIP_BLOCKED_S:
            return []
        self._tagged_turns, self._tagged_words = len(self._live_turns), words
        return [{"type": "tag_energy", "context": self._context(ENERGY_CONTEXT_TURNS)}]

    def _is_live_turn(self, turn: Turn) -> bool:
        return self._phase == "live" and self._live_at is not None and turn.started_at >= self._live_at

    def _turn_effect(self, rec: TurnRecord) -> Effect:
        return {"type": "turn", "turn": {**rec.turn.model_dump(), "hold": rec.hold_kind}}

    # ------------------------------------------------------------------ holds (PRD 8.6)

    def _silence_followed(self, rec: TurnRecord) -> bool:
        no_speech_since = self._last_human_activity_at <= rec.turn.ended_at + EPS
        others_open = any(r is not rec and not r.provisional for r in self._tracker.open_records())
        return no_speech_since and not others_open

    def _arm_lexical_hold(self, rec: TurnRecord, now: float, kind: HoldKind | None) -> list[Effect]:
        if kind is None or not self._is_live_turn(rec.turn) or not self._silence_followed(rec):
            return []
        if self._hold is not None and (self._hold.source == "manual" or self._hold.turn_id == rec.turn.turn_id):
            return []
        self._hold = start_hold(kind, now, "lexical", self.params, turn_id=rec.turn.turn_id)
        rec.hold_kind = kind
        return [self._hold_decision("Hold phrase at the end of the turn and silence followed", "start", now)]

    def _final_hold_check(self, rec: TurnRecord, now: float) -> list[Effect]:
        """Re-evaluate the hold on the final transcript and ask the classifier when needed."""
        if self._phase != "live" or not self._silence_followed(rec):
            return []
        if self._hold is not None and self._hold.source == "manual":
            return []
        text = rec.best_text
        kind = match_hold(text, self.params.HOLD_TAIL_WORDS)
        effects: list[Effect] = []
        mine = self._hold if self._hold is not None and self._hold.turn_id == rec.turn.turn_id else None
        if kind is not None and mine is None:
            effects += self._arm_lexical_hold(rec, now, kind)
        elif kind is not None and mine is not None and mine.kind != kind and mine.source == "lexical":
            self._hold = start_hold(kind, mine.started_at, "lexical", self.params, turn_id=rec.turn.turn_id)
            rec.hold_kind = kind
            effects.append(self._hold_decision("Final transcript changed the hold kind", "upgrade", now))
        elif kind is None and mine is not None and mine.source == "lexical":
            effects += self._release_hold(now, "Final transcript has no hold phrase")
            rec.hold_kind = None
        if rec.classified:
            return effects
        if kind is not None:
            # PRD 8.6: every lexical hit is checked at once (the lexical hold is already in effect).
            rec.classified = True
            effects.append({"type": "classify_hold", "turn_id": rec.turn.turn_id, "context": self._context(HOLD_CONTEXT_TURNS)})
        elif not rec.turn.is_backchannel and (hold_vetoed(text, self.params.HOLD_TAIL_WORDS) or is_ambiguous_fragment(text)):
            # An ambiguous fragment (or a vetoed phrase) only matters if silence follows it: the classifier
            # is asked once that silence reaches HOLD_CHECK_SILENCE_S (see _deferred_hold_check), not while
            # the conversation is still moving. Backchannels ("yeah", "oh nice") never pause anything.
            self._hold_check = rec.turn.turn_id
        return effects

    def _deferred_hold_check(self, now: float) -> list[Effect]:
        turn_id = self._hold_check
        if turn_id is None:
            return []
        rec = self._tracker.by_id(turn_id)
        if rec is None or rec.classified or self._phase != "live" or not self._silence_followed(rec):
            self._hold_check = None  # someone spoke: the fragment was not followed by silence
            return []
        if self._hold is not None and self._hold.source == "manual":
            return []
        if self.silence_s(now) + EPS < self.params.HOLD_CHECK_SILENCE_S:
            return []
        self._hold_check = None
        self._hold_asked = turn_id
        rec.classified = True
        return [{"type": "classify_hold", "turn_id": turn_id, "context": self._context(HOLD_CONTEXT_TURNS)}]

    def _hold_answer_pending(self) -> bool:
        """A deferred hold check was asked and its answer is still out, with nobody speaking since.

        Asking HOLD_CHECK_SILENCE_S late must not shrink the classifier's window: while the answer is
        out, automatic crumbs wait for it (until HOLD_CHECK_SILENCE_S past the long threshold, so the
        window is the same as asking at the commit), and the next crumb is not voiced yet."""
        turn_id = self._hold_asked
        if turn_id is None:
            return False
        rec = self._tracker.by_id(turn_id)
        if rec is None or self._phase != "live" or not self._silence_followed(rec):
            self._hold_asked = None
            return False
        return True

    def hold_check_failed(self, turn_id: int) -> None:
        """The classifier call for `turn_id` failed or timed out: automatic crumbs stop waiting for it."""
        if self._hold_asked == turn_id:
            self._hold_asked = None

    def apply_hold_decision(self, turn_id: int, d: HoldDecision, now: float) -> list[Effect]:
        """Classifier result: confirm, upgrade or cancel, but only if nobody has spoken since that turn."""
        self._touch(now)
        self.hold_check_failed(turn_id)  # answered: nothing waits for it any more
        rec = self._tracker.by_id(turn_id)
        if rec is None or rec.open or self._phase != "live":
            return []
        if self._last_human_activity_at > rec.turn.ended_at + EPS:
            return []
        if self._hold is not None and self._hold.source == "manual":
            return []
        if d.confidence < HOLD_CONFIDENCE_MIN:
            return []
        mine = self._hold if self._hold is not None and self._hold.turn_id == turn_id else None
        if d.hold and d.kind != "none":
            kind: HoldKind = d.kind  # type: ignore[assignment]
            started = mine.started_at if mine is not None else rec.turn.ended_at
            change = "confirm" if mine is not None and mine.kind == kind else ("upgrade" if mine is not None else "start")
            hold = start_hold(kind, started, "llm", self.params, turn_id=turn_id)
            if hold.expired(now):
                return []
            self._hold = hold
            rec.hold_kind = kind
            reason = {
                "confirm": "Classifier confirmed the hold",
                "upgrade": "Classifier changed the hold kind",
                "start": "Classifier detected an intentional pause",
            }[change]
            return [self._hold_decision(reason, change, now, expected_seconds=d.expected_seconds, confidence=d.confidence)]
        if mine is not None:
            rec.hold_kind = None
            return self._release_hold(now, "Classifier says this was not an intentional pause", confidence=d.confidence)
        return []

    def _hold_decision(self, reason: str, change: str, now: float, **extra: Any) -> Effect:
        h = self._hold
        assert h is not None
        return self._decision(
            "hold", f"{reason} ({h.kind})", now,
            kind=h.kind, source=h.source, change=change, max_s=max_duration(h.kind, self.params),
            remaining_s=round(h.remaining(now), 2), turn_id=h.turn_id, **extra,
        )

    def _release_hold(self, now: float, reason: str, *, restart_timer: bool = False, **extra: Any) -> list[Effect]:
        h = self._hold
        if h is None:
            return []
        self._hold = None
        if restart_timer:
            self._timer.reset(now)
        return [self._decision(
            "release", f"{reason} ({h.kind})", now,
            kind=h.kind, source=h.source, held_s=round(now - h.started_at, 2), **extra,
        )]

    def _active_hold(self, now: float) -> Hold | None:
        return self._hold if self._hold is not None and not self._hold.expired(now) else None

    # ------------------------------------------------------------------ inputs: VAD, TTS, controls

    def on_vad(self, active: bool, rms: float, now: float) -> list[Effect]:
        self._touch(now)
        if self._phase == "ended" or self._listening_paused:
            return []  # mic stream stopped: frames are stale
        was_active, self._vad_active = self._vad_active, bool(active)
        if not active:
            self._vad_human = False
            if was_active:
                self._vad_quiet_since = now
            return []
        if not was_active or self._vad_since is None:
            rested = now - self._vad_quiet_since + EPS >= NOISE_LATCH_CLEAR_S
            if rested:
                self._noise_latched = False
            # The confirmation window is kept through short releases: bursts of noise (nearby talk, dishes,
            # a TV) share one, so they cannot each count as fresh speech and hold the timer forever. A new
            # window opens after a quiet spell of NOISE_LATCH_CLEAR_S, or once Muse heard someone since.
            if self._vad_since is None or rested or self._muse_heard_at > self._vad_since:
                self._vad_since = now
        effects: list[Effect] = []
        if not self._vad_speech(now):
            # Unconfirmed past VAD_UNCONFIRMED_S: background noise, not a person. It no longer holds the
            # pause timer, cancels crumbs, or barges in (issue: steady room noise froze every crumb).
            self._noise_latched = True
            return effects
        crumb_iv = self._bot.crumb_playing()
        if crumb_iv is not None and now - crumb_iv.start + EPS >= VAD_GRACE_S:
            effects += self._barge_in(now, source="vad")
            self._vad_human = True
            return effects
        if self._bot.suspect(now, self._tail_s):
            return effects  # chime grace, bot speech or its tail: likely echo
        self._timer.reset(now)
        if not self._vad_human:
            self._vad_human = True
            effects += self._human_activity(now)
        return effects

    def on_tts_state(self, crumb_id: str, playing: bool, now: float) -> list[Effect]:
        """Crumb ids starting with "sys_" are system lines: bot speech, but not crumbs."""
        self._touch(now)
        rec = self._ledger.get(crumb_id)
        if self._awaiting is not None and self._awaiting[0] == crumb_id:
            self._awaiting = None
        if playing:
            self._force_closed.discard(crumb_id)
            iv = self._bot.start(crumb_id, now)
            if rec is not None and rec.play_started_at is None:
                rec.play_started_at = now
                if self._human_talking():
                    return self._cancel_overtaken(rec, iv, now)
                if rec.skipped or rec.cancelled:
                    return self._fade_unwanted(rec, iv, now)
            return []
        if crumb_id in self._force_closed:
            self._force_closed.discard(crumb_id)
            if self._bot.playing_interval(crumb_id) is None:
                return []  # a late end for speech already closed: the timer must not restart again
        self._end_bot_speech(crumb_id, now)
        return []

    def on_table_disconnect(self, now: float) -> list[Effect]:
        """The last Table events socket closed (reload, closed tab): nobody will report the end of the
        speech that was playing, so close it now instead of freezing the engine as "bot speaking"."""
        self._touch(now)
        self._awaiting = None
        for iv in self._bot.open_intervals():
            self._force_closed.add(iv.speech_id)
            self._end_bot_speech(iv.speech_id, now)
        return []

    def _end_bot_speech(self, speech_id: str, now: float) -> None:
        self._bot.stop(speech_id, now)
        self._timer.reset(now)  # PRD 8.3: when a crumb finishes, the silence timer restarts at 0
        rec = self._ledger.get(speech_id)
        if rec is not None:
            rec.ended_at = now
            if rec.kind == "opener":
                self._warmup_ref = now

    def on_control(self, action: str, now: float) -> list[Effect]:
        self._touch(now)
        if self._phase == "ended":
            return []
        if action == "spark":
            return self._on_spark(now)
        if action == "skip":
            return self._on_skip(now)
        if action == "hold":
            self._hold = start_hold("show", now, "manual", self.params, duration_s=MANUAL_HOLD_S)
            return [self._hold_decision("Hold tapped", "start", now)]
        if action == "resume":
            return self._release_hold(now, "Resume tapped", restart_timer=True)
        if action == "pause_listening":
            if not self._listening_paused:
                self._timer.freeze(self.silence_s(now))
                self._listening_paused = True
                self._vad_active = self._vad_human = False
            return []
        if action == "resume_listening":
            if self._listening_paused:
                self._listening_paused = False
                self._vad_active = self._vad_human = False
                self._vad_since = None
                self._noise_latched = False
                self._timer.unfreeze(now)
            return []
        return []  # end / xray are handled by the runner

    def _on_spark(self, now: float) -> list[Effect]:
        if self._phase != "live":
            return []
        effects = self._release_hold(now, "Drop a crumb tapped")
        if self._manual_on_its_way(now):
            return effects  # double tap: the crumb from the first tap has not started playing yet
        return effects + self._request_manual("manual", "Drop a crumb tapped: context switch", now)

    def _manual_on_its_way(self, now: float) -> bool:
        if self._pending is not None and self._pending.kind == "manual" and not self._pending.cancelled:
            return True
        if self._queued is not None and self._queued[0] in ("manual", "opener"):
            return True  # a queued opener is the crumb the tap asked for: do not replace it
        awaiting = self._awaiting_playback(now)
        rec = self._ledger.get(awaiting) if awaiting is not None else None
        return rec is not None and rec.kind == "manual"

    def _on_skip(self, now: float) -> list[Effect]:
        if self._phase != "live":
            return []
        crumb_iv = self._bot.crumb_playing()
        crumb_id = crumb_iv.speech_id if crumb_iv is not None else None
        if crumb_id is None:
            # Dispatched but still in its chime: interrupt it before it starts (the Table drops it).
            crumb_id = self._awaiting_playback(now)
            self._awaiting = None
        if crumb_id is not None:
            rec = self._ledger.get(crumb_id)
            if rec is not None:
                rec.skipped = True
        generating = crumb_id is None and self._pending is not None and not self._pending.cancelled
        if generating:
            self._cancel_pending(requeue=False)  # PRD 6.6: discard the crumb that is still being generated
        effects: list[Effect] = [
            {"type": "skip", "crumb_id": crumb_id},
            self._decision("skip", "Skip tapped: discarding the current crumb", now, crumb_playing=crumb_id is not None,
                           crumb_generating=generating),
        ]
        return effects + self._request_manual("switch", "Skip tapped: a different context switch", now)

    def _request_manual(self, kind: CrumbKind, reason: str, now: float) -> list[Effect]:
        """Manual crumbs ignore cooldowns but never fire while someone is talking (queued until silence)."""
        if self._can_deliver_now():
            self._queued = None
            return self._deliver(kind, reason, now, auto=False)
        self._queued = (kind, reason)
        return []

    def _can_deliver_now(self) -> bool:
        return (
            self._phase == "live"
            and self._pending is None
            and not self._turn_open()
            and not self._vad_speech(self._now)
            and not self._bot.playing
            and self._awaiting_playback(self._now) is None
        )

    def _awaiting_playback(self, now: float) -> str | None:
        """A crumb sent to the Table that has not reported playing yet (its chime is still on)."""
        if self._awaiting is None or now - self._awaiting[1] >= AWAIT_PLAYBACK_S:
            return None
        return self._awaiting[0]

    # ------------------------------------------------------------------ async results

    def apply_energy_tag(self, tag: EnergyTag, now: float) -> list[Effect]:
        self._touch(now)
        previous = self.current_topic
        self._energy = tag
        self._recompute_staleness(now)
        if tag.topic and tag.topic != previous:
            return [{"type": "prefetch", "reason": "topic changed"}]
        return []

    @property
    def pending_cancelled(self) -> bool:
        """The crumb being generated was cancelled (someone spoke, or Skip): the runner should call
        `crumb_cancelled` instead of sending it to the Table (PRD 8.1)."""
        return self._pending is not None and self._pending.cancelled

    def crumb_dispatched(self, crumb: Crumb, now: float, latency_ms: float, cache_hit: bool) -> None:
        self._touch(now)
        pending, self._pending = self._pending, None
        rec = CrumbRecord(
            crumb_id=crumb.id, kind=crumb.kind, auto=bool(pending and pending.auto),
            dispatched_at=now, latency_ms=float(latency_ms), cache_hit=bool(cache_hit),
        )
        if pending is not None and pending.cancelled:
            # Sent although it was cancelled. Still wanted (manual, opener, warning) and nobody is talking:
            # it stands in for the re-queued request. Otherwise it is faded the moment it starts playing.
            if pending.requeued and not self._human_talking():
                self._take_back_requeue(pending)
            else:
                rec.cancelled = True
                if pending.auto:
                    self._ledger.cancel_auto_decision()  # faded at playback start: not a delivered crumb
        self._ledger.add(rec)
        self._note_bot_line(crumb.id, crumb.text)
        self._awaiting = (crumb.id, now)
        spoke_since = pending is not None and self._last_human_activity_at > pending.decided_at
        if not spoke_since and not rec.cancelled:
            self._crumbs_this_silence += 1
        self._timer.reset(now)
        if crumb.kind == "opener":
            self._warmup_ref = now

    def crumb_failed(self, now: float) -> None:
        self._touch(now)
        pending, self._pending = self._pending, None
        if pending is None:
            return
        if pending.auto:
            self._ledger.cancel_auto_decision()
        if pending.kind == "opener":
            self._warmup_ref = now

    def crumb_cancelled(self, now: float) -> list[Effect]:
        """The runner dropped a cancelled crumb without sending it: nothing was delivered. An automatic
        decision does not count toward the rate limit; a still-wanted request is already re-queued."""
        self._touch(now)
        pending, self._pending = self._pending, None
        if pending is not None and pending.auto:
            self._ledger.cancel_auto_decision()
        return []

    def _cancel_pending(self, *, requeue: bool) -> None:
        p = self._pending
        if p is None or p.cancelled:
            return
        p.cancelled = True
        if not requeue or p.auto:
            return
        p.requeued = True
        if p.round_warning:
            self._round_warned = False  # re-armed: the warning goes out at the next quiet moment
        elif self._queued is None:
            self._queued = (p.kind, p.reason)

    def _drop_opener(self, now: float) -> list[Effect]:
        """PRD 15.3: the opener is automatic, so after a distress signal one still waiting for silence or
        being generated is dropped (a generating one is vetoed or faded like any cancelled crumb)."""
        queued = self._queued is not None and self._queued[0] == "opener"
        p = self._pending
        generating = p is not None and p.kind == "opener"
        if queued:
            self._queued = None
        if generating:
            self._cancel_pending(requeue=False)
            p.requeued = False  # a re-queue made when the speech started is withdrawn too
        if not (queued or generating):
            return []
        return [self._decision("calibration", "Opening crumb skipped: automatic crumbs are paused", now, step="done")]

    def _take_back_requeue(self, p: _Pending) -> None:
        if p.round_warning:
            self._round_warned = True
        elif self._queued == (p.kind, p.reason):
            self._queued = None

    # ------------------------------------------------------------------ tick (PRD 8.7)

    def tick(self, now: float) -> list[Effect]:
        self._touch(now)
        if self._phase == "ended":
            return []
        effects: list[Effect] = []
        effects += self._close_stuck_turns(now)
        effects += self._expire_bot_speech(now)
        effects += self._expire_hold(now)
        effects += self._deferred_hold_check(now)
        effects += self._calibration_fallback(now)
        effects += self._round_timer(now)
        effects += self._barge_in_check(now)
        effects += self._deliver_queued(now)
        effects += self._auto_policy(now)
        self._bot.prune(now)
        return effects

    def _close_stuck_turns(self, now: float) -> list[Effect]:
        effects: list[Effect] = []
        for rec in self._tracker.open_records():
            if now - rec.turn.started_at >= STUCK_TURN_S:
                effects += self._close(rec, now)
        for rec in self._tracker.active_records():
            if not rec.open and not rec.committed and now - rec.turn.ended_at >= COMPLETE_TIMEOUT_S:
                effects += self._commit(rec, now)
        return effects

    def _expire_bot_speech(self, now: float) -> list[Effect]:
        """A lost `tts_state playing:false` (Table reload or crash mid-crumb) must not leave the engine
        "bot speaking" forever: no crumb or system line runs longer than BOT_SPEECH_MAX_S."""
        if self._awaiting is not None and self._awaiting_playback(now) is None:
            self._awaiting = None
        for iv in self._bot.open_intervals():
            if now - iv.start + EPS >= BOT_SPEECH_MAX_S:
                self._force_closed.add(iv.speech_id)
                self._end_bot_speech(iv.speech_id, now)
        return []

    def _expire_hold(self, now: float) -> list[Effect]:
        h = self._hold
        if h is None or not h.expired(now):
            return []
        # PRD 8.6: after a show (or external) hold times out, thresholds count from 0 again.
        return self._release_hold(now, "Hold reached its max duration", restart_timer=h.suppresses)

    def _calibration_fallback(self, now: float) -> list[Effect]:
        if not self._calib.active or now - self._calib.started_at < CALIBRATION_TIMEOUT_S:
            return []
        records = sorted(self._tracker.epoch_records(), key=lambda r: (r.turn.started_at, r.turn.turn_id))
        labels = [r.turn.label for r in records if r.is_human and r.turn.label is not None]
        return self._after_calibration_steps(self._calib.fallback(labels), now, fallback=True)

    def _round_timer(self, now: float) -> list[Effect]:
        remaining = self._round_remaining(now)
        if remaining is None or self._round_ended:
            return []
        if remaining <= EPS:
            self._round_ended = True
            return [{"type": "round_end"}]
        if self._round_warned or remaining > ROUND_WARNING_S + EPS:
            return []
        if self._auto_paused:
            self._round_warned = True  # PRD 15.3: no automatic crumb after a distress signal, this one included
            return []
        if not self._round_warning_ready(now):
            return []  # deferred to the next quiet moment, never dropped
        self._round_warned = True
        # The runner generates this switch itself; tracking it as the pending delivery keeps the policy
        # from deciding a second crumb while it is in flight and keeps the ledger's auto/manual labels right.
        self._pending = _Pending(kind="switch", auto=False, decided_at=now, reason=ROUND_WARNING_REASON,
                                 round_warning=True)
        return [{"type": "round_warning"}, self._decision("switch", ROUND_WARNING_REASON, now,
                                                          remaining_s=round(remaining, 1))]

    def _round_warning_ready(self, now: float) -> bool:
        if not self._can_deliver_now():
            return False
        hold = self._active_hold(now)
        if hold is not None and hold.suppresses:
            return False
        if self._crumbs_this_silence >= self.params.MAX_UNANSWERED_CRUMBS:
            return False  # quiet after 2 unanswered crumbs, until someone speaks
        since = self._ledger.since_last_end(now)
        return since is None or since + EPS >= ROUND_WARNING_GAP_S

    def _round_remaining(self, now: float) -> float | None:
        total = ROUND_TIMER_S.get(self.mode)
        if total is None or self._live_at is None:
            return None
        return max(0.0, self._live_at + total - now)

    def _barge_in_check(self, now: float) -> list[Effect]:
        """Someone was already talking when the crumb started: fade it out once the chime grace passes."""
        crumb_iv = self._bot.crumb_playing()
        if crumb_iv is None or now - crumb_iv.start + EPS < VAD_GRACE_S:
            return []
        pre_existing = any(
            not r.provisional and r.turn.started_at < crumb_iv.start for r in self._tracker.open_records()
        )
        if pre_existing or (self._vad_human and self._vad_speech(now)):
            return self._barge_in(now, source="talking at playback start")
        return []

    def _deliver_queued(self, now: float) -> list[Effect]:
        if self._queued is None or not self._can_deliver_now():
            return []
        kind, reason = self._queued
        self._queued = None
        return self._deliver(kind, reason, now, auto=False)

    def _auto_policy(self, now: float) -> list[Effect]:
        inp = self._tick_inputs(now)
        out = evaluate(inp, self.params)
        effects: list[Effect] = []
        if out.quiet and not self._quiet_announced:
            self._quiet_announced = True
            effects.append(self._decision(
                "quiet", "2 crumbs in a row got no reply: going quiet until someone speaks", now, **self._inputs(now, inp),
            ))
        answer_pending = self._hold_answer_pending()
        if out.action is not None and answer_pending and inp.silence_s + EPS < out.thresholds.t_long + self.params.HOLD_CHECK_SILENCE_S:
            pass  # the hold classifier has not answered yet: this pause may be an intentional one
        elif out.action is not None:
            effects += self._deliver(out.action, out.reason, now, auto=True, inputs=self._inputs(now, inp))
        elif (self._phase == "live" and out.band == "warming" and not answer_pending
              and self._warming_prefetch_anchor != self._timer.anchor):
            self._warming_prefetch_anchor = self._timer.anchor
            upcoming = self.next_auto_crumb(now)
            if upcoming is not None:
                # Voice exactly the crumb this pause will get, about 2 s before it is due (lazy voicing).
                kind, due_in = upcoming
                effects.append({"type": "prefetch", "reason": "pause entered the warming band", "voice": kind,
                                "due_in_s": round(due_in, 2)})
        return effects

    # ------------------------------------------------------------------ outlook (usage: what is worth preparing)

    def next_auto_crumb(self, now: float) -> tuple[CrumbKind, float] | None:
        """The automatic crumb the current pause gets if nobody speaks, and the seconds until it fires.

        The policy is evaluated at the long and the exceptional threshold with the cooldowns, rate cap and
        warm-up as they will be then. None when no automatic crumb can fire in this pause."""
        if self._phase != "live":
            return None
        inp = self._tick_inputs(now)
        th = effective_thresholds(inp.base, inp.hold)
        round_left = self._round_remaining(now)
        for silence in (th.t_long, th.t_exc):
            wait = max(0.0, silence - inp.silence_s)
            if round_left is not None and wait + EPS >= round_left:
                break  # the round is over by then: no automatic crumb
            out = evaluate(self._inputs_after(now, inp, wait, max(silence, inp.silence_s)), self.params)
            if out.action in ("steer", "switch"):
                return out.action, wait  # type: ignore[return-value]
        return None

    def steer_wanted(self, now: float) -> bool:
        """A steer could fire in the pause that starts now: worth keeping a fresh steer text for. False
        while someone is already talking again (the pause has not started) or a steer is blocked."""
        if self._human_turn_open():
            return False
        upcoming = self.next_auto_crumb(now)
        return upcoming is not None and upcoming[0] == "steer"

    def auto_blocked_for(self, now: float) -> float:
        """Seconds automatic crumbs stay blocked whatever happens: the 10-minute rate cap (inf: safety pause)."""
        cap = self.params.MAX_CRUMBS_PER_10_MIN
        if self._auto_paused or cap <= 0:
            return math.inf
        recent = sorted(t for t in self._ledger.auto_decisions if now - t < RATE_WINDOW_S)
        if len(recent) < cap:
            return 0.0
        return max(0.0, recent[len(recent) - cap] + RATE_WINDOW_S - now)

    def _inputs_after(self, now: float, inp: TickInputs, wait: float, silence: float) -> TickInputs:
        later = now + wait
        return replace(
            inp,
            turn_active=False,
            vad_active=False,
            tts_playing=False,
            pending_delivery=False,
            silence_s=silence,
            in_warmup=self._warmup_remaining(later) > 0,
            rate_ok=self._ledger.auto_count(later) < self.params.MAX_CRUMBS_PER_10_MIN,
            exceptional_ok=self._ledger.exceptional_cooldown_ok(later, self.params),
            regular_ok=self._ledger.regular_cooldown_ok(later, self.params),
        )

    def _tick_inputs(self, now: float) -> TickInputs:
        hold = self._active_hold(now)
        live = self._phase == "live" and not self._round_ended
        since_wild_ref = self._last_wildcard_at if self._last_wildcard_at is not None else self._live_at
        wildcard_ok = (
            self.params.WILDCARD_ENABLED
            and since_wild_ref is not None
            and now - since_wild_ref + EPS >= WILDCARD_MIN_GAP_S
        )
        return TickInputs(
            live=live,
            listening_paused=self._listening_paused,
            seats_unmapped=not self._calib.complete,
            auto_paused=self._auto_paused,
            turn_active=self._turn_open(),
            vad_active=self._vad_speech(now),
            tts_playing=self._bot.playing or self._awaiting_playback(now) is not None,
            hold=hold.kind if hold is not None else None,
            silence_s=self.silence_s(now),
            base=thresholds(self.params, self.mode, self._staleness.gap_ref),
            staleness=self._staleness.score,
            crumbs_this_silence=self._crumbs_this_silence,
            pending_delivery=self._pending is not None,
            in_warmup=self._warmup_remaining(now) > 0,
            rate_ok=self._ledger.auto_count(now) < self.params.MAX_CRUMBS_PER_10_MIN,
            exceptional_ok=self._ledger.exceptional_cooldown_ok(now, self.params),
            regular_ok=self._ledger.regular_cooldown_ok(now, self.params),
            steer_unlanded=self._ledger.last_steer_unlanded(),
            wildcard_ok=wildcard_ok,
        )

    def _warmup_remaining(self, now: float) -> float:
        if self._warmup_ref is None:
            return 0.0
        return max(0.0, self._warmup_ref + self.params.WARMUP_S - now - EPS)

    def _deliver(self, kind: CrumbKind, reason: str, now: float, *, auto: bool, inputs: dict | None = None) -> list[Effect]:
        self._pending = _Pending(kind=kind, auto=auto, decided_at=now, reason=reason)
        if auto:
            self._ledger.note_auto_decision(now)
            hold = self._active_hold(now)
            if self._human_talking() or (hold is not None and hold.suppresses):
                self._false_interruptions += 1
        if kind == "wildcard":
            self._last_wildcard_at = now
        return [
            {"type": "deliver", "kind": kind, "reason": reason},
            self._decision(kind, reason, now, **(inputs or {})),
        ]

    # ------------------------------------------------------------------ barge-in, human activity

    def _barge_in(self, now: float, *, source: str) -> list[Effect]:
        crumb_iv = self._bot.crumb_playing()
        if crumb_iv is None or crumb_iv.speech_id in self._barged:
            return []
        self._barged.add(crumb_iv.speech_id)
        crumb_iv.barged_at = now
        rec = self._ledger.get(crumb_iv.speech_id)
        if rec is not None:
            rec.interrupted = True
        effects: list[Effect] = [
            {"type": "barge_in", "crumb_id": crumb_iv.speech_id},
            self._decision(
                "barge_in", "Someone started talking during the crumb: fading it out", now,
                ms_into_crumb=round((now - crumb_iv.start) * 1000), trigger=source,
            ),
        ]
        return effects + self._human_activity(now)

    def _cancel_overtaken(self, rec: CrumbRecord, iv: BotInterval, now: float) -> list[Effect]:
        """PRD 8.1: speech that started while the crumb was being generated cancels it. The contract has no
        cancel effect, so the crumb is faded the moment it starts (no chime grace: the speech predates it)."""
        if rec.auto:
            self._false_interruptions += 1
        rec.skipped = True
        self._barged.add(rec.crumb_id)
        iv.barged_at = now
        return [
            {"type": "barge_in", "crumb_id": rec.crumb_id},
            self._decision(
                "barge_in", "Someone was already talking when the crumb was ready: cancelling it", now,
                trigger="overtaken", latency_ms=round(rec.latency_ms),
            ),
        ]

    def _fade_unwanted(self, rec: CrumbRecord, iv: BotInterval, now: float) -> list[Effect]:
        """A crumb that was skipped or cancelled before it started still reached playback: fade it now."""
        self._barged.add(rec.crumb_id)
        iv.barged_at = now
        return [
            {"type": "barge_in", "crumb_id": rec.crumb_id},
            self._decision(
                "barge_in", "Crumb was skipped or cancelled before it started: fading it out", now,
                trigger="skipped" if rec.skipped else "cancelled",
            ),
        ]

    def _human_activity(self, now: float) -> list[Effect]:
        """Human speech: crumbs_this_silence resets, the quiet state ends, automatic holds release, and a
        crumb still being generated is cancelled (PRD 8.1: it was meant for a silence that just ended)."""
        self._last_human_activity_at = now
        self._crumbs_this_silence = 0
        self._quiet_announced = False
        effects: list[Effect] = []
        p = self._pending
        if p is not None and not p.cancelled:
            self._cancel_pending(requeue=True)
            effects.append(self._decision(
                "barge_in", "Someone started talking while the crumb was being prepared: cancelling it", now,
                trigger="pending", kind=p.kind, requeued=p.requeued,
            ))
        if self._hold is not None and self._hold.source != "manual":
            effects += self._release_hold(now, "Speech resumed")
        return effects

    def _human_talking(self) -> bool:
        return self._human_turn_open() or (self._vad_human and self._vad_speech(self._now))

    def _human_turn_open(self) -> bool:
        return any(not r.provisional for r in self._tracker.open_records())

    def _turn_open(self) -> bool:
        """A Muse turn is open (our own line echoed back by Muse does not count)."""
        return any(not r.echo for r in self._tracker.open_records())

    def _muse_heard(self, now: float) -> None:
        """Muse heard a person: the local VAD activity around now is speech, not background noise."""
        self._muse_heard_at = max(self._muse_heard_at, now)
        self._noise_latched = False

    def _vad_speech(self, now: float) -> bool:
        """Local VAD activity that counts as speech: Muse confirms it (a human turn is open or was heard
        within VAD_UNCONFIRMED_S), or the stretch is younger than that and the room is not known noisy."""
        if not self._vad_active:
            return False
        if self._human_turn_open():
            return True
        window = self.params.VAD_UNCONFIRMED_S
        ref = self._muse_heard_at
        if not self._noise_latched and self._vad_since is not None:
            ref = max(ref, self._vad_since)
        return now - ref + EPS < window

    # ------------------------------------------------------------------ calibration (PRD 6.5)

    def _begin_calibration(self, now: float, *, resync: bool) -> list[Effect]:
        self._calib.begin(now, resync)
        if not resync:
            self._phase = "calibrating"
        reason = "Re-syncing voices after a transcription reconnect" if resync else "Calibration started: mapping voices to seats"
        effects = [self._decision("calibration", reason, now, step="seat1", resync=resync)]
        return effects + self._calibration_prompt("seat1", now)

    def _calibration_prompt(self, step: Literal["seat1", "seat2"], now: float) -> list[Effect]:
        self._calib.prompt_at = now
        resync = self._calib.resync
        if step == "seat1":
            name2 = self.names.get(2) or "your partner"
            speech_id = "sys_resync1" if resync else "sys_cal1"
            text = (
                "Quick voice re-sync: Seat 1, say a few words."
                if resync else f"Before we start: Seat 1, say hi and tell {name2} where you're from."
            )
        else:
            speech_id = "sys_resync2" if resync else "sys_cal2"
            text = "Seat 2, your turn."
        self._note_bot_line(speech_id, text)
        return [
            {"type": "calibration", "step": step, "label_map": self.label_map},
            {"type": "say", "id": speech_id, "text": text},
        ]

    def _observe_label(self, rec: TurnRecord, now: float) -> list[Effect]:
        if not self._calib.active or rec.turn.label is None or rec.epoch != self._tracker.epoch:
            return []
        return self._after_calibration_steps(self._calib.observe(rec.turn.label, rec.turn.started_at), now)

    def _after_calibration_steps(self, steps: list[str], now: float, *, fallback: bool = False) -> list[Effect]:
        effects: list[Effect] = []
        for step in steps:
            effects += self._backfill_seats()
            how = "15 s fallback, order of appearance" if fallback else "first new voice after the prompt"
            if step == "seat1":
                effects.append(self._decision("calibration", f"Seat 1 voice mapped ({how})", now, step="seat1"))
                if not self._calib.complete:
                    effects += self._calibration_prompt("seat2", now)
            else:
                effects.append(self._decision("calibration", f"Seat 2 voice mapped ({how})", now, step="seat2"))
        if self._calib.complete and steps:
            effects += self._finish_calibration(now)
        return effects

    def _backfill_seats(self) -> list[Effect]:
        effects: list[Effect] = []
        for rec in self._tracker.epoch_records():
            label = rec.turn.label
            if label is not None and rec.turn.seat is None and label in self._calib.label_map:
                rec.turn.seat = self._calib.label_map[label]  # type: ignore[assignment]
                if rec.committed:
                    effects.append(self._turn_effect(rec))
        return effects

    def _finish_calibration(self, now: float) -> list[Effect]:
        effects: list[Effect] = [{"type": "calibration", "step": "done", "label_map": self.label_map}]
        if self._calib.resync:
            effects.append(self._decision("calibration", "Voices re-synced: automatic crumbs resume", now, step="done"))
            self._timer.reset(now)
            return effects
        self._phase = "live"
        self._live_at = now
        self._warmup_ref = now
        self._timer.reset(now)
        effects.append(self._decision("calibration", "Both voices mapped: going live", now, step="done"))
        effects.append({"type": "live"})
        if self._auto_paused:  # PRD 15.3: distress during calibration, so no automatic opener (Drop still works)
            effects.append(self._decision("calibration", "Opening crumb skipped: automatic crumbs are paused", now,
                                          step="done"))
            return effects
        # PRD 8.7: the opener only ever fires into silence. The 15 s fallback can go live mid-turn, so it is
        # queued like a manual crumb and goes out at the first quiet tick.
        return effects + self._request_manual("opener", "Calibration done: opening crumb from a shared overlap", now)

    # ------------------------------------------------------------------ views

    @property
    def _tail_s(self) -> float:
        return self.params.TTS_TAIL_MS / 1000.0

    def silence_s(self, now: float) -> float:
        running = not (self._turn_open() or self._vad_speech(now) or self._bot.playing)
        return self._timer.silence(now, running)

    def _recompute_staleness(self, now: float) -> None:
        if self._live_at is None:
            return
        self._staleness = stale_mod.compute(
            self._live_turns,
            live_started_at=self._live_at,
            now=now,
            energy=self._energy.energy if self._energy else None,
            params=self.params,
        )

    def _speaker_name(self, turn: Turn) -> str | None:
        return self.names.get(turn.seat) if turn.seat is not None else None

    def _context(self, n_turns: int) -> str:
        lines = []
        for t in self._live_turns[-n_turns:]:
            name = self._speaker_name(t) or "Someone"
            lines.append(f"{name}: {t.text}")
        return "\n".join(lines)

    def recent_transcript(self, now: float, seconds: float = 120) -> str:
        lines = []
        for rec in sorted(self._tracker.records(), key=lambda r: r.turn.started_at):
            t = rec.turn
            name = self._speaker_name(t)
            if rec.committed and not t.dropped_as_bot and name and t.ended_at >= now - seconds and t.text:
                lines.append(f"{name}: {t.text}")
        return "\n".join(lines)

    def _inputs(self, now: float, inp: TickInputs) -> dict:
        th = effective_thresholds(inp.base, inp.hold)
        return {
            "silence_s": _r(inp.silence_s),
            "t_normal": _r(th.t_normal),
            "t_long": _r(th.t_long),
            "t_exc": _r(th.t_exc),
            "staleness": _r(inp.staleness, 3),
            "stale_switch": self.params.STALE_SWITCH,
            "since_last_crumb_s": _r(self._ledger.since_last_end(now)),
            "human_turns_since_crumb": self._ledger.human_turns_since_last(),
            "crumbs_this_silence": inp.crumbs_this_silence,
            "crumbs_last_10min": self._ledger.auto_count(now),
            "regular_cooldown_ok": inp.regular_ok,
            "exceptional_cooldown_ok": inp.exceptional_ok,
            "hold": inp.hold,
        }

    def _decision(self, action: str, reason: str, now: float, **inputs: Any) -> Effect:
        ref = self._live_at if self._live_at is not None else (self._t0 if self._t0 is not None else now)
        decision = {"at_s": round(now - ref, 2), "action": action, "reason": reason, "inputs": inputs}
        self._last_decision = decision
        return {"type": "decision", "decision": decision}

    def _touch(self, now: float) -> None:
        self._now = max(self._now, now)
        if self._t0 is None:
            self._t0 = now

    def snapshot(self, now: float) -> dict:
        """EngineSnapshot (web/src/lib/types.ts) minus `cached`, which the runner fills."""
        self._touch(now)
        inp = self._tick_inputs(now)
        th = effective_thresholds(inp.base, inp.hold)
        hold = self._active_hold(now)
        silence = inp.silence_s
        return {
            "phase": self._phase,
            "silence_s": round(silence, 2),
            "band": band(silence, th),
            "t_normal": round(th.t_normal, 2),
            "t_long": round(th.t_long, 2),
            "t_exc": round(th.t_exc, 2),
            "staleness": round(self._staleness.score, 3),
            "stale": self._staleness.stale,
            "parts": {k: round(v, 3) for k, v in self._staleness.parts.items()},
            "hold": hold.to_snapshot(now) if hold is not None else None,
            "cooldown_s": round(max(self._warmup_remaining(now), self._ledger.regular_cooldown_remaining(now, self.params)), 1),
            "next": next_action(inp, self.params),
            "listening_paused": self._listening_paused,
            "auto_paused": self._auto_paused,
            "turn_active": inp.turn_active,
            "vad_active": self._vad_active,
            "noise_suspected": self._vad_active and not inp.vad_active,
            "tts_playing": inp.tts_playing,
            "pending_delivery": inp.pending_delivery,
            "crumbs_this_silence": self._crumbs_this_silence,
            "quiet_hint": self._crumbs_this_silence >= self.params.MAX_UNANSWERED_CRUMBS,
            "depth_max": self.depth_max(now),
            "topic": self.current_topic,
            "energy": self._energy.energy if self._energy else None,
            "label_map": self.label_map,
            "round_remaining_s": _r(self._round_remaining(now), 1),
            "crumbs_last_10min": self._ledger.auto_count(now),
            "last_decision": self._last_decision,
            "metrics": self.metrics(),
        }

    def metrics(self) -> dict:
        """EngineMetrics: aggregate counts and latencies only, no text."""
        now = self._now
        records = self._ledger.records
        decided = [r for r in records if r.answered or now - r.dispatched_at >= 60.0]
        answered = sum(1 for r in decided if r.answered)
        latencies = [r.latency_ms for r in records]
        return {
            "crumbs": len(records),
            "interrupted": sum(1 for r in records if r.interrupted),
            "answered": answered,
            "success_rate": round(answered / len(decided), 3) if decided else None,
            "false_interruptions": self._false_interruptions,
            "latency_ms_p50": _percentile(latencies, 0.50),
            "latency_ms_p95": _percentile(latencies, 0.95),
        }
