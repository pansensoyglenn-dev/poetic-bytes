#!/usr/bin/env python3
"""Keep unfinished lessons away from Google (default: preview only).

For every lesson card on the home page (<a class="card" href="lesson.html">):
  * the lesson page is MISSING, or has fewer than --min-words visible words
      -> the card stops being a link and shows "Coming soon", so the crawler never reaches an
         empty or 404 page from your home page
  * the lesson page is finished again (enough words)
      -> the card becomes a normal link again

Optional --noindex-thin also puts <meta name="robots" content="noindex, follow"> on lesson pages that
exist but are too short, and removes it again once they grow.
Unfinished lessons are also taken out of sitemap.xml and sitemap.html (kept inside comments, so they come
back by themselves when the lesson is finished).  Only pages that are lesson cards on the home page are
ever touched: privacy, terms, contact and the other short pages are left alone.  Nothing is written
without --apply.

  python scripts/hide_unfinished_lessons.py --root .                 # preview
  python scripts/hide_unfinished_lessons.py --root . --apply --noindex-thin
"""
import argparse, re, sys
from pathlib import Path

CARD = re.compile(r'<a\b(?P<attrs>[^>]*\bclass="[^"]*\bcard\b[^"]*"[^>]*)>(?P<inner>.*?)</a>', re.S | re.I)
SOON = re.compile(r'<!--soon:(?P<href>[^ ]+?)-->\s*<div\b(?P<attrs>[^>]*)>(?P<inner>.*?)</div>\s*<!--/soon-->', re.S)
HREF = re.compile(r'\shref="([^"]+)"', re.I)
ARROW = re.compile(r'<span class="card-arrow">.*?</span>', re.S)
BADGE = '<span class="soon-badge">Coming soon</span>'
CSS = '.card.is-soon{opacity:.62;cursor:default}.card.is-soon:hover{transform:none}.soon-badge{display:inline-block;margin-top:.5rem;font-size:.75rem;opacity:.85}'
NOINDEX = '<meta name="robots" content="noindex, follow" data-auto="thin">'
TAGS = re.compile(r'<(script|style|noscript|svg)\b.*?</\1>', re.S | re.I)


def words(html):
    text = re.sub(r'<[^>]+>', ' ', TAGS.sub(' ', html))
    return len(re.findall(r"[A-Za-z0-9_'’]+", text))


def page_words(root, href):
    p = root / href.split('#')[0].split('?')[0]
    if not p.is_file():
        return None
    return words(p.read_text(encoding='utf-8', errors='ignore'))


def local_lesson(href):
    return bool(href) and not re.match(r'(?i)(https?:|mailto:|tel:|#|//)', href) and href.split('#')[0].split('?')[0].endswith('.html')


def process_home(root, home, min_words, report):
    html = home.read_text(encoding='utf-8')
    cache = {}
    def wc(h):
        if h not in cache:
            cache[h] = page_words(root, h)
        return cache[h]

    # 1) cards that are linked but unfinished
    def to_soon(m):
        attrs = m.group('attrs')
        h = HREF.search(attrs)
        if not h or not local_lesson(h.group(1)) or 'target="_blank"' in attrs:
            return m.group(0)
        href = h.group(1)
        n = wc(href)
        if n is not None and n >= min_words:
            return m.group(0)
        report.append(('hide', href, n))
        a = HREF.sub('', attrs, count=1)
        a = re.sub(r'\s(target|rel)="[^"]*"', '', a)
        a = re.sub(r'class="([^"]*\bcard\b[^"]*)"', r'class="\1 is-soon"', a, count=1)
        inner = ARROW.sub(BADGE, m.group('inner'))
        if BADGE not in inner:
            inner += BADGE
        return f'<!--soon:{href}--><div{a}>{inner}</div><!--/soon-->'
    html = CARD.sub(to_soon, html)

    # 2) cards hidden earlier whose page is finished now
    def to_link(m):
        href = m.group('href')
        n = wc(href)
        if n is None or n < min_words:
            return m.group(0)
        report.append(('relink', href, n))
        a = m.group('attrs').replace(' is-soon', '')
        inner = m.group('inner').replace(BADGE, '<span class="card-arrow">Start learning →</span>')
        return f'<a{a} href="{href}">{inner}</a>'
    html = SOON.sub(to_link, html)

    if 'is-soon' in html and '.card.is-soon' not in html:
        html = html.replace('</style>', CSS + '\n</style>', 1)
    return html


def process_thin_pages(root, home_name, lessons, min_words, apply, report):
    for p in sorted(root.glob('*.html')):
        if p.name == home_name or p.name not in lessons:
            continue
        src = p.read_text(encoding='utf-8', errors='ignore')
        n = words(src)
        has = 'data-auto="thin"' in src
        if n < min_words and not has and '</head>' in src:
            report.append(('noindex', p.name, n))
            if apply:
                p.write_text(src.replace('</head>', NOINDEX + '\n</head>', 1), encoding='utf-8', newline='')
        elif n >= min_words and has:
            report.append(('index-again', p.name, n))
            if apply:
                p.write_text(re.sub(r'[ \t]*<meta name="robots"[^>]*data-auto="thin"[^>]*>\n?', '', src), encoding='utf-8', newline='')


def lesson_pages(home_html):
    """Every local .html page that the home page shows as a lesson card (linked or marked coming soon)."""
    out = set()
    for m in CARD.finditer(home_html):
        h = HREF.search(m.group('attrs'))
        if h and local_lesson(h.group(1)) and 'target="_blank"' not in m.group('attrs'):
            out.add(h.group(1).split('#')[0].split('?')[0])
    for m in re.finditer(r'<!--soon:([^ ]+?)-->', home_html):
        out.add(m.group(1))
    return out


XML_URL = re.compile(r'<url>\s*<loc>(?P<loc>[^<]+)</loc>.*?</url>', re.S | re.I)
XML_OFF = re.compile(r'<!--soon-url:\s*(?P<block><url>\s*<loc>(?P<loc>[^<]+)</loc>.*?</url>)\s*-->', re.S | re.I)
LI = re.compile(r'<li\b[^>]*>(?:(?!</li>).)*?<a\b[^>]*\shref="(?P<href>[^"#?]+\.html)"[^>]*>.*?</li>', re.S | re.I)
LI_OFF = re.compile(r'<!--soon-li:\s*(?P<block><li\b.*?</li>)\s*-->', re.S | re.I)


COMMENT = re.compile(r'<!--.*?-->', re.S)


def sub_outside_comments(pattern, fn, text):
    """Apply pattern.sub only to the parts of text that are not inside <!-- comments -->."""
    out, pos = [], 0
    for c in COMMENT.finditer(text):
        out.append(pattern.sub(fn, text[pos:c.start()]))
        out.append(c.group(0))
        pos = c.end()
    out.append(pattern.sub(fn, text[pos:]))
    return ''.join(out)


def loc_to_name(loc):
    path = re.sub(r'^https?://[^/]+', '', loc.strip()).lstrip('/')
    return path.split('#')[0].split('?')[0]


def process_sitemaps(root, lessons, min_words, apply, report):
    def state(name):
        n = page_words(root, name)
        return n, (n is None or n < min_words)

    xml = root / 'sitemap.xml'
    if xml.is_file():
        src = xml.read_text(encoding='utf-8')
        def off(m):
            name = loc_to_name(m.group('loc'))
            if name not in lessons or '--' in m.group(0)[5:-6]:
                return m.group(0)
            n, unfinished = state(name)
            if not unfinished:
                return m.group(0)
            report.append(('sitemap-out', 'sitemap.xml: ' + name, n))
            return '<!--soon-url: ' + m.group(0) + ' -->'
        def on(m):
            name = loc_to_name(m.group('loc'))
            n, unfinished = state(name)
            if unfinished:
                return m.group(0)
            report.append(('sitemap-in', 'sitemap.xml: ' + name, n))
            return m.group('block')
        new = XML_OFF.sub(on, src)
        new = sub_outside_comments(XML_URL, off, new)
        if apply and new != src:
            xml.write_text(new, encoding='utf-8', newline='')

    page = root / 'sitemap.html'
    if page.is_file():
        src = page.read_text(encoding='utf-8')
        def off(m):
            name = m.group('href')
            if name not in lessons or '--' in m.group(0):
                return m.group(0)
            n, unfinished = state(name)
            if not unfinished:
                return m.group(0)
            report.append(('sitemap-out', 'sitemap.html: ' + name, n))
            return '<!--soon-li: ' + m.group(0) + ' -->'
        def on(m):
            h = re.search(r'\shref="([^"#?]+\.html)"', m.group('block'))
            if not h:
                return m.group(0)
            n, unfinished = state(h.group(1))
            if unfinished:
                return m.group(0)
            report.append(('sitemap-in', 'sitemap.html: ' + h.group(1), n))
            return m.group('block')
        new = LI_OFF.sub(on, src)
        new = sub_outside_comments(LI, off, new)
        if apply and new != src:
            page.write_text(new, encoding='utf-8', newline='')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--root', default='.')
    ap.add_argument('--home', default='index.html')
    ap.add_argument('--min-words', type=int, default=300)
    ap.add_argument('--noindex-thin', action='store_true')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    root = Path(a.root).resolve()
    home = root / a.home
    if not home.is_file():
        sys.exit(f'{home} not found')
    report = []
    new = process_home(root, home, a.min_words, report)
    lessons = lesson_pages(home.read_text(encoding='utf-8'))
    if a.noindex_thin:
        process_thin_pages(root, home.name, lessons, a.min_words, a.apply, report)
    process_sitemaps(root, lessons, a.min_words, a.apply, report)
    for kind, name, n in report:
        print(f"{'' if a.apply else 'WOULD '}{kind:11s} {name}  ({'missing' if n is None else str(n) + ' words'})")
    if a.apply and new != home.read_text(encoding='utf-8'):
        home.write_text(new, encoding='utf-8', newline='')
    print(f"\n{len(report)} change(s)." + ('' if a.apply or not report else '  Run again with --apply to write them.'))


if __name__ == '__main__':
    main()
