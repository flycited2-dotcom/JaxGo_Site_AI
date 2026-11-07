"""Persistent, single-writer JAXGO publisher. Python standard library, Linux VPS."""
import argparse
import contextlib
import datetime as dt
import fcntl
import ftplib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import sqlite3
import tempfile
import time
import urllib.request

UTC = dt.timezone.utc


def digest(data):
    return hashlib.sha256(data).hexdigest()


def connect():
    password = os.environ.get('JAXGO_FTP_PASSWORD')
    if not password:
        raise ValueError('credentials_missing')
    user = os.environ.get('JAXGO_FTP_USER')
    if not user:
        configured_user = Path('/opt/jaxgo-publisher/ftp-user')
        if not configured_user.is_file():
            raise ValueError('ftp_user_missing')
        user = configured_user.read_text(encoding='utf-8').strip()
    if not user:
        raise ValueError('ftp_user_missing')
    ftp = ftplib.FTP(timeout=30)
    ftp.connect(os.environ.get('JAXGO_FTP_HOST', 'ftp.jaxgo.ru'))
    ftp.login(user, password)
    return ftp


def retrieve(ftp, name):
    data = io.BytesIO()
    try:
        ftp.retrbinary('RETR /public_html/'+name, data.write)
    except ftplib.error_perm as exc:
        if str(exc).startswith('550'):
            # Distinguish missing file from missing read permission.
            parent, _, leaf = name.rpartition('/')
            listing = ftp.nlst('/public_html/'+parent)
            if not any(x.rsplit('/', 1)[-1] == leaf for x in listing):
                return None
        raise
    return data.getvalue()


def fetch(name):
    request = urllib.request.Request('https://jaxgo.ru/'+name+'?jaxgo_verify='+str(time.time_ns()),
        headers={'Cache-Control':'no-cache', 'User-Agent':'JAXGO-Publisher/1.0'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        return response.read()


def make_directories(ftp, files):
    folders = {parent.as_posix() for name in files for parent in PurePosixPath(name).parents if parent.name}
    for folder in sorted(folders, key=lambda value: (value.count('/'), value)):
        remote = '/public_html/' + folder
        try:
            ftp.mkd(remote)
        except ftplib.error_perm:
            ftp.cwd(remote)
            ftp.cwd('/')


def install_import(root, state, staged):
    """Append reviewed inbox releases to a migrated plan under the publisher lock."""
    with (state/'publisher.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current_file = root/'plan.json'
        current = json.loads(current_file.read_text(encoding='utf-8'))
        proposed = json.loads((staged/'plan.json').read_text(encoding='utf-8'))
        if not current or proposed[:len(current)] != current:
            raise ValueError('Import must append to the installed plan')
        last = Path('releases')/current[-1]['id']/'inventory.json'
        if json.loads((root/last).read_bytes()) != json.loads((staged/last).read_bytes()):
            raise ValueError('Existing article sources or site assets changed; review as a separate release')
        additions = proposed[len(current):]
        ids = {job['id'] for job in current}
        urls = {job['url'] for job in current}
        latest = dt.datetime.fromisoformat(current[-1]['scheduled_at'])
        with tempfile.TemporaryDirectory(prefix='.article-import-', dir=root) as temp:
            pending = Path(temp)
            for job in additions:
                aid = job['id']
                url = job['url']
                due = dt.datetime.fromisoformat(job['scheduled_at'])
                if (not re.fullmatch(r'md-[a-z0-9-]+', aid) or url != f'/articles/{aid[3:]}/'
                        or aid in ids or url in urls or due <= latest or job['approved'] is not True):
                    raise ValueError('Invalid appended article job')
                ids.add(aid)
                urls.add(url)
                latest = due
                source = staged/'releases'/aid
                target = root/'releases'/aid
                inventory_file = (source/'inventory.json').read_bytes()
                inventory = json.loads(inventory_file)
                for name, checksum in inventory.items():
                    relative = PurePosixPath(name)
                    if relative.is_absolute() or '..' in relative.parts or digest((source/name).read_bytes()) != checksum:
                        raise ValueError('Invalid staged inventory: '+aid)
                if target.exists():
                    if (json.loads((target/'inventory.json').read_bytes()) != inventory
                            or any(digest((target/name).read_bytes()) != checksum for name, checksum in inventory.items())):
                        raise ValueError('Conflicting release directory: '+aid)
                    continue
                shutil.copytree(source, pending/aid)
            if additions:
                for folder in pending.iterdir():
                    folder.rename(root/'releases'/folder.name)
                plan = pending/'plan.json'
                plan.write_bytes((staged/'plan.json').read_bytes())
                plan.chmod(current_file.stat().st_mode & 0o777)
                plan.replace(current_file)
        return {'status': 'imported', 'jobs': [job['id'] for job in additions]}


def put(ftp, db, state, name, data, release):
    """Journal intent before mutation; reconcile remote bytes after any crash."""
    row = db.execute('SELECT expected FROM files WHERE path=?', (name,)).fetchone()
    remote = retrieve(ftp, name)
    wanted = digest(data)
    if remote is not None and digest(remote) == wanted:
        db.execute('UPDATE files SET committed=? WHERE path=?', (wanted, name))
        db.commit()
        return
    current_hash = digest(remote) if remote is not None else 'absent'
    known = db.execute('SELECT committed FROM files WHERE path=?', (name,)).fetchone()
    allowed = {row[0] if row else current_hash, known[0] if known else current_hash}
    if current_hash not in allowed:
        raise ValueError('remote_conflict:'+name)
    if not row:
        backup = state/'backups'/name
        backup.parent.mkdir(parents=True, exist_ok=True)
        if remote is not None and not backup.exists():
            with backup.open('xb') as stream:
                stream.write(remote)
                stream.flush()
                os.fsync(stream.fileno())
        db.execute('INSERT INTO files(path,expected,committed) VALUES(?,?,?)', (name,current_hash,current_hash))
        db.commit()
    # Pending hash survives failure after rename but before local acknowledgement.
    db.execute('UPDATE files SET expected=? WHERE path=?', (wanted,name))
    db.commit()
    temp = name+'.jaxgo-'+release+'.tmp'
    ftp.storbinary('STOR /public_html/'+temp, io.BytesIO(data))
    if retrieve(ftp, temp) != data:
        raise ValueError('upload_verification_failed:'+name)
    ftp.rename('/public_html/'+temp, '/public_html/'+name)
    if retrieve(ftp, name) != data:
        raise ValueError('rename_verification_failed:'+name)
    db.execute('UPDATE files SET committed=? WHERE path=?', (wanted,name))
    db.commit()


def open_db(state):
    state.mkdir(parents=True, exist_ok=True)
    if (state/'initialized').exists() and not (state/'queue.sqlite').exists():
        raise ValueError('queue_missing_restore_backup')
    db = sqlite3.connect(state/'queue.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, due REAL, state TEXT,
      attempts INTEGER DEFAULT 0, retry_at REAL DEFAULT 0, published REAL, error TEXT, stage TEXT);
    CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, expected TEXT, committed TEXT);
    CREATE TABLE IF NOT EXISTS events(at REAL, job TEXT, kind TEXT);
    ''')
    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise ValueError('queue_corrupt_restore_backup')
    (state/'initialized').touch(exist_ok=True)
    return db


def tick(root, state):
    state.mkdir(parents=True, exist_ok=True)
    with (state/'publisher.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status':'already_running'}
        db = open_db(state)
        plan = json.loads((root/'plan.json').read_text())
        for item in plan:
            db.execute('INSERT OR IGNORE INTO jobs(id,due,state) VALUES(?,?,?)',
                (item['id'], dt.datetime.fromisoformat(item['scheduled_at']).timestamp(), 'scheduled'))
        db.commit()
        now = time.time()
        if db.execute("SELECT 1 FROM jobs WHERE state='needs_attention' LIMIT 1").fetchone():
            return {'status':'needs_attention'}
        # Incomplete operations take precedence even when a newer job is due.
        job = db.execute("SELECT id,state,attempts,retry_at FROM jobs WHERE state IN ('running','retry_wait') ORDER BY due LIMIT 1").fetchone()
        if not job:
            last = db.execute('SELECT MAX(published) FROM jobs').fetchone()[0]
            if last and now-last < 86400:
                return {'status':'spacing_wait'}
            job = db.execute("SELECT id,state,attempts,retry_at FROM jobs WHERE state='scheduled' AND due<=? ORDER BY due LIMIT 1", (now,)).fetchone()
        if not job:
            return {'status':'idle'}
        aid, status, attempts, retry_at = job
        if retry_at > now:
            return {'status':'retry_wait','id':aid,'next_attempt_at':retry_at}
        item = next(x for x in plan if x['id'] == aid)
        if not item.get('approved'):
            return {'status':'not_approved','id':aid}
        try:
            bundle = root/'releases'/aid
            inventory = json.loads((bundle/'inventory.json').read_text())
            for name, checksum in inventory.items():
                if digest((bundle/name).read_bytes()) != checksum:
                    raise ValueError('approved_content_changed:'+name)
            db.execute("UPDATE jobs SET state='running',stage='connecting' WHERE id=?", (aid,))
            db.commit()
            with contextlib.closing(connect()) as ftp:
                make_directories(ftp, inventory)
                baseline=json.loads((root/'baseline-hashes.json').read_text())
                for name,checksum in baseline.items():
                    if not db.execute('SELECT 1 FROM files WHERE path=?',(name,)).fetchone():
                        current=retrieve(ftp,name)
                        if current is None or digest(current)!=checksum:
                            raise ValueError('remote_conflict:'+name)
                # Upload targets before pages and sitemaps that link to them.
                for name in inventory:
                    db.execute('UPDATE jobs SET stage=? WHERE id=?', (name,aid))
                    db.commit()
                    put(ftp,db,state,name,(bundle/name).read_bytes(),aid)
                    if digest(fetch(name)) != inventory[name]:
                        raise ValueError('https_verification_failed:'+name)
                receipt = json.dumps({'id':aid,'inventory':inventory},sort_keys=True).encode()
                put(ftp,db,state,'articles/release-receipt.json',receipt,aid)
            now = time.time()
            db.execute("UPDATE jobs SET state='published',published=?,error=NULL,stage='complete' WHERE id=?", (now,aid))
            db.execute('INSERT INTO events VALUES(?,?,?)',(now,aid,'published'))
            db.commit()
            with sqlite3.connect(state/'queue-backup.sqlite') as backup:
                db.backup(backup)
            return {'status':'published','id':aid,'url':item['url']}
        except Exception as exc:
            # Avoid exception messages from transport libraries: may contain secrets.
            safe = str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            delays = [60,300,900,1800,3600]
            delay = delays[min(attempts,len(delays)-1)] + random.randrange(0,20)
            permanent = safe.startswith(('remote_conflict','approved_content_changed'))
            if isinstance(exc,ftplib.error_perm) and str(exc).startswith('530'):
                safe = 'authentication_failed'
                permanent = True
            db.execute('UPDATE jobs SET state=?,attempts=attempts+1,retry_at=?,error=? WHERE id=?',
                ('needs_attention' if permanent else 'retry_wait', now+delay,safe,aid))
            db.execute('INSERT INTO events VALUES(?,?,?)',(now,aid,safe))
            db.commit()
            return {'status':'needs_attention' if permanent else 'retry_wait','id':aid,'error':safe,'retry_seconds':delay}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('/opt/jaxgo-publisher'))
    parser.add_argument('--state',type=Path,default=Path('/var/lib/jaxgo-publisher'))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--status',action='store_true')
    mode.add_argument('--install-import', type=Path, help='Append reviewed releases from a staged build')
    args = parser.parse_args()
    if args.install_import:
        print(json.dumps(install_import(args.root, args.state, args.install_import), ensure_ascii=False))
    elif args.status:
        db = open_db(args.state)
        rows = db.execute('SELECT id,state,due,attempts,error,published FROM jobs ORDER BY due').fetchall()
        print(json.dumps(rows,ensure_ascii=False))
    else:
        result = tick(args.root,args.state)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        health = args.state/'health.json'
        temp = health.with_suffix('.tmp')
        temp.write_text(json.dumps({'checked_at':dt.datetime.now(UTC).isoformat(),**result}))
        os.replace(temp,health)


if __name__ == '__main__':
    main()
