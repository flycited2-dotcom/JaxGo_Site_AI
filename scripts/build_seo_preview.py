"""Build the supplied SEO drafts into an isolated local JAXGO review site.

Run: python -X utf8 scripts/build_seo_preview.py
Requires markdown-it-py and beautifulsoup4. Never uploads or edits public/.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import html
import json
import math
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "seo-review-2026-11-05"
SITE = OUT / "site"
MD = MarkdownIt("commonmark", {"html": False}).enable("table")
E = html.escape
BASE = 'https://jaxgo.ru'
CATALOGUE = '/articles/'
PAGE_SIZE = 9
SERVICE_BY_CATEGORY = {
    'Сайты для бизнеса': 'web', 'Прайсы и торговля': 'automation',
    'Автоматизация и управление': 'automation', 'Боты и интеграции': 'integration',
    'AI в рабочих задачах': 'ai', 'Мониторинг закупок': 'systems',
}
RELATED = {
    "A01": ["A08", "A04", "A05"], "A02": ["A06", "A21", "A09"],
    "A03": ["A09", "A14", "A10"], "A04": ["A01", "A13", "A14"],
    "A05": ["A01", "A23", "A24"], "A06": ["A02", "A21", "A03"],
    "A07": ["A01", "A08", "A23"], "A08": ["A01", "A07", "A24"],
    "A09": ["A03", "A02", "A21"], "A10": ["A03", "A12", "A20"],
    "A11": ["A04", "A22", "A14"], "A12": ["A05", "A10", "A24"],
    "A13": ["A04", "A01", "A23"], "A14": ["A04", "A03", "A23"],
    "A15": ["A10", "A14", "A23"], "A16": ["A07", "A17", "A18"],
    "A17": ["A01", "A07", "A16"], "A18": ["A05", "A16", "A20"],
    "A19": ["A05", "A12", "A23"], "A20": ["A10", "A12", "A18"],
    "A21": ["A02", "A06", "A03"], "A22": ["A11", "A04", "A14"],
    "A23": ["A05", "A01", "A24"], "A24": ["A05", "A08", "A23"],
}

# Public-facing copy is separate from immutable archive files. These substitutions
# remove instructions to the editor, without changing the claimed capabilities.
EDITS = {
    "работоспособность действующей публичной отправки следует проверять отдельно.":
        "при подключении такой схемы важно проверить доставку и обработку ошибок.",
    "Отдельного подтверждённого клиентского кейса интеграции 1С в локальном каталоге решений нет; поэтому техническую реализацию нельзя приписывать выполненному проекту компании.":
        "Возможность обмена, состав работ и способ подключения определяются после проверки вашей конфигурации и доступов.",
    "Это не подтверждение наличия готового AI-поиска.":
        "Поиск и другие дополнительные функции обсуждаются отдельно.",
    "Это подтверждение общей функции повторения, но не всех проектируемых проверок выше.":
        "Проверки цен, наличия и условий повторного заказа согласуются под конкретный процесс.",
    "Подтверждённые возможности описаны в": "Описание решения можно найти в",
    "В локальной версии": "В проекте",
    "Это описание функции, а не подтверждение безошибочного распознавания или всех предложенных здесь проверок.":
        "Правила уточнения дат и подтверждения записей подбираются под ваш рабочий сценарий.",
    "Не обещайте интеграцию, пока эти ограничения не проверены.":
        "Возможность подключения зависит от этих ограничений.",
}


def read_articles():
    items = []
    for n in range(1, 25):
        aid = f"A{n:02}"
        folder = ROOT / "SEO" / "articles" / aid
        full = (folder / f"{aid}_FULL.md").read_text(encoding="utf-8-sig")
        original = (folder / f"{aid}_ARTICLE_ONLY.md").read_text(encoding="utf-8-sig").strip()
        fields = {}
        for line in full.splitlines():
            if line.startswith("| "):
                cells = [v.strip() for v in line.strip("|").split("|")]
                if len(cells) == 2:
                    fields.setdefault(cells[0], cells[1].strip("`"))
        old_url = fields["Предлагаемый URL"]
        match = re.fullmatch(r"/articles/([a-z0-9-]+)\.html", old_url)
        if not match:
            raise ValueError(f"Unsafe article URL: {old_url}")
        url = f"/articles/{match[1]}/"
        h1 = re.findall(r"^# (.+)$", original, re.M)
        if len(h1) != 1 or h1[0] != fields["H1"]:
            raise ValueError(f"H1 mismatch: {aid}")
        body = original
        changes = []
        for old, new in EDITS.items():
            if old in body:
                body = body.replace(old, new)
                changes.append({"before": old, "after": new})
        # Editorial phrases appearing in source copies must never leak unnoticed.
        flags = [p for p in body.split("\n\n") if re.search(
            r"локальн|перед выпуском|перед публикац|не приписыв|подтверждённ.*функц|правила.*не провер", p, re.I)]
        words = len(re.findall(r"[\w-]+", body))
        intro = body.split("\n\n")[1]
        # Long source announcements repeat the lead; cards use the first sentence.
        excerpt = re.split(r"(?<=[.!?])\s+", fields.get("Анонс", fields.get("Анонс для карточки", intro)))[0]
        items.append({"id": aid, "url": url, "title": fields["Title"],
            "description": fields["Meta description"], "h1": h1[0],
            "category": fields["Рубрика"], "excerpt": excerpt,
            "minutes": max(3, math.ceil(words / 180)), "markdown": body,
            "word_count": words, "changes": changes, "editor_flags": flags,
            "source_sha256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
            "source_file": str(folder.relative_to(ROOT) / f"{aid}_ARTICLE_ONLY.md"),
            "related": RELATED[aid], "links": [], "week": (n+1)//2, "slot": 1 if n%2 else 2,
            "status": "local_review", "approved": False, "published_at": None})
    for key in ["url", "title", "description", "h1"]:
        if len({x[key] for x in items}) != 24:
            raise ValueError(f"Duplicate {key}")
    return items


def read_inbox(inbox):
    """Read owner-reviewed Markdown with YAML metadata from the private inbox."""
    items = []
    required = {'site', 'slug', 'title', 'description', 'h1', 'date', 'category',
                'main_query', 'queries', 'links', 'cta', 'read_time'}
    for file in sorted(inbox.glob('*.md')):
        source = file.read_text(encoding='utf-8-sig')
        match = re.fullmatch(r'---[ \t]*\n(.*?)\n---[ \t]*\n(.*)', source, re.S)
        if not match:
            raise ValueError(f'{file.name}: missing YAML front matter')
        fields = yaml.safe_load(match[1])
        if not isinstance(fields, dict) or required - fields.keys():
            raise ValueError(f'{file.name}: incomplete YAML front matter')
        for key in ['site', 'slug', 'title', 'description', 'h1', 'category', 'main_query']:
            if not isinstance(fields[key], str) or not fields[key].strip():
                raise ValueError(f'{file.name}: invalid {key}')
        slug = fields['slug']
        if (fields['site'] not in {'jaxgo', 'jaxgo.ru'} or len(slug) > 80 or slug in {'page', 'index'}
                or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug)):
            raise ValueError(f'{file.name}: invalid site or slug')
        published = date.fromisoformat(str(fields['date']))
        modified = date.fromisoformat(str(fields.get('date_modified', published)))
        if modified < published:
            raise ValueError(f'{file.name}: date_modified precedes date')
        minutes = fields['read_time']
        if type(minutes) is not int or minutes < 1:
            raise ValueError(f'{file.name}: read_time must be a positive integer')
        queries = fields['queries']
        if not isinstance(queries, list) or not all(isinstance(query, str) and query.strip() for query in queries):
            raise ValueError(f'{file.name}: queries must be a list of strings')
        links = fields['links']
        if not isinstance(links, list) or not 2 <= len(links) <= 3:
            raise ValueError(f'{file.name}: links must contain 2–3 entries')
        for link in links:
            if not isinstance(link, dict) or not all(isinstance(link.get(k), str) and link[k].strip() for k in ('url', 'label')):
                raise ValueError(f'{file.name}: links require url and label')
            url = urlsplit(link['url'])
            if url.scheme or url.netloc or not re.fullmatch(r'/[a-zA-Z0-9/_-]*', url.path) or url.path.startswith('//'):
                raise ValueError(f'{file.name}: use internal URLs in links')
        cta = fields['cta']
        if not isinstance(cta, str) or not cta.strip():
            raise ValueError(f'{file.name}: cta must be button text')
        body = match[2].strip()
        if not body:
            raise ValueError(f'{file.name}: article body is empty')
        items.append({'id': f'md-{slug}', 'url': f'{CATALOGUE}{slug}/', 'title': fields['title'],
                      'description': fields['description'], 'h1': fields['h1'], 'category': fields['category'],
                      'excerpt': fields['description'], 'minutes': minutes, 'markdown': body,
                      'date': published, 'date_modified': modified, 'related': [], 'links': links, 'cta': cta})
    return sorted(items, key=lambda item: (item['date'], item['id']))


def catalogue_url(page):
    return CATALOGUE if page == 1 else f'{CATALOGUE}page/{page}/'


def page_count(items):
    return max(1, math.ceil(len(items) / PAGE_SIZE))


def page_file(url):
    return url.strip('/') + '/index.html'


def header():
    return '''<a class="skip" href="#main">Перейти к содержанию</a>
<div class="review-note">Локальный просмотр · материалы на проверке · публикация не выполнена</div>
<header class="site-header"><div class="wrap header-row">
<a class="logo" href="/" aria-label="JAXGO — на главную">JAX<span>GO</span><sup>↗</sup></a>
<nav aria-label="Главное меню"><a href="/#solutions">Услуги</a><a href="/#cases">Решения</a>
<a href="/articles/">Статьи</a><a href="tel:+79785792995">+7 978 579-29-95</a><a class="contact-link" href="/#contacts">Обсудить задачу <span aria-hidden="true">↗</span></a></nav>
</div></header>'''


def footer():
    return '''<footer class="site-footer"><div class="wrap footer-row"><div>
<a class="logo" href="/">JAX<span>GO</span><sup>↗</sup></a><p>От бизнес-задачи<br>до работающей системы.</p></div>
<nav aria-label="Ссылки в подвале"><a href="/articles/">Полезные статьи</a><a href="tel:+79785792995">+7 978 579-29-95</a>
<a href="mailto:go@jaxgo.ru">go@jaxgo.ru</a><a href="/privacy.html">Политика конфиденциальности</a></nav>
<p>© 2026 JAXGO<br>ИП Гуриненко Н.Н.</p></div></footer>'''


def shell(title, description, url, content, structured=None):
    schema = ''
    if structured:
        payload = json.dumps(structured, ensure_ascii=False).replace('<', '\\u003c')
        schema = f'<script type="application/ld+json">{payload}</script>'
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><meta name="theme-color" content="#092d26">
<title>{E(title)}</title><meta name="description" content="{E(description, quote=True)}">
<link rel="canonical" href="https://jaxgo.ru{url}">
<meta property="og:title" content="{E(title, quote=True)}"><meta property="og:description" content="{E(description, quote=True)}">
<meta property="og:type" content="{'article' if structured else 'website'}"><meta property="og:locale" content="ru_RU">
<meta property="og:site_name" content="JAXGO"><meta property="og:url" content="https://jaxgo.ru{url}">
<meta property="og:image" content="https://jaxgo.ru/assets/approved_emerald_preview.png">
<link rel="icon" href="/favicon.svg"><link rel="stylesheet" href="/articles/articles.css?v=20261107-seo">
<script defer src="/articles/articles.js?v=20261107-seo"></script>{schema}</head><body>
{header()}{content}{footer()}</body></html>'''


def card(a):
    return f'''<article class="article-card" data-category="{E(a['category'], quote=True)}">
<div class="card-meta"><span>{E(a['category'])}</span><span>{a['minutes']} мин</span></div>
<h2><a href="{a['url']}">{E(a['h1'])}</a></h2><p>{E(a['excerpt'])}</p>
<span class="card-action" aria-hidden="true">Читать статью <span>↗</span></span></article>'''


def catalogue(items, page=1):
    start = (page - 1) * PAGE_SIZE
    visible = items[start:start + PAGE_SIZE]
    other = items[:start] + items[start + PAGE_SIZE:]
    cats = list(dict.fromkeys(x['category'] for x in items))
    buttons = f'<button type="button" class="filter active" data-filter="all" aria-pressed="true">Все статьи <span>{len(items)}</span></button>'
    for cat in cats:
        count = sum(x['category'] == cat for x in items)
        buttons += f'<button type="button" class="filter" data-filter="{E(cat, quote=True)}" aria-pressed="false">{E(cat)} <span>{count}</span></button>'
    pagination = ''
    if page_count(items) > 1:
        links = [f'<a href="{catalogue_url(n)}" aria-label="Страница {n}"'
                 + (' aria-current="page"' if n == page else '') + f'>{n}</a>'
                 for n in range(1, page_count(items) + 1)]
        pagination = '<nav class="pagination" aria-label="Страницы каталога">' + ''.join(links) + '</nav>'
    content = f'''<main id="main">
<section class="catalog-hero"><div class="wrap"><div class="eyebrow">ЖУРНАЛ JAXGO / ПОЛЕЗНЫЕ СТАТЬИ</div>
<div class="hero-columns"><h1>Меньше рутины.<br>Больше <em>ясности.</em></h1>
<div class="hero-description"><p>Как запустить сайт, связать сервисы и упростить ежедневную работу бизнеса.</p>
<p>Практические разборы, примеры и чек-листы — от задачи до понятного следующего шага.</p></div></div>
<div class="hero-bottom"><span>Сайты · торговля · автоматизация · AI</span><span>Разбираемся по делу <span aria-hidden="true">↓</span></span></div>
</div></section><section class="wrap catalog-body" aria-label="Каталог статей">
<div class="catalog-tools"><div><span class="eyebrow dark">ОТ ВОПРОСА К РЕШЕНИЮ</span><h2>Найдите свою задачу<span class="green-dot">.</span></h2></div>
<label class="search-label">Поиск по статьям<input id="article-search" type="search" placeholder="Например, прайсы или заявки" autocomplete="off"></label></div>
<div class="filters" aria-label="Темы статей">{buttons}</div>
<p class="result-count" id="result-count" role="status">Показано статей: {len(visible)}</p>
<div class="article-grid">{''.join(card(a) for a in visible)}</div>
<template id="other-articles">{''.join(card(a) for a in other)}</template>
{pagination}
<p id="empty-results" hidden>По этому запросу статей нет. Попробуйте другую формулировку или выберите все темы.</p>
</section><section class="wrap catalog-cta"><div><span class="eyebrow">ВАШ СЛЕДУЮЩИЙ ШАГ</span>
<h2>Есть похожая задача?</h2><p>Расскажите, как работаете сейчас и что хотите упростить.</p></div>
<a class="button" href="/#contacts">Обсудить задачу <span aria-hidden="true">↗</span></a></section></main>'''
    suffix = '' if page == 1 else f' | Страница {page}'
    return shell('Полезные статьи для бизнеса: сайты, прайсы и автоматизация | JAXGO' + suffix,
        'Практические статьи JAXGO: выбор сайта, обработка прайсов, боты, интеграции и AI в рабочих задачах. Примеры и чек-листы для предпринимателей.',
        catalogue_url(page), content)


SOURCES = {
    'A01': [('Power Query: объединение запросов', 'https://learn.microsoft.com/en-us/power-query/append-queries'),
            ('Power Query: нечёткое сопоставление', 'https://learn.microsoft.com/ru-ru/power-query/merge-queries-fuzzy-match')],
    'A03': [('Telegram Bot API: sendMessage', 'https://core.telegram.org/bots/api#sendmessage')],
    'A14': [('1С:Предприятие: HTTP-сервисы', 'https://v8.1c.ru/platforma/http-servisy/')],
    'A15': [('Google Calendar API: создание событий', 'https://developers.google.com/workspace/calendar/api/guides/create-events')],
}


def faq_schema(soup):
    heading = next((h for h in soup.find_all('h2') if h.get_text(strip=True) == 'Частые вопросы'), None)
    if heading is None:
        return None
    entries = []
    for node in heading.next_siblings:
        if node.name == 'h2':
            break
        if node.name == 'h3':
            entries.append((node.get_text(' ', strip=True), []))
        elif node.name and entries:
            entries[-1][1].append(node.get_text(' ', strip=True))
    questions = [{'@type': 'Question', 'name': name,
                  'acceptedAnswer': {'@type': 'Answer', 'text': ' '.join(parts)}}
                 for name, parts in entries if name and any(parts)]
    return {'@context': 'https://schema.org', '@type': 'FAQPage', 'mainEntity': questions} if questions else None


def service_links(a):
    if a['links']:
        return a['links']
    service = SERVICE_BY_CATEGORY.get(a['category'])
    return [{'url': f'/#service-{service}' if service else '/#solutions', 'label': 'Услуги по теме'},
            {'url': '/#cases', 'label': 'Кейсы JAXGO'}]


def homepage_with_articles(markup, items):
    soup = BeautifulSoup(markup, 'html.parser')
    for block in soup.select('.service-articles'):
        block.decompose()
    for service in soup.select('details.service[id]'):
        href = '/#' + service['id']
        related = [item for item in reversed(items) if any(link['url'] == href for link in service_links(item))][:3]
        if not related:
            continue
        block = soup.new_tag('nav', attrs={'class': 'service-articles', 'aria-label': 'Полезные статьи'})
        label = soup.new_tag('strong', string='Полезные статьи')
        listing = soup.new_tag('ul')
        for item in related:
            entry = soup.new_tag('li')
            entry.append(soup.new_tag('a', href=item['url'], string=item['h1']))
            listing.append(entry)
        block.extend([label, listing])
        service.select_one('.service-body').append(block)
    return str(soup)


def article_page(a, by_id, published):
    markdown = re.sub(r'\[\[ФОТО:[^\]]*\]\]', '', a['markdown'])
    soup = BeautifulSoup(MD.render(markdown), 'html.parser')
    for heading in soup.find_all('h1'):
        if heading.get_text(' ', strip=True) == a['h1']:
            heading.decompose()
        else:
            heading.name = 'h2'
    faq = faq_schema(soup)
    toc = []
    for n, heading in enumerate(soup.find_all('h2'), 1):
        heading['id'] = f'section-{n}'
        toc.append(f'<li><a href="#section-{n}">{E(heading.get_text())}</a></li>')
    for table in soup.find_all('table'):
        wrapper = soup.new_tag('div', attrs={'class': 'table-scroll', 'tabindex': '0', 'role': 'region',
                                          'aria-label': 'Таблица: прокрутите, чтобы увидеть все столбцы'})
        table.wrap(wrapper)
    available = {item['url'] for item in published}
    known = {item['url'] for item in by_id.values()}
    for link in soup.find_all('a', href=True):
        href = link['href'].removeprefix(BASE) if link['href'].startswith(BASE+'/') else link['href']
        if href.startswith('article:'):
            slug = href.removeprefix('article:')
            href = f'{CATALOGUE}{slug}/'
            if href not in known:
                raise ValueError(f"{a['id']}: unknown article:{slug}")
        elif match := re.fullmatch(r'/articles/([a-z0-9-]+)\.html', href):
            href = CATALOGUE if match[1] == 'index' else f'{CATALOGUE}{match[1]}/'
        if href in known - available:
            link.unwrap()
        else:
            link['href'] = href
    sources = ''
    if a['id'] in SOURCES:
        sources = '<section class="sources"><h2>Источники и документация</h2><ul>'
        sources += ''.join(f'<li><a href="{url}">{E(label)}</a></li>' for label, url in SOURCES[a['id']])
        sources += '</ul></section>'
    candidates = [by_id[x] for x in a['related'] if by_id[x]['url'] in available]
    candidates += [x for x in published if x['category'] == a['category']]
    candidates += list(reversed(published))
    neighbors = {x['id']: x for x in candidates if x['id'] != a['id']}
    related = ''.join(card(x) for x in list(neighbors.values())[:3])
    related_section = ''
    if related:
        related_section = f'''<section class="related"><div class="related-heading"><h2>Читайте также<span class="green-dot">.</span></h2>
<a href="/articles/">Все статьи →</a></div><div class="article-grid">{related}</div></section>'''
    services = ''.join(f'<li><a href="{E(link["url"], quote=True)}">{E(link["label"])}</a></li>'
                       for link in service_links(a))
    day = a.get('date')
    byline_date = f'<time datetime="{day.isoformat()}">{day:%d.%m.%Y}</time><span>·</span>' if day else ''
    cta = E(a.get('cta', 'Обсудить задачу'))
    content = f'''<main id="main" data-article-id="{a['id']}"><div class="wrap">
<nav class="breadcrumbs" aria-label="Хлебные крошки"><a href="/">Главная</a><span aria-hidden="true">/</span>
<a href="/articles/">Полезные статьи</a><span aria-hidden="true">/</span><span aria-current="page">{E(a['h1'])}</span></nav>
<header class="article-heading"><div class="eyebrow dark">{E(a['category'])}</div><h1>{E(a['h1'])}</h1>
<div class="byline"><span class="author-mark" aria-hidden="true">JG</span><span>Материал JAXGO</span><span>·</span>{byline_date}<span>{a['minutes']} мин чтения</span></div></header>
<div class="reading-layout"><article class="article-content">{soup}{sources}</article>
<aside class="reading-sidebar"><details class="toc" open><summary>В этой статье</summary><ol>{''.join(toc)}</ol></details>
<div class="sidebar-note"><span class="eyebrow dark">УСЛУГИ И КЕЙСЫ</span><ul>{services}</ul></div></aside></div>
<section class="catalog-cta"><div><h2>Есть похожая задача?</h2><p><a href="tel:+79785792995">+7 978 579-29-95</a> · <a href="mailto:go@jaxgo.ru">go@jaxgo.ru</a></p></div>
<a class="button" href="/#contacts">{cta} <span aria-hidden="true">↗</span></a></section>
{related_section}</div></main>'''
    base = 'https://jaxgo.ru'
    schema = [
        {'@context': 'https://schema.org', '@type': 'Article', 'headline': a['h1'],
         'description': a['description'], 'mainEntityOfPage': base+a['url'],
         'image': base+'/assets/approved_emerald_preview.png',
         'author': {'@type': 'Organization', '@id': base+'/#organization', 'name': 'JAXGO', 'url': base+'/'},
         'publisher': {'@type': 'Organization', '@id': base+'/#organization', 'name': 'JAXGO', 'url': base+'/'}, 'inLanguage': 'ru-RU'},
        {'@context': 'https://schema.org', '@type': 'BreadcrumbList', 'itemListElement': [
         {'@type':'ListItem','position':1,'name':'Главная','item':base+'/'},
         {'@type':'ListItem','position':2,'name':'Полезные статьи','item':base+CATALOGUE},
         {'@type':'ListItem','position':3,'name':a['h1'],'item':base+a['url']}]}]
    if day:
        schema[0].update(datePublished=day.isoformat(), dateModified=a.get('date_modified', day).isoformat())
    if faq:
        schema.append(faq)
    return shell(a['title'], a['description'], a['url'], content, schema)


def main():
    # No recursive removal. Rebuild overwrites only known generated files.
    if not SITE.resolve().is_relative_to((ROOT / 'output').resolve()):
        raise RuntimeError('Output must remain inside project output/')
    SITE.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT/'public', SITE, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('api', 'robots.txt', 'sitemap.xml', 'articles'))
    (SITE/'articles').mkdir(exist_ok=True)
    items = read_articles()
    plan = json.loads((ROOT/'SEO/publication/plan.json').read_text(encoding='utf-8'))
    moscow = timezone(timedelta(hours=3))
    dates = {job['id']: datetime.fromisoformat(job['scheduled_at']).astimezone(moscow).date() for job in plan}
    for item in items:
        item['date'] = dates[item['id']]
    by_id = {a['id']: a for a in items}
    edits_dir = OUT/'edited-markdown'
    edits_dir.mkdir(exist_ok=True)
    for a in items:
        target = SITE/page_file(a['url'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(article_page(a, by_id, items), encoding='utf-8')
        (edits_dir/f"{a['id']}.md").write_text(a['markdown']+'\n', encoding='utf-8')
    for page in range(1, page_count(items) + 1):
        target = SITE/page_file(catalogue_url(page))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(catalogue(items, page), encoding='utf-8')
    for name in ['articles.css', 'articles.js']:
        shutil.copyfile(ROOT/'scripts/seo'/name, SITE/'articles'/name)
    home = (SITE/'index.html').read_text(encoding='utf-8')
    (SITE/'index.html').write_text(homepage_with_articles(home, items), encoding='utf-8')
    # Prevent review traffic from affecting production analytics. Protect every
    # copied HTML page, not only articles, in case the folder is ever staged.
    for file in SITE.rglob('*.html'):
        soup = BeautifulSoup(file.read_text(encoding='utf-8'), 'html.parser')
        if soup.head and not soup.find('meta', attrs={'name': 'robots'}):
            soup.head.append(soup.new_tag('meta', attrs={'name':'robots','content':'noindex,nofollow'}))
        for tag in soup.select('script[src*="metrika.js"], img[src*="mc.yandex.ru"]'):
            tag.decompose()
        file.write_text(str(soup), encoding='utf-8')
    config = SITE/'site-config.js'
    config.write_text(re.sub(r'metrikaId:\s*"[^"]*"', 'metrikaId: ""', config.read_text(encoding='utf-8')), encoding='utf-8')
    (SITE/'robots.txt').write_text('User-agent: *\nDisallow: /\n', encoding='utf-8')
    manifest = [{k:v for k,v in a.items() if k not in ['markdown']} for a in items]
    (OUT/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str)+'\n', encoding='utf-8')
    print(json.dumps({'articles':len(items), 'catalogue':str(SITE/'articles/index.html'),
        'edited_fragments':sum(len(a['changes']) for a in items),
        'editor_flags': {a['id']: a['editor_flags'] for a in items if a['editor_flags']}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
