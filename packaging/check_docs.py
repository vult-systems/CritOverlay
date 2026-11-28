"""Check relative links and #anchors in the repository's Markdown files.

    python packaging/check_docs.py

Stdlib only. External (http, mailto) links aren't fetched. Exit 1 lists every
broken link. CI runs this for every change, and it's the only check a
docs-only pull request needs.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
INLINE = re.compile(r"!?\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REFDEF = re.compile(r"^\s*\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$")
CODE_SPAN = re.compile(r"`[^`]*`")


def markdown_files():
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.splitlines()]


def prose_lines(path):
    """Yield (line number, text) outside fenced code blocks."""
    fenced = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            yield n, line


def slug(text):
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to '-'."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # link text only
    text = text.replace("`", "").strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path, cache={}):
    if path not in cache:
        counts, found = {}, set()
        for _, line in prose_lines(path):
            m = HEADING.match(line)
            if m:
                base = slug(m.group(1))
                k = counts.get(base, 0)
                counts[base] = k + 1
                found.add(base if k == 0 else f"{base}-{k}")  # repeats get -1, -2, ...
        cache[path] = found
    return cache[path]


def main():
    errors = []
    for md in markdown_files():
        for n, line in prose_lines(md):
            text = CODE_SPAN.sub("", line)
            targets = INLINE.findall(text)
            m = REFDEF.match(text)
            if m:
                targets.append(m.group(1))
            for target in targets:
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                    continue  # http:, https:, mailto:, ...
                file_part, _, anchor = target.partition("#")
                dest = (md.parent / file_part).resolve() if file_part else md
                where = f"{md.relative_to(ROOT).as_posix()}:{n}"
                if not dest.exists():
                    errors.append(f"{where}: {target}: no such file")
                elif anchor and dest.suffix.lower() == ".md" and anchor.lower() not in anchors(dest):
                    errors.append(f"{where}: {target}: no heading with that anchor")
    for e in errors:
        print(e)
    print(f"{len(errors)} broken link(s)" if errors else "All Markdown links resolve.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
