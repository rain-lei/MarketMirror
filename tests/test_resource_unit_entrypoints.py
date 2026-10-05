import importlib
import unittest


class ResourceEntrypointTests(unittest.TestCase):
    def test_all_current_actual_execution_entrypoints_import_and_use_revision_two(self):
        names=("run_temporal_resource_unit_study_v2","audit_temporal_resource_unit_study_v2",
               "finish_temporal_resource_unit_study_v2","audit_temporal_resource_unit_summary_v2",
               "temporal_resource_unit_jobs_v2")
        for name in names:
            with self.subTest(module=name):
                module=importlib.import_module("research.simulation."+name)
                self.assertEqual(module.CONFIG.name,"temporal_resource_unit_study_2022_v2.json")
                self.assertEqual(module.OUTPUT.name,"temporal_transport_resource_units_2022_v2")
                self.assertIn(module.CONFIG, (module.load_study.__defaults__ or ()))


if __name__=="__main__": unittest.main()
