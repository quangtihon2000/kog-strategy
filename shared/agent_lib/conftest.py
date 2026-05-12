"""Pytest conftest: add shared/agent_lib to sys.path for bare-name imports."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
