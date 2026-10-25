"""Crumb validation (PRD 9.4 server-side checks, 15.3 safety) and the shared keyword filters."""
from __future__ import annotations

import pytest

from ai import prompts
from ai.crumbs import validate_crumb
from ai.profiles import allowed_items
from ai.safety import EM_DASH, clean_text, expand_off_limits, validate_crumb_text, word_count
from models import Crumb, Profile, ProfileItem

NAMES = {1: "Maya", 2: "Dev"}


def _profile(seat: int, name: str, items: list[tuple[str, str]], **kw) -> Profile:
    return Profile(
        seat=seat,
        display_name=name,
        items=[ProfileItem(id=i, kind="interest", text=t, hook="What got you into it?", depth=1) for i, t in items],
        **kw,
    )


def _profiles(off1: list[str] | None = None, removed1: list[str] | None = None) -> dict[int, Profile]:
    return {
        1: _profile(1, "Maya", [("s1_i1", "training for first half marathon"), ("s1_i2", "works at a robotics startup")],
                    off_limits=off1 or [], removed_item_ids=removed1 or []),
        2: _profile(2, "Dev", [("s2_i1", "loves rock climbing")]),
    }


def _crumb(text: str, ids: list[str] | None = None, topic: str = "running", why: str = "Because. Extra.") -> Crumb:
    return Crumb(id="c_1", kind="switch", format="question", text=text, addressed_to="both",
                 source="profile_seat1", source_item_ids=ids or [], topic=topic, depth=1, why=why)


def test_valid_crumb_passes_and_trims_why_to_one_sentence():
    ok, cleaned, errors = validate_crumb(_crumb("Dev, ask Maya about her half marathon. Maya, how is it going?"),
                                         names=NAMES, profiles=_profiles())
    assert ok, errors
    assert cleaned.why == "Because."


def test_word_cap():
    text = "Maya, " + " ".join(["word"] * 28) + "?"
    ok, _, errors = validate_crumb(_crumb(text), names=NAMES, profiles=_profiles())
    assert not ok and any("too many words" in e for e in errors)
    exactly = "Maya, " + " ".join(["word"] * 27) + "?"
    assert word_count(exactly) == 28
    assert validate_crumb(_crumb(exactly), names=NAMES, profiles=_profiles())[0]


def test_inline_tts_tags_do_not_count_as_words():
    assert word_count("Maya, <short pause> what's next?") == 3


def test_requires_a_participant_name():
    ok, _, errors = validate_crumb(_crumb("What's something you could talk about for an hour?"),
                                   names=NAMES, profiles=_profiles())
    assert not ok and "no participant name" in errors
    # First names count even when the display name is a full name.
    assert validate_crumb_text("Maya, what's new?", names={1: "Maya Lopez", 2: "Dev"}, profiles={})[0]


def test_name_match_is_whole_word():
    ok, _, _ = validate_crumb_text("Devon, what's new?", names={1: "Maya", 2: "Dev"}, profiles={})
    assert not ok


def test_removed_item_ids_fail():
    profiles = _profiles(removed1=["s1_i1"])
    ok, cleaned, errors = validate_crumb(_crumb("Dev, ask Maya about it.", ids=["s1_i1"]), names=NAMES, profiles=profiles)
    assert not ok and any("removed item ids" in e for e in errors)
    assert "s1_i1" not in cleaned.source_item_ids


def test_unknown_item_ids_are_stripped_not_fatal():
    ok, cleaned, _ = validate_crumb(_crumb("Dev, ask Maya about it.", ids=["s9_i9", "s2_i1"]), names=NAMES,
                                    profiles=_profiles())
    assert ok and cleaned.source_item_ids == ["s2_i1"]


@pytest.mark.parametrize(
    ("toggle", "text"),
    [
        ("work", "Maya, how is your job going? Dev, you next."),
        ("work", "Maya, what are you working on this week?"),
        ("family", "Dev, does your sister climb too?"),
        ("dating history", "Maya, what did your ex think of running?"),
        ("politics", "Dev, who did you vote for?"),
        ("religion", "Maya, do you go to church on Sundays?"),
        ("health", "Dev, any climbing injuries lately?"),
    ],
)
def test_off_limits_quick_toggles(toggle: str, text: str):
    ok, _, errors = validate_crumb(_crumb(text), names=NAMES, profiles=_profiles(off1=[toggle]))
    assert not ok and any("off-limits" in e for e in errors)
    # The same line is fine when nobody marked the topic off-limits.
    assert validate_crumb(_crumb(text), names=NAMES, profiles=_profiles())[0]


def test_off_limits_free_text_and_topic_field():
    profiles = _profiles(off1=["marathon, Tokyo trip"])
    assert not validate_crumb(_crumb("Dev, ask Maya about the marathon."), names=NAMES, profiles=profiles)[0]
    assert not validate_crumb(_crumb("Dev, any trips coming up?", topic="the Tokyo trip"), names=NAMES,
                              profiles=profiles)[0]
    assert "tokyo trip" in expand_off_limits(["marathon, Tokyo trip"])


def test_off_limits_items_are_not_allowed():
    profile = _profiles(off1=["work"])[1]
    ids = [i.id for i in allowed_items(profile)]
    assert ids == ["s1_i1"]  # "works at a robotics startup" is filtered
    removed = _profiles(removed1=["s1_i1"])[1]
    assert [i.id for i in allowed_items(removed)] == ["s1_i2"]


def test_em_dash_replaced_with_comma():
    ok, cleaned, _ = validate_crumb(_crumb(f"Maya {EM_DASH} what's your pace?"), names=NAMES, profiles=_profiles())
    assert ok
    assert EM_DASH not in cleaned.text
    assert cleaned.text == "Maya, what's your pace?"
    assert clean_text(f"one{EM_DASH}two") == "one, two"


@pytest.mark.parametrize(
    "text",
    [
        "Maya, what do you find most attractive in a person?",
        "Dev, describe your body type in three words.",
        "Maya, what is the sexiest thing about climbing?",
        "Dev, where would you kiss someone on a first date?",
        "Maya, what outfit is Dev wearing tonight?",
    ],
)
def test_sexual_appearance_and_body_content_blocked_in_any_mode(text: str):
    ok, _, errors = validate_crumb(_crumb(text), names=NAMES, profiles=_profiles())
    assert not ok and any("unsafe content" in e for e in errors)


def test_everyday_words_are_not_false_positives():
    for text in ("Maya, did everybody at the gym cheer?", "Dev, what's the best Essex pub?",
                 "Maya, do you mean the hot sauce?"):
        assert validate_crumb_text(text, names=NAMES, profiles=_profiles(off1=["work"]))[0], text


def test_prompts_and_canned_crumbs_have_no_em_dash():
    texts = list(prompts.ALL_PROMPTS) + [t for v in prompts.CANNED.values() for _, t in v]
    texts += [prompts.crumb_system(m) for m in ("social", "networking", "speed_dating")]
    assert all(EM_DASH not in t for t in texts)


def test_canned_crumbs_pass_validation_once_filled():
    for mode in ("social", "networking", "speed_dating"):
        for _, template in prompts.canned_crumbs(mode):
            text = template.format(name1="Maya", name2="Dev")
            assert validate_crumb_text(text, names=NAMES, profiles=_profiles())[0], text
