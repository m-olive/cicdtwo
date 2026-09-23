import json
import os
import re
import urllib.request
from pathlib import Path

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL = os.environ.get("HEALER_MODEL", "qwen2.5-coder:7b")
TIMEOUT = int(os.environ.get("HEALER_TIMEOUT", "1200"))
NEIGHBORS = os.environ.get("HEALER_NEIGHBORS", "1") != "0"

SYSTEM = """You repair failing builds in a Java 21 Maven project.

You are given the build failure, the source files most likely involved, the failing tests, and the results of any earlier repair attempts.

Rules:
- You may change files under src/main/java only.
- Never change tests. The tests define the intended behavior and are correct.
- Never delete functionality, weaken checks, or special-case test inputs to make a test pass.
- Fix the root cause. If the failing line only uses a bad value, find where the value is produced and fix it there.
- Make the smallest change that fixes the defect.
- If an earlier attempt failed, do not repeat it.

Respond with JSON only:
{"hypothesis": "<one or two sentences: what is wrong and why>",
 "confidence": <number from 0 to 1>,
 "edits": [{"file": "<path relative to project root>",
            "find": "<exact text copied from the current file, including indentation, long enough to occur exactly once>",
            "replace": "<the text that replaces it>"}]}

Each edit replaces one exact occurrence of "find". Copy "find" character for character from the source shown, without line numbers."""

SCHEMA = {
    "type": "object",
    "properties": {
        "hypothesis": {"type": "string"},
        "confidence": {"type": "number"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "find": {"type": "string"}, "replace": {"type": "string"}},
                "required": ["file", "find", "replace"],
            },
        },
    },
    "required": ["hypothesis", "confidence", "edits"],
}


def context_files(request, root):
    main = [p for p in request["suspect_files"] if p.startswith("src/main/java/")]
    tests = [p for p in request["suspect_files"] if p.startswith("src/test/java/")]
    names = {Path(p).stem for p in main}
    for path in (list(main) + tests) if NEIGHBORS else []:
        text = (root / path).read_text()
        for candidate in sorted((root / "src/main/java").rglob("*.java")):
            rel = str(candidate.relative_to(root))
            if rel not in main and candidate.stem not in names and re.search(rf"\b{candidate.stem}\b", text):
                main.append(rel)
                names.add(candidate.stem)
    return main, tests


def numbered(text):
    return "\n".join(f"{i:4d}  {line}" for i, line in enumerate(text.splitlines(), 1))


def build_prompt(request, root, history):
    main, tests = context_files(request, root)
    parts = [f"## Failure\nclass: {request['class']}\nphase: {request['phase']}\n{request.get('excerpt') or ''}"]
    for e in request["compile_errors"]:
        parts.append(f"compile error: {e['file']}:{e['line']}:{e['column']} {e['message']}")
    for f in request["failures"]:
        frames = ", ".join(f"{fr['class']}({fr['file']}:{fr['line']})" for fr in f["frames"])
        parts.append(f"failing test: {f['test']}\n  {f['kind']} {f['type']}: {f['message']}\n  project frames: {frames or 'none'}")
    all_main = sorted(str(p.relative_to(root)) for p in (root / "src/main/java").rglob("*.java"))
    parts.append("## Project source files\n" + "\n".join(all_main))
    for path in main:
        parts.append(f"## Source (editable): {path}\n```java\n{numbered((root / path).read_text())}\n```")
    for path in tests:
        parts.append(f"## Test (read-only, do not change): {path}\n```java\n{(root / path).read_text()}\n```")
    for h in history:
        parts.append(
            f"## Attempt {h['attempt']} failed\nhypothesis: {h['hypothesis']}\n"
            f"outcome: {h['outcome']}\n```diff\n{h['diff'][:4000]}\n```"
        )
    parts.append("Line numbers are for reference only; never include them in find or replace.")
    return "\n\n".join(parts)


def propose(request, root, history, temperature=0.2, seed=None):
    prompt = build_prompt(request, root, history)
    options = {"temperature": temperature, "num_ctx": 16384}
    if seed is not None:
        options["seed"] = seed
    body = {
        "model": MODEL,
        "stream": False,
        "format": SCHEMA,
        "options": options,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        payload = json.load(resp)
    raw = payload["message"]["content"]
    try:
        proposal = json.loads(raw)
    except json.JSONDecodeError:
        proposal = {"hypothesis": "unparseable model output", "confidence": 0, "edits": [], "raw": raw[:2000]}
    proposal["prompt_tokens"] = payload.get("prompt_eval_count")
    proposal["output_tokens"] = payload.get("eval_count")
    proposal["prompt_chars"] = len(prompt)
    return proposal
