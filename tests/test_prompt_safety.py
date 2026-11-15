#!/usr/bin/env python3
"""
Guards for the prompt files themselves (nudges, skill, handlers).

Prompt text steers Claude on every turn, so it is reviewed like code: these
tests fail a pull request that hides a nudge where reviewers would not look,
smuggles invisible characters into prompt text, or adds a keyword regex that
slows down on long pasted input.
"""
import sys
import time
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
NUDGES_DIR = PROJECT_ROOT / "nudges"
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import rules  # noqa: E402

PROMPT_FILE_GLOBS = (
    "nudges/**/*.json",
    "skills/**/*.md",
    "scripts/*.py",
    "hooks/*.json",
    ".claude-plugin/*.json",
)


def test_nudge_tree_layout():
    """Every nudge is a visible nudges/<EventName>/<name>.json regular file"""
    problems = []
    for path in NUDGES_DIR.rglob("*"):
        rel = path.relative_to(NUDGES_DIR)
        if any(part.startswith(".") for part in rel.parts):
            problems.append(f"{rel}: hidden file or folder")
        elif path.is_symlink():
            problems.append(f"{rel}: symlink")
        elif path.is_dir():
            if len(rel.parts) != 1 or rel.name not in rules.CAPABILITY_MATRIX:
                problems.append(f"{rel}: folder is not a known event name")
        elif len(rel.parts) != 2 or path.suffix != ".json":
            problems.append(f"{rel}: not nudges/<EventName>/<name>.json")
    assert not problems, "Unexpected entries under nudges/:\n" + "\n".join(problems)


def _is_invisible(ch):
    cp = ord(ch)
    return (
        unicodedata.category(ch) == "Cf"  # zero-width, bidi controls, BOM, tag characters
        or 0xFE00 <= cp <= 0xFE0F  # variation selectors
        or 0xE0100 <= cp <= 0xE01EF  # variation selectors supplement
    )


def test_prompt_files_have_no_invisible_characters():
    """Prompt text contains no characters a reviewer cannot see"""
    problems = []
    for pattern in PROMPT_FILE_GLOBS:
        for path in PROJECT_ROOT.glob(pattern):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                for ch in line:
                    if _is_invisible(ch):
                        name = unicodedata.name(ch, "UNNAMED")
                        problems.append(f"{path.relative_to(PROJECT_ROOT)}:{lineno} U+{ord(ch):04X} {name}")
    assert not problems, "Invisible characters found:\n" + "\n".join(problems[:20])


def test_keyword_regexes_stay_fast_on_long_input():
    """Every match/exclude regex stays fast on a long single-line paste"""
    long_input = "add " * 30000  # ~120 KB, one line
    for event in rules.CAPABILITY_MATRIX:
        for rule in rules.rules_for(event):
            criteria = rule.get("criteria") or {}
            for pattern in criteria.get("match", []) + criteria.get("exclude", []):
                start = time.perf_counter()
                pattern.search(long_input)
                elapsed = time.perf_counter() - start
                assert elapsed < 1.0, f"{rule['id']}: {pattern.pattern!r} took {elapsed:.2f}s"
