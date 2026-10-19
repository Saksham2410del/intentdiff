"""Project settings. Paths and the model can be overridden with env variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DB_PATH = Path(os.getenv("INTENTDIFF_DB", ROOT / "data" / "bank.db"))
NOTES_PATH = ROOT / "data" / "schema_notes.yaml"
HISTORY_PATH = Path(os.getenv("INTENTDIFF_HISTORY", ROOT / "data" / "ticket_history.yaml"))
REVIEWS_DB = Path(os.getenv("INTENTDIFF_REVIEWS_DB", ROOT / "data" / "reviews.db"))
CHECKPOINTS_DB = Path(os.getenv("INTENTDIFF_CHECKPOINTS_DB", ROOT / "data" / "checkpoints.db"))
CACHE_DIR = Path(os.getenv("INTENTDIFF_CACHE", ROOT / ".cache" / "llm"))

MODEL = os.getenv("INTENTDIFF_MODEL", "gemini-3.8-flash")

# Number of independent readings of the ticket. Two is the smallest number
# that can disagree, which is the whole point of asking more than once.
INTENT_SAMPLES = 2

# Past tickets shown to the model as examples (the retrieval step)
EXAMPLES_IN_PROMPT = 3
# If the model's reference SQL fails on the copy, it gets the error back and
# tries again. This is the total number of tries before we give up.
MAX_READ_ATTEMPTS = 3
# How many times one review may go back to the requester with questions
MAX_QUESTION_ROUNDS = 2

DRY_RUN_TIMEOUT_S = 15
MAX_DIFF_ROWS = 20_000      # past this we only count changed rows instead of comparing each one
LARGE_CHANGE_ROWS = 1_000   # bigger changes get a "needs senior approval" warning
