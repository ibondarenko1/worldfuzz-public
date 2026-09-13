"""Workspace recovery with real published task artifacts and explicit failure cases."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run
from worldfuzz.platform.workspace import backup_workspace,restore_workspace
from worldfuzz.platform.contracts import Fault


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=Service(self.root/'original',self.root/'original.sqlite3',launch=False)

    def tearDown(self):self.temp.cleanup()

    def job(self,execute=True):
        plan=self.service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',case_indices=[0],max_seconds=30)
        job=self.service.execute_plan(plan['plan_id'],plan['plan_digest'],plan['plan_id'])['run_id']
        if execute:run(self.service,job)
        return job,plan

    def backup(self):
        return backup_workspace(self.service.corpus.path,self.service.root,self.root/'backup')

    def test_restore_preserves_results_artifacts_and_idempotency(self):
        job,plan=self.job();original=self.service.get_result(job)
        data=self.service.artifact(job,'route-0.json');before=self.service.corpus.listing()
        backup=self.backup();self.assertEqual(backup['files'],2)
        receipt=restore_workspace(self.root/'backup',self.root/'restored')
        restored=Service(receipt['artifact_root'],receipt['database'],launch=False)
        self.assertEqual(restored.get_result(job),original)
        self.assertEqual(restored.artifact(job,'route-0.json'),data)
        self.assertEqual(restored.corpus.listing(),before)
        self.assertEqual(restored.execute_plan(plan['plan_id'],plan['plan_digest'],plan['plan_id'])['run_id'],job)
        with self.service.corpus.connect() as db:
            self.assertEqual(db.execute("SELECT value FROM wf_metadata WHERE key='artifact_root'").fetchone()[0],str(self.service.root))
        with self.assertRaises(FileExistsError):restore_workspace(self.root/'backup',self.root/'restored')
        self.assertFalse(any(self.root.glob('workspace-restore-*')))

    def test_active_jobs_and_mismatched_root_block_backup(self):
        self.job(False)
        with self.assertRaises(Fault) as raised:self.backup()
        self.assertEqual(raised.exception.code,'WORKSPACE_BUSY')
        self.assertFalse((self.root/'backup').exists())

    def test_tampered_or_missing_artifact_never_publishes_backup(self):
        job,_=self.job();path=self.service.root/job/'route-0.json'
        path.write_text('corrupted')
        with self.assertRaises(Fault):self.backup()
        path.unlink()
        with self.assertRaises(Fault):self.backup()
        self.assertFalse((self.root/'backup').exists())

    def test_restore_rejects_tampering_and_escaping_manifest_paths(self):
        job,_=self.job();self.backup()
        path=self.root/'backup'/'results'/job/'route-0.json';original=path.read_bytes();path.write_bytes(b'changed')
        with self.assertRaises(Fault):restore_workspace(self.root/'backup',self.root/'restore')
        self.assertFalse((self.root/'restore').exists());path.write_bytes(original)
        manifest=self.root/'backup'/'manifest.json';value=json.loads(manifest.read_text())
        value['files']['../escape']=value['files']['catalog.sqlite3'];manifest.write_text(json.dumps(value))
        with self.assertRaises(Fault):restore_workspace(self.root/'backup',self.root/'restore')
        self.assertFalse((self.root/'escape').exists())

    def test_empty_workspace_cli_and_byte_limit(self):
        with self.assertRaises(Fault):backup_workspace(self.service.corpus.path,self.service.root,self.root/'small',1)
        command=[sys.executable,'-m','worldfuzz','platform','--root',str(self.service.root),'--store',str(self.service.corpus.path),
                 'workspace-backup','--destination',str(self.root/'backup')]
        result=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'],'ok')
        result=subprocess.run([sys.executable,'-m','worldfuzz','platform','workspace-restore',
            '--backup',str(self.root/'backup'),'--destination',str(self.root/'restored')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['data']['jobs_restarted'],0)

    def test_wrong_binding_and_missing_database(self):
        with self.assertRaises(Fault):backup_workspace(self.service.corpus.path,self.root/'wrong',self.root/'backup')
        with self.assertRaises(Fault):backup_workspace(self.root/'missing.sqlite3',self.service.root,self.root/'backup')
        self.assertFalse((self.root/'missing.sqlite3').exists())
