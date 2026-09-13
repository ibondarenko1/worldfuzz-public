"""Contract conformance and local authorization gates. No device or provider calls."""
import copy
import unittest
from worldfuzz.entities import EntityError,plan_digest,validate_entity
from worldfuzz.entities.examples import examples
from worldfuzz.entities.schemas import schema
from worldfuzz.entities.compatibility import check_profiles
from worldfuzz.entities.adapter_contract import conformance,require_operation,UnsupportedOperation
from worldfuzz.entities.policy import PolicyAuthority,PolicyDenied

class ReferenceAdapter:
    """Authored conformance fixture; computation intentionally unsupported."""
    def describe(self):
        e=examples('sensors')
        return {'contract_version':'2.0.0','adapter_id':'test','version':'1.0.0',
                'supported_profiles':[{'id':e['EmbodimentProfile']['profile_id'],'version':'1.0.0'}],
                'operations':['inspect'],'input_schema_refs':[{'operation':'inspect','schema_ref':'urn:test:request'}],
                'output_schema_refs':[{'operation':'inspect','schema_ref':schema('AssetVersion')['$id']}],
                'environment_lock':{'id':'test-environment','version':'1.0.0'},'resource_profile':e['RunPlan']['resource_limits'],
                'license':'MIT','verification_method':{'id':'test-verifier','version':'1.0.0'},
                'execution_modes':['metadata_inspection'],'limitations':['Authored metadata-only fixture']}
    def inspect(self,request):return [examples('sensors')['AssetVersion']]
    def check_compatibility(self,request):return check_profiles(request['required'],request['offered'],checked_at=0)
    def prepare(self,request):raise UnsupportedOperation('UNSUPPORTED_OPERATION: prepare; supported: inspect')
    def execute(self,plan,context):raise UnsupportedOperation('UNSUPPORTED_OPERATION: execute; supported: inspect')
    def collect(self,run_id):raise UnsupportedOperation('UNSUPPORTED_OPERATION: collect; supported: inspect')
    def verify(self,request):raise UnsupportedOperation('UNSUPPORTED_OPERATION: verify; supported: inspect')

class ContractGateTests(unittest.TestCase):
    def conformance(self,a):
        return conformance(a,[examples('sensors')['EmbodimentProfile']],{'urn:test:request':{'type':'object','properties':{},'required':[],'additionalProperties':False}},{schema('AssetVersion')['$id']:schema('AssetVersion')})

    def test_v2_conformance_does_not_execute_and_rejects_incomplete_protocol(self):
        report=self.conformance(ReferenceAdapter());self.assertFalse(report['execution_tested']);self.assertTrue(report['metadata_valid'])
        a=ReferenceAdapter();a.execute=None
        with self.assertRaises(EntityError):self.conformance(a)
        a=ReferenceAdapter();a.execute=lambda:None
        with self.assertRaises(EntityError):self.conformance(a)
        with self.assertRaisesRegex(UnsupportedOperation,'supported: inspect'):require_operation(ReferenceAdapter().describe(),'train')

    def test_equal_shapes_do_not_make_actions_compatible(self):
        p=examples('navigation')['EmbodimentProfile'];p['unknown_fields']=[]
        q=copy.deepcopy(p);q['action_schema'][0]['semantic']='velocity setpoint'
        result=check_profiles(p,q,checked_at=0);self.assertEqual(result['status'],'incompatible');self.assertEqual(result['conflicts'][0]['field'],'action_schema')
        self.assertEqual(check_profiles(p,p,checked_at=0)['status'],'compatible')
        p['unknown_fields']=['controller convention'];self.assertEqual(check_profiles(p,p,checked_at=0)['status'],'needs_information')

    def test_sensor_absent_action_never_inferred(self):
        p=examples('sensors')['EmbodimentProfile'];p['unknown_fields']=[]
        q=copy.deepcopy(p);q['action_schema']=copy.deepcopy(q['state_schema'])
        self.assertEqual(check_profiles(p,q,checked_at=0)['status'],'incompatible');self.assertIsNone(p['action_schema'])

    def setup_authority(self):
        e=examples('navigation');plan=e['RunPlan'];grant=e['PolicyGrant'];asset=e['AssetVersion'];decision=e['CompatibilityDecision']
        plan['expires_at']=100;grant['expires_at']=100;grant['allowed_actions']=['execute','verify'];grant['data_scopes'][0]['actions']=['evaluate']
        asset.update(access_status='file_verified',observed_hash_or_null='a'*64,size_or_null=1)
        asset['rights'][0].update(status='verified',license='fixture-only permission',allowed_uses=['evaluate'],evidence_urls=['urn:test:owner-declaration'])
        decision.update(status='compatible',missing_fields=[])
        plan['digest']=plan_digest(plan)
        authority=PolicyAuthority([grant],asset_resolver=lambda r:asset,decision_resolver=lambda r:decision,clock=lambda:10)
        context={'grant_id':grant['grant_id'],'workspace_id':grant['workspace_id'],'principal_id':grant['principal']['principal_id']}
        return authority,plan,grant,asset,decision,context

    def test_scoped_grant_reused_and_revocation_rechecked(self):
        a,p,g,asset,d,context=self.setup_authority();calls=[]
        for _ in range(2):a.execute(p,runner=lambda plan,receipt:calls.append(receipt['plan_digest']),**context)
        self.assertEqual(calls,[p['digest']]*2) # Policy reuse; job deduplication belongs to WP14.
        a.revoke(g['grant_id'])
        with self.assertRaises(PolicyDenied):a.execute(p,runner=lambda *_:calls.append('bad'),**context)
        self.assertEqual(len(calls),2)

    def test_all_denials_happen_before_runner(self):
        for mutate in ('digest','workspace','principal','grant','expired','limit','rights','version','decision','publication'):
            with self.subTest(case=mutate):
                a,p,g,asset,d,c=self.setup_authority();calls=[]
                if mutate=='digest':p['steps'][0]['parameters']=[{'name':'changed','value':1,'unit':None}]
                elif mutate=='workspace':c['workspace_id']='other'
                elif mutate=='principal':c['principal_id']='other'
                elif mutate=='grant':c['grant_id']='forged'
                elif mutate=='expired':a._clock=lambda:100
                elif mutate=='limit':p['resource_limits']['wall_seconds']=31;p['digest']=plan_digest(p)
                elif mutate=='rights':asset['rights'][0].update(status='unknown',allowed_uses=[])
                elif mutate=='version':asset['version']='2.0.0'
                elif mutate=='decision':d['status']='needs_information';d['missing_fields']=['frames']
                elif mutate=='publication':
                    with self.assertRaises(PolicyDenied):a.authorize(p,action='publish',**c)
                    continue
                with self.assertRaises(EntityError):a.execute(p,runner=lambda *_:calls.append('bad'),**c)
                self.assertEqual(calls,[])

    def test_owner_grants_are_copied_and_changed_plan_requires_new_digest(self):
        a,p,g,asset,d,c=self.setup_authority();g['allowed_actions'].append('publish')
        with self.assertRaises(PolicyDenied):a.authorize(p,action='publish',**c)
        p['resource_limits']['wall_seconds']=20
        with self.assertRaises(EntityError):a.authorize(p,action='execute',**c)
        p['digest']=plan_digest(p);self.assertTrue(a.authorize(p,action='execute',**c)['authorized'])
