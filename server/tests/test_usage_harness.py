"""The usage harness (sim/usage.py) runs hermetically and its ledger adds up."""
from sim import usage


def test_usage_harness_runs_and_counts():
    r = usage.measure()
    assert r["session"]["live_minutes"] >= 9.9
    kinds = [c["kind"] for c in r["crumbs_delivered"]]
    assert kinds[0] == "opener" and "manual" in kinds and ("steer" in kinds or "switch" in kinds)
    assert r["claude_total"]["calls"] > 0 and r["elevenlabs_total"]["calls"] > 0
    assert r["system_lines"]["count"] == 2
    # Credits add up per model and wasted work is never more than the total.
    e = r["elevenlabs_total"]
    assert abs(sum(m["credits"] for m in r["elevenlabs_by_model"].values()) - e["credits"]) < 1
    assert 0 <= e["wasted_credits"] <= e["credits"]
    assert "hold" in r["session"]["decisions"]


def test_usage_harness_quick_setup_costs_no_more_claude_calls():
    """"Just talk": no profiles, overlaps or say-hi lines; the icebreaker is fixed text (no model call) and
    the live memory rides on the energy tag calls, so the session never makes more Claude calls."""
    quick = usage.measure(setup="quick")
    profiles = usage.measure()
    assert quick["session"]["setup"] == "quick" and quick["session"]["memory_items_at_end"] > 0
    first = quick["crumbs_delivered"][0]
    assert first["kind"] == "opener" and first["source"] == "outside"
    assert quick["system_lines"]["count"] == 0
    purposes = {name.split(" [")[0] for name in quick["claude"]}
    assert not purposes & {"profile", "overlaps"} and "energy tagger" in purposes
    assert quick["claude_total"]["calls"] <= profiles["claude_total"]["calls"]
    energy = lambda r: sum(v["calls"] for k, v in r["claude"].items() if k.startswith("energy tagger"))  # noqa: E731
    setup_calls = sum(v["calls"] for k, v in profiles["claude"].items() if k.split(" [")[0] in ("profile", "overlaps"))
    # Memory adds no call of its own; under the rate cap "Just talk" keeps tagging (at half rate) so the
    # memory keeps learning, within the calls it saves on profiles and overlaps.
    assert energy(profiles) <= energy(quick) <= energy(profiles) + setup_calls
    assert quick["session"]["trail_items"] > 0  # the Table's own trail, from the memory and the transcript
