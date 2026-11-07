"""Build reviewed JAXGO article releases without touching the installed queue."""

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_seo_preview as preview

ROOT = Path(__file__).resolve().parents[2]
CURRENT = ROOT / 'SEO/publication'
MOSCOW = dt.timezone(dt.timedelta(hours=3))


def production(markup):
    soup = BeautifulSoup(markup, 'html.parser')
    for node in soup.select('.review-note, meta[name="robots"]'):
        node.decompose()
    article_script = soup.find('script', src='/articles/articles.js?v=20261107-seo')
    for name in ['/site-config.js', '/metrika.js?v=20261105']:
        tag = soup.new_tag('script', src=name, defer=True)
        article_script.insert_before(tag)
    return str(soup).encode('utf-8')


def use_scheduled_date(item, scheduled):
    item['date'] = max(item.get('date', scheduled), scheduled)
    item['date_modified'] = max(item.get('date_modified', scheduled), item['date'])


def publication_plan(current, items):
    by_id = {item['id']: item for item in items}
    if len(by_id) != len(items) or len({item['url'] for item in items}) != len(items):
        raise ValueError('Duplicate article ID or URL')
    plan = json.loads(current.read_text(encoding='utf-8'))
    planned = set()
    for job in plan:
        if job['id'] in planned or job['id'] not in by_id:
            raise ValueError(f"Duplicate or missing plan source: {job['id']}")
        planned.add(job['id'])
        item = by_id[job['id']]
        job['url'] = item['url']
        scheduled = dt.datetime.fromisoformat(job['scheduled_at']).astimezone(MOSCOW)
        use_scheduled_date(item, scheduled.date())
    if any(item['id'] not in planned and not item['id'].startswith('md-') for item in items):
        raise ValueError('The existing plan must include all reviewed A01–A24 articles')

    latest = max(dt.datetime.fromisoformat(job['scheduled_at']).astimezone(MOSCOW) for job in plan)
    today = dt.datetime.now(MOSCOW).date()
    for item in items:
        if item['id'] in planned:
            continue
        due = max(item['date'], today, latest.date() + dt.timedelta(days=1))
        while due.weekday() not in (1, 4):
            due += dt.timedelta(days=1)
        latest = dt.datetime.combine(due, dt.time(11), MOSCOW)
        use_scheduled_date(item, due)
        plan.append({'id': item['id'], 'url': item['url'], 'approved': True,
                     'scheduled_at': latest.isoformat(), 'authorization': 'Reviewed Markdown inbox import'})
    return plan, by_id


def sitemap(baseline, articles):
    ns = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
    tree = ET.fromstring(baseline)
    for node in list(tree):
        url = node.findtext(ns + 'loc') or ''
        if url.startswith(preview.BASE + preview.CATALOGUE):
            tree.remove(node)
    pages = [(preview.catalogue_url(n), None) for n in range(1, preview.page_count(articles) + 1)]
    for url, updated in pages + [(a['url'], a.get('date_modified', a['date'])) for a in articles]:
        node = ET.SubElement(tree, ns + 'url')
        ET.SubElement(node, ns + 'loc').text = preview.BASE + url
        if updated is not None:
            ET.SubElement(node, ns + 'lastmod').text = updated.isoformat()
    ET.register_namespace('', ns[1:-1])
    ET.indent(tree, space='  ')
    return ET.tostring(tree, encoding='utf-8', xml_declaration=True)


def build(args):
    if args.output.resolve() == args.plan.parent.resolve():
        raise ValueError('Write staged releases to a separate directory')
    items = preview.read_articles()
    if args.inbox:
        if not args.inbox.is_dir():
            raise ValueError('The Markdown inbox directory does not exist')
        items += preview.read_inbox(args.inbox)
    plan, by_id = publication_plan(args.plan, items)
    public = ROOT / 'public'
    if args.baseline:
        baseline = {name: (args.baseline / name).read_bytes() for name in ('app.js', 'index.html', 'sitemap.xml')}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in baseline.items()}
        baseline_sitemap = baseline['sitemap.xml']
    else:
        hashes = json.loads((CURRENT/'baseline-hashes.json').read_text(encoding='utf-8'))
        baseline_sitemap = (public/'sitemap.xml').read_bytes()
    home = (public / 'index.html').read_text(encoding='utf-8')
    assets = {f'articles/{name}': (ROOT / 'scripts/seo' / name).read_bytes()
              for name in ('articles.css', 'articles.js')}
    assets.update({name: (public / name).read_bytes() for name in ('styles.css', 'app.js')})
    selected = []
    for job in plan:
        if not job['approved']:
            raise ValueError(f"Cannot include an unapproved job: {job['id']}")
        selected.append(by_id[job['id']])
        files = dict(assets)
        for item in reversed(selected):
            files[preview.page_file(item['url'])] = production(preview.article_page(item, by_id, selected))
        files['index.html'] = preview.homepage_with_articles(home, selected).encode('utf-8')
        newest = list(reversed(selected))
        for page in reversed(range(1, preview.page_count(selected) + 1)):
            files[preview.page_file(preview.catalogue_url(page))] = production(preview.catalogue(newest, page))
        files['sitemap.xml'] = sitemap(baseline_sitemap, selected)

        bundle = args.output / 'releases' / job['id']
        inventory = {}
        for name, data in files.items():
            target = bundle / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            inventory[name] = hashlib.sha256(data).hexdigest()
        (bundle / 'inventory.json').write_text(json.dumps(inventory, indent=2), encoding='utf-8')

    (args.output / 'plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output / 'baseline-hashes.json').write_text(json.dumps(hashes, indent=2), encoding='utf-8')
    (args.output / '.htaccess').write_bytes((public / '.htaccess').read_bytes())
    print(f'Staged {len(plan)} releases in {args.output}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, default=CURRENT / 'plan.json')
    parser.add_argument('--baseline', type=Path, help='Override the source sitemap and first-install hashes')
    parser.add_argument('--output', type=Path, default=ROOT / 'output/seo-publisher-seo')
    parser.add_argument('--inbox', type=Path, help='Directory of owner-reviewed Markdown ready to schedule')
    build(parser.parse_args())


if __name__ == '__main__':
    main()
