#!/usr/bin/env python3
"""Fail the commit when *newly added* Python lines break a hard house rule.

Only added lines in the staged diff are checked, so existing/legacy code is grandfathered
(matching the "legacy gets a TODO, new code is banned" policy). Escape a genuinely-needed
case with a trailing `# house: allow` comment.

Run manually:  python scripts/check_house_rules.py --staged
"""

import re
import subprocess
import sys

# Built from escaped parts so this file never matches its own rules.
RULES = [
	(re.compile(r"\.db\.sql\s*\("), "raw SQL — use frappe.qb / frappe.get_all instead (.db.sql is banned)"),
	(
		re.compile(r"\bjson\." + r"loads\s*\("),
		"json." + "loads — use frappe.parse_json (handles dict/list/str safely)",
	),
	(re.compile(r"^\s*except\s*:\s*$"), "bare except — catch a specific exception (e.g. except Exception)"),
]

# Don't lint this checker or its tests against itself.
SKIP_PATHS = ("scripts/check_house_rules.py",)


def get_added_lines():
	"""Yield (path, new_line_no, text) for every added line in the staged diff."""
	diff = subprocess.run(
		["git", "diff", "--cached", "--unified=0", "--", "*.py"],
		capture_output=True,
		text=True,
		check=True,
	).stdout

	current_path = None
	new_line_no = 0
	for line in diff.splitlines():
		if line.startswith("+++ b/"):
			current_path = line[6:]
			continue
		if line.startswith("@@"):
			# @@ -old,+new @@  -> grab the new-file start line
			match = re.search(r"\+(\d+)", line)
			new_line_no = int(match.group(1)) if match else 0
			continue
		if line.startswith("+") and not line.startswith("+++"):
			yield current_path, new_line_no, line[1:]
			new_line_no += 1


def main():
	violations = []
	for path, line_no, text in get_added_lines():
		if not path or any(skip in path for skip in SKIP_PATHS):
			continue
		if "house: allow" in text:
			continue
		for pattern, message in RULES:
			if pattern.search(text):
				violations.append(f"{path}:{line_no}: {message}")

	if violations:
		sys.stderr.write("\nHouse-rule violations in newly added code:\n")
		for violation in violations:
			sys.stderr.write(f"  {violation}\n")
		sys.stderr.write("\nFix them, or add `# house: allow` on the line if it's a justified exception.\n")
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
