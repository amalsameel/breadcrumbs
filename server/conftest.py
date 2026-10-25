# Makes `server/` the import root for pytest (modules import as `from models import ...`).
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
