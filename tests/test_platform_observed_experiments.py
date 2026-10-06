"""Portable boundary tests; fixture prices are not research observations."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from design.observed_experiments import ObservedExperimentLibrary, ObservedArchiveIntegrityError, SCHEMA
from design.server import create_server
from research.baselines.event_study import DailyObservation
from research.data_pipeline.provenance import file_sha256
from research.semantic.run_model import DEFAULT_MODEL
from research.semantic.source_case_facts import canonical, digest, normalize_facts
from research.simulation.single_observed_case import VERSION, REPLAY_CONFIG, PROMPT, read, write_new, run, select_steps
from research.simulation.source_case_decision_link import load_mapping


class ObservedExperimentLibraryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root/'archive'; self.directory.mkdir()
        config = read(REPLAY_CONFIG)
        config.update(start_date='2020-01-26', end_date='2020-03-15')
        rows = [DailyObservation(date(2020,1,1)+timedelta(days=i),
                    (i%9-4)*0.002,(i%7-3)*0.003) for i in range(75)]
        case = {'case_id':'qa_test','stock_code':'300294','source_kind':'company_reply_workbook',
            'available_at':'2020-02-05T23:59:59.999999+08:00',
            'segments':[{'source':'question','text':'收购获批吗？'},
                        {'source':'reply','text':'原料调拨审批具有不确定性。'}]}
        case['case_text_sha256'] = digest(canonical(case['segments']))
        facts = {'facts':[{'kind':'approval_uncertainty','status':'uncertain',
            'claim':'原料调拨审批具有不确定性。','evidence':[{'source':'reply','quote':case['segments'][1]['text']}]}]}
        plan = {'schema_version':VERSION,'case':case,'steps':select_steps(rows,case,config),
            'sessions':30,'information_duration_steps':6,'mapping':load_mapping(),
            'agents':config['agents'],'fee_rate':0.001,'initial_price_index':100.0,
            'model':DEFAULT_MODEL,'market_dataset_id':'synthetic_portable_test_fixture',
            'market_retrieved_at':'fixture_only','automatic_semantic_gate_enabled':False,
            'market_signal_rule':'fixture momentum','execution_rule':'fixture index units',
            'input_sha256':{},'code_sha256':{}}
        for step in plan['steps']: step['execution_available'] = True
        write_new(self.directory/'plan.json',plan)
        write_new(self.directory/'plan_receipt.json',{'plan_sha256':file_sha256(self.directory/'plan.json')})
        payload = {'source_kind':case['source_kind'],'segments':case['segments']}
        model = {'plan_sha256':file_sha256(self.directory/'plan.json'),'model':DEFAULT_MODEL,
            'prompt_sha256':file_sha256(PROMPT),'sent_payload_sha256':digest(canonical(payload)),
            'llm_called_now':True,'parse_error':None,'raw_response':canonical(facts).decode(),
            'normalized':normalize_facts(case,canonical(facts).decode()),'finished_at_utc':'2000-01-01T00:00:00+00:00'}
        write_new(self.directory/'model.json',model)
        write_new(self.directory/'review.json',{'model_sha256':file_sha256(self.directory/'model.json'),
            'review_basis':'single_assistant_source_review_before_replay','independent_gold':False,
            'checks':[{'fact_index':0,'notes':'Fixture source matches.'}],'reviewed_record':model['normalized']})
        run(self.directory)
        self.registry = self.root/'registry.json'
        write_new(self.registry,{'schema':SCHEMA,'experiments':[{'id':'observed_test','label':'测试归档',
            'run_directory':'archive','manifest_sha256':file_sha256(self.directory/'result_manifest.json')}]})
        self.library = ObservedExperimentLibrary(self.registry,self.root)

    def test_verified_archive_keeps_original_decisions_and_adds_uncertainty_source(self):
        original = read(self.directory/'result.json')
        with patch('research.simulation.single_observed_case.request_completion',side_effect=AssertionError('no live model')):
            catalog = self.library.catalog()
            bundle = self.library.get('observed_test')
        self.assertTrue(catalog['available'])
        self.assertEqual(bundle['paths'],original['paths'])
        self.assertFalse(bundle['verification']['llm_called_now'])
        self.assertTrue(bundle['verification']['offline_replay_exact'])
        for name, row in bundle['explanations']['reviewed_llm'][4]['agents'].items():
            self.assertEqual(row['text_direction_contribution'],0)
            self.assertLess(row['uncertainty_contribution'],0)
            self.assertEqual(row['text_evidence_used'][0]['quote'],'原料调拨审批具有不确定性。')
            self.assertEqual(row['text_evidence_used'][0]['fact_index'],0)
        for mode in ('no_text','reviewed_llm'):
            for day in bundle['explanations'][mode][:4]:
                self.assertTrue(all(not row['text_evidence_used'] for row in day['agents'].values()))
        bundle['paths']['reviewed_llm']['trace'].clear()
        self.assertEqual(len(self.library.get('observed_test')['paths']['reviewed_llm']['trace']),30)

    def test_keyword_evidence_comes_from_actual_reply_matches_and_expires(self):
        bundle = self.library.get('observed_test')
        row = bundle['explanations']['keywords'][4]['agents']['aggressive']
        self.assertEqual([e['keyword'] for e in row['text_evidence_used']],['不确定'])
        self.assertTrue(all(not d['agents']['aggressive']['text_evidence_used']
                            for d in bundle['explanations']['reviewed_llm'][10:]))

    def test_changed_source_result_or_manifest_is_rejected_after_successful_read(self):
        self.library.get('observed_test')
        for name in ('review.json','result.json','result_manifest.json'):
            path = self.directory/name
            original = path.read_bytes()
            path.write_bytes(original+b'changed')
            with self.subTest(name=name),self.assertRaises(ObservedArchiveIntegrityError):
                self.library.get('observed_test')
            path.write_bytes(original)

    def test_missing_archives_and_unknown_ids_never_produce_substitute_results(self):
        with self.assertRaises(FileNotFoundError):self.library.get('unknown')
        (self.directory/'result.json').unlink()
        catalog = self.library.catalog()
        self.assertFalse(catalog['available']);self.assertEqual(catalog['experiments'],[])
        self.assertEqual(catalog['unavailable_archives'],1)

    def test_unsafe_registry_directory_is_rejected(self):
        with self.assertRaises(ObservedArchiveIntegrityError):self.library._path('../outside')
        registry = read(self.registry);registry['experiments'][0]['run_directory']='../outside'
        self.registry.write_bytes(canonical(registry))
        with self.assertRaises(ObservedArchiveIntegrityError):self.library.catalog()

    def test_http_catalog_export_query_rejection_and_integrity_failure(self):
        with patch('design.observed_experiments.ObservedExperimentLibrary',return_value=self.library):
            server = create_server(self.root/'workspace',0)
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base = f'http://127.0.0.1:{server.server_port}/api/platform/observed-experiments'
        try:
            with urlopen(base,timeout=10) as response:self.assertTrue(__import__('json').load(response)['available'])
            with urlopen(base+'/observed_test/export',timeout=10) as response:
                self.assertIn('attachment',response.headers['Content-Disposition'])
                self.assertEqual(__import__('json').load(response)['id'],'observed_test')
            with self.assertRaises(HTTPError) as error:urlopen(base+'/observed_test?mode=keywords',timeout=10)
            self.assertEqual(error.exception.code,400)
            with (self.directory/'result.json').open('ab') as stream:stream.write(b'changed')
            with self.assertRaises(HTTPError) as error:urlopen(base+'/observed_test',timeout=10)
            self.assertEqual(error.exception.code,503)
        finally:server.shutdown();server.server_close();thread.join(5)


if __name__ == '__main__': unittest.main()
