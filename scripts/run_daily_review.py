from __future__ import annotations

import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.scheduler import create_daily_reviews

if __name__ == "__main__":
    result = create_daily_reviews(os.getenv("STUDYHUB_DATABASE_URL", "sqlite:///instance/studyhub.db"))
    print(f"Daily review complete: considered={result['considered']} created={result['created']}")
