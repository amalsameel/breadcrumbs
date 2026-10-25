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
