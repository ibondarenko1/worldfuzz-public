"""WP29 source-input forms against the current M6 organization service."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import unittest

from worldfuzz.platform.contracts import OPERATIONS
from worldfuzz.platform.http import make_server
from worldfuzz.platform.identity import IdentityStore, OrganizationGateway


@unittest.skipUnless(os.environ.get('WORLDFUZZ_UI_PUBLISHED_INPUTS'), 'Requires the retained published WP31 excerpts')
class ImportUITests(unittest.TestCase):
    def test_scoped_file_import_to_common_worker(self):
        inputs=Path(os.environ['WORLDFUZZ_UI_PUBLISHED_INPUTS'])
        provenance=json.loads((inputs/'inputs.json').read_text())
        self.assertEqual(hashlib.sha256((inputs/'sample.mcap').read_bytes()).hexdigest(),provenance['derived_mcap_sha256'])
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);authority=IdentityStore(root/'identity.sqlite3');password=secrets.token_urlsafe(24)
            existing=['get_capabilities','find_resources','compile_goal','prepare_plan','execute_plan','get_run','get_result','get_result_report','get_budget','cancel_run','compare_results']
            authority.provision('org','runner',password,'alpha',root/'alpha/results',root/'alpha/catalog.sqlite3',existing)
            authority.provision('org','importer',password,'alpha',root/'alpha/results',root/'alpha/catalog.sqlite3',list(OPERATIONS))
            authority.provision('org','importer',password,'beta',root/'beta/results',root/'beta/catalog.sqlite3',list(OPERATIONS))
            services=[]
            class TrackedGateway(OrganizationGateway):
                def authenticate(self,token):
                    service=super().authenticate(token);services.append(service);return service
            server=make_server(TrackedGateway(authority));thread=threading.Thread(target=server.serve_forever);thread.start()
            evidence=Path(os.environ.get('WORLDFUZZ_UI_IMPORT_EVIDENCE',temporary));evidence.mkdir(parents=True,exist_ok=True)
            try:
                for principal in ('runner','importer'):
                    process=subprocess.run(['node','tests/test_import_ui.mjs'],cwd=Path(__file__).parents[1],
                        env=os.environ|{'WORLDFUZZ_UI_TEST_URL':f'http://127.0.0.1:{server.server_port}','WORLDFUZZ_UI_TEST_INPUTS':str(inputs),'WORLDFUZZ_UI_TEST_PRINCIPAL':principal,'WORLDFUZZ_UI_TEST_PASSWORD':password,'WORLDFUZZ_UI_TEST_IMPORT':str(int(principal=='importer')),'WORLDFUZZ_CHECK_RECEIPT':str(evidence/(principal+'.json'))},
                        text=True,capture_output=True,timeout=55)
                    self.assertEqual(process.returncode,0,process.stdout+process.stderr);print(process.stdout)
                alpha=authority.session(authority.login('org','importer',password,'alpha',list(OPERATIONS))['token'],launch=False)
                beta=authority.session(authority.login('org','importer',password,'beta',list(OPERATIONS))['token'],launch=False)
                with alpha.corpus.connect() as db:
                    assets=[json.loads(row['body']) for row in db.execute('SELECT body FROM wf_assets')]
                    self.assertEqual(len(assets),2)
                    rows=list(db.execute('SELECT status FROM wf_runs'));self.assertEqual([r['status'] for r in rows],['completed','completed'])
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM episodes').fetchone()[0],0)
                for asset in assets:
                    self.assertEqual(asset['data_license'],provenance['sensor']['data_license'])
                    self.assertEqual(asset['code_license'],provenance['sensor']['code_license'])
                with beta.corpus.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_assets').fetchone()[0],0)
                self.assertEqual(len(json.loads((evidence/'importer.json').read_text())['results']),2)
            finally:
                server.shutdown();server.server_close();thread.join()
                for service in services:
                    for child in service.worker_processes:
                        if child.poll() is None:child.terminate()
                        child.wait(timeout=5)
