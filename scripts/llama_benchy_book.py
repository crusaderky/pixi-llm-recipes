"""Seed llama-benchy's corpus cache from the bundled benchmark text.

llama-benchy downloads ``BOOK_URL`` on first use unless a file named after the
URL's MD5 hash already exists in ``~/.cache/llama-benchy``. The bundled
``sample-data/wiki.test.raw`` is a public-domain benchmark corpus, so both
llama-benchy launchers copy it into that cache before starting a run.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

#: llama-benchy's default --book-url; its cache filename is md5 of this string.
BOOK_URL = "https://www.gutenberg.org/files/1661/1661-0.txt"

BOOK_FILE = Path(__file__).resolve().parent.parent / "sample-data/wiki.test.raw"


def seed_book_cache() -> Path:
    """Put the bundled corpus where llama-benchy will look for BOOK_URL."""
    digest = hashlib.md5(BOOK_URL.encode("utf-8")).hexdigest()
    cache_file = Path.home() / ".cache" / "llama-benchy" / f"{digest}.txt"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BOOK_FILE, cache_file)
    return cache_file
