#!/usr/bin/env python3
"""
seo_automation.py - keep a static site discoverable.

What it does (standard library only):
  1. Finds every indexable .html page in the repo.
  2. Writes sitemap.xml (real lastmod dates from git history) and feed.xml (RSS 2.0).
  3. Makes sure robots.txt points at the sitemap (never overwrites your other rules).
  4. Lints pages: titles, descriptions, canonicals, og:image host, <h1>, broken internal
     links, "#" placeholder links, orphan pages, missing RSS <link>.
  5. Optionally submits changed URLs to IndexNow (Bing, Yandex, Naver, Seznam) after
     waiting until the new version is actually live on the site.

Google does not support IndexNow. For Google, submit sitemap.xml once in Search Console
and keep internal links healthy; this script's lint report helps with the second part.

Requires full git history for accurate dates (actions/checkout with fetch-depth: 0).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from fnmatch import fnmatch
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, urldefrag, urljoin, urlparse
from xml.sax.saxutils import escape

SKIP_DIRS = {"node_modules", "scripts", "api"}
SKIP_FILES = re.compile(r"^(404|google[0-9a-f]+)\.html$")
NOT_IN_FEED = re.compile(
    r"^(index|privacy|terms|cookie|disclaimer|accessibility|contact|sitemap|about)\.html$"
)
KEY_FILE = re.compile(r"^([0-9a-f]{32})\.txt$")
UA = "poetic-bytes-seo-bot/1.0"


# --------------------------------------------------------------------------- parsing
class Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self.canonical = "", {}, None
        self.hrefs: list[str] = []
        self.h1 = 0
        self.feed_link = False
        self._in_title = False
        self._title_done = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title" and not self._title_done:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key and key not in self.meta:
                self.meta[key] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel", "").lower()
            if rel == "canonical":
                self.canonical = a.get("href")
            elif "alternate" in rel and "rss" in a.get("type", "").lower():
                self.feed_link = True
        elif tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        elif tag == "h1":
            self.h1 += 1

    def handle_endtag(self, tag):
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True

    def handle_data(self, data):
        if self._in_title:
            self.title += data


class Page:
    def __init__(self, path: Path, rel: str, url: str) -> None:
        self.path, self.rel, self.url = path, rel, url
        raw = path.read_bytes()
        self.sha = hashlib.sha256(raw).hexdigest()
        head = Head()
        head.feed(raw.decode("utf-8", "replace"))
        self.title = " ".join(head.title.split())
        self.meta = head.meta
        self.description = " ".join(self.meta.get("description", "").split())
        self.canonical = head.canonical
        self.hrefs = head.hrefs
        self.h1 = head.h1
        self.feed_link = head.feed_link
        self.noindex = "noindex" in self.meta.get("robots", "").lower()
        self.lastmod = self.added = datetime.now(timezone.utc)


def url_for(base: str, rel: str) -> str:
    parts = rel.split("/")
    if parts[-1] == "index.html":
        d = "/".join(quote(x) for x in parts[:-1])
        return f"{base}/{d}/" if d else f"{base}/"
    return f"{base}/" + "/".join(quote(x) for x in parts)


def git(root: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def page_dates(root: Path, p: Page) -> tuple[datetime, datetime]:
    last = git(root, "log", "-1", "--format=%cI", "--", p.rel)
    first = git(root, "log", "--diff-filter=A", "--format=%cI", "--", p.rel).splitlines()
    fallback = datetime.fromtimestamp(p.path.stat().st_mtime, timezone.utc).isoformat()
    to_utc = lambda s: datetime.fromisoformat(s).astimezone(timezone.utc)
    lastmod = to_utc(last or fallback)
    added = to_utc(first[-1]) if first else lastmod
    return lastmod, added


def discover(root: Path, base: str, excludes: list[str]) -> tuple[list[Page], list[Page]]:
    indexable, noindexed = [], []
    for path in sorted(root.rglob("*.html")):
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if any(x.startswith(".") or x in SKIP_DIRS for x in parts[:-1]):
            continue
        if SKIP_FILES.match(parts[-1]) or any(fnmatch(rel, g) for g in excludes):
            continue
        p = Page(path, rel, url_for(base, rel))
        p.lastmod, p.added = page_dates(root, p)
        (noindexed if p.noindex else indexable).append(p)
    return indexable, noindexed


# --------------------------------------------------------------------------- generators
def build_sitemap(pages: list[Page]) -> str:
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for p in sorted(pages, key=lambda p: (p.rel != "index.html", p.url)):
        out += [
            "  <url>",
            f"    <loc>{escape(p.url)}</loc>",
            f"    <lastmod>{p.lastmod:%Y-%m-%d}</lastmod>",
            "  </url>",
        ]
    out.append("</urlset>")
    return "\n".join(out) + "\n"


def build_feed(pages: list[Page], base: str, title: str, desc: str, limit: int) -> str:
    items = sorted(
        (p for p in pages if not NOT_IN_FEED.match(p.rel.split("/")[-1])),
        key=lambda p: p.added,
        reverse=True,
    )[:limit]
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"  <title>{escape(title)}</title>",
        f"  <link>{base}/</link>",
        f"  <description>{escape(desc)}</description>",
        "  <language>en</language>",
        f'  <atom:link href="{base}/feed.xml" rel="self" type="application/rss+xml"/>',
    ]
    if items:  # deterministic: no "now", so unchanged content means no commit
        out.append(f"  <lastBuildDate>{format_datetime(max(p.lastmod for p in items))}</lastBuildDate>")
    for p in items:
        out += [
            "  <item>",
            f"    <title>{escape(p.title or p.rel)}</title>",
            f"    <link>{escape(p.url)}</link>",
            f'    <guid isPermaLink="true">{escape(p.url)}</guid>',
            f"    <pubDate>{format_datetime(p.added)}</pubDate>",
            f"    <description>{escape(p.description)}</description>",
            "  </item>",
        ]
    out += ["</channel>", "</rss>"]
    return "\n".join(out) + "\n"


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def ensure_robots(root: Path, base: str) -> bool:
    f = root / "robots.txt"
    line = f"Sitemap: {base}/sitemap.xml"
    if not f.exists():
        return write_if_changed(f, f"User-agent: *\nAllow: /\n\n{line}\n")
    text = f.read_text(encoding="utf-8")
    if re.search(r"(?im)^sitemap:", text):
        return False  # respect whatever is already there
    return write_if_changed(f, text.rstrip("\n") + f"\n\n{line}\n")


def ensure_key(root: Path, create: bool = True) -> tuple[str | None, bool]:
    for f in root.glob("*.txt"):
        m = KEY_FILE.match(f.name)
        if m and f.read_text(encoding="utf-8").strip() == m.group(1):
            return m.group(1), False
    if not create:
        return None, False
    key = secrets.token_hex(16)
    (root / f"{key}.txt").write_text(key, encoding="utf-8")
    return key, True


# --------------------------------------------------------------------------- lint
def resolve(root: Path, urlpath: str) -> str | None:
    rel = urlpath.lstrip("/")
    if rel == "":
        cands = ["index.html"]
    elif rel.endswith("/"):
        cands = [rel + "index.html"]
    else:
        cands = [rel, rel + ".html", rel + "/index.html"]
    for c in cands:
        if (root / c).is_file():
            return c
    return None


def lint(pages: list[Page], noindexed: list[Page], root: Path, base: str) -> list[str]:
    host = urlparse(base).netloc
    issues: dict[str, list[str]] = {}
    inbound = {p.rel: 0 for p in pages}

    def add(p: Page, msg: str) -> None:
        issues.setdefault(p.rel, []).append(msg)

    seen_titles: dict[str, list[str]] = {}
    seen_desc: dict[str, list[str]] = {}
    for p in pages:
        if not p.title:
            add(p, "missing <title>")
        else:
            seen_titles.setdefault(p.title, []).append(p.rel)
            if len(p.title) > 65:
                add(p, f"title is {len(p.title)} chars; results will likely truncate it (aim for under 60)")
        if not p.description:
            add(p, "missing meta description")
        else:
            seen_desc.setdefault(p.description, []).append(p.rel)
            if not 70 <= len(p.description) <= 165:
                add(p, f"meta description is {len(p.description)} chars (aim for 70-160)")
        if not p.canonical:
            add(p, "missing rel=canonical")
        elif p.canonical.rstrip("/") != p.url.rstrip("/"):
            add(p, f"canonical points to {p.canonical}, but this page is {p.url}")
        og = p.meta.get("og:image", "")
        if not og:
            add(p, "missing og:image (link previews will be blank)")
        elif urlparse(og).netloc not in ("", host):
            add(p, f"og:image is hosted on {urlparse(og).netloc}, not {host}; verify it still loads")
        if p.h1 != 1:
            add(p, f"has {p.h1} <h1> tags (expected exactly 1)")
        if p.rel == "index.html" and not p.feed_link:
            add(p, 'missing <link rel="alternate" type="application/rss+xml" href="/feed.xml">')

        placeholders, broken = 0, []
        for href in p.hrefs:
            href = href.strip()
            if href == "#":
                placeholders += 1
                continue
            if href.startswith("#") or re.match(r"(?i)^(mailto:|tel:|javascript:|data:)", href):
                continue
            full, _ = urldefrag(urljoin(p.url, href))
            u = urlparse(full)
            if u.netloc != host:
                continue
            target = resolve(root, u.path)
            if target is None:
                broken.append(href)
            elif target in inbound and target != p.rel:
                inbound[target] += 1
        if placeholders:
            add(p, f'{placeholders} links use href="#" (dead unless JavaScript rewires them)')
        for b in sorted(set(broken)):
            add(p, f"broken internal link: {b}")

    for t, rels in seen_titles.items():
        if len(rels) > 1:
            issues.setdefault(rels[0], []).append(f"duplicate title shared with {', '.join(rels[1:])}")
    for d, rels in seen_desc.items():
        if len(rels) > 1:
            issues.setdefault(rels[0], []).append(f"duplicate description shared with {', '.join(rels[1:])}")
    orphans = [r for r, n in inbound.items() if n == 0 and r != "index.html"]

    total = sum(len(v) for v in issues.values())
    lines = [
        "## SEO report",
        f"- {len(pages)} pages in sitemap, {len(noindexed)} noindexed (left out)",
        f"- {total} issues across {len(issues)} pages, {len(orphans)} orphan pages",
        "",
    ]
    if orphans:
        lines += ["### Orphan pages (no internal link points here)"] + [f"- {r}" for r in sorted(orphans)] + [""]
    if issues:
        lines.append("### Issues by page")
        for rel in sorted(issues):
            lines.append(f"**{rel}**")
            lines += [f"- {m}" for m in issues[rel]]
            lines.append("")
    return lines


# --------------------------------------------------------------------------- IndexNow
def http(url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, r.read()


def wait_live(checks: list[tuple[str, callable]], minutes: float) -> bool:
    deadline = time.time() + minutes * 60
    pending = list(checks)
    while pending:
        still = []
        for url, ok in pending:
            try:
                _, body = http(f"{url}{'&' if '?' in url else '?'}_={int(time.time())}")
                if not ok(body):
                    still.append((url, ok))
            except (urllib.error.URLError, OSError):
                still.append((url, ok))
        pending = still
        if not pending:
            return True
        if time.time() > deadline:
            print(f"Timed out waiting for {len(pending)} URL(s) to go live, e.g. {pending[0][0]}")
            return False
        print(f"Waiting for deployment... {len(pending)} URL(s) not live yet")
        time.sleep(20)
    return True


def changed_pages(root: Path, pages: list[Page], since: str) -> list[Page] | None:
    r = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AM", since, "HEAD"],
        cwd=root, capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    names = set(r.stdout.split())
    return [p for p in pages if p.rel in names]


def submit_indexnow(base: str, key: str, targets: list[Page], wait_min: float, dry: bool) -> bool:
    payload = {
        "host": urlparse(base).netloc,
        "key": key,
        "keyLocation": f"{base}/{key}.txt",
        "urlList": [p.url for p in targets][:10000],
    }
    if dry:
        print("DRY RUN - would POST to https://api.indexnow.org/indexnow:")
        print(json.dumps(payload, indent=2))
        return True
    checks = [(payload["keyLocation"], lambda b, k=key: b.decode("utf-8", "ignore").strip() == k)]
    checks += [(p.url, lambda b, s=p.sha: hashlib.sha256(b).hexdigest() == s) for p in targets[:5]]
    wait_live(checks, wait_min)
    try:
        status, body = http(
            "https://api.indexnow.org/indexnow",
            json.dumps(payload).encode("utf-8"),
            {"Content-Type": "application/json; charset=utf-8"},
        )
        print(f"IndexNow accepted {len(payload['urlList'])} URL(s): HTTP {status}")
        return True
    except urllib.error.HTTPError as e:
        print(f"IndexNow rejected the request: HTTP {e.code} {e.read()[:200]!r}")
        return False
    except (urllib.error.URLError, OSError) as e:
        print(f"Could not reach IndexNow: {e}")
        return False


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="site origin, e.g. https://poetic-bytes.vercel.app")
    ap.add_argument("--root", default=".", help="folder that is the site root (default: .)")
    ap.add_argument("--exclude", action="append", default=[], help="glob of files to leave out (repeatable)")
    ap.add_argument("--site-title", default="Poetic Bytes - Learn Python Free")
    ap.add_argument("--feed-limit", type=int, default=30)
    ap.add_argument("--report", help="append the markdown report to this file (use $GITHUB_STEP_SUMMARY)")
    ap.add_argument("--no-generate", action="store_true", help="skip sitemap/feed/robots/lint")
    ap.add_argument("--indexnow", action="store_true", help="submit URLs to IndexNow")
    ap.add_argument("--all", action="store_true", help="IndexNow: submit every page")
    ap.add_argument("--changed-from", help="IndexNow: only pages added/modified since this git SHA")
    ap.add_argument("--wait", type=float, default=8, help="minutes to wait for deploy before pinging")
    ap.add_argument("--dry-run", action="store_true", help="IndexNow: print the payload, send nothing")
    args = ap.parse_args()

    root, base = Path(args.root).resolve(), args.base.rstrip("/")
    if git(root, "rev-parse", "--is-shallow-repository") == "true":
        print("WARNING: shallow clone; lastmod dates will be wrong. Use fetch-depth: 0.")
    pages, noindexed = discover(root, base, args.exclude)
    if not pages:
        print("No indexable .html files found under", root)
        return 1

    if not args.no_generate:
        home = next((p for p in pages if p.rel == "index.html"), None)
        desc = home.description if home and home.description else "Free interactive Python tutorials."
        changed = []
        if write_if_changed(root / "sitemap.xml", build_sitemap(pages)):
            changed.append("sitemap.xml")
        if write_if_changed(root / "feed.xml", build_feed(pages, base, args.site_title, desc, args.feed_limit)):
            changed.append("feed.xml")
        if ensure_robots(root, base):
            changed.append("robots.txt")
        key, created = ensure_key(root)
        if created:
            changed.append(f"{key}.txt (new IndexNow key)")
        print("Updated:", ", ".join(changed) if changed else "nothing (already up to date)")
        text = "\n".join(lint(pages, noindexed, root, base)) + "\n"
        print(text)
        if args.report:
            with open(args.report, "a", encoding="utf-8") as fh:
                fh.write(text)

    if args.indexnow:
        key, _ = ensure_key(root, create=False)
        if not key:
            print("No IndexNow key file found; run without --no-generate first.")
            return 1
        targets = pages
        if not args.all and args.changed_from:
            found = changed_pages(root, pages, args.changed_from)
            if found is None:
                print(f"Could not diff against {args.changed_from}; submitting all pages instead.")
            else:
                targets = found
        if not targets:
            print("No changed pages to submit.")
            return 0
        if not submit_indexnow(base, key, targets, args.wait, args.dry_run):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
