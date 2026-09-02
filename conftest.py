import sys
import os
from pathlib import Path

# Ensure project root is on PYTHONPATH so `from src.xxx import` works
sys.path.insert(0, os.path.dirname(__file__))

# Load .env so API keys are available during tests
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(_env_file, override=False)
