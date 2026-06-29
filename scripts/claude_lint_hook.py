#!/usr/bin/env python3
"""Claude Code PostToolUse hook: lint a just-edited Python file with ruff.

Reads the tool-call payload on stdin, runs `ruff check` on the edited *.py file, and on any
finding exits 2 with the report on stderr so Claude sees it and self-corrects in-session.
Stays silent (exit 0) for non-Python files, missing files, or when ruff isn't installed —
the hook must never break a session.
"""

import json
import shutil
import subprocess
import sys


def main():
	try:
		payload = json.load(sys.stdin)
	except (json.JSONDecodeError, ValueError):
		return 0

	file_path = (payload.get("tool_input") or {}).get("file_path") or ""
	if not file_path.endswith(".py"):
		return 0

	if shutil.which("ruff") is None:
		return 0

	result = subprocess.run(
		["ruff", "check", file_path],
		capture_output=True,
		text=True,
	)
	if result.returncode != 0:
		sys.stderr.write("ruff found issues in the file you just edited:\n")
		sys.stderr.write(result.stdout or result.stderr)
		return 2
	return 0


if __name__ == "__main__":
	sys.exit(main())
