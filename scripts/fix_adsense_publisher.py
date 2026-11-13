#!/usr/bin/env python3
"""Make every page use ONE AdSense publisher ID, and collapse ad areas that stay empty (preview by default).

  * every ca-pub-XXXXXXXXXXXXXXXX in an .html file that is not your ID is replaced by your ID
  * pages that style .ad-slot get a small CSS rule that hides an ad area when AdSense leaves it
    unfilled, so no blank gap is left on the page (the rule is added once)

  python scripts/fix_adsense_publisher.py --root .                 # preview
  python scripts/fix_adsense_publisher.py --root . --apply         # write the changes
"""
import argparse, re, sys
from pathlib import Path

PUB = "ca-pub-6428799122405621"
SKIP_DIRS = {".git", "node_modules", "deleted-essays", "video-jobs", ".github"}
ID = re.compile(r"ca-pub-\d{10,20}")
MARK = "/* adsense-collapse-unfilled */"
RULE = (MARK + "\n.adsbygoogle[data-ad-status=\"unfilled\"]{display:none!important}"
        "\n.ad-slot:has(> .adsbygoogle[data-ad-status=\"unfilled\"]){display:none!important}\n")


def fix(src, pub):
    notes = []
    found = {m for m in ID.findall(src) if m != pub}
    if found:
        src = ID.sub(pub, src)
        notes.append("publisher ID " + ", ".join(sorted(found)) + " -> " + pub)
    if "adsbygoogle" in src and MARK not in src and "</style>" in src:
        src = src.replace("</style>", RULE + "</style>", 1)
        notes.append("collapse-unfilled CSS added")
    return src, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--pub", default=PUB, help="the publisher ID to keep (default: yours)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    scanned = changed = 0
    for p in sorted(root.rglob("*.html")):
        if SKIP_DIRS & set(p.relative_to(root).parts):
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        scanned += 1
        new, notes = fix(src, a.pub)
        if notes and new != src:
            changed += 1
            print(("FIXED " if a.apply else "WOULD FIX ") + str(p.relative_to(root)) + ": " + "; ".join(notes))
            if a.apply:
                p.write_text(new, encoding="utf-8", newline="")
    print(f"\n{scanned} page(s) checked, {changed} {'fixed' if a.apply else 'would be fixed'}."
          + ("" if a.apply or not changed else "  Run again with --apply to write the changes."))


if __name__ == "__main__":
    sys.exit(main())
