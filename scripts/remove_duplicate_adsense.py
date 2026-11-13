#!/usr/bin/env python3
"""Remove DUPLICATE AdSense code from the .html files of a site (default: preview only).

What counts as a duplicate, per page:
  1. more than one adsbygoogle.js loader  -> the first is kept
  2. more than one google-adsense-account meta tag -> the first is kept
  3. the same ad unit (same data-ad-slot) placed more than once -> the first is kept,
     together with the small "(adsbygoogle = ...).push({})" script that follows each repeat

Different ad slots on one page are NOT touched. Nothing is written unless you pass --apply.

  python scripts/remove_duplicate_adsense.py --root .            # preview
  python scripts/remove_duplicate_adsense.py --root . --apply    # write the changes
"""
import argparse, re, sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "deleted-essays", "video-jobs", ".github"}

LOADER = re.compile(r'<script\b[^>]*adsbygoogle\.js[^>]*>\s*</script>[ \t]*\n?', re.I | re.S)
META = re.compile(r'<meta\b[^>]*name=["\']google-adsense-account["\'][^>]*>[ \t]*\n?', re.I)
INS = re.compile(r'<ins\b[^>]*class=["\'][^"\']*adsbygoogle[^"\']*["\'][^>]*>.*?</ins>', re.I | re.S)
PUSH = re.compile(r'\s*<script\b[^>]*>\s*\(?\s*adsbygoogle\s*=\s*window\.adsbygoogle\s*\|\|\s*\[\]\s*\)?\.push\(\s*\{\s*\}\s*\)\s*;?\s*</script>', re.I | re.S)
EMPTY_SLOT = re.compile(r'<div\b[^>]*class=["\'][^"\']*\bad-slot\b[^"\']*["\'][^>]*>\s*</div>[ \t]*\n?', re.I)
SLOT_ATTR = re.compile(r'data-ad-slot=["\']?([^"\'\s>]+)', re.I)


def drop_repeats(html, pattern):
    """Keep the first match of pattern, delete the others. Returns (new_html, removed_count)."""
    seen = [0]
    def sub(m):
        seen[0] += 1
        return m.group(0) if seen[0] == 1 else ""
    out = pattern.sub(sub, html)
    return out, max(0, seen[0] - 1)


def clean(html):
    notes = []
    html, n = drop_repeats(html, LOADER)
    if n: notes.append(f"{n} extra adsbygoogle.js loader(s)")
    html, n = drop_repeats(html, META)
    if n: notes.append(f"{n} extra google-adsense-account meta tag(s)")

    # repeated ad units with the same slot
    seen, removed, pieces, pos = set(), 0, [], 0
    for m in INS.finditer(html):
        slot = (SLOT_ATTR.search(m.group(0)) or [None, "?"])[1]
        if slot in seen:
            end = m.end()
            p = PUSH.match(html, end)
            if p:
                end = p.end()
            pieces.append(html[pos:m.start()])
            pos = end
            removed += 1
        else:
            seen.add(slot)
    pieces.append(html[pos:])
    html = "".join(pieces)
    if removed:
        notes.append(f"{removed} repeated ad unit(s) with the same slot")
        html, _ = EMPTY_SLOT.subn("", html)   # wrapper left empty by the removal
    return html, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--apply", action="store_true", help="write the changes (default: preview only)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    scanned = changed = 0
    for p in sorted(root.rglob("*.html")):
        if SKIP_DIRS & set(p.relative_to(root).parts):
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  skipped {p.relative_to(root)} ({e})"); continue
        scanned += 1
        if "adsbygoogle" not in src and "google-adsense-account" not in src:
            continue
        new, notes = clean(src)
        if notes and new != src:
            changed += 1
            print(f"{'FIXED ' if a.apply else 'WOULD FIX '}{p.relative_to(root)}: " + "; ".join(notes))
            if a.apply:
                p.write_text(new, encoding="utf-8", newline="")
    print(f"\n{scanned} page(s) checked, {changed} {'fixed' if a.apply else 'would be fixed'}."
          + ("" if a.apply or not changed else "  Run again with --apply to write the changes."))


if __name__ == "__main__":
    sys.exit(main())
