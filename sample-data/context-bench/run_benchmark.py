#!/usr/bin/env python
"""Long-context recall benchmark runner.

Reads a TOML config describing one or more OpenAI-compatible model endpoints,
sends each model the shared ``AGENTS.md`` system prompt followed by a book (the
book text already has its 20 questions appended), collects the answers, grades
them against ``<book>.answers.txt`` and writes a TOML report.

Usage:
    python run_benchmark.py config.toml [-o results.toml]

Config format (one table per model; the table name is the "model tag")::

    [my-model]
    url = "http://localhost:8080/v1"   # OpenAI endpoint; defaults to localhost
    api_key = "sk-..."                 # optional, literal key
    api_key_env = "OPENAI_API_KEY"     # optional, name of env var holding key
    model_name = "Qwen3.6-35B"         # optional; defaults to the table name
    ctx-size = ["16k", "64k", "256k"]  # required; the books to run (each int,
                                       # or a k / M suffix; must match a book)
    temperature = 0.0                  # optional; default: let server decide
    max_tokens = 4096                  # optional; default: let server decide
    seed = 42                          # optional; default: server picks a random
                                       # seed per request (non-reproducible)
    timeout = 3600                     # optional; request timeout in seconds

An optional ``[*]`` table supplies defaults applied to every model; values set
in a specific model table override the ``[*]`` defaults::

    [*]
    ctx-size = ["16k", "32k", "64k", "128k", "256k"]   # run every model on all
    timeout = 1800

For every model the runner evaluates each book listed in ``ctx-size``, so you
can watch recall degrade as the context grows. Output (one table per
model/book pair)::

    [my-model.64k]
    raw_answers = '''...the model's unprocessed answer text...'''
    thoughts = '''...the model's raw train of thought (if any)...'''
    outcomes = ["PASS", "NO ANSWER", "WRONG", ...]   # 20 elements, A1..A20
    grade = 0.85           # (#PASS - #WRONG) / 20, in [-1, 1]
    wall_time_s = 12.34    # request wall-clock time
    completion_tokens = 256  # output tokens (when the server reports usage)
    tokens_per_s = 20.7    # completion_tokens / wall_time_s

The reply is streamed to stdout as it arrives: the train of thought is shown
in grey, the answers in the default colour. Press Ctrl+C to stop early —
whatever has been collected so far (including the partial in-flight reply) is
still written to the output TOML.
"""

import argparse
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

import tomli_w
import tomllib
from openai import OpenAI, OpenAIError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)

HERE = Path(__file__).resolve().parent
N_QUESTIONS = 20
GREY = "\033[90m"  # train of thought, as per standard coding agents
RESET = "\033[0m"


# --------------------------------------------------------------------------- #
# Config models
# --------------------------------------------------------------------------- #
def parse_size(value: int | str) -> int:
    """Parse a context size such as 65536, "64k" or "1M" into an int."""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kKmM])?", s)
    if not m:
        raise ValueError(f"cannot parse size {value!r}")
    num = float(m.group(1))
    suffix = (m.group(2) or "").lower()
    factor = {"": 1, "k": 1024, "m": 1024 * 1024}[suffix]
    return int(num * factor)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    url: str = "http://localhost:8080/v1"
    api_key: str | None = None
    api_key_env: str | None = None
    model_name: str | None = None
    ctx_size: list[int] = Field(..., alias="ctx-size")
    temperature: float | None = None
    max_tokens: int | None = None
    seed: int | None = None
    timeout: float = 600.0

    @field_validator("ctx_size", mode="before")
    @classmethod
    def _parse_ctx_size(cls, v: object) -> list[int]:
        if not isinstance(v, (list, tuple)):
            raise TypeError('ctx-size must be a list, e.g. ["16k", "64k"]')
        return [parse_size(x) for x in v]  # type: ignore[arg-type]

    def resolve_api_key(self) -> str:
        """Return the API key, reading the env var if requested.

        OpenAI-compatible local servers ignore the key, so a sentinel is used
        when none is configured.
        """
        if self.api_key is not None:
            return self.api_key
        if self.api_key_env is not None:
            try:
                return os.environ[self.api_key_env]
            except KeyError:
                raise ValueError(
                    f"environment variable {self.api_key_env!r} is not set"
                ) from None
        return "no-key"


class Config(RootModel[dict[str, ModelConfig]]):
    @model_validator(mode="before")
    @classmethod
    def _apply_wildcard(cls, data: object) -> object:
        """Merge the optional ``[*]`` table of defaults into every model.

        Per-model values override the wildcard defaults; the ``*`` table itself
        is removed and never treated as a model.
        """
        if not isinstance(data, dict) or "*" not in data:
            return data
        defaults = data["*"] or {}
        if not isinstance(defaults, dict):
            raise TypeError('the "*" config table must be a table of defaults')
        return {
            tag: {**defaults, **(table or {})}
            for tag, table in data.items()
            if tag != "*"
        }


# --------------------------------------------------------------------------- #
# Book discovery and answer parsing
# --------------------------------------------------------------------------- #
class Book:
    def __init__(self, label: str, size: int, text_path: Path, answers_path: Path):
        self.label = label
        self.size = size
        self.text_path = text_path
        self.answers_path = answers_path

    def text(self) -> str:
        return self.text_path.read_text(encoding="utf-8")

    def answers(self) -> dict[int, str]:
        return parse_answers(self.answers_path.read_text(encoding="utf-8"))


def discover_books(directory: Path) -> list[Book]:
    """Find ``<size>.txt`` books (with a matching ``.answers.txt``)."""
    books: list[Book] = []
    for txt in sorted(directory.glob("*.txt")):
        if txt.name.endswith(".answers.txt"):
            continue
        label = txt.stem
        if not re.fullmatch(r"\d+[kKmM]?", label):
            continue
        ans = directory / f"{label}.answers.txt"
        if not ans.exists():
            continue
        books.append(Book(label, parse_size(label), txt, ans))
    books.sort(key=lambda b: b.size)
    return books


# "A1. some answer text [12-34]"  ->  {1: "some answer text"}
_ANSWER_LINE = re.compile(r"^\s*A\s*(\d{1,2})\s*[.):\-]\s*(.*?)\s*$")
_TRAILING_REF = re.compile(r"\s*\[[^\]]*\]\s*$")


def parse_answers(text: str) -> dict[int, str]:
    """Parse a reference answer file into ``{question_number: answer}``."""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _ANSWER_LINE.match(line)
        if not m:
            continue
        q = int(m.group(1))
        if not 1 <= q <= N_QUESTIONS or q in out:
            continue
        out[q] = _TRAILING_REF.sub("", m.group(2)).strip()
    return out


# Model output: tolerate "A1.", "A1)", "**A1.**", bullets, etc.
_MODEL_LINE = re.compile(r"^\s*[*\-•]*\s*\**\s*A(\d{1,2})\b\**\s*[.):\-]?\s*(.*?)\s*$")


def parse_model_answers(text: str) -> dict[int, str]:
    """Extract ``{question_number: answer}`` from a model's raw reply."""
    out: dict[int, str] = {}
    for line in text.splitlines():
        m = _MODEL_LINE.match(line)
        if not m:
            continue
        q = int(m.group(1))
        if not 1 <= q <= N_QUESTIONS or q in out:
            continue
        ans = m.group(2).strip().strip("*").strip().strip('"').strip()
        out[q] = ans
    return out


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #
_ONES = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALES = {
    "hundred": 100,
    "thousand": 1000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}
_NUMWORDS = set(_ONES) | set(_TENS) | set(_SCALES)


def _run_to_int(tokens: list[str]) -> int:
    """Convert a run of number-words (incl. archaic "eight-and-twenty") to int."""
    total = current = 0
    for t in tokens:
        if t == "and":
            continue
        if t in _ONES:
            current += _ONES[t]
        elif t in _TENS:
            current += _TENS[t]
        elif t == "hundred":
            current = (current or 1) * 100
        else:  # thousand / million / billion
            current = (current or 1) * _SCALES[t]
            total += current
            current = 0
    return total + current


def _words_to_digits(text: str) -> str:
    """Replace English number-words with digits, token by token."""
    tokens = [t for t in re.split(r"[\s\-]+", text) if t]
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] in _NUMWORDS:
            run: list[str] = []
            j = i
            while j < len(tokens):
                if tokens[j] in _NUMWORDS:
                    run.append(tokens[j])
                    j += 1
                elif (
                    tokens[j] == "and"
                    and j + 1 < len(tokens)
                    and tokens[j + 1] in _NUMWORDS
                ):
                    run.append("and")
                    j += 1
                else:
                    break
            out.append(str(_run_to_int(run)))
            i = j
        else:
            out.append(tokens[i])
            i += 1
    return " ".join(out)


def normalize(s: str) -> str:
    """Normalize an answer for comparison.

    Case-insensitive; strips a leading article; maps number-words to digits
    ("nine" -> "9", "eight-and-twenty" -> "28"); drops currency symbols,
    thousands separators, spacing and punctuation (keeping decimal points).
    """
    # Replace vulgar fractions before NFKC (which would split ½ into "1⁄2").
    s = s.replace("½", ".5").replace("¼", ".25").replace("¾", ".75")
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r"^(the|a|an)\s+", "", s)
    s = _words_to_digits(s)
    s = re.sub(r"[^a-z0-9.]", "", s)  # drop spaces, £, commas, etc.
    s = re.sub(r"\.(?!\d)", "", s)  # drop dots not part of a decimal
    s = re.sub(r"(?<!\d)\.", "", s)
    return s


def grade(model_answers: dict[int, str], reference: dict[int, str]) -> list[str]:
    """Return a 20-element list of "PASS" / "NO ANSWER" / "WRONG"."""
    outcomes: list[str] = []
    for q in range(1, N_QUESTIONS + 1):
        got = model_answers.get(q, "")
        expected = reference.get(q, "")
        if not got.strip():
            outcomes.append("NO ANSWER")
        elif normalize(got) == normalize(expected):
            outcomes.append("PASS")
        else:
            outcomes.append("WRONG")
    return outcomes


def score(outcomes: list[str]) -> float:
    points = {"PASS": 1, "NO ANSWER": 0, "WRONG": -1}
    return round(sum(points[o] for o in outcomes) / N_QUESTIONS, 6)


# --------------------------------------------------------------------------- #
# Inference
# --------------------------------------------------------------------------- #
class _ThinkSplitter:
    """Split a streamed ``content`` field into ``(text, is_thought)`` pieces.

    Treats ``<think>...</think>`` spans as train of thought, correctly handling
    a tag that is split across streaming chunks.
    """

    def __init__(self) -> None:
        self.in_think = False
        self._buf = ""

    @staticmethod
    def _partial_suffix(buf: str, tag: str) -> int:
        # longest suffix of buf that is a prefix of tag (a possibly-split tag)
        for k in range(min(len(tag) - 1, len(buf)), 0, -1):
            if buf[-k:] == tag[:k]:
                return k
        return 0

    def feed(self, text: str) -> list[tuple[str, bool]]:
        self._buf += text
        pieces: list[tuple[str, bool]] = []
        while True:
            tag = "</think>" if self.in_think else "<think>"
            idx = self._buf.find(tag)
            if idx != -1:
                if idx:
                    pieces.append((self._buf[:idx], self.in_think))
                self._buf = self._buf[idx + len(tag) :]
                self.in_think = not self.in_think
                continue
            keep = self._partial_suffix(self._buf, tag)
            emit = self._buf[: len(self._buf) - keep]
            if emit:
                pieces.append((emit, self.in_think))
            self._buf = self._buf[len(self._buf) - keep :]
            return pieces

    def flush(self) -> list[tuple[str, bool]]:
        if not self._buf:
            return []
        pieces = [(self._buf, self.in_think)]
        self._buf = ""
        return pieces


def _reasoning_delta(delta: object) -> str | None:
    """Extract a separate ``reasoning_content`` field from a streaming delta."""
    rc = getattr(delta, "reasoning_content", None)
    if rc is None:
        extra = getattr(delta, "model_extra", None)
        if extra:
            rc = extra.get("reasoning_content")
    return rc


def query_model(
    cfg: ModelConfig, system_prompt: str, book_text: str, header: str
) -> tuple[str, str, bool]:
    """Stream the reply, echoing it to stdout (thoughts in grey).

    Returns ``(answers, thoughts, interrupted)``: the answer text (content
    outside any ``<think>`` span), the raw train of thought (``reasoning_content``
    plus any inline ``<think>`` spans), and whether streaming was cut short by a
    Ctrl+C (in which case the returned text is whatever streamed before it).
    """
    client = OpenAI(
        base_url=cfg.url, api_key=cfg.resolve_api_key(), timeout=cfg.timeout
    )
    kwargs = {}
    if cfg.temperature is not None:
        kwargs["temperature"] = cfg.temperature
    if cfg.max_tokens is not None:
        kwargs["max_tokens"] = cfg.max_tokens
    if cfg.seed is not None:
        kwargs["seed"] = cfg.seed

    start = time.monotonic()
    stream = client.chat.completions.create(
        model=cfg.model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": book_text},
        ],
        stream=True,
        stream_options={"include_usage": True},
        **kwargs,
    )

    use_color = sys.stdout.isatty()
    answers: list[str] = []
    thoughts: list[str] = []
    cur_thought: bool | None = None

    def emit(text: str, is_thought: bool) -> None:
        nonlocal cur_thought
        if not text:
            return
        (thoughts if is_thought else answers).append(text)
        if use_color and is_thought != cur_thought:
            sys.stdout.write(GREY if is_thought else RESET)
            cur_thought = is_thought
        sys.stdout.write(text)
        sys.stdout.flush()

    sys.stdout.write(header)
    sys.stdout.flush()
    splitter = _ThinkSplitter()
    interrupted = False
    usage = None
    try:
        for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            rc = _reasoning_delta(delta)
            if rc:
                emit(rc, True)
            content = getattr(delta, "content", None)
            if content:
                for piece, is_thought in splitter.feed(content):
                    emit(piece, is_thought)
        for piece, is_thought in splitter.flush():
            emit(piece, is_thought)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if use_color and cur_thought:
            sys.stdout.write(RESET)
        sys.stdout.write("\n")
        sys.stdout.flush()

    wall = time.monotonic() - start
    completion_tokens = getattr(usage, "completion_tokens", None)
    tps = completion_tokens / wall if completion_tokens and wall > 0 else None

    metrics: dict[str, float | int] = {"wall_time_s": round(wall, 3)}
    if completion_tokens is not None:
        metrics["completion_tokens"] = int(completion_tokens)
    if tps is not None:
        metrics["tokens_per_s"] = round(tps, 2)

    footer = f"⏱  {wall:.1f}s"
    if completion_tokens is not None:
        footer += f"  ·  {int(completion_tokens)} tok"
    if tps is not None:
        footer += f"  ·  {tps:.1f} tok/s"
    sys.stdout.write(f"{GREY}{footer}{RESET}\n" if use_color else f"{footer}\n")
    sys.stdout.flush()

    return "".join(answers), "".join(thoughts), interrupted, metrics


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def run(config_path: Path, output_path: Path) -> None:
    raw_cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config = Config.model_validate(raw_cfg)

    system_prompt = (HERE / "AGENTS.md").read_text(encoding="utf-8")
    books = discover_books(HERE)
    if not books:
        raise SystemExit(f"no benchmark books found in {HERE}")
    by_size = {b.size: b for b in books}

    results: dict[str, dict[str, dict[str, object]]] = {}

    try:
        for tag, cfg in config.root.items():
            cfg.model_name = cfg.model_name or tag
            eligible: list[Book] = []
            seen: set[int] = set()
            for size in cfg.ctx_size:
                if size in seen:
                    continue
                book = by_size.get(size)
                if book is None:
                    avail = ", ".join(b.label for b in books)
                    print(
                        f"[{tag}] no book of context size {size}; "
                        f"available: {avail}. Skipping that size.",
                        file=sys.stderr,
                    )
                    continue
                seen.add(size)
                eligible.append(book)
            if not eligible:
                print(
                    f"[{tag}] no valid ctx-size entries; skipping model.",
                    file=sys.stderr,
                )
                continue

            results[tag] = {}
            for book in eligible:
                print(
                    f"[{tag}] {book.label}: querying {cfg.model_name} ...",
                    file=sys.stderr,
                )
                header = f"\n===== [{tag}] {book.label} =====\n"
                thoughts = ""
                interrupted = False
                metrics: dict[str, float | int] = {}
                try:
                    raw, thoughts, interrupted, metrics = query_model(
                        cfg, system_prompt, book.text(), header
                    )
                    if raw.strip() == "":
                        print(f"[{tag}] {book.label}: EMPTY RESPONSE", file=sys.stderr)
                        outcomes = []
                    else:
                        outcomes = grade(parse_model_answers(raw), book.answers())
                except (
                    OpenAIError,
                    OSError,
                ) as exc:  # network / API errors: record and continue
                    print(f"[{tag}] {book.label}: ERROR {exc}", file=sys.stderr)
                    raw = f"<error: {exc}>"
                    outcomes = []

                g = score(outcomes)
                n_pass = outcomes.count("PASS")
                n_no_answer = outcomes.count("NO ANSWER")
                n_wrong = outcomes.count("WRONG")
                perf = ""
                if "wall_time_s" in metrics:
                    perf += f", {metrics['wall_time_s']}s"
                if "completion_tokens" in metrics:
                    perf += f", {metrics['completion_tokens']} tok"
                if "tokens_per_s" in metrics:
                    perf += f", {metrics['tokens_per_s']} tok/s"
                print(
                    f"[{tag}] {book.label}: "
                    f"{n_pass} PASS, {n_no_answer} NO ANSWER, {n_wrong} WRONG, grade={g}"
                    f"{perf}",
                    file=sys.stderr,
                )
                results[tag][book.label] = {
                    "raw_answers": raw,
                    "thoughts": thoughts,
                    "outcomes": outcomes,
                    "grade": g,
                    **metrics,
                }

                # Ctrl+C during streaming: the partial reply above is recorded;
                # now stop and write what we have.
                if interrupted:
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("\nInterrupted — writing partial results so far.", file=sys.stderr)
    finally:
        with output_path.open("wb") as f:
            tomli_w.dump(results, f, multiline_strings=True)
        print(f"Wrote {output_path}", file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=HERE / "config.toml",
        help="path to the TOML config file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=HERE / "context-bench-results.toml",
        help="output TOML path (default: context-bench-results.toml)",
    )
    args = parser.parse_args(argv)

    run(args.config, args.output)


if __name__ == "__main__":
    main()
