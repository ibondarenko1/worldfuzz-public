import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from worldfuzz.platform.agent_acceptance import assess

ROOT=Path(__file__).resolve().parents[1]


class AgentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.manifest=json.loads((ROOT/'examples/wp32-agent-acceptance-20260911/heldout.json').read_text())
        self.task=self.manifest['tasks'][0]
        self.hash=hashlib.sha256((json.dumps(self.manifest,indent=2)+'\n').encode()).hexdigest()

    def test_frozen_prompts_and_empty_collection_never_pass(self):
        spec=importlib.util.spec_from_file_location('wp32_prepare',ROOT/'scripts/prepare-wp32.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.build(),self.manifest)
        self.assertEqual((ROOT/'examples/wp32-agent-acceptance-20260911/heldout.sha256').read_text().split()[0],self.hash)
        report=assess(self.manifest,[],ROOT)
        self.assertEqual(report['supported_completion_rate'],0)
        self.assertIsNone(report['false_verified_outcomes'])
        self.assertFalse(report['targets_met_from_reviewed_evidence']);self.assertFalse(report['milestone_accepted'])

    def test_scripted_and_changed_manifests_rejected(self):
        item={'task_id':self.task['id'],'assigned_client':self.task['assigned_client'],'attempt':1,
              'manifest_sha256':self.hash,'execution_kind':'scripted'}
        with self.assertRaisesRegex(ValueError,'Scripted'):assess(self.manifest,[item],ROOT)
        item['manifest_sha256']='different'
        with self.assertRaisesRegex(ValueError,'manifest'):assess(self.manifest,[item],ROOT)

    def test_review_requires_real_evidence_identity_and_first_attempt(self):
        # Authored unit receipts, never put in the WP32 records collection.
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'trace.txt').write_text('authored host trace fixture');(p/'service.txt').write_text('authored service receipt fixture')
            def ref(name):return {'path':name,'sha256':hashlib.sha256((p/name).read_bytes()).hexdigest()}
            item={'task_id':self.task['id'],'assigned_client':self.task['assigned_client'],'attempt':1,'manifest_sha256':self.hash,
                'execution_kind':'real_agent','client':{'family':'authored-unit-fixture','version':'0','model':'fixture','inference_mode':'fixture'},
                'host_session_id':'unit-fixture','host_trace':ref('trace.txt'),'service_evidence':ref('service.txt'),
                'operation_choices':[],'errors':[],'interventions':[],'execution':{},'final_response':'unit fixture',
                'cost':{'usd':None,'input_tokens':None,'output_tokens':None,'wall_seconds':1,'worker_cpu_seconds':0},
                'review':{'status':'pending','reviewer':'','host_trace_matches_service':False,'workflow_completed':True,'false_verified_outcome':False,'correct_refusal':False}}
            report=assess(self.manifest,[item],p);self.assertEqual(report['reviewed_tasks'],0);self.assertFalse(report['targets_met_from_reviewed_evidence'])
            (p/'trace.txt').write_text('changed')
            with self.assertRaisesRegex(ValueError,'hash changed'):assess(self.manifest,[item],p)
            item['attempt']=2
            with self.assertRaisesRegex(ValueError,'first'):assess(self.manifest,[item],p)
