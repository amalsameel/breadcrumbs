"""Engine state primitives (PRD 8.1 to 8.3, 8.11, 6.5).

Turn tracking keyed on Muse `turnId`, the pause timer, bot-speaking intervals (self-hearing guard)
and the calibration label map. Pure and deterministic: every method takes `now` (monotonic seconds).
"""
from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from models import HoldKind, Turn

if TYPE_CHECKING:
    from config import Params

STUCK_TURN_S = 30.0  # speechStart with no speechEnd for this long is force-closed
COMPLETE_TIMEOUT_S = 5.0  # a closed turn with no speechComplete is committed with its best partial
VAD_GRACE_S = 0.3  # PRD 8.11: barge-in ignored for the first 300 ms of playback (chime)
BOT_INTERVAL_KEEP_S = 300.0
BOT_SPEECH_MAX_S = 20.0  # longest crumb (28 words) or system line, even via slow speechSynthesis, plus margin
SYS_PREFIX = "sys_"
ECHO_WINDOW_S = 1.0  # a Muse turn starting during bot speech or this soon after may be the bot's own voice
ECHO_MIN_WORDS = 2  # words needed before the echo check decides (the first partial is often one word)
ECHO_REPLY_WORDS = 3  # new words at the end of an echo-marked turn that make it a person's reply (same Muse turn)
# Foreground (who is at the table): the Table's VAD frames are kept for a minute so each Muse turn can be
# judged at speechEnd against what the laptop mic heard during it (level and coverage).
VADLOG_FRAMES = 600  # 60 s at 10 Hz
VAD_FRAME_S = 0.1  # the Table sends one VAD frame every 100 ms
VAD_EVIDENCE_FRAC = 0.7  # frames received / frames expected for the VAD to have a say about a span
SEAT_LEVEL_ALPHA = 0.3  # EMA weight of a new seat-turn level
SEAT_LEVEL_MIN_SAMPLES = 2  # turns before the seated level is trusted (the two calibration turns)
SEAT_ACTIVE_MIN_FRAC = 0.5  # a seat turn feeds the seated level only when VAD covered at least this much
LEVEL_PERCENTILE = 0.8

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


def _echo_words(text: str) -> list[str]:
    """Lowercased words with simple ASR variants folded (possessive 's, plural s)."""
    out: list[str] = []
    for w in _WORD_RE.findall(text.lower().replace("’", "'")):
        if w.endswith("'s"):
            w = w[:-2]
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def _line_pairs(lines: list[str]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for line in lines:
        words = _echo_words(line)
        pairs.update(zip(words, words[1:]))
    return pairs


def _echo_covered(words: list[str], pairs: set[tuple[str, str]]) -> list[bool]:
    """Per word: it sits in an adjacent word pair that also occurs in the line(s)."""
    covered = [False] * len(words)
    for i in range(len(words) - 1):
        if (words[i], words[i + 1]) in pairs:
            covered[i] = covered[i + 1] = True
    return covered


def echo_overlap(text: str, line: str) -> float:
    """How much of `text` repeats `line` (0 to 1): the share of the text's words that sit in a word pair
    (adjacent bigram) that also occurs in the line. Echo transcripts are runs of the line, so they score
    high even with a few ASR errors; a reply that merely shares words with it ("Hi, I'm from Austin" after
    "say hi and tell Dev where you're from") scores low. Fewer than 2 words gives 0 (undecidable)."""
    words = _echo_words(text)
    if len(words) < 2:
        return 0.0
    covered = _echo_covered(words, _line_pairs([line]))
    return sum(covered) / len(words)


def echo_tail_words(text: str, lines: list[str]) -> int:
    """How many words at the end of `text` repeat none of `lines` (no shared adjacent word pair). Muse can
    keep one turn open when the bot's echo runs straight into a person's answer: the transcript is then the
    line followed by the reply, and this counts the reply words after the echo. A single ASR slip inside an
    echo uncovers one word, so a run of several uncovered words at the end is a person talking."""
    words = _echo_words(text)
    n = 0
    for c in reversed(_echo_covered(words, _line_pairs(lines))):
        if c:
            break
        n += 1
    return n


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
    echo_check: bool = False  # started during (or just after) a bot line: wait for words before acting
    echo: bool = False  # its words repeat the bot line being spoken: our own voice, never a human turn
    suspect_at_start: bool = False  # started inside bot speech or its tail (provisional before the echo check)
    reply_after_echo: bool = False  # echo-marked first, then a person's reply in the same Muse turn
    # Foreground: every non-echo turn is "pending" while open and decided once at speechEnd (classify_turn).
    fg: Literal["pending", "seat", "background"] = "pending"
    fg_reason: str = ""
    near_at_start: bool = False  # the Table mic heard a near voice when it started: acts at once, as before
    dev: bool = False  # injected from the X-ray (no VAD): always a seat
    dev_seat: int | None = None  # the seat a dev turn was typed as ("Just talk" maps its label to that seat)
    upgradable: bool = False  # background on 0 words or a filler: re-judged on the final transcript
    vad_stats: VadStats | None = None  # the VAD over the turn, kept for that re-judgement

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


# --- Foreground: VAD log, seated level, turn classification -----------------------------------------


@dataclass(frozen=True)
class VadFrame:
    t: float
    active: bool
    level_db: float
    quiet_ms: int | None = None


@dataclass(frozen=True)
class VadStats:
    """What the Table mic heard over a span (numbers only)."""

    frames: int
    expected: float
    active_frac: float
    level_db: float | None  # 80th percentile over the active frames (all frames when none is active)
    last_active_t: float | None
    voice_end_t: float | None  # end of the last voiced stretch (client quiet_ms), else its last active frame

    @property
    def evidence(self) -> bool:
        """Enough frames arrived for the VAD to have a say (the Table holds back echo-level frames)."""
        return self.frames > 0 and self.frames + 1e-9 >= VAD_EVIDENCE_FRAC * self.expected


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))]


class VadLog:
    """The last VADLOG_FRAMES Table VAD frames: (t, active, level_db, quiet_ms)."""

    def __init__(self, maxlen: int = VADLOG_FRAMES) -> None:
        self._frames: deque[VadFrame] = deque(maxlen=maxlen)

    def add(self, t: float, active: bool, level_db: float, quiet_ms: int | None = None) -> None:
        self._frames.append(VadFrame(t, bool(active), float(level_db), quiet_ms))

    def window(self, t0: float, t1: float) -> list[VadFrame]:
        out: list[VadFrame] = []
        for f in reversed(self._frames):
            if f.t < t0 - 1e-9:
                break
            if f.t <= t1 + 1e-9:
                out.append(f)
        out.reverse()
        return out

    def stats(self, t0: float, t1: float) -> VadStats:
        frames = self.window(t0, t1)
        active = [f for f in frames if f.active]
        level = _percentile([f.level_db for f in (active or frames)], LEVEL_PERCENTILE)
        last_active_t = active[-1].t if active else None
        voice_end_t = last_active_t
        if active:
            idx = len(frames) - 1 - next(i for i, f in enumerate(reversed(frames)) if f.active)
            fall = next((f for f in frames[idx + 1:] if not f.active), None)
            if fall is not None and fall.quiet_ms is not None:
                voice_end_t = fall.t - fall.quiet_ms / 1000.0
        return VadStats(
            frames=len(frames),
            expected=max(0.0, (t1 - t0) / VAD_FRAME_S),
            active_frac=len(active) / len(frames) if frames else 0.0,
            level_db=level,
            last_active_t=last_active_t,
            voice_end_t=voice_end_t,
        )

    def last_at_or_above(self, t0: float, t1: float, level_db: float) -> float | None:
        """End of the last active frame at or above `level_db` in [t0, t1] (the seated voice under noise)."""
        for f in reversed(self.window(t0, t1)):
            if f.active and f.level_db + 1e-9 >= level_db:
                return f.t + VAD_FRAME_S
        return None


@dataclass
class SeatLevel:
    """EMA of the speech-band level of seat-classified turns (dBFS), seeded by the calibration turns."""

    db: float | None = None
    count: int = 0

    def observe(self, level_db: float) -> None:
        self.db = level_db if self.db is None else self.db + SEAT_LEVEL_ALPHA * (level_db - self.db)
        self.count += 1

    @property
    def known(self) -> bool:
        return self.db is not None and self.count >= SEAT_LEVEL_MIN_SAMPLES


def vad_near(stats: VadStats, seat: SeatLevel, params: Params) -> bool:
    """The span sounds like someone at the table: enough coverage, and (once known) near the seated level."""
    if not stats.evidence or stats.active_frac + 1e-9 < params.NEAR_ACTIVE_FRAC:
        return False
    if not seat.known or stats.level_db is None:
        return True
    return stats.level_db + 1e-9 >= seat.db - params.NEAR_MARGIN_DB  # type: ignore[operator]


def vad_far(stats: VadStats, seat: SeatLevel, params: Params) -> bool:
    """The span sounds like a voice away from the table: barely any VAD, or (once known) far below the seats."""
    if not stats.evidence:
        return False
    if stats.active_frac < params.FAR_ACTIVE_FRAC:
        return True
    return seat.known and stats.level_db is not None and stats.level_db < seat.db - params.FAR_MARGIN_DB  # type: ignore[operator]


def classify_turn(rec: TurnRecord, stats: VadStats, seat: SeatLevel, label_mapped: bool, calib_complete: bool,
                  params: Params, *, ordering: bool = False) -> tuple[Literal["seat", "background"], str]:
    """Seat or background for a closed (non-echo) Muse turn, from its label, words and the VAD over it.

    `ordering`: "Just talk" maps labels to Person 1 / 2 by order of first appearance and has no say-hi
    prompts, so a turn is never a seat just because the mapping is incomplete: a new label must sound like
    someone at the table to take a free seat (a bystander must not become Person 2).

    Reasons are short fixed strings (they go into the X-ray decision log; never transcript text)."""
    if rec.dev:
        return "seat", "dev"
    if not params.BYSTANDER_GATING or (not calib_complete and not ordering):
        return "seat", "ungated"
    words = count_words(rec.best_text)
    if words == 0:
        return "background", "empty"
    near = vad_near(stats, seat, params)
    far = vad_far(stats, seat, params)
    if ordering and not calib_complete and not label_mapped:
        if far:
            return "background", "far voice before mapping"
        if near or (not stats.evidence and words >= 2):
            return "seat", "new voice, mapped by order"
        return "background", "new voice, not near"
    if label_mapped:
        if far:
            return "background", "far voice on a seat label"
        if words <= params.FILLER_MAX_WORDS and stats.evidence and not near:
            return "background", "filler"
        return "seat", "seat label"
    if near and words >= params.DRIFT_MIN_WORDS and stats.active_frac + 1e-9 >= SEAT_ACTIVE_MIN_FRAC:
        return "seat", "near voice, unmapped label"
    if not stats.evidence and rec.turn.label is None and words >= 2:
        return "seat", "no VAD evidence"
    return "background", "unmapped label"


UPGRADABLE_REASONS = frozenset({"empty", "filler"})


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

    def open_intervals(self) -> list[BotInterval]:
        return [iv for iv in self.intervals if iv.end is None]

    def crumb_playing(self) -> BotInterval | None:
        for iv in reversed(self.intervals):
            if iv.end is None and iv.is_crumb:
                return iv
        return None

    def echo_sources(self, since: float, now: float, window_s: float) -> list[BotInterval]:
        """Bot lines that may still be sounding in the room at `since`: playing, or ended less than
        `window_s` before it. A line a human barged in on before `since` is theirs from then on."""
        return [
            iv for iv in self.intervals
            if iv.start <= now
            and (iv.end is None or since < iv.end + window_s)
            and (iv.barged_at is None or iv.barged_at > since)
        ]

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
    """silence_s = now - anchor, where anchor is the latest reset point (the end of a seat-classified human
    turn, bot speech end, crumb dispatch, hold timeout, resume). Local VAD never moves it. Frozen while
    listening is paused."""

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

CalibrationStep = Literal["idle", "seat1", "seat2", "auto", "done"]


@dataclass
class Calibration:
    """Maps Muse diarization labels to seats: seat 1 prompt, seat 2 prompt, 15 s order-of-appearance fallback.

    `auto` ("Just talk"): no prompts; each new label of a seat turn takes the next free seat, in order of
    first appearance (a dev turn takes the seat it was typed as, when free)."""

    label_map: dict[str, int] = field(default_factory=dict)
    step: CalibrationStep = "idle"
    started_at: float = 0.0
    prompt_at: float = 0.0
    resync: bool = False
    labels_seen: list[str] = field(default_factory=list)

    def begin(self, now: float, resync: bool, *, auto: bool = False) -> None:
        self.label_map = {}
        self.labels_seen = []
        self.step = "auto" if auto else "seat1"
        self.started_at = now
        self.prompt_at = now
        self.resync = resync

    @property
    def active(self) -> bool:
        return self.step in ("seat1", "seat2")

    @property
    def ordering(self) -> bool:
        """Labels are mapped by order of first appearance, with no prompts ("Just talk")."""
        return self.step == "auto"

    @property
    def complete(self) -> bool:
        return len(self.label_map) >= 2

    def observe(self, label: str, turn_started_at: float, seat: int | None = None) -> list[str]:
        """Feed the label of a human turn. Returns the steps completed ("seat1", "seat2")."""
        if label not in self.labels_seen:
            self.labels_seen.append(label)
        if self.step == "auto":
            if label in self.label_map:
                return []
            free = [s for s in (1, 2) if s not in self.label_map.values()]
            target = seat if seat in free else free[0]
            self.label_map[label] = target
            if len(self.label_map) >= 2:
                self.step = "done"
            return [f"seat{target}"]
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
