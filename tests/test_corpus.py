"""Data integrity, leak prevention, and real loopback API contracts."""
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from worldfuzz.corpus import Corpus, import_navigation, import_robomimic
from worldfuzz.dashboard import Dashboard, make_server

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'examples/published-street-cases-20260910'


class CorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Corpus(Path(self.temp.name) / 'corpus.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_reimport_is_idempotent_and_export_retains_real_routes(self):
        self.assertEqual(import_navigation(self.store, SOURCE)['added'], 9)
        self.assertEqual(import_navigation(self.store, SOURCE)['added'], 0)
        reopened = Corpus(self.store.path)
        episodes = [json.loads(line) for line in reopened.export('navigation').splitlines()]
        self.assertEqual(len(episodes),9)
        groups = {}
        for item in episodes:
            ep = item['episode']
            self.assertEqual(len(ep['actions']),len(ep['states'])-1)
            self.assertTrue(ep['check']['matches_published_optimum'])
            groups.setdefault(ep['group_id'],set()).add(item['split'])
        self.assertEqual(len(groups),3)
        self.assertTrue(all(len(splits)==1 for splits in groups.values()))

    def test_conflict_rolls_back_whole_import(self):
        ep = {'source_key':'a','domain':'navigation','task':'test_fixture','status':'STRUCTURE_VALIDATED',
              'label':'fixture','group_id':'shared','states':[[0,0]]}
        self.store.ingest({'name':'fixture'},[ep])
        first = {**ep,'source_key':'b'}
        conflict = {**ep,'states':[[1,1]]}
        with self.assertRaises(ValueError): self.store.ingest({'name':'fixture'},[first,conflict])
        self.assertEqual(len(self.store.listing()['episodes']),1)

    def test_export_rejects_tampering_empty_filters_and_mixed_domains(self):
        import_navigation(self.store,SOURCE)
        with self.assertRaises(ValueError): self.store.export('all')
        with self.assertRaises(ValueError): self.store.export('manipulation')
        with self.store.connect() as db: db.execute("UPDATE episodes SET payload='{}'")
        with self.assertRaises(ValueError): self.store.export('navigation')

    def test_filter_index_cannot_upgrade_verification(self):
        import_navigation(self.store,SOURCE)
        with self.store.connect() as db: db.execute("UPDATE episodes SET status='STRUCTURE_VALIDATED'")
        with self.assertRaises(ValueError): self.store.export('navigation',status='STRUCTURE_VALIDATED')

    def test_hdf5_external_links_are_rejected(self):
        try: import h5py
        except ImportError: self.skipTest('optional h5py unavailable')
        path = Path(self.temp.name)/'external.hdf5'
        with h5py.File(path,'w') as file:
            file['data'] = h5py.ExternalLink('must-not-open.hdf5','/data')
        with self.assertRaises(ValueError):
            import_robomimic(self.store,path,{'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        self.assertEqual(self.store.listing()['episodes'],[])

    def test_modified_route_rejected_without_partial_import(self):
        import shutil
        folder = Path(self.temp.name)/'recording'
        shutil.copytree(SOURCE,folder)
        report = json.loads((folder/'report.json').read_text())
        report['cases'][-1]['route'][1]=[-1,-1]
        (folder/'report.json').write_text(json.dumps(report))
        with self.assertRaises((ValueError,IndexError)): import_navigation(self.store,folder)
        self.assertEqual(self.store.listing()['episodes'],[])

    def test_http_catalog_export_and_import_token(self):
        app = Dashboard(Path(self.temp.name)/'dashboard')
        server = make_server(app,0)
        worker = threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        try:
            with patch('worldfuzz.corpus.Corpus',return_value=self.store):
                connection = http.client.HTTPConnection('127.0.0.1',server.server_port)
                connection.request('POST','/api/data/import-included','{}',{'Content-Type':'application/json'})
                self.assertEqual(connection.getresponse().status,403)
                connection.close()
                connection = http.client.HTTPConnection('127.0.0.1',server.server_port)
                connection.request('POST','/api/data/import-included','{}',{'Content-Type':'application/json','X-WorldFuzz-Token':app.token})
                response=connection.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.loads(response.read())['added'],9)
                connection.request('GET','/api/data');response=connection.getresponse();self.assertEqual(len(json.loads(response.read())['episodes']),9)
                connection.request('GET','/api/data/export?domain=navigation&status=OUTCOME_VERIFIED');response=connection.getresponse();self.assertEqual(response.status,200);self.assertEqual(len(response.read().splitlines()),9)
                connection.request('GET','/data');response=connection.getresponse();self.assertEqual(response.status,200);self.assertIn(b'data.mjs',response.read())
                connection.close()
        finally:
            server.shutdown();server.server_close();worker.join()

    def test_robot_checksum_rejection(self):
        try: import h5py
        except ImportError: self.skipTest('optional h5py unavailable')
        file = Path(self.temp.name)/'invalid.hdf5';file.write_bytes(b'not a dataset')
        with self.assertRaises(ValueError): import_robomimic(self.store,file,{'sha256':'0'*64})


if __name__ == '__main__': unittest.main()
