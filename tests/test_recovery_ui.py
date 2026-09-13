"""WP29 recovery controls, current workspace authority, and one real resumed worker."""
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


class RecoveryUITests(unittest.TestCase):
    def test_explicit_scoped_recovery_and_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);authority=IdentityStore(root/'identity.sqlite3');password=secrets.token_urlsafe(24)
            existing=['get_capabilities','find_resources','compile_goal','prepare_plan','execute_plan','get_run','get_result','get_result_report','get_budget','cancel_run','compare_results']
            for principal,operations in [('reader',existing),('recoverer',list(OPERATIONS))]:
                authority.provision('org',principal,password,'alpha',root/'alpha/results',root/'alpha/catalog.sqlite3',operations)
            authority.provision('org','recoverer',password,'beta',root/'beta/results',root/'beta/catalog.sqlite3',list(OPERATIONS))
            services=[];injected=[];signal=root/'revoke.signal'
            class InterruptedGateway(OrganizationGateway):
                def authenticate(self,token):
                    if signal.exists():authority.revoke(token)
                    service=super().authenticate(token);services.append(service)
                    ordinary_execute=service.execute_plan
                    def execute_with_authored_interruption(**request):
                        # Controlled pre-worker interruption; the retry uses the real runner.
                        service.launch=False
                        try:run=ordinary_execute(**request)
                        finally:service.launch=True
                        identifier=run['run_id']
                        if identifier not in injected:
                            injected.append(identifier);attempt=service.jobs.claim(identifier)
                            service.budgets.sample(identifier,'search',{'cpu_seconds':.2,'wall_seconds':.3})
                            artifact=service.jobs.collect(identifier,'interruption.json',b'{"scope":"authored interruption diagnostic"}',1024)
                            if len(injected)<3:
                                service.finish(identifier,'incomplete',{'records':[],'artifacts':[artifact]},'AUTHORED_METERED_INTERRUPTION')
                            else:
                                # Authored expired lease, no physical worker was killed in this test.
                                with service.corpus.connect() as db:db.execute('UPDATE wf_attempts SET lease_until=0 WHERE attempt_id=?',(attempt['attempt_id'],))
                        return service.get_run(identifier)
                    service.execute_plan=execute_with_authored_interruption
                    return service
            server=make_server(InterruptedGateway(authority));thread=threading.Thread(target=server.serve_forever);thread.start()
            evidence=Path(os.environ.get('WORLDFUZZ_UI_RECOVERY_EVIDENCE',temporary));evidence.mkdir(parents=True,exist_ok=True)
            try:
                for mode in ('restricted','full'):
                    process=subprocess.run(['node','tests/test_recovery_ui.mjs'],cwd=Path(__file__).parents[1],
                        env=os.environ|{'WORLDFUZZ_UI_TEST_URL':f'http://127.0.0.1:{server.server_port}','WORLDFUZZ_UI_TEST_PASSWORD':password,'WORLDFUZZ_UI_RECOVERY_MODE':mode,'WORLDFUZZ_UI_REVOKE_SIGNAL':str(signal),'WORLDFUZZ_CHECK_RECEIPT':str(evidence/(mode+'.json'))},text=True,capture_output=True,timeout=45)
                    self.assertEqual(process.returncode,0,process.stdout+process.stderr);print(process.stdout)
                session=authority.session(authority.login('org','recoverer',password,'alpha',list(OPERATIONS))['token'],launch=False)
                with session.corpus.connect() as db:
                    self.assertEqual([r['status'] for r in db.execute('SELECT status FROM wf_runs ORDER BY created')],['incomplete','completed','incomplete'])
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_budget_reservations').fetchone()[0],3)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_attempts').fetchone()[0],4)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_retries').fetchone()[0],1)
                beta=authority.session(authority.login('org','recoverer',password,'beta',list(OPERATIONS))['token'],launch=False)
                with beta.corpus.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],0)
                workers=[child for service in services for child in service.worker_processes];self.assertEqual(len(workers),1)
                self.assertTrue(all((session.root/identifier/'interruption.json').exists() for identifier in injected))
            finally:
                server.shutdown();server.server_close();thread.join()
                for service in services:
                    for child in service.worker_processes:
                        if child.poll() is None:child.terminate()
                        child.wait(timeout=5)
