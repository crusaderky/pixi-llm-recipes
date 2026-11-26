#!/usr/bin/env python3
"""Run llama-benchy with the bundled corpus instead of a live download.

Arguments are passed through to llama-benchy unchanged.
"""

from __future__ import annotations

import subprocess
import sys

from llama_benchy_book import BOOK_URL, seed_book_cache


def main() -> int:
    cache_file = seed_book_cache()
    print(f"llama-benchy corpus: {cache_file} (from {BOOK_URL})", flush=True)
    return subprocess.run(
        [sys.executable, "-m", "llama_benchy", *sys.argv[1:]], check=False
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
