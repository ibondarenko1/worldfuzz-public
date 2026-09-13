"""Changed integration only. No inference, Docker or cloud requests."""
import importlib.util
import json
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


recipe=module('competition',Path(__file__).resolve().parents[1]/'deployment/nebius/competition.py')
jobs=module('render_job',Path(__file__).resolve().parents[1]/'deployment/nebius/render_job.py')


class CompetitionRecipe(unittest.TestCase):
    def test_new_job_entrypoint_with_explicit_mocked_provider_and_real_common_worker(self):
        # Diagnostic of the new composition, never live NVIDIA/Nebius evidence.
        with tempfile.TemporaryDirectory() as folder:
            prepared=recipe.prepare(Path(folder)/'prepare')
            value={'worldfuzz_version':recipe.VERSION,'model':recipe.MODEL,'idempotency_key':recipe.KEY,
                   'resource':prepared['resource'],'proposal':{'algorithm':'astar','reason':'goal_directed_search'},
                   'provenance':{'source':'live_api','model':recipe.MODEL,'completion_request_attempted':True,
                                 'usage_state':'known','validation_state':'validated','fixture':True}}
            value['goal']=recipe.validate_proposal(value['proposal'])
            with contextlib.redirect_stdout(io.StringIO()):receipt=recipe.execute(value,Path(folder)/'execution')
            outcome=receipt['workflow'];self.assertEqual(outcome['run']['status'],'completed')
            result=outcome['result']['result'];self.assertEqual(result['records'][0]['case_index'],8)
            self.assertEqual(result['verification']['proven_result'],'no_violation_found')
            self.assertEqual(result['verification']['performed_checks'][-1]['mode'],'metric_recompute')
            self.assertFalse(receipt['cloud_job_identity_verified']);self.assertFalse(receipt['physical_replay'])
            self.assertEqual(len(receipt['audit']['attempts']),1)
            import os
            self.assertNotEqual(receipt['audit']['attempts'][0]['pid'],os.getpid())
            out=os.environ.get('WORLDFUZZ_RECIPE_DIAGNOSTIC')
            if out:
                Path(out).write_text(json.dumps({'classification':'LOCAL_DIAGNOSTIC_WITH_MOCKED_PROVIDER',
                    'provider_calls':0,'cloud_jobs':0,'run_id':outcome['run_id'],'case_index':8,
                    'verification':result['verification'],'worker_pid':receipt['audit']['attempts'][0]['pid']},indent=2)+'\n',encoding='utf-8')

    def test_offline_prepare_and_common_dispatcher_source_and_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared=recipe.prepare(Path(folder)/'prepare')
            self.assertEqual(prepared['resource']['id'],'movingai:8');self.assertEqual(prepared['resource']['case_index'],8)
            self.assertEqual(prepared['model_calls'],0);self.assertLessEqual(prepared['prompt_bytes'],8192)
            authority,token,service=recipe.workspace(Path(folder)/'check',False)
            try:
                goal=recipe.validate_proposal({'algorithm':'astar','reason':'goal_directed_search'})
                result=recipe.call(service,'compile_goal',goal=goal)
                self.assertEqual(result['status'],'ready')
                with service.corpus.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM wf_runs').fetchone()[0],0)
                authority.revoke(token)
                with self.assertRaises(recipe.Fault) as error:recipe.call(service,'compile_goal',goal=goal)
                self.assertEqual(error.exception.code,'ACCESS_DENIED')
            finally:authority.revoke(token)

    def test_one_attempt_even_after_uncertain_inference_and_no_implicit_allowance(self):
        with tempfile.TemporaryDirectory() as folder:
            prepared=recipe.prepare(Path(folder)/'prepare');output=Path(folder)/'output'
            class Planner:
                calls=0
                def _complete(self,*args):
                    self.calls+=1
                    raise recipe.NebiusError('transport_error','Uncertain request',{'request_state':'possibly_sent','usage_state':'unknown'})
            planner=Planner();authorization={'approved':True,'request_sha256':recipe.sha(recipe.encoded(prepared)),
                'max_requests':1,'max_completion_tokens':512,'maximum_usd':1,'rates_and_project_confirmed':True}
            with self.assertRaises(ValueError):recipe.propose(prepared,{},output,planner)
            self.assertEqual(planner.calls,0)
            with self.assertRaises(recipe.NebiusError):recipe.propose(prepared,authorization,output,planner)
            with self.assertRaises(FileExistsError):recipe.propose(prepared,authorization,output,planner)
            self.assertEqual(planner.calls,1)
            self.assertEqual(json.loads((output/'inference-attempt.json').read_text())['usage_state'],'unknown')

    def test_no_model_budget_task_or_verifier_substitution(self):
        for proposal in ({'algorithm':'other','reason':'goal_directed_search'},
                         {'algorithm':'astar','reason':'goal_directed_search','max_seconds':180},
                         {'algorithm':'astar','reason':'uninformed_reference'}):
            with self.assertRaises(ValueError):recipe.validate_proposal(proposal)
        value={'worldfuzz_version':recipe.VERSION,'model':recipe.MODEL,'idempotency_key':recipe.KEY,
               'proposal':{'algorithm':'astar','reason':'goal_directed_search'}}
        value['goal']=recipe.validate_proposal(value['proposal']);value['goal']['resource_limits']['cpu_seconds']=21
        with self.assertRaises(ValueError):recipe.execute(value,'unused-workspace')
        value['goal']=recipe.validate_proposal(value['proposal']);value['provenance']={'source':'contract_only'}
        with self.assertRaises(ValueError):recipe.execute(value,'unused-workspace')

    def test_job_payload_has_provider_minimum_timeout_and_no_secrets(self):
        value=jobs.render('registry.example/worldfuzz@sha256:'+'a'*64,'project-fixture','subnet-fixture',b'{"fixture":true}')
        self.assertEqual(value['spec']['timeout'],'3600s');self.assertEqual(value['spec']['preset'],'4vcpu-16gb')
        self.assertIn('300s',value['spec']['args']);self.assertNotIn('registryCredentials',value['spec'])
        self.assertNotIn('NEBIUS_API_KEY',json.dumps(value))
        with self.assertRaises(ValueError):jobs.render('python:latest','project-fixture','subnet-fixture',b'{}')
        with self.assertRaises(ValueError):jobs.render('x@sha256:'+'a'*64,'project-fixture','subnet-fixture',b'x'*65537)


if __name__=='__main__':unittest.main()
