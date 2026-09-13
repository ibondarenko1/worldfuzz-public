"""Entity contract acceptance and backwards compatibility, not robotics execution."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from worldfuzz.entities import EntityError,validate_entity,loads,plan_digest
from worldfuzz.entities.schemas import PROPERTIES,schema
from worldfuzz.entities.examples import examples

class EntityTests(unittest.TestCase):
    def test_all_entities_in_three_domains_preserve_input(self):
        for domain in ('navigation','manipulation','sensors'):
            values=examples(domain)
            self.assertEqual(len(values),16)
            for name,v in values.items():
                with self.subTest(domain=domain,name=name):
                    before=copy.deepcopy(v);self.assertIs(validate_entity(v,name),v);self.assertEqual(v,before)
                    self.assertEqual(loads(json.dumps(v)),v)
            self.assertIsNone(values['EmbodimentProfile']['action_schema']) if domain=='sensors' else self.assertTrue(values['EmbodimentProfile']['action_schema'])

    def test_missing_unknown_and_wrong_version_rejected_for_every_entity(self):
        for name,source in examples('navigation').items():
            for field in schema(name)['required']:
                v=copy.deepcopy(source);del v[field]
                with self.subTest(name=name,field=field),self.assertRaises(EntityError):validate_entity(v)
            for key,value in [('unexpected',1),('schema_version','2.0.0')]:
                v=copy.deepcopy(source);v[key]=value
                with self.assertRaises(EntityError):validate_entity(v)

    def test_nested_fields_types_and_explicit_unknowns(self):
        v=examples('sensors')['EmbodimentProfile'];v['observation_schema'][0]['guessed_action']=1
        with self.assertRaisesRegex(EntityError,'Unknown field'):validate_entity(v)
        v=examples('navigation')['Run'];v['event_cursor']=True
        with self.assertRaises(EntityError):validate_entity(v)
        v=examples('navigation')['Outcome'];v['unknown_reason']=None
        with self.assertRaises(EntityError):validate_entity(v)
        v=examples('navigation')['AssetVersion'];v['observed_hash_or_null']='a'*64
        with self.assertRaises(EntityError):validate_entity(v)
        v=examples('navigation')['AssetVersion'];v['rights'][0]['allowed_uses']=['train']
        with self.assertRaises(EntityError):validate_entity(v)

    def test_groups_outcomes_and_evidence_cannot_be_upgraded(self):
        v=examples('manipulation')['SplitManifest'];v['group_assignments'].append({'group_id':'example-group','split':'train'})
        with self.assertRaises(EntityError):validate_entity(v)
        v=examples('manipulation')['Outcome'];v.update(verdict='pass',unknown_reason=None)
        with self.assertRaises(EntityError):validate_entity(v)
        v=examples('manipulation')['Evidence'];v['kind']='physical_measurement'
        with self.assertRaises(EntityError):validate_entity(v)

    def test_plan_digest_and_step_order(self):
        v=examples('navigation')['RunPlan'];v['resource_limits']['wall_seconds']=31
        with self.assertRaisesRegex(EntityError,'digest'):validate_entity(v)
        v['digest']=plan_digest(v);validate_entity(v)
        v['steps'][0]['depends_on']=['evaluate'];v['digest']=plan_digest(v)
        with self.assertRaisesRegex(EntityError,'dependencies'):validate_entity(v)

    def test_parser_duplicates_nonfinite_and_non_json(self):
        for raw in ['{"entity_type":"Run","entity_type":"Outcome"}','{"x":NaN}','[]','{']:
            with self.assertRaises(EntityError):loads(raw)
        v=examples('sensors')['Run'];v['started_at']=float('inf')
        with self.assertRaises(EntityError):validate_entity(v)
        v=examples('sensors')['Run'];v['extra']=object()
        with self.assertRaises(EntityError):validate_entity(v)

    def test_cli_does_not_create_catalog_and_keeps_unknown_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);f=root/'entity.json';db=root/'must-not-exist.sqlite3'
            v=examples('sensors')['EmbodimentProfile'];f.write_text(json.dumps(v))
            cmd=[sys.executable,'-m','worldfuzz','platform','--store',str(db),'entity','validate','--json-file',str(f)]
            result=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertFalse(db.exists())
            before=f.read_bytes();v['extra']='do not drop';f.write_text(json.dumps(v));before=f.read_bytes()
            result=subprocess.run(cmd,capture_output=True,text=True);self.assertEqual(result.returncode,1);self.assertEqual(before,f.read_bytes());self.assertFalse(db.exists())
