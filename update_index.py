#!/usr/bin/env python3
"""
update_index.py - Poetic Bytes helper

Scans index.html for "Coming soon" cards written like this:

    <!--soon:python-dunder-methods.html--><div class="card is-soon">...</div><!--/soon-->

If the lesson file (python-dunder-methods.html) now exists in the same folder,
the card is converted into a normal, clickable lesson card and the
<!--soon:...--> markers are removed. Cards whose page does not exist yet are
left untouched, so the script is safe to run as often as you like.

Usage:
    python update_index.py              # update ./index.html
    python update_index.py --dry-run    # only show what would change
    python update_index.py path/to/index.html
"""

import re
import shutil
import sys
from pathlib import Path

SOON_BLOCK = re.compile(
    r"<!--soon:(?P<file>[^>\s]+?)-->(?P<body>.*?)<!--/soon-->",
    re.DOTALL,
)
CARD_OPEN = '<div class="card is-soon">'
SOON_BADGE = re.compile(r'<span class="soon-badge">.*?</span>', re.DOTALL)
ARROW = '<span class="card-arrow">Start learning \u2192 <i class="fas fa-arrow-right"></i></span>'


def make_live_card(filename: str, body: str) -> str:
    """Turn the inner HTML of a 'soon' block into a live <a class="card"> card."""
    body = body.strip()
    if not body.startswith(CARD_OPEN) or not body.endswith("</div>"):
        raise ValueError("unexpected card markup")
    inner = body[len(CARD_OPEN):-len("</div>")]
    inner = SOON_BADGE.sub(ARROW, inner)
    return f'<a class="card" href="{filename}">{inner}</a>'


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv

    index_path = Path(args[0]) if args else Path("index.html")
    if not index_path.is_file():
        print(f"ERROR: {index_path} not found. Run this from your site folder.")
        return 1

    folder = index_path.resolve().parent
    html = index_path.read_text(encoding="utf-8")

    published, waiting = [], []

    def replace(match: re.Match) -> str:
        filename = match.group("file")
        if (folder / filename).is_file():
            try:
                card = make_live_card(filename, match.group("body"))
            except ValueError as err:
                print(f"SKIPPED {filename}: {err}")
                waiting.append(filename)
                return match.group(0)
            published.append(filename)
            return card
        waiting.append(filename)
        return match.group(0)

    new_html = SOON_BLOCK.sub(replace, html)

    print(f"Published now : {len(published)}")
    for name in published:
        print(f"  + {name}")
    print(f"Still waiting : {len(waiting)}")

    if not published:
        print("Nothing to update.")
        return 0
    if dry_run:
        print("Dry run: index.html was not changed.")
        return 0

    shutil.copyfile(index_path, index_path.with_suffix(".html.bak"))
    index_path.write_text(new_html, encoding="utf-8")
    print(f"Updated {index_path} (backup saved as {index_path.name}.bak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
