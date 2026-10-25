"""Engine state primitives (PRD 8.1 to 8.3, 8.11, 6.5).

Turn tracking keyed on Muse `turnId`, the pause timer, bot-speaking intervals (self-hearing guard)
and the calibration label map. Pure and deterministic: every method takes `now` (monotonic seconds).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from models import HoldKind, Turn

STUCK_TURN_S = 30.0  # speechStart with no speechEnd for this long is force-closed
COMPLETE_TIMEOUT_S = 5.0  # a closed turn with no speechComplete is committed with its best partial
VAD_GRACE_S = 0.3  # PRD 8.11: barge-in ignored for the first 300 ms of playback (chime)
BOT_INTERVAL_KEEP_S = 300.0
SYS_PREFIX = "sys_"

QUESTION_WORDS = frozenset({
    "what", "why", "how", "when", "where", "who", "whom", "whose", "which",
    "is", "are", "do", "does", "did", "can", "could", "would", "will", "should",
    "have", "has", "was", "were",
})
BACKCHANNEL_PHRASES = frozenset({
    "for sure", "uh huh", "mm hmm", "no way", "fair enough", "i see", "got it", "makes sense",
    "oh nice", "oh wow", "oh cool", "yeah totally", "oh yeah", "oh really",
})
BACKCHANNEL_WORDS = frozenset({
    "yeah", "yes", "yep", "yup", "yah", "totally", "haha", "lol", "mm", "mhm", "right", "true",
    "nice", "cool", "wow", "oh", "okay", "ok", "sure", "exactly", "same", "definitely", "ah", "aw",
    "damn", "word", "fair", "really", "nope", "no",
})

_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")


# --- Text features (PRD 8.2) ---------------------------------------------------


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text.replace("’", "'")))


def is_question(text: str) -> bool:
    stripped = text.strip()
    if stripped.endswith("?"):
        return True
    words = _WORD_RE.findall(stripped.lower())
    return bool(words) and words[0] in QUESTION_WORDS


def _normalize_backchannel(text: str) -> list[str]:
    words = _WORD_RE.findall(text.lower().replace("’", "'"))
    out: list[str] = []
    for w in words:
        if re.fullmatch(r"(ha)+h?", w):
            w = "haha"
        elif re.fullmatch(r"m+", w):
            w = "mm"
        elif re.fullmatch(r"m+h+m+", w):
            w = "mhm"
        out.append(w)
    return out


def is_backchannel(text: str) -> bool:
    words = _normalize_backchannel(text)
    if not words or len(words) > 4:
        return False
    if " ".join(words) in BACKCHANNEL_PHRASES:
        return True
    return all(w in BACKCHANNEL_WORDS for w in words)


# --- Turns -------------------------------------------------------------------------


@dataclass
class TurnRecord:
    """A Turn plus engine-only bookkeeping (the shared Turn model stays exactly as in models.py)."""

    turn: Turn
    epoch: int
    muse_id: int
    open: bool = True
    partial: str = ""
    final: str | None = None
    committed: bool = False
    provisional: bool = False  # started while the bot was speaking: human status decided at speechEnd
    pre_hold: HoldKind | None = None  # lexical hold seen on the latest partial
    hold_kind: HoldKind | None = None  # hold armed by this turn (for the `turn` effect)
    classified: bool = False

    @property
    def best_text(self) -> str:
        return self.final if self.final is not None else self.partial

    @property
    def is_human(self) -> bool:
        return not self.open and not self.turn.dropped_as_bot


class TurnTracker:
    """Turn state keyed on (epoch, Muse turnId). A Muse reconnect starts a new epoch (turnIds restart)."""

    def __init__(self) -> None:
        self.epoch = 0
        self._records: dict[int, TurnRecord] = {}
        self._active: dict[int, TurnRecord] = {}  # open or not yet committed (scanned every tick)
        self._by_key: dict[tuple[int, int], int] = {}
        self._next_id = 1
        self.last_human_end: float | None = None

    def start(self, muse_id: int, now: float) -> tuple[TurnRecord, bool]:
        """Returns (record, created). A repeated speechStart for a known turnId is a no-op."""
        existing = self.by_muse(muse_id)
        if existing is not None:
            return existing, False
        rec = TurnRecord(turn=Turn(turn_id=self._next_id, started_at=now), epoch=self.epoch, muse_id=muse_id)
        self._records[rec.turn.turn_id] = rec
        self._active[rec.turn.turn_id] = rec
        self._by_key[(self.epoch, muse_id)] = rec.turn.turn_id
        self._next_id += 1
        return rec, True

    def by_muse(self, muse_id: int) -> TurnRecord | None:
        tid = self._by_key.get((self.epoch, muse_id))
        return self._records.get(tid) if tid is not None else None

    def by_id(self, turn_id: int) -> TurnRecord | None:
        return self._records.get(turn_id)

    def records(self) -> list[TurnRecord]:
        return list(self._records.values())

    def active_records(self) -> list[TurnRecord]:
        return list(self._active.values())

    def mark_committed(self, rec: TurnRecord) -> None:
        rec.committed = True
        if not rec.open:
            self._active.pop(rec.turn.turn_id, None)

    def mark_closed(self, rec: TurnRecord) -> None:
        rec.open = False
        if rec.committed:
            self._active.pop(rec.turn.turn_id, None)

    def open_records(self) -> list[TurnRecord]:
        return [r for r in self._active.values() if r.open]

    def any_open(self) -> bool:
        return any(r.open for r in self._active.values())

    def current_open(self) -> TurnRecord | None:
        """The most recently started open turn (transcript partials carry no turnId)."""
        opened = self.open_records()
        return max(opened, key=lambda r: (r.turn.started_at, r.turn.turn_id)) if opened else None

    def latest_uncommitted(self) -> TurnRecord | None:
        pending = [r for r in self._active.values() if not r.committed and r.epoch == self.epoch]
        return max(pending, key=lambda r: r.turn.turn_id) if pending else None

    def latest_unlabeled(self) -> TurnRecord | None:
        """`speaker` labels the preceding span: prefer the open turn, else the newest unlabeled one."""
        current = self.current_open()
        if current is not None and current.turn.label is None:
            return current
        unlabeled = [r for r in self._records.values() if r.turn.label is None and r.epoch == self.epoch]
        return max(unlabeled, key=lambda r: r.turn.turn_id) if unlabeled else None

    def epoch_records(self) -> list[TurnRecord]:
        return [r for r in self._records.values() if r.epoch == self.epoch]

    def new_epoch(self) -> list[TurnRecord]:
        """Start a new label/turnId epoch; returns turns of the old epoch that were still open."""
        stale = self.open_records()
        self.epoch += 1
        return stale

    def turns(self) -> list[Turn]:
        return [self._records[k].turn for k in sorted(self._records)]


# --- Bot speech (self-hearing guard, PRD 8.11) -------------------------------------------


@dataclass
class BotInterval:
    speech_id: str
    start: float
    end: float | None = None  # audio end; None while playing
    barged_at: float | None = None  # a human barged in: from here on the speech in the room is theirs

    @property
    def is_crumb(self) -> bool:
        return not self.speech_id.startswith(SYS_PREFIX)

    def covered_until(self, now: float, tail_s: float) -> float:
        """End of the span that may contain our own voice (audio end + tail, or the barge-in moment)."""
        if self.barged_at is not None:
            return self.barged_at
        return self.end + tail_s if self.end is not None else now


@dataclass
class BotSpeech:
    """TTS playback intervals for crumbs and `sys_` prompts. The 300 ms tail is applied at query time."""

    intervals: list[BotInterval] = field(default_factory=list)
    last_end: float | None = None

    def start(self, speech_id: str, now: float) -> BotInterval:
        current = self.playing_interval(speech_id)
        if current is not None:
            return current
        iv = BotInterval(speech_id=speech_id, start=now)
        self.intervals.append(iv)
        return iv

    def stop(self, speech_id: str, now: float) -> BotInterval | None:
        iv = self.playing_interval(speech_id)
        if iv is not None:
            iv.end = now
        self.last_end = now
        return iv

    def playing_interval(self, speech_id: str) -> BotInterval | None:
        for iv in reversed(self.intervals):
            if iv.speech_id == speech_id and iv.end is None:
                return iv
        return None

    @property
    def playing(self) -> bool:
        return any(iv.end is None for iv in self.intervals)

    def crumb_playing(self) -> BotInterval | None:
        for iv in reversed(self.intervals):
            if iv.end is None and iv.is_crumb:
                return iv
        return None

    def suspect(self, now: float, tail_s: float) -> bool:
        """True while the bot is speaking or within the tail after it stopped (speech may be echo)."""
        return any(iv.barged_at is None and (iv.end is None or now < iv.end + tail_s) for iv in self.intervals)

    def overlap_ratio(self, start: float, end: float, now: float, tail_s: float) -> float:
        """Share of [start, end] covered by bot speech (plus tail)."""
        if end <= start:
            inside = any(iv.start <= start <= iv.covered_until(now, tail_s) for iv in self.intervals)
            return 1.0 if inside else 0.0
        covered = 0.0
        for iv in self.intervals:
            covered += max(0.0, min(end, iv.covered_until(now, tail_s)) - max(start, iv.start))
        return min(1.0, covered / (end - start))

    def prune(self, now: float) -> None:
        self.intervals = [iv for iv in self.intervals if iv.end is None or now - iv.end < BOT_INTERVAL_KEEP_S]


# --- Pause timer (PRD 8.3) -----------------------------------------------------------------


@dataclass
class PauseTimer:
    """silence_s = now - anchor, where anchor is the latest reset point (human speech end, VAD activity,
    bot speech end, crumb dispatch, hold timeout, resume). Frozen while listening is paused."""

    anchor: float = 0.0
    frozen: float | None = None

    def reset(self, now: float) -> None:
        self.anchor = max(self.anchor, now)

    def silence(self, now: float, running: bool) -> float:
        if self.frozen is not None:
            return self.frozen
        if not running:
            return 0.0
        return max(0.0, now - self.anchor)

    def freeze(self, value: float) -> None:
        self.frozen = value

    def unfreeze(self, now: float) -> None:
        self.frozen = None
        self.reset(now)


# --- Calibration (PRD 6.5) -----------------------------------------------------------------

CalibrationStep = Literal["idle", "seat1", "seat2", "done"]


@dataclass
class Calibration:
    """Maps Muse diarization labels to seats: seat 1 prompt, seat 2 prompt, 15 s order-of-appearance fallback."""

    label_map: dict[str, int] = field(default_factory=dict)
    step: CalibrationStep = "idle"
    started_at: float = 0.0
    prompt_at: float = 0.0
    resync: bool = False
    labels_seen: list[str] = field(default_factory=list)

    def begin(self, now: float, resync: bool) -> None:
        self.label_map = {}
        self.labels_seen = []
        self.step = "seat1"
        self.started_at = now
        self.prompt_at = now
        self.resync = resync

    @property
    def active(self) -> bool:
        return self.step in ("seat1", "seat2")

    @property
    def complete(self) -> bool:
        return len(self.label_map) >= 2

    def observe(self, label: str, turn_started_at: float) -> list[str]:
        """Feed the label of a human turn. Returns the steps completed ("seat1", "seat2")."""
        if label not in self.labels_seen:
            self.labels_seen.append(label)
        if self.step == "seat1" and turn_started_at >= self.prompt_at:
            self.label_map[label] = 1
            self.step = "seat2"
            return ["seat1"]
        if self.step == "seat2" and label not in self.label_map and turn_started_at >= self.prompt_at:
            self.label_map[label] = 2
            self.step = "done"
            return ["seat2"]
        return []

    def fallback(self, labels_in_order: list[str]) -> list[str]:
        """After the timeout: map the first distinct labels by order of appearance (human turns of the
        current epoch, including any heard before the prompt)."""
        seen = list(dict.fromkeys(labels_in_order + self.labels_seen))
        done: list[str] = []
        if self.step == "seat1" and seen:
            self.label_map[seen[0]] = 1
            self.step = "seat2"
            done.append("seat1")
        if self.step == "seat2":
            others = [lab for lab in seen if lab not in self.label_map]
            if others:
                self.label_map[others[0]] = 2
                self.step = "done"
                done.append("seat2")
        return done
