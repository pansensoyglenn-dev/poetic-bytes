#!/usr/bin/env python3
"""
seo_tools.py - helps Google (and Bing) find and index a static site.

  python seo_tools.py check    [--root .] [--site https://poetic-bytes.vercel.app]
  python seo_tools.py sitemap  [--root .] [--site https://poetic-bytes.vercel.app]
  python seo_tools.py indexnow [--root .] [--site https://poetic-bytes.vercel.app] [--dry-run]
  python seo_tools.py all      (check + sitemap + robots.txt + indexnow)

What it does NOT do: nothing can force Google to index a page. Google decides. This script removes the
common blockers and tells Google and Bing about every page. Then, in Search Console, submit sitemap.xml
and use URL Inspection > "Request indexing" on your most important pages.

Only the standard library is needed (indexnow uses urllib).
"""
import argparse
import datetime as dt
import hashlib
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SKIP_NAMES = {"404.html", "google0cf0da0571a5948b.html"}          # pages that should not be in a sitemap
SKIP_DIRS = {".git", ".github", "node_modules", "scripts", "tests", "api"}


def pages(root: Path):
    out = []
    for p in sorted(root.rglob("*.html")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_NAMES or p.name.startswith("google"):
            continue
        out.append(p)
    return out


def url_for(site: str, root: Path, p: Path) -> str:
    rel = p.relative_to(root).as_posix()
    if rel == "index.html":
        return site.rstrip("/") + "/"
    return site.rstrip("/") + "/" + rel


def check(root: Path, site: str) -> int:
    """Find the usual reasons Google skips a page: noindex, wrong or duplicate canonical, nested pages."""
    problems = 0
    for p in pages(root):
        txt = p.read_text(encoding="utf-8", errors="ignore")
        me = url_for(site, root, p)
        issues = []
        if re.search(r'<meta[^>]+name=["\']robots["\'][^>]+noindex', txt, re.I):
            issues.append("has a noindex tag")
        canon = re.findall(r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']*)["\']', txt, re.I)
        if not canon:
            issues.append("no canonical link")
        elif len(set(canon)) > 1:
            issues.append("conflicting canonicals: " + " | ".join(sorted(set(canon))))
        elif canon[0].rstrip("/") != me.rstrip("/"):
            issues.append(f"canonical points elsewhere: {canon[0]}")
        if len(re.findall(r"<html[\s>]", txt, re.I)) > 1:
            issues.append("a whole web page is nested inside the page (more than one <html>)")
        if len(re.findall(r"<title[\s>]", txt, re.I)) > 1:
            issues.append("more than one <title>")
        if len(re.sub(r"<[^>]+>", " ", txt).split()) < 150:
            issues.append("very little text (under 150 words): Google may call it low quality")
        if issues:
            problems += 1
            print(f"- {p.relative_to(root).as_posix()}")
            for i in issues:
                print(f"    * {i}")
    total = len(pages(root))
    print(f"\nChecked {total} page(s): {problems} with problems, {total - problems} fine.")
    return problems


def build_sitemap(root: Path, site: str) -> str:
    today = dt.date.today().isoformat()
    rows = []
    for p in pages(root):
        if re.search(r'<meta[^>]+name=["\']robots["\'][^>]+noindex', p.read_text(encoding="utf-8", errors="ignore"), re.I):
            continue   # a page that says noindex must not be in the sitemap
        loc = url_for(site, root, p)
        canon = re.findall(r'<link[^>]+rel=["\']canonical["\'][^>]*href=["\']([^"\']*)["\']',
                           p.read_text(encoding="utf-8", errors="ignore"), re.I)
        if canon and canon[0].rstrip("/") != loc.rstrip("/"):
            print(f"left out of the sitemap (its canonical is another address): {p.relative_to(root).as_posix()}")
            continue
        mtime = dt.date.fromtimestamp(p.stat().st_mtime).isoformat()
        prio = "1.0" if loc.rstrip("/") == site.rstrip("/") else "0.7"
        rows.append(f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{mtime or today}</lastmod>\n    <priority>{prio}</priority>\n  </url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "\n".join(rows) + "\n</urlset>\n")


def write_sitemap(root: Path, site: str):
    (root / "sitemap.xml").write_text(build_sitemap(root, site), encoding="utf-8")
    print(f"wrote sitemap.xml with {len(pages(root))} URL(s)")
    robots = root / "robots.txt"
    line = f"Sitemap: {site.rstrip('/')}/sitemap.xml"
    if robots.exists():
        t = robots.read_text(encoding="utf-8")
        if "sitemap:" not in t.lower():
            robots.write_text(t.rstrip("\n") + "\n\n" + line + "\n", encoding="utf-8")
            print("added the Sitemap line to robots.txt")
    else:
        robots.write_text("User-agent: *\nAllow: /\n\n" + line + "\n", encoding="utf-8")
        print("created robots.txt")


def indexnow(root: Path, site: str, dry: bool):
    """Tell Bing/Yandex (IndexNow) about every URL. Google does not use IndexNow."""
    host = urllib.parse.urlparse(site).netloc
    key = hashlib.sha1(host.encode()).hexdigest()[:32]
    keyfile = root / f"{key}.txt"
    if not keyfile.exists():
        keyfile.write_text(key, encoding="utf-8")
        print(f"created {keyfile.name} (upload it to the site root)")
    urls = [url_for(site, root, p) for p in pages(root)]
    body = ('{"host":"%s","key":"%s","keyLocation":"%s/%s.txt","urlList":[%s]}'
            % (host, key, site.rstrip("/"), key, ",".join('"%s"' % u for u in urls[:10000])))
    if dry:
        print(f"[dry run] would ping IndexNow with {len(urls)} URL(s)")
        return
    req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body.encode(),
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"IndexNow answered HTTP {r.status} for {len(urls)} URL(s)")
    except Exception as e:
        print(f"IndexNow failed: {e}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["check", "sitemap", "indexnow", "all"])
    ap.add_argument("--root", default=".")
    ap.add_argument("--site", default="https://poetic-bytes.vercel.app")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    if a.cmd in ("check", "all"):
        check(root, a.site)
    if a.cmd in ("sitemap", "all"):
        write_sitemap(root, a.site)
    if a.cmd in ("indexnow", "all"):
        indexnow(root, a.site, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
