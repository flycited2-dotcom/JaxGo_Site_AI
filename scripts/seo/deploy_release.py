"""Migrate the published JAXGO snapshot and install its matching future releases."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import time
from urllib.parse import urlsplit

import publisher


def verify_http(published, future, bundle, inventory):
    query = f'?jaxgo_seo_check={time.time_ns()}'
    article = published[-1]['url']
    legacy = article.rstrip('/') + '.html'
    checks = [(host, tls, path, 301, 'https://jaxgo.ru' + canonical + query)
              for host, tls in [('jaxgo.ru', False), ('www.jaxgo.ru', False), ('www.jaxgo.ru', True)]
              for path, canonical in [('/', '/'), (legacy, article), ('/articles', '/articles/')]]
    checks += [('jaxgo.ru', True, path, code, location) for path, code, location in [
        ('/', 200, None), ('/articles/', 200, None), (article, 200, None),
        (legacy, 301, 'https://jaxgo.ru' + article + query),
        ('/jaxgo-seo-missing-page', 404, None),
    ]]
    if future:
        checks.append(('jaxgo.ru', True, future[0]['url'], 404, None))
    for host, tls, path, code, location in checks:
        connection = http.client.HTTPSConnection if tls else http.client.HTTPConnection
        gateway = os.environ.get('JAXGO_VERIFICATION_PROXY')
        endpoint = path + query
        if gateway:
            parsed = urlsplit(gateway)
            if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port:
                raise ValueError('Verification proxy must use a local SSH tunnel')
            client = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=20)
            endpoint = ('https' if tls else 'http')+'://'+host+endpoint
        else:
            client = connection(host, timeout=12)
        with closing(client):
            client.request('GET', endpoint, headers={'Cache-Control': 'no-cache'})
            response = client.getresponse()
            if (response.status, response.getheader('Location')) != (code, location):
                raise ValueError(f'Unexpected HTTP result for {host}{path}: {response.status}')
    for name, checksum in inventory.items():
        if gateway:
            with closing(http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=20)) as client:
                client.request('GET', 'https://jaxgo.ru/'+name+query)
                response = client.getresponse()
                if response.status != 200:
                    raise ValueError('Inventory check failed: '+name)
                data = response.read()
        else:
            data = publisher.fetch(name)
        if publisher.digest(data) != checksum:
            raise ValueError(f'Published bytes differ from {bundle.name}: {name}')


def upload(ftp, name, data, tag):
    tmp = f'{name}.jaxgo-seo-{tag}.tmp'
    ftp.storbinary('STOR /public_html/' + tmp, io.BytesIO(data))
    if publisher.retrieve(ftp, tmp) != data:
        raise ValueError('FTP upload verification failed: ' + name)
    ftp.rename('/public_html/' + tmp, '/public_html/' + name)
    if publisher.retrieve(ftp, name) != data:
        raise ValueError('FTP rename verification failed: ' + name)


def migrate(root, state, stage):
    old = json.loads((root/'plan.json').read_text(encoding='utf-8'))
    new = json.loads((stage/'plan.json').read_text(encoding='utf-8'))
    compare = lambda plan: [{key: value for key, value in job.items() if key != 'url'} for job in plan]
    if compare(old) != compare(new):
        raise ValueError('Staged plan differs from the installed schedule or approvals')
    rules = (stage/'.htaccess').read_bytes()
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')

    with (state/'publisher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(publisher.open_db(state)) as db:
            jobs = dict(db.execute('SELECT id, state FROM jobs'))
            published = [job for job in new if jobs.get(job['id']) == 'published']
            future = new[len(published):]
            if (not published or new[:len(published)] != published or set(jobs) != {job['id'] for job in new}
                    or any(jobs[job['id']] != 'scheduled' for job in future)):
                raise ValueError('Queue is not a contiguous published prefix followed by scheduled jobs')
            for job in new:
                source = stage/'releases'/job['id']
                inventory = json.loads((source/'inventory.json').read_text(encoding='utf-8'))
                for name, expected in inventory.items():
                    path = PurePosixPath(name)
                    if path.is_absolute() or '..' in path.parts or publisher.digest((source/name).read_bytes()) != expected:
                        raise ValueError('Invalid release file: ' + name)
            bundle = stage/'releases'/published[-1]['id']
            inventory = json.loads((bundle/'inventory.json').read_text(encoding='utf-8'))
            receipt_name = 'articles/release-receipt.json'
            receipt = json.dumps({'id': bundle.name, 'inventory': inventory}, sort_keys=True).encode()
            payload = {name: (bundle/name).read_bytes() for name in inventory}
            payload.update({'.htaccess': rules, receipt_name: receipt})
            backup = root/f'seo-backup-{tag}'
            backup.mkdir(mode=0o700)
            print(f'Backup directory: {backup}', flush=True)
            with closing(sqlite3.connect(backup/'queue.sqlite')) as copy:
                db.backup(copy)

            with tempfile.TemporaryDirectory(prefix='.seo-install-', dir=root) as prepared:
                prepared = Path(prepared)
                shutil.copytree(stage/'releases', prepared/'releases')
                shutil.copyfile(stage/'plan.json', prepared/'plan.json')
                shutil.copyfile(Path(__file__).with_name('publisher.py'), prepared/'publisher.py')
                for path in prepared.rglob('*'):
                    path.chmod(0o755 if path.is_dir() else 0o644)
                swapped = []
                changed = []
                retired = {}
                originals = {}
                with closing(publisher.connect()) as ftp:
                    publisher.make_directories(ftp, payload)
                    for name in payload:
                        before = publisher.retrieve(ftp, name)
                        actual = publisher.digest(before) if before is not None else 'absent'
                        tracked = db.execute('SELECT expected, committed FROM files WHERE path=?', (name,)).fetchone()
                        if tracked and tracked != (actual, actual):
                            raise ValueError('Live file changed outside the queue: ' + name)
                        if name == '.htaccess' and before not in (None, rules):
                            raise ValueError('Live .htaccess differs and must be merged before migration')
                        originals[name] = before
                        if before is not None:
                            destination = backup/'public'/name
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_bytes(before)
                    (backup/'absent.json').write_text(json.dumps([n for n, data in originals.items() if data is None]))
                    try:
                        for name, data in payload.items():
                            if originals[name] == data:
                                continue
                            changed.append(name)
                            if name == '.htaccess':
                                upload(ftp, name, data, tag)
                            else:
                                publisher.put(ftp, db, state, name, data, 'seo-'+tag)
                        # SprintHost can serve existing HTML through its static layer.
                        # Retire old files so requests reach the canonical rewrite rules.
                        for job in published:
                            name = job['url'].strip('/') + '.html'
                            before = publisher.retrieve(ftp, name)
                            if before is None:
                                continue
                            checksum = publisher.digest(before)
                            tracked = db.execute('SELECT expected, committed FROM files WHERE path=?', (name,)).fetchone()
                            if tracked and tracked != (checksum, checksum):
                                raise ValueError('Legacy article changed outside the queue: '+name)
                            destination = backup/'public'/name
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            destination.write_bytes(before)
                            archived = str(PurePosixPath(name).with_name('.jaxgo-retired-'+tag+'-'+PurePosixPath(name).name))
                            retired[name] = archived
                            ftp.rename('/public_html/'+name, '/public_html/'+archived)
                        for attempt in range(3):
                            try:
                                verify_http(published, future, bundle, inventory)
                                break
                            except OSError:
                                if attempt == 2:
                                    raise
                                time.sleep(2)
                        for name in ('releases', 'plan.json', 'publisher.py'):
                            (root/name).rename(backup/name)
                            swapped.append(name)
                            (prepared/name).rename(root/name)
                        with closing(sqlite3.connect(state/'queue-backup.sqlite')) as copy:
                            db.backup(copy)
                    except Exception:
                        for name, archived in retired.items():
                            if publisher.retrieve(ftp, name) is None and publisher.retrieve(ftp, archived) is not None:
                                ftp.rename('/public_html/'+archived, '/public_html/'+name)
                        for name in reversed(swapped):
                            if (root/name).exists():
                                (root/name).rename(prepared/name)
                            (backup/name).rename(root/name)
                        for name in reversed(changed):
                            before = originals[name]
                            current = publisher.retrieve(ftp, name)
                            if current == before:
                                continue
                            if current != payload[name]:
                                raise ValueError('Rollback found a concurrent change: ' + name)
                            if before is None:
                                ftp.rename('/public_html/'+name, '/public_html/'+name+'.jaxgo-rollback-'+tag)
                            else:
                                upload(ftp, name, before, tag+'-rollback')
                        with closing(sqlite3.connect(backup/'queue.sqlite')) as copy:
                            copy.backup(db)
                            with closing(sqlite3.connect(state/'queue-backup.sqlite')) as standby:
                                copy.backup(standby)
                        (backup/'rollback-complete').touch()
                        raise
    print(f'Published SEO migration of {bundle.name}; {len(future)} jobs still scheduled')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', type=Path)
    parser.add_argument('--root', type=Path, default=Path('/opt/jaxgo-publisher'))
    parser.add_argument('--state', type=Path, default=Path('/var/lib/jaxgo-publisher'))
    args = parser.parse_args()
    migrate(args.root, args.state, args.stage)
