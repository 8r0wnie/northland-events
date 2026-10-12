"""PreToolUse hook: before any `git commit` / `git push`, remind Claude to run the
CLAUDE.md doc-sync checklist, and flag when code changed without any doc change.

Reads the hook payload on stdin. Silent (exit 0, no output) for every other
command. Never blocks: it only injects context.
"""
import json
import re
import subprocess
import sys

DOC_PREFIXES = ("CLAUDE.md", "README.md", "docs/", "moderation/README.md")
# Files the daily CI data commit touches; changes here never need docs.
DATA_PREFIXES = ("site/data/", "scraper/output/", "moderation/decisions.csv")


def changed_files(cmd: str) -> list[str]:
    def run(*args):
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
            return [l for l in out.stdout.splitlines() if l.strip()]
        except Exception:
            return []

    if "git push" in cmd:
        return run("diff", "--name-only", "@{u}..HEAD") or run("diff", "--name-only", "origin/main..HEAD")
    staged = run("diff", "--cached", "--name-only")
    if re.search(r"git commit\b.*\s-[a-zA-Z]*a", cmd):  # commit -a / -am also takes tracked edits
        staged += run("diff", "--name-only")
    return staged


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    cmd = (payload.get("tool_input") or {}).get("command", "")
    if not re.search(r"\bgit\s+(commit|push)\b", cmd):
        return

    files = changed_files(cmd)
    code = [f for f in files if not f.startswith(DOC_PREFIXES + DATA_PREFIXES)]
    docs = [f for f in files if f.startswith(DOC_PREFIXES)]

    msg = ("Doc-sync gate (project rule): before this commit/push, walk the "
           "'Before you finish any change' checklist in CLAUDE.md and update "
           "CLAUDE.md / docs/*.md / README.md in the SAME commit.")
    if code and not docs:
        msg += (" WARNING: this change touches " + ", ".join(code[:8])
                + (" ..." if len(code) > 8 else "")
                + " but no documentation file. Update the relevant docs first, "
                "or state explicitly to the user why no doc change is needed.")

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": msg,
    }}))


if __name__ == "__main__":
    main()
