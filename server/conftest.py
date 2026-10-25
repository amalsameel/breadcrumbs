# Makes `server/` the import root for pytest (modules import as `from models import ...`).
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _hermetic_ai(monkeypatch):
    """Never let a test reach a real AI provider because keys happen to be in .env.

    The process-wide gateway (ai.gemini.get_gemini) is built from config, so with ANTHROPIC_API_KEY or
    GEMINI_API_KEY set it would make real, billed calls. Tests that exercise real request shapes build
    their own client with a fake transport; this only pins the shared singleton to mock mode.
    """
    import ai.gemini as gemini_mod
    import config

    monkeypatch.setattr(config, "GEMINI_MOCK", True)
    monkeypatch.setattr(config, "AI_MOCK", True)
    monkeypatch.setattr(config, "AI_PROVIDER", "mock")
    monkeypatch.setattr(config, "CLAUDE_ENABLED", False)
    monkeypatch.setattr(gemini_mod, "_singleton", gemini_mod.Gemini(mock=True))
