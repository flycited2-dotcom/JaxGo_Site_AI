"""Fault-injection tests. No real network or credentials are used."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('publisher',Path(__file__).with_name('publisher.py'))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)


class FakeFTP:
    def __init__(self):
        self.files={};self.uploads=0;self.crash=False
    def close(self):pass
    def mkd(self,*args):pass
    def cwd(self,*args):pass
    def storbinary(self,cmd,stream):
        self.files[cmd.removeprefix('STOR /public_html/')]=stream.read();self.uploads+=1
    def rename(self,src,dst):
        self.files[dst.removeprefix('/public_html/')]=self.files.pop(src.removeprefix('/public_html/'))
        if self.crash:
            self.crash=False
            raise SystemExit('simulated process killed after rename')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'release';self.state=Path(self.tmp.name)/'state'
        folder=self.root/'releases/A01/articles';folder.mkdir(parents=True)
        data=b'<html>approved article</html>'
        (folder/'one.html').write_bytes(data)
        (folder.parent/'inventory.json').write_text(json.dumps({'articles/one.html':p.digest(data)}))
        (self.root/'baseline-hashes.json').write_text('{}')
        (self.root/'plan.json').write_text(json.dumps([{'id':'A01','url':'/articles/one.html','approved':True,'scheduled_at':'2026-01-01T00:00:00+00:00'}]))
        self.ftp=FakeFTP()
        self.mocks=[patch.object(p,'connect',return_value=self.ftp),
            patch.object(p,'retrieve',side_effect=lambda ftp,name:ftp.files.get(name)),
            patch.object(p,'fetch',side_effect=lambda name:self.ftp.files[name])]
        for m in self.mocks:m.start()
    def tearDown(self):
        for m in self.mocks:m.stop()
        self.tmp.cleanup()
    def test_crash_after_remote_commit_is_reconciled(self):
        self.ftp.crash=True
        with self.assertRaises(SystemExit):p.tick(self.root,self.state)
        before=self.ftp.uploads
        self.assertEqual(p.tick(self.root,self.state)['status'],'published')
        self.assertEqual(self.ftp.uploads,before+1) # receipt only; no second article upload
        self.assertEqual(p.tick(self.root,self.state)['status'],'spacing_wait')
    def test_network_retry_survives_new_dispatcher(self):
        with patch.object(p,'connect',side_effect=OSError('simulated network outage')):
            self.assertEqual(p.tick(self.root,self.state)['status'],'retry_wait')
        self.assertEqual(p.tick(self.root,self.state)['status'],'retry_wait')
        db=p.open_db(self.state);db.execute('UPDATE jobs SET retry_at=0');db.commit();db.close()
        self.assertEqual(p.tick(self.root,self.state)['status'],'published')
    def test_changed_approved_file_stops(self):
        (self.root/'releases/A01/articles/one.html').write_bytes(b'changed')
        self.assertEqual(p.tick(self.root,self.state)['status'],'needs_attention')
        self.assertEqual(self.ftp.uploads,0)
    def test_parallel_run_does_not_publish(self):
        self.state.mkdir()
        with (self.state/'publisher.lock').open('a') as lock:
            p.fcntl.flock(lock,p.fcntl.LOCK_EX|p.fcntl.LOCK_NB)
            self.assertEqual(p.tick(self.root,self.state)['status'],'already_running')
    def test_missing_database_does_not_reset_queue(self):
        self.state.mkdir();(self.state/'initialized').touch()
        with self.assertRaisesRegex(ValueError,'queue_missing'):p.tick(self.root,self.state)


if __name__=='__main__':unittest.main(verbosity=2)
