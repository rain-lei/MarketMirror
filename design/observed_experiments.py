"""Read registered real-return development experiments with verified source links."""
from copy import deepcopy
import json
from pathlib import Path
import re

from research.data_pipeline.provenance import file_sha256
from research.simulation.single_observed_case import MODES, read, verify
from research.simulation.observed_decision_evidence import explain_path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = Path(__file__).with_name('observed-experiment-registry.json')
SCHEMA = 'platform-observed-experiment-v1'


class ObservedArchiveIntegrityError(ValueError):
    pass


class ObservedExperimentLibrary:
    def __init__(self, registry=REGISTRY, root=ROOT):
        self.registry, self.root = Path(registry), Path(root).resolve()

    def _path(self, name):
        path = (self.root/name).resolve()
        if not path.is_relative_to(self.root):
            raise ObservedArchiveIntegrityError('历史实验路径超出登记目录')
        return path

    def _entries(self):
        try:
            registry = read(self.registry)
            if registry['schema'] != SCHEMA:
                raise ValueError('registry schema')
            entries = registry['experiments']
            ids = [e['id'] for e in entries]
            if (not isinstance(entries,list) or len(ids) != len(set(ids))
                    or any(not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', i) for i in ids)):
                raise ValueError('registry identity')
            return entries
        except (ValueError,KeyError,TypeError,OSError) as exc:
            raise ObservedArchiveIntegrityError('历史实验登记核验失败') from exc

    def _load(self, entry):
        try:
            directory = self._path(entry['run_directory'])
            manifest = directory/'result_manifest.json'
            if file_sha256(manifest) != entry['manifest_sha256']:
                raise ValueError('registered result manifest changed')
            checks = verify(directory)
            plan, model, review, result = [read(directory/name) for name in
                                         ('plan.json','model.json','review.json','result.json')]
            if (result['mode'] != 'observed_return_price_taking_replay'
                    or result['stock_code'] != plan['case']['stock_code']
                    or set(result['paths']) != set(MODES) or len(result['comparison']) != 9
                    or plan['automatic_semantic_gate_enabled'] is not False
                    or review['independent_gold'] is not False):
                raise ValueError('real-return development scope')
            explanations = {mode:explain_path(plan,review['reviewed_record'],mode,path)
                            for mode,path in result['paths'].items()}
            return {'schema':SCHEMA,'id':entry['id'],'label':entry['label'],
                'data_kind':'observed_return_price_taking_replay',
                'case':plan['case'],'model':model,'review':review,
                'parameters':plan['agents'],'fee_rate':plan['fee_rate'],
                'sessions':plan['sessions'],'information_duration_steps':plan['information_duration_steps'],
                'market_dataset_id':plan['market_dataset_id'],'market_retrieved_at':plan['market_retrieved_at'],
                'market_signal_rule':plan['market_signal_rule'],'execution_rule':plan['execution_rule'],
                'paths':result['paths'],'comparison':result['comparison'],'explanations':explanations,
                'verification':{**checks,'result_manifest_sha256':entry['manifest_sha256'],
                    'read_only_archive':True,'decision_sources_reconstructed':True,
                    'independent_semantic_validation':False,'price_impact_simulated':False,
                    'investor_parameters_calibrated':False}}
        except FileNotFoundError:
            raise
        except (ValueError,KeyError,TypeError,OSError,AssertionError,StopIteration) as exc:
            raise ObservedArchiveIntegrityError('历史实验来源、版本、账户或决策解释核验失败') from exc

    @staticmethod
    def _metadata(bundle):
        trace = bundle['paths']['reviewed_llm']['trace']
        first = next(d for d in trace if d['source_active'])
        return {'id':bundle['id'],'label':bundle['label'],'stock_code':bundle['case']['stock_code'],
            'start_date':trace[0]['trade_date'],'end_date':trace[-1]['trade_date'],
            'first_text_trade_date':first['trade_date'],'sessions':bundle['sessions'],
            'agent_decisions':bundle['verification']['agent_decisions'],
            'model':bundle['model']['model'],'data_kind':bundle['data_kind']}

    def catalog(self):
        experiments, unavailable = [], 0
        for entry in self._entries():
            try:
                experiments.append(self._metadata(self._load(entry)))
            except FileNotFoundError:
                unavailable += 1
        return {'schema':SCHEMA,'available':bool(experiments),'experiments':experiments,
            'unavailable_archives':unavailable,
            'message':'本机尚无完整的历史行情实验归档，请恢复登记的本地研究产物。' if not experiments else '',
            'llm_called_now':False}

    def get(self, experiment_id):
        entry = next((e for e in self._entries() if e['id'] == experiment_id),None)
        if entry is None:
            raise FileNotFoundError(experiment_id)
        return deepcopy(self._load(entry))

    def verify_all(self):
        counts = {'archives':0,'conditions':0,'market_days':0,'agent_decisions':0,'source_receipts':0}
        for entry in self._entries():
            bundle = self._load(entry)
            counts['archives'] += 1
            for key in ('market_days','agent_decisions'):
                counts[key] += bundle['verification'][key]
            counts['conditions'] += len(bundle['paths'])
            counts['source_receipts'] += sum(len(row['text_evidence_used'])
                for days in bundle['explanations'].values() for day in days for row in day['agents'].values())
        return {'passed':True,**counts,'llm_called_now':False,'old_archives_modified':False}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-all',action='store_true')
    args = parser.parse_args()
    library = ObservedExperimentLibrary()
    catalog = library.verify_all() if args.verify_all else library.catalog()
    print(json.dumps(catalog,ensure_ascii=False,indent=2))
