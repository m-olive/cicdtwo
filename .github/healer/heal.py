import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import classify
import repair

MAX_ATTEMPTS = 3
TEMPERATURES = (0.2, 0.6, 0.9)
RESAMPLES = 2
ALLOWED_PREFIXES = ("src/main/java/",)
PROTECTED_PREFIXES = ("src/test/", ".github/", "pom.xml", ".mvn/", "mvnw")
SKIP_DIRS = {"target", ".git"}


def snapshot(root):
    files = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if p.is_file() and not (set(rel.parts) & SKIP_DIRS):
            files[str(rel)] = p.read_bytes()
    return files


def restore(root, baseline):
    current = snapshot(root)
    for rel in current:
        if rel not in baseline:
            (root / rel).unlink()
    for rel, data in baseline.items():
        if current.get(rel) != data:
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)


def allowed(rel):
    return rel.startswith(ALLOWED_PREFIXES) and rel.endswith(".java") and not rel.startswith(PROTECTED_PREFIXES)


def locate(text, find):
    if not find:
        return None, 0 if text else 1
    count = text.count(find)
    if count:
        return (find, count)
    chars = [c for c in find if not c.isspace()]
    if not chars:
        return None, 0
    pattern = re.compile(r"\s*".join(re.escape(c) for c in chars))
    matches = list(pattern.finditer(text))
    return (matches[0].group(0) if len(matches) == 1 else None), len(matches)


def apply(root, proposal):
    escapes, misses = [], []
    for edit in proposal.get("edits", []):
        rel = edit.get("file", "").removeprefix("./")
        target = (root / rel).resolve()
        if not target.is_relative_to(root.resolve()) or target == root.resolve():
            escapes.append(rel)
            continue
        find, replace = edit.get("find", ""), edit.get("replace", "")
        text = target.read_text() if target.exists() else ""
        anchor, count = locate(text, find)
        if count != 1:
            misses.append(f"{rel}: find text matched {count} times: {find[:120]!r}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text.replace(anchor, replace, 1) if anchor else replace)
    return escapes, misses


def changed_files(root, baseline):
    after = snapshot(root)
    return sorted(rel for rel in set(baseline) | set(after) if baseline.get(rel) != after.get(rel))


def unified_diff(root, baseline, files):
    out = []
    for rel in files:
        before = baseline.get(rel, b"").decode(errors="replace").splitlines(keepends=True)
        path = root / rel
        after = path.read_text(errors="replace").splitlines(keepends=True) if path.exists() else []
        out.extend(difflib.unified_diff(before, after, f"a/{rel}", f"b/{rel}"))
    return "".join(out)


def run_build(root, log_path):
    shutil.rmtree(root / "target" / "surefire-reports", ignore_errors=True)
    with open(log_path, "w") as log:
        subprocess.run(["mvn", "-B", "-ntp", "test"], cwd=root, stdout=log, stderr=subprocess.STDOUT)
    return classify.classify(log_path, root / "target" / "surefire-reports", root)


def outcome_summary(result):
    if result["class"] == "none":
        return "build green"
    lines = [f"{result['class']} in {result['phase']}"]
    for e in result["compile_errors"][:5]:
        lines.append(f"{e['file']}:{e['line']} {e['message']}")
    for f in result["failures"][:5]:
        lines.append(f"{f['test']}: {f['type']}: {f['message'][:200]}")
    return "\n".join(lines)


def signature(proposal):
    return json.dumps([[e.get("file", ""), "".join(e.get("find", "").split()), "".join(e.get("replace", "").split())]
                       for e in proposal.get("edits", [])], sort_keys=True)


def fresh_proposal(request, root, history, seen, attempt, temperatures, seed):
    temperature = temperatures[min(attempt, len(temperatures)) - 1]
    for sample in range(RESAMPLES + 1):
        proposal = repair.propose(request, root, history, temperature=min(temperature + 0.3 * sample, 1.2),
                                  seed=None if seed is None else seed + 10 * attempt + sample)
        proposal["samples"] = sample + 1
        if signature(proposal) not in seen:
            break
        proposal["duplicate"] = True
    seen.add(signature(proposal))
    return proposal


def heal(request, root, out, max_attempts=MAX_ATTEMPTS, temperatures=TEMPERATURES, seed=None):
    root = Path(root).resolve()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    baseline = snapshot(root)
    history = []
    attempts = []
    seen = set()
    for attempt in range(1, max_attempts + 1):
        restore(root, baseline)
        proposal = fresh_proposal(request, root, history, seen, attempt, temperatures, seed)
        escapes, misses = apply(root, proposal)
        files = changed_files(root, baseline)
        diff = unified_diff(root, baseline, files)
        record = {
            "attempt": attempt,
            "hypothesis": proposal.get("hypothesis"),
            "confidence": proposal.get("confidence"),
            "files": files,
            "diff": diff,
            "prompt_tokens": proposal.get("prompt_tokens"),
            "output_tokens": proposal.get("output_tokens"),
            "edit_misses": misses,
            "samples": proposal.get("samples"),
            "duplicate": proposal.get("duplicate", False),
        }
        violations = escapes + [f for f in files if not allowed(f)]
        if violations:
            record["outcome"] = "rejected by allowlist: " + ", ".join(violations)
            record["status"] = "rejected"
        elif not files:
            record["outcome"] = "no change produced" + ("; " + "; ".join(misses) if misses else "")
            record["status"] = "empty"
        else:
            result = run_build(root, out / f"attempt-{attempt}.log")
            record["outcome"] = outcome_summary(result) + ("\nedits not applied: " + "; ".join(misses) if misses else "")
            record["status"] = "green" if result["class"] == "none" else "red"
            record["result_class"] = result["class"]
        attempts.append(record)
        if record["status"] == "green":
            (out / "patch.diff").write_text(diff)
            final = {"status": "fixed", "attempts": attempt, "hypothesis": record["hypothesis"],
                     "confidence": record["confidence"], "files": files, "history": attempts}
            (out / "patch-result.json").write_text(json.dumps(final, indent=2))
            return final
        history.append(record)
    restore(root, baseline)
    final = {"status": "exhausted", "attempts": max_attempts, "hypothesis": None,
             "confidence": None, "files": [], "history": attempts}
    (out / "patch-result.json").write_text(json.dumps(final, indent=2))
    return final


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--request", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default="heal-out")
    ap.add_argument("--attempts", type=int, default=MAX_ATTEMPTS)
    args = ap.parse_args()
    request = json.loads(Path(args.request).read_text())
    final = heal(request, args.root, args.out, max_attempts=min(args.attempts, MAX_ATTEMPTS))
    print(json.dumps({k: v for k, v in final.items() if k != "history"}, indent=2))
    for h in final["history"]:
        print(f"--- attempt {h['attempt']} [{h['status']}] {h['hypothesis']}\n{h['outcome']}\n{h['diff']}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"status={final['status']}\nattempts={final['attempts']}\n")


if __name__ == "__main__":
    main()
