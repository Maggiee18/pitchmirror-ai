import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# deterministic, fast test configuration (set before the app imports settings)
os.environ.update({
    "LLM_PROVIDER": "offline",
    "GEMINI_API_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "GROQ_API_KEY": "",
    "ANALYSIS_MIN_INTERVAL_S": "0",
    "ANALYSIS_MIN_NEW_WORDS": "20",
    "AUTO_QUESTION_MIN_INTERVAL_S": "0",
    "LLM_TIMEOUT_SECONDS": "3",
    "DATA_DIR": tempfile.mkdtemp(prefix="pm_test_"),
})
