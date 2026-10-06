"""Portable archive-boundary tests; test data is not research evidence."""
from copy import deepcopy
from datetime import date, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from design.server import create_server
from design.source_cases import SourceCaseLibrary, CaseArchiveIntegrityError, MODES, SEEDS
from design.strategy_config import load_model
from research.semantic.source_case_facts import canonical, digest, normalize_facts
from research.simulation.run_source_case_decisions import run_case
from research.simulation.source_case_decision_link import load_mapping


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class SourceCaseLibraryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        case = {'case_id':'case0','title':'测试原文','source_kind':'company_reply_workbook',
                'available_at':'2000-01-04T00:00:00+08:00','visibility_precision':'date_only_end_of_day',
                'segments':[{'source':'reply','text':'项目审批结果仍不确定，敬请关注后续公告。'}]}
        case['case_text_sha256'] = digest(canonical(case['segments']))
        facts = normalize_facts(case, canonical({'facts':[{'kind':'approval_uncertainty','status':'uncertain',
            'claim':'项目审批结果不确定。','evidence':[{'source':'reply','quote':'项目审批结果仍不确定'}]}]}).decode())
        mapping, mechanism = load_mapping(), load_model()[0]
        dates = [(date(2000,1,1)+timedelta(days=i)).isoformat() for i in range(20)]
        cls.calendar = (dates, {'calendar_sha256':'unit-test-calendar','calendar_path':'unit-test'})
        with patch('research.simulation.source_case_decision_link.case_calendar', return_value=cls.calendar):
            cls.runs = {m: run_case(case,facts,mapping,mechanism,7,m) for m in ('no_text','reviewed_llm')}
        cls.case, cls.facts, cls.mapping, cls.mechanism = case, facts, mapping, mechanism

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.archive = self.root/'archive'; self.archive.mkdir()
        review_dir = self.root/'review'; review_dir.mkdir()
        cases = [{**deepcopy(self.case), 'case_id':f'case{i}'} for i in range(6)]
        records = {c['case_id']:{**deepcopy(self.facts),'case_id':c['case_id']} for c in cases}
        notes = [{'case_id':c['case_id'], 'case_text_sha256':c['case_text_sha256'],
                  'reviewed_record':records[c['case_id']], 'raw_facts':deepcopy(records[c['case_id']]['facts']),
                  'checks':[{'fact_index':0, 'changes':{}, 'notes':'测试源文支持审批不确定。'}],
                  'review_basis':'single_ai_post_model_source_review', 'independent_gold':False,
                  'ready_for_registered_development_case':True} for c in cases]
        (review_dir/'case_reviews.jsonl').write_text('\n'.join(json.dumps(n) for n in notes),encoding='utf-8')
        counts = {'paths':120,'portfolio_days':2160,'decisions':25920}
        self.config = {'case_ids':[c['case_id'] for c in cases], 'schema_version':'unit-test-protocol',
                       'expected_counts':{**counts,'pairs':120}, 'bindings':{}, 'review_directory':'review'}
        self.protocol = self.root/'protocol.json'
        self.protocol.write_text(json.dumps(self.config),encoding='utf-8')
        protocol_hash = file_hash(self.protocol)
        artifacts = {}
        for i in range(6):
            for seed in SEEDS:
                for mode in MODES:
                    path = self.archive/f'case{i}_seed{seed}_{mode}.json.gz'
                    value = self.runs[mode] if i==0 and seed==7 and mode in self.runs else {}
                    path.write_bytes(gzip.compress(canonical(value),mtime=0))
                    artifacts[path.name] = file_hash(path)
        for name, value in [('frozen_plan.json',self.config), ('summary.json',{
            'counts':counts,'status':'COMPLETE_ALL_SIX_SOURCE_CASES_120_PATHS_AND_FULL_DECISION_LINKS',
            'version':'unit-test-protocol','protocol_sha256':protocol_hash,
            'historical_returns_or_prices_applied':False,'independent_semantic_validation':False})]:
            path=self.archive/name;path.write_text(json.dumps(value),encoding='utf-8');artifacts[name]=file_hash(path)
        manifest={'artifacts':artifacts,'protocol_sha256':protocol_hash,'version':'unit-test-protocol','bindings':{},'counts':counts}
        (self.archive/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        self.registry=self.root/'registry.json'
        self.registry.write_text(json.dumps({'schema':'platform-real-source-case-registry-v1',
            'study_config':'protocol.json','study_config_sha256':protocol_hash,'archive_directory':'archive',
            'archive_manifest_sha256':file_hash(self.archive/'manifest.json'),
            'labels':{c['case_id']:'测试案例' for c in cases}}),encoding='utf-8')
        self.library=SourceCaseLibrary(self.registry,self.root)
        loader=patch('design.source_cases.load_study',return_value=(self.config,cases,records,self.mapping,self.mechanism))
        loader.start();self.addCleanup(loader.stop)
        calendar=patch('research.simulation.source_case_decision_link.case_calendar',return_value=self.calendar)
        calendar.start();self.addCleanup(calendar.stop)

    def test_real_engine_archive_is_readable_and_returned_data_is_independent(self):
        catalog=self.library.catalog()
        self.assertEqual(len(catalog['cases']),6)
        value=self.library.get('case0')
        self.assertEqual(value['source_links']['with_message'][3]['source_visible'],True)
        self.assertTrue(value['result']['audit']['passed'])
        self.assertFalse(value['result']['provenance']['llm_called_now'])
        self.assertEqual(value['result']['paths']['with_message'],self.runs['reviewed_llm']['model_result'])
        value['result']['paths']['with_message']['trace'].clear()
        self.assertEqual(len(self.library.get('case0')['result']['paths']['with_message']['trace']),18)
        self_reference = self.library.get('case0', mode='no_text')
        self.assertEqual(self_reference['result']['audit'], {'passed':True, 'paths_checked':1, 'days_checked':18})
        self.assertEqual(self_reference['result']['paths']['baseline'], self_reference['result']['paths']['with_message'])
        self.assertTrue(all(row['requests_changed']==0 and row['actual_fills_changed']==0
                            for row in self_reference['comparison']['role_changes'].values()))

    def test_changed_path_is_rejected_even_after_cache_is_populated(self):
        self.library.get('case0')
        with (self.archive/'case0_seed7_reviewed_llm.json.gz').open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(CaseArchiveIntegrityError): self.library.get('case0')

    def test_missing_archive_never_produces_fake_cases_or_zero_results(self):
        (self.archive/'case3_seed11_no_text.json.gz').unlink()
        self.assertEqual(self.library.catalog()['cases'],[])
        self.assertFalse(self.library.catalog()['available'])

    def test_missing_registered_archive_root_reports_unavailable_without_fallback(self):
        missing = self.root/'archive-missing'
        self.archive.rename(missing)
        catalog = self.library.catalog()
        self.assertFalse(catalog['available'])
        self.assertEqual(catalog['cases'], [])
        self.assertIn('尚无完整案例归档', catalog['message'])

    def test_changed_manifest_or_unsafe_path_is_rejected(self):
        with self.assertRaises(CaseArchiveIntegrityError):self.library._path('../outside')
        (self.archive/'manifest.json').write_text('{}',encoding='utf-8')
        with self.assertRaises(CaseArchiveIntegrityError):self.library.catalog()

    def test_incomplete_review_is_rejected_instead_of_exposing_partial_facts(self):
        review_path = self.root/'review'/'case_reviews.jsonl'
        notes = [json.loads(line) for line in review_path.read_text(encoding='utf-8').splitlines()]
        del notes[0]['raw_facts']
        review_path.write_text('\n'.join(json.dumps(n) for n in notes), encoding='utf-8')
        with self.assertRaises(CaseArchiveIntegrityError):self.library.catalog()

    def test_invalid_seed_mode_and_unknown_case_do_not_fall_back(self):
        for seed,mode in [(True,'reviewed_llm'),(19,'reviewed_llm'),(7,'automatic')]:
            with self.assertRaises(ValueError):self.library.get('case0',seed,mode)
        with self.assertRaises(FileNotFoundError):self.library.get('unknown')

    def test_http_catalog_selected_archive_export_and_bad_query(self):
        with patch('design.source_cases.SourceCaseLibrary',return_value=self.library):
            server=create_server(self.root/'workspace',0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base+'/api/platform/source-cases',timeout=10) as response:
                self.assertEqual(len(json.load(response)['cases']),6)
            with urlopen(base+'/api/platform/source-cases/case0/export?seed=7&mode=reviewed_llm',timeout=10) as response:
                self.assertIn('attachment',response.headers['Content-Disposition'])
                self.assertEqual(json.load(response)['result']['paths']['with_message'],self.runs['reviewed_llm']['model_result'])
            with self.assertRaises(HTTPError) as error:
                urlopen(base+'/api/platform/source-cases/case0?seed=7&seed=11',timeout=10)
            self.assertEqual(error.exception.code,400)
            with (self.archive/'case0_seed7_reviewed_llm.json.gz').open('ab') as stream:stream.write(b'bad')
            with self.assertRaises(HTTPError) as error:
                urlopen(base+'/api/platform/source-cases/case0',timeout=10)
            self.assertEqual(error.exception.code,503)
        finally:server.shutdown();server.server_close();thread.join(5)
