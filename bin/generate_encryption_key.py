#!/usr/bin/env python3
"""Print a Fernet key for ENCRYPTION_KEY (.env and docker-compose.yml)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.security import generate_encryption_key


def main() -> None:
    print(generate_encryption_key())


if __name__ == "__main__":
    main()
