from __future__ import annotations

import re
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.i18n import TRANSLATIONS


def main() -> None:
    keys: set[str] = set()
    for path in Path("app/templates").glob("*.html"):
        matches = re.findall(
            r"(?<![\w.])t\(\s*(['\"])([^'\"]+)\1",
            path.read_text(encoding="utf-8"),
        )
        keys.update(key for _quote, key in matches if not key.endswith("_"))
    missing = {
        language: sorted(keys - set(translations))
        for language, translations in TRANSLATIONS.items()
    }
    for language, values in missing.items():
        print(f"missing_{language}: {values}")
    print(f"static_keys: {len(keys)}")
    if any(missing.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
