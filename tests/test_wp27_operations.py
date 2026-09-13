import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from worldfuzz.platform.contracts import OPERATIONS
from worldfuzz.platform.identity import IdentityStore
from worldfuzz.platform.service import Service
from test_wp25_organizations import PLAN


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.authority=IdentityStore(self.base/'authority.sqlite3');self.password=secrets.token_urlsafe(24)
        self.authority.provision('ops','owner',self.password,'workspace',self.base/'results',self.base/'catalog.sqlite3',list(OPERATIONS))
        token=self.authority.login('ops','owner',self.password,'workspace',list(OPERATIONS))['token']
        self.service=self.authority.session(token)

    def tearDown(self):
        for child in self.service.worker_processes:child.wait(timeout=40)
        self.temp.cleanup()

    def ok(self,response):
        self.assertEqual(response['status'],'ok',response);return response['data']

    def run_one(self):
        p=self.ok(self.service.call('prepare_plan',PLAN))
        run=self.ok(self.service.call('execute_plan',{'plan_id':p['plan_id'],'plan_digest':p['plan_digest'],'idempotency_key':'published'}))
        for child in self.service.worker_processes:child.wait(timeout=40)
        result=self.ok(self.service.call('get_result',{'result_id':run['run_id']}))
        self.assertEqual(result['result']['execution_status'],'completed')
        return run['run_id'],result

    def test_backup_restore_access_history_blobs_and_retention_recovery(self):
        service=self.service;run_id,result=self.run_one()
        audit=self.ok(service.call('get_run_audit',{'run_id':run_id}))
        self.assertEqual([r['outcome'] for r in audit['entries']],['queued','running','completed'])
        backup=self.ok(service.call('create_backup',{'max_mib':16}))
        restored=self.ok(service.call('restore_backup',{'backup_id':backup['backup_id'],'max_mib':16}))
        self.assertNotEqual(Path(restored['database']),service.corpus.path)
        token=self.authority.login('ops','owner',self.password,restored['workspace_id'],list(OPERATIONS))['token']
        recovered=self.authority.session(token,launch=False)
        self.assertEqual(self.ok(recovered.call('get_result',{'result_id':run_id})),result)
        self.assertEqual(self.ok(recovered.call('get_run_audit',{'run_id':run_id})),audit)
        with service.corpus.connect() as a,recovered.corpus.connect() as b:
            for table in ('datasets','episodes','corpus_group_splits','wf_runs','wf_events','wf_attempts','wf_attempt_artifacts','wf_usage'):
                self.assertEqual([tuple(r) for r in a.execute('SELECT * FROM '+table+' ORDER BY rowid')],
                                 [tuple(r) for r in b.execute('SELECT * FROM '+table+' ORDER BY rowid')])
        # Delete only this disposable source instance, after validating its backup.
        args={'run_ids':[run_id],'before':time.time()+1,'dry_run':True}
        self.assertEqual(self.ok(service.call('apply_retention',args))['state'],'preview')
        with patch.object(Path,'unlink',side_effect=OSError('simulated interrupted deletion')):
            self.assertEqual(service.call('apply_retention',{**args,'dry_run':False})['status'],'error')
        self.assertEqual(service.call('get_result',{'result_id':run_id})['error']['code'],'RETENTION_PENDING')
        self.assertEqual(service.call('create_backup',{'max_mib':16})['error']['code'],'WORKSPACE_BUSY')
        removed=self.ok(service.call('apply_retention',{**args,'dry_run':False}))
        self.assertGreater(removed['deleted_files'],0)
        self.assertEqual(self.ok(service.call('apply_retention',{**args,'dry_run':False}))['deleted_files'],0)
        self.assertEqual(service.call('get_result',{'result_id':run_id})['error']['code'],'RESULT_RETAINED_OUT')
        self.assertEqual(self.ok(recovered.call('get_result',{'result_id':run_id})),result)
        self.assertEqual(self.ok(service.call('get_service_metrics'))['retained_out_runs'],1)
        self.ok(service.call('create_backup',{'max_mib':16}))
        if os.environ.get('WORLDFUZZ_CHECK_RECEIPT'):
            Path(os.environ['WORLDFUZZ_CHECK_RECEIPT']).write_text(json.dumps({'run_id':run_id,
                'result_sha256':result['result_sha256'],'backup':backup,'restored_workspace':restored['workspace_id'],
                'restored_files_verified':restored['files_verified'],'retention':removed,
                'worker_pids':[x.pid for x in service.worker_processes],'exit_codes':[x.returncode for x in service.worker_processes]},indent=2))

    def test_unknown_run_blocks_backup_until_reconciled(self):
        service=self.service;service.launch=False
        p=self.ok(service.call('prepare_plan',PLAN));run=self.ok(service.call('execute_plan',{
            'plan_id':p['plan_id'],'plan_digest':p['plan_digest'],'idempotency_key':'unstarted'}))
        self.assertEqual(service.call('create_backup',{'max_mib':16})['error']['code'],'WORKSPACE_BUSY')
        service.jobs.claim(run['run_id'])
        with service.corpus.connect() as db:db.execute('UPDATE wf_attempts SET lease_until=0 WHERE run_id=?',(run['run_id'],))
        reconciled=self.ok(service.call('reconcile_run',{'run_id':run['run_id']}))
        self.assertEqual(reconciled['status'],'incomplete')
        self.ok(service.call('create_backup',{'max_mib':16}))
        self.assertEqual(service.call('resume_run',{'run_id':run['run_id'],'idempotency_key':'unknown-retry'})['error']['code'],'BUDGET_EXCEEDED')

    def test_completed_expired_plan_can_be_backed_up(self):
        self.run_one()
        self.service.clock=lambda:time.time()+3600
        backup=self.ok(self.service.call('create_backup',{'max_mib':16}))
        self.assertEqual(backup['files'],2)

    def test_backup_keeps_collected_partial_not_present_in_final_result(self):
        service=self.service;service.launch=False
        p=self.ok(service.call('prepare_plan',PLAN));run=self.ok(service.call('execute_plan',{
            'plan_id':p['plan_id'],'plan_digest':p['plan_digest'],'idempotency_key':'partial'}))['run_id']
        service.jobs.claim(run)
        item=service.jobs.collect(run,'partial.json',b'{"scope":"partial-test"}',1024)
        service.finish(run,'failed',{'records':[]},'interrupted before checkpoint')
        backup=self.ok(service.call('create_backup',{'max_mib':16}))
        restored=self.ok(service.call('restore_backup',{'backup_id':backup['backup_id'],'max_mib':16}))
        path=Path(restored['artifact_root'])/run/item['storage_name']
        self.assertEqual(path.read_bytes(),b'{"scope":"partial-test"}')
        with service.corpus.connect() as db:
            db.execute('CREATE TABLE wf_suites(id TEXT PRIMARY KEY,body TEXT,sha256 TEXT)')
            db.execute('INSERT INTO wf_suites VALUES(?,?,?)',('held',json.dumps({'result_ref':run}),'reference-only retention fixture'))
        self.assertEqual(service.call('apply_retention',{'run_ids':[run],'before':time.time()+1,'dry_run':False})['error']['code'],'RETENTION_PROTECTED')

    def test_restore_preserves_revoked_owner_and_nested_history_scope(self):
        self.ok(self.service.call('prepare_plan',PLAN))
        self.service.authorization.revoke()
        backup=self.ok(self.service.call('create_backup',{'max_mib':16}))
        restored=self.ok(self.service.call('restore_backup',{'backup_id':backup['backup_id'],'max_mib':16}))
        token=self.authority.login('ops','owner',self.password,restored['workspace_id'],list(OPERATIONS))['token']
        recovered=self.authority.session(token,launch=False)
        self.assertEqual(recovered.call('prepare_plan',PLAN)['error']['code'],'ACCESS_DENIED')
        with recovered.corpus.connect() as db:
            self.assertEqual(db.execute('SELECT revoked FROM wf_policy_grants WHERE grant_id=?',(recovered.authorization.grant_id,)).fetchone()[0],1)
        backup2=self.ok(recovered.call('create_backup',{'max_mib':16}))
        restored2=self.ok(recovered.call('restore_backup',{'backup_id':backup2['backup_id'],'max_mib':16}))
        token2=self.authority.login('ops','owner',self.password,restored2['workspace_id'],list(OPERATIONS))['token']
        recovered2=self.authority.session(token2,launch=False)
        self.assertEqual(set(recovered2.identity.bound['restored_from']),{'workspace',restored['workspace_id']})
        self.assertEqual(recovered2.call('prepare_plan',PLAN)['error']['code'],'ACCESS_DENIED')


if __name__=='__main__':unittest.main()
