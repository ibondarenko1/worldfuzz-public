"""Regression for malformed compatibility requests, without database access."""
import copy
import unittest
from worldfuzz.entities import EntityError
from worldfuzz.entities.examples import examples
from worldfuzz.entities.compatibility import check_compatibility


class CompatibilityErrorTests(unittest.TestCase):
    def arguments(self):
        e=examples('navigation')
        return {'required':e['EmbodimentProfile'],'offered':copy.deepcopy(e['EmbodimentProfile']),
                'assets':[e['AssetVersion']],
                'required_rights':{'data':'evaluate','code':None,'model':None,'scene':None},
                'required_resources':copy.deepcopy(e['RunPlan']['resource_limits']),
                'offered_resources':copy.deepcopy(e['RunPlan']['resource_limits']),
                'checked_at':0}

    def test_missing_rights_subjects_are_domain_error(self):
        args=self.arguments();args['required_rights']={'data':'evaluate'}
        with self.assertRaises(EntityError):check_compatibility(**args)

    def test_unsupported_rights_use_is_domain_error(self):
        args=self.arguments();args['required_rights']['data']='invented_use'
        with self.assertRaises(EntityError):check_compatibility(**args)

    def test_malformed_resource_limits_are_domain_error(self):
        for field,value in [('wall_seconds',-1),('model_calls',True),('memory_bytes',1.5),('cpu_seconds',float('nan'))]:
            args=self.arguments();args['required_resources'][field]=value
            with self.subTest(field=field),self.assertRaises(EntityError):check_compatibility(**args)
        args=self.arguments();del args['offered_resources']['artifact_bytes']
        with self.assertRaises(EntityError):check_compatibility(**args)

    def test_duplicate_asset_version_is_domain_error(self):
        args=self.arguments();args['assets'].append(copy.deepcopy(args['assets'][0]))
        with self.assertRaises(EntityError):check_compatibility(**args)

    def test_unknown_metadata_remains_needs_information(self):
        result=check_compatibility(**self.arguments())
        self.assertEqual(result['status'],'needs_information')
        self.assertTrue(result['missing_fields'])
