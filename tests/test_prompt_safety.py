#!/usr/bin/env python3
"""
Guards for the prompt files themselves (nudges, skill, handlers).

Prompt text steers Claude on every turn, so it is reviewed like code: these
tests fail a pull request that hides a nudge where reviewers would not look,
smuggles invisible characters into prompt text, or adds a keyword regex that
slows down on long pasted input.
"""
import json
import os
import subprocess
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


def test_hook_commands_ignore_python_environment():
    """Every hook command runs Python with -E -s on both sides of the fallback"""
    config = json.loads((PROJECT_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for event, groups in config["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                for half in hook["command"].split("||"):
                    words = half.split()
                    assert words[:3] in (["python3", "-E", "-s"], ["python", "-E", "-s"]), \
                        f"hooks.{event}: {half.strip()!r} does not start with python3/python -E -s"
                    # -I also drops the script folder from sys.path, so the engine's imports fail.
                    assert "-I" not in words, f"hooks.{event}: -I breaks the engine's sibling imports"


def test_engine_with_hook_flags_ignores_pythonpath(tmp_path):
    """With -E -s a module planted on PYTHONPATH cannot replace the standard library"""
    (tmp_path / "json.py").write_text("raise SystemExit('planted json module was imported')\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    engine = str(PROJECT_ROOT / "scripts" / "engine.py")
    payload = '{"prompt": "fix typo in README"}'

    hardened = subprocess.run([sys.executable, "-E", "-s", engine, "UserPromptSubmit"],
                              input=payload, capture_output=True, text=True, env=env)
    assert hardened.returncode == 0, hardened.stderr
    assert "PROMPT EVALUATION" in hardened.stdout

    # Control: without the flags the planted module wins, so the test proves something.
    plain = subprocess.run([sys.executable, engine, "UserPromptSubmit"],
                           input=payload, capture_output=True, text=True, env=env)
    assert "PROMPT EVALUATION" not in plain.stdout
