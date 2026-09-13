"""WP13 acceptance on published navigation and actual process limits."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from worldfuzz.platform.service import Service
from worldfuzz.platform.worker import run


class RunnerTests(unittest.TestCase):
    def test_published_navigation_uses_pinned_enforced_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=Service(Path(tmp)/'results',Path(tmp)/'db',launch=False)
            prepared=service.prepare_plan(operation='solve_navigation',profile='movingai.octile.v1',
                                          case_indices=[0],max_seconds=15)
            job=service.execute_plan(prepared['plan_id'],prepared['plan_digest'],'wp13')['run_id']
            run(service,job)
            result=service.get_result(job)['result']
            self.assertEqual(result['execution_status'],'completed',result)
            self.assertEqual(result['records'][0]['evidence'],'independent_grid_outcome')
            self.assertTrue(result['records'][0]['check']['matches_published_optimum'])
            self.assertEqual(prepared['run_plan']['input_versions'][0]['version'],'pinned-original-selection-v1')
            self.assertIn(result['runner']['enforcement']['mechanism'],
                          ('windows_job_object','linux_rlimit_cpu_as'))
            self.assertNotEqual(result['runner']['pid'],os.getpid())
            self.assertTrue(all(child.poll() is not None for child in service.worker_processes))

    def test_unsupported_mandatory_capability_denies_preparation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service=Service(Path(tmp)/'results',Path(tmp)/'db',launch=False)
            response=service.call('prepare_plan',{'operation':'solve_navigation',
                'profile':'movingai.octile.v1','case_indices':[0],
                'required_runner_capabilities':['network_isolation']})
            self.assertEqual(response['error']['code'],'RUNNER_UNSUPPORTED')
            with service.corpus.connect() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],0)

    def test_native_memory_limit_rejects_allocation(self):
        code="""from worldfuzz.platform.runner import install_limits
import os
print('LIMIT_PROBE_PID='+str(os.getpid()),flush=True)
install_limits({'cpu_seconds':3,'memory_bytes':128*1024*1024,'wall_seconds':4,'artifact_bytes':0})
try:
    value=bytearray(256*1024*1024)
except MemoryError:
    print('ALLOCATION_BLOCKED',flush=True)
else:
    raise SystemExit('Memory limit was not enforced')
"""
        child=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            stdout,stderr=child.communicate(timeout=6)
            self.assertEqual(child.returncode,0,stderr)
            self.assertIn('ALLOCATION_BLOCKED',stdout)
        finally:
            if child.poll() is None:child.kill()
            child.wait()


if __name__=='__main__':unittest.main()
