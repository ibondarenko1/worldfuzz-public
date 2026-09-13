"""Focused checks of the changed example; the kit walkthrough is a required run."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

from worldfuzz.platform.adapters import check_descriptor
from worldfuzz.platform.contracts import Fault

ROOT=Path(__file__).resolve().parents[1]/'examples/adapters/trajectory_extent'
SPEC=importlib.util.spec_from_file_location('wp36_example',ROOT/'trajectory_extent/__init__.py')
EXAMPLE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXAMPLE)


class ScopedReader:
    def __init__(self, asset, denied=False):
        self.asset=asset; self.denied=denied; self.calls=[]
    def invoke(self, operation, **arguments):
        self.calls.append((operation,arguments))
        if self.denied:raise Fault('ACCESS_DENIED','get_asset scope denied')
        return {'asset_id':'fixture','sha256':'unchanged','asset':self.asset}
    def get_asset(self, *args, **kwargs):
        raise AssertionError('Direct method bypassed the dispatcher')


class ExampleTests(unittest.TestCase):
    def setUp(self):
        self.adapter=EXAMPLE.ExtentAdapter()
        self.asset=json.loads((ROOT/'trajectory_extent/sample.json').read_text())
        self.request={'profile':'sensor.xyz.extent.v1','operation':'summarize_trajectory',
                      'parameters':{'asset_id':'fixture'}}

    def test_scoped_reads_bounds_and_collected_artifact(self):
        descriptor=check_descriptor(self.adapter)
        self.assertEqual(descriptor['source_contract']['operations']['summarize_trajectory']['required_rights']['data'],'inspect')
        reader=ScopedReader(self.asset);snapshot=self.adapter.snapshot(reader,self.request)
        records=[];files={};checks=[]
        self.adapter.execute(reader,{'snapshot':snapshot,'request':self.request},records,[],
                             lambda:checks.append(True),lambda name,raw:files.update({name:raw}))
        self.assertEqual(len(reader.calls),2)
        self.assertEqual(records[0]['min_xyz_m'],[2,-3,1]);self.assertEqual(records[0]['max_xyz_m'],[5,6,4])
        self.assertFalse(records[0]['control_outcome_verified'])
        self.assertEqual(json.loads(files['extent.json']),records[0]);self.assertTrue(checks)

    def test_recording_and_unknown_units_refused_in_both_phases(self):
        for bad in ({'import_provenance':{'adapter':'mcap.ros.recording.v1'}},
                    {**self.asset,'units':'unknown'}):
            for execute in (False,True):
                with self.subTest(execute=execute,kind=bad.get('units','recording')):
                    reader=ScopedReader(bad)
                    with self.assertRaises(Fault) as found:
                        if execute:self.adapter.execute(reader,{'snapshot':{},'request':self.request},[],[],lambda:None,lambda *a:None)
                        else:self.adapter.snapshot(reader,self.request)
                    self.assertEqual(found.exception.code,'PROFILE_INCOMPATIBLE')

    def test_revoked_scope_and_changed_snapshot_do_not_produce_records(self):
        reader=ScopedReader(self.asset,denied=True)
        with self.assertRaises(Fault) as denied:self.adapter.snapshot(reader,self.request)
        self.assertEqual(denied.exception.code,'ACCESS_DENIED')
        reader.denied=False
        records=[]
        with self.assertRaises(Fault) as changed:
            self.adapter.execute(reader,{'snapshot':{'asset_id':'fixture','sha256':'old'},'request':self.request},
                                 records,[],lambda:None,lambda *a:self.fail('Unexpected artifact'))
        self.assertEqual(changed.exception.code,'PLAN_CHANGED');self.assertEqual(records,[])


if __name__=='__main__':unittest.main()
