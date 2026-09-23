import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GOAL_RE = re.compile(r"Failed to execute goal (?:(\S+?):(\S+?):(\S+?):(\S+) )?(?:\(\S+\) )?on project")
COMPILE_RE = re.compile(r"^\[ERROR\] (\S+\.java):\[(\d+),(\d+)\] (.+)$")
FRAME_RE = re.compile(r"^\s*at ([\w.$]+)\.[\w$<>]+\(([\w$]+\.java):(\d+)\)")

INFRA_PATTERNS = [
    ("oom", re.compile(r"java\.lang\.OutOfMemoryError|Killed\s*$|exit code 137", re.M)),
    ("registry_5xx", re.compile(r"status code: 5\d\d|\b50[234]\b.*(Gateway|Unavailable)")),
    ("network", re.compile(r"Connection (reset|refused|timed out)|UnknownHostException|Read timed out")),
    ("fork_crash", re.compile(r"The forked VM terminated without properly saying goodbye")),
]
DRIFT_PATTERNS = [
    ("artifact_missing", re.compile(r"Could not find artifact|status code: 404|was not found in")),
]

ACTIONS = {
    "none": "none",
    "infrastructure": "retry",
    "environment_drift": "escalate",
    "compile": "repair",
    "test_compile": "escalate",
    "test_failure": "repair",
    "test_error": "repair",
    "unknown": "escalate",
}


def classify(log_path, reports_dir, root):
    if not log_path.exists():
        return result("unknown", reason="build.log missing")
    log = log_path.read_text(errors="replace")

    for name, pattern in INFRA_PATTERNS:
        m = pattern.search(log)
        if m:
            return result("infrastructure", reason=name, excerpt=line_of(log, m.start()))

    goal = GOAL_RE.search(log)
    if goal is None:
        if "BUILD SUCCESS" in log:
            return result("none")
        return result("unknown", reason="no failing goal and no BUILD SUCCESS", excerpt=tail(log))

    plugin, goal_name = goal.group(2), goal.group(4)
    phase = f"{plugin}:{goal_name}" if plugin else "dependency-resolution"
    excerpt = line_of(log, goal.start())

    if plugin is None:
        for name, pattern in DRIFT_PATTERNS:
            if pattern.search(log):
                return result("environment_drift", phase=phase, reason=name, excerpt=excerpt)
        return result("unknown", phase=phase, excerpt=excerpt)

    if plugin == "maven-compiler-plugin":
        errors = compile_errors(log, root)
        cls = "test_compile" if goal_name == "testCompile" else "compile"
        suspects = sorted({e["file"] for e in errors})
        return result(cls, phase=phase, excerpt=excerpt, compile_errors=errors, suspect_files=suspects)

    if plugin == "maven-surefire-plugin":
        failures = surefire_failures(reports_dir, root)
        if not failures:
            return result("unknown", phase=phase, reason="surefire failed with no failing testcase", excerpt=excerpt)
        cls = "test_error" if any(f["kind"] == "error" for f in failures) else "test_failure"
        suspects = []
        for f in failures:
            for s in f["suspect_files"]:
                if s not in suspects:
                    suspects.append(s)
        return result(cls, phase=phase, excerpt=excerpt, failures=failures, suspect_files=suspects)

    return result("unknown", phase=phase, excerpt=excerpt)


def compile_errors(log, root):
    errors = {}
    lines = log.splitlines()
    for i, line in enumerate(lines):
        m = COMPILE_RE.match(line)
        if not m:
            continue
        detail = [m.group(4)]
        for follow in lines[i + 1:i + 4]:
            if follow.startswith("[ERROR]   "):
                detail.append(follow[len("[ERROR] "):].strip())
            else:
                break
        key = (m.group(1), m.group(2), m.group(3))
        if key in errors and len(errors[key]["message"]) >= len(" | ".join(detail)):
            continue
        errors[key] = {
            "file": relative(m.group(1), root),
            "line": int(m.group(2)),
            "column": int(m.group(3)),
            "message": " | ".join(detail),
        }
    return list(errors.values())


def surefire_failures(reports_dir, root):
    failures = []
    for xml_file in sorted(reports_dir.glob("TEST-*.xml")):
        for case in ET.parse(xml_file).getroot().iter("testcase"):
            for kind in ("failure", "error"):
                node = case.find(kind)
                if node is None:
                    continue
                classname = case.get("classname", "")
                frames = project_frames(node.text or "", classname)
                failures.append({
                    "test": f"{classname}#{case.get('name')}",
                    "kind": kind,
                    "type": node.get("type"),
                    "message": (node.get("message") or "")[:500],
                    "frames": frames[:5],
                    "suspect_files": suspect_files(frames, classname, root),
                })
    return failures


def project_frames(trace, classname):
    prefix = ".".join(classname.split(".")[:2]) + "."
    frames = []
    for line in trace.splitlines():
        m = FRAME_RE.match(line)
        if m and m.group(1).startswith(prefix):
            frames.append({"class": m.group(1), "file": m.group(2), "line": int(m.group(3))})
    return frames


def suspect_files(frames, classname, root):
    found = []
    for f in frames:
        pkg = f["class"].split("$")[0].rsplit(".", 1)[0].replace(".", "/")
        for src in ("src/main/java", "src/test/java"):
            p = Path(src) / pkg / f["file"]
            if (root / p).exists() and str(p) not in found:
                found.append(str(p))
    if classname.endswith("Test"):
        under_test = Path("src/main/java") / (classname[:-4].replace(".", "/") + ".java")
        if (root / under_test).exists() and str(under_test) not in found:
            found.append(str(under_test))
    return [p for p in found if p.startswith("src/main/")] + [p for p in found if not p.startswith("src/main/")]


def relative(path, root):
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
    except ValueError:
        m = re.search(r"(src/(?:main|test)/java/.+)$", path)
        return m.group(1) if m else path


def line_of(text, pos):
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return text[start:end if end != -1 else None][:1000]


def tail(text, n=20):
    return "\n".join(text.splitlines()[-n:])


def result(cls, phase=None, reason=None, excerpt=None, compile_errors=None, failures=None, suspect_files=None):
    return {
        "class": cls,
        "action": ACTIONS[cls],
        "phase": phase,
        "reason": reason,
        "excerpt": excerpt,
        "compile_errors": compile_errors or [],
        "failures": failures or [],
        "suspect_files": suspect_files or [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="build.log")
    ap.add_argument("--reports", default="target/surefire-reports")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    out = classify(Path(args.log), Path(args.reports), Path(args.root))
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
