#!/usr/bin/env python3
"""Take the ad units off policy and utility pages (preview by default).

AdSense reviewers expect no ads on privacy, terms, contact and similar short pages. Only the ad UNITS
on these pages are removed; the AdSense loader in the <head> stays, so the pages still belong to the site.

  python scripts/remove_ads_from_policy_pages.py --root .                 # preview
  python scripts/remove_ads_from_policy_pages.py --root . --apply         # write the changes
  python scripts/remove_ads_from_policy_pages.py --root . --pages privacy.html,terms.html
"""
import argparse, re, sys
from pathlib import Path

DEFAULT_PAGES = ["accessibility.html", "disclaimer.html", "privacy.html", "terms.html", "cookie.html",
                 "contact.html", "sitemap.html", "404.html", "about.html"]

INS = re.compile(r'<ins\b[^>]*class=["\'][^"\']*adsbygoogle[^"\']*["\'][^>]*>.*?</ins>', re.I | re.S)
PUSH = re.compile(r'\s*<script\b[^>]*>\s*\(?\s*adsbygoogle\s*=\s*window\.adsbygoogle\s*\|\|\s*\[\]\s*\)?\.push\(\s*\{\s*\}\s*\)\s*;?\s*</script>', re.I | re.S)
EMPTY_WRAP = re.compile(r'<(div|aside|section)\b[^>]*class=["\'][^"\']*\bad[-_a-z]*\b[^"\']*["\'][^>]*>\s*</\1>[ \t]*\n?', re.I)


def strip_units(html):
    out, pos, n = [], 0, 0
    for m in INS.finditer(html):
        end = m.end()
        p = PUSH.match(html, end)
        if p:
            end = p.end()
        out.append(html[pos:m.start()])
        pos = end
        n += 1
    out.append(html[pos:])
    html = "".join(out)
    if n:
        html = EMPTY_WRAP.sub("", html)
    return html, n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--pages", default=",".join(DEFAULT_PAGES), help="comma-separated page names")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    names = [x.strip() for x in a.pages.split(",") if x.strip()]
    changed = 0
    for name in names:
        p = root / name
        if not p.is_file():
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        new, n = strip_units(src)
        if n:
            changed += 1
            print(("REMOVED " if a.apply else "WOULD REMOVE ") + f"{n} ad unit(s) from {name}")
            if a.apply:
                p.write_text(new, encoding="utf-8", newline="")
    print(f"\n{changed} page(s) {'changed' if a.apply else 'would change'}."
          + ("" if a.apply or not changed else "  Run again with --apply to write the changes."))


if __name__ == "__main__":
    sys.exit(main())
