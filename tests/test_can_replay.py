"""WP18 focused checks; authored unit cases are not published replay acceptance."""
from copy import deepcopy
import xml.etree.ElementTree as ET
import unittest
from unittest.mock import Mock, patch

from worldfuzz.entities import validate_entity
from worldfuzz.platform.adapters.can_metadata import load, ROOT, relocated_xml, runtime_pin
from worldfuzz.platform.adapters.can_replay import advance_actions, replay_contract
from worldfuzz.platform.adapters.can_checker import geometry
from worldfuzz.platform.source_contracts import m1_contract
from worldfuzz.platform.contracts import Fault


class CanReplayTests(unittest.TestCase):
    def test_exact_supplement_dimensions_and_unknowns(self):
        manifest, env, version = load()
        self.assertEqual(len(manifest['demos']), 200)
        self.assertEqual(len(manifest['models']), 1)
        model = next(iter(manifest['models'].values()))
        self.assertEqual((model['nq'], model['nv'], model['state_size']), (37, 33, 71))
        self.assertEqual(model['joints'][-1]['state_qpos_slice'], [31, 38])
        self.assertEqual(model['joints'][-1]['state_qvel_slice'], [65, 71])
        self.assertEqual(model['joints'][-1]['name'], 'Can_joint0')
        self.assertTrue(all(d['ep_meta'] is None and not d['ep_meta_present'] for d in manifest['demos'].values()))
        self.assertEqual(env['env_kwargs']['control_freq'], 20)
        self.assertEqual(env['env_kwargs']['controller_configs']['body_parts']['right']['input_ref_frame'], 'world')

    def test_supplement_is_a_new_version_not_an_m1_mutation(self):
        original = m1_contract('manipulation'); result = replay_contract()
        self.assertEqual(original['asset'], result['asset'])
        self.assertNotEqual(original['profile']['profile_id'], result['profile']['profile_id'])
        self.assertIn('original MuJoCo version', result['profile']['unknown_fields'])
        self.assertIsNone(result['profile']['state_schema'][1]['frame'])
        self.assertIsNone(result['profile']['state_schema'][2]['frame'])
        self.assertEqual(result['profile']['action_schema'][0]['frame'], 'world')
        self.assertEqual(m1_contract('manipulation'), original)
        for entity in [result['family'], result['asset'], result['profile'], *result['supplements']]:
            validate_entity(entity)

    def test_one_initial_restore_then_actions_and_cleanup_on_cancel(self):
        env = Mock(); initial = [0.] * 71; actions = [[0.] * 7, [1.] * 7]
        env.sim.get_state.return_value.flatten.return_value.tolist.return_value = initial
        emitted = []
        advance_actions(env, initial, actions, lambda: None, lambda i, s: emitted.append(i))
        self.assertEqual(env.sim.set_state_from_flattened.call_count, 1)
        self.assertEqual(env.step.call_count, 2)
        self.assertEqual(emitted, [0, 1, 2])
        env.reset_mock()
        with self.assertRaises(Fault):
            advance_actions(env, initial, actions, Mock(side_effect=Fault('CANCELLED', 'test')), lambda *a: None)
        self.assertEqual(env.sim.set_state_from_flattened.call_count, 1)
        env.step.assert_not_called()

    def test_geometry_does_not_accept_other_compartment_or_retained_grasp(self):
        center = [.1, .28, .8]; half = [.2, .25]; can = [.2, .4, .85]
        self.assertTrue(geometry(can, center, half, [.2, .4, 1.1])['success'])
        self.assertFalse(geometry(can, center, half, can)['success'])
        self.assertFalse(geometry([0, .4, .85], center, half, [0, .4, 1.1])['success'])
        self.assertFalse(geometry([.1, .4, .85], center, half, [0, 0, 1])['success'])
        with self.assertRaises(Fault):geometry([float('nan'), 0, 0], center, half, [0, 0, 0])

    def test_runtime_xml_changes_only_paths_and_allocator(self):
        try:runtime_pin()
        except Fault as exc:self.skipTest('Optional pinned Can runtime absent: '+str(exc))
        name = next(iter(load()[0]['models']))
        original = ET.fromstring((ROOT/name).read_bytes())
        relocated, mapping = relocated_xml(name); runtime = ET.fromstring(relocated)
        self.assertEqual(runtime.find('size').attrib, {'memory':'64M','nconmax':'5000'})
        runtime.find('size').attrib.clear(); runtime.find('size').attrib.update(original.find('size').attrib)
        for before, after, pinned in zip(original.findall('.//*[@file]'), runtime.findall('.//*[@file]'), mapping):
            self.assertEqual(before.get('file'), pinned['original'])
            after.set('file',before.get('file'))
        self.assertEqual(ET.tostring(original), ET.tostring(runtime))

    def test_missing_windows_dll_blocks_preparation(self):
        import sys
        if sys.platform != 'win32':self.skipTest('Windows-specific DLL capability gate')
        try:runtime_pin()
        except Fault as exc:self.skipTest('Optional pinned Can runtime absent: '+str(exc))
        with patch('pathlib.Path.is_file', return_value=False):
            with self.assertRaises(Fault) as raised:runtime_pin()
        self.assertEqual(raised.exception.code, 'RUNNER_UNSUPPORTED')


if __name__ == '__main__':unittest.main()
