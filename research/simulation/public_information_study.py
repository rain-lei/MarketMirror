"""Complete development grid for public coverage at equal expected received risk."""
from collections import Counter
from pathlib import Path
import statistics

from . import fixed_risk_study as prior
from .fixed_risk_study import read, require, read_gzip_json, write_gzip_json
from .public_information_risk import with_public_delivery
from .public_factor_metrics import values, difference
from ..data_pipeline.provenance import file_sha256

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'research/configs/pre_wuhan_public_information_risk_2019_v1.json'
OUTPUT = ROOT / 'research_outputs/pre_wuhan_public_information_risk_2019_v1'
CELLS = [{'name': a + '_feedback_' + label + '_risk_' + mode + '_delivery_' + delivery,
    'background_anchor': a, 'feedback_scale': scale, 'risk_mode': mode, 'delivery': delivery}
    for a in ('initial_inventory', 'current_inventory')
    for label, scale in (('full', 1.0), ('both_off', 0.0))
    for mode in ('market_independent', 'market_shared') for delivery in ('masked', 'public')]


def prior_index(cell):
    return (0 if cell['background_anchor'] == 'initial_inventory' else 6) + (0 if cell['feedback_scale'] else 3) + (1 if cell['risk_mode'] == 'market_independent' else 2)


def path_key(cell):
    return cell['risk_mode'] + '_' + cell['delivery']


def coverage_paths(old_paths):
    return {'legacy': old_paths['legacy'], **{mode + '_' + delivery:
        old_paths[mode] if delivery == 'masked' else with_public_delivery(old_paths[mode])
        for mode in ('market_independent', 'market_shared') for delivery in ('masked', 'public')}}


def load_study():
    cfg = read(CONFIG)
    require(cfg['version'] == 'public-information-matched-received-risk-complete-study-v1'
        and cfg['variants'] == CELLS and cfg['semantic_gate_enabled'] is False, 'public coverage scope differs')
    for name, expected in {**cfg['code_bindings'], **cfg['reference_bindings']}.items():
        require(file_sha256(ROOT / name) == expected, 'public coverage frozen binding changed: ' + name)
    old_cfg, loaded = prior.load_study()
    require(cfg['seeds'] == old_cfg['seeds'] and cfg['joint_limits'] == old_cfg['joint_limits'], 'public study seeds/limits changed')
    return cfg, loaded


def seed_paths(loaded, seed, independent=False):
    core, background, old_paths = prior.seed_paths(loaded, seed, independent=independent)
    return core, background, coverage_paths(old_paths)


def seal(folder, names, identity):
    from .run_pre_wuhan_public_factor_channels_2019 import record_bytes
    record = {'identity': identity, 'protocol_sha256': file_sha256(CONFIG),
              'artifacts': {name: file_sha256(folder / name) for name in names}}
    (folder / 'checkpoint.json').write_bytes(record_bytes(record))
    return record


def checkpoint(folder, identity):
    record = read(folder / 'checkpoint.json')
    require(record['identity'] == identity and record['protocol_sha256'] == file_sha256(CONFIG), 'public checkpoint identity differs')
    require({p.name for p in folder.iterdir()} == set(record['artifacts']) | {'checkpoint.json'}, 'public checkpoint artifact scope differs')
    for name, expected in record['artifacts'].items():
        require(Path(name).name == name and file_sha256(folder / name) == expected, 'public checkpoint changed: ' + name)
    return record


def summary(variants, seeds):
    lookup = {(r['seed_id'], r['name']): r for r in variants}
    require(len(lookup) == len(variants) == 80 and set(lookup) == {(s['seed_id'], c['name']) for s in seeds for c in CELLS}, 'public study complete grid differs')
    groups, paired = {}, []
    for cell in CELLS:
        rows = [lookup[s['seed_id'], cell['name']] for s in seeds]
        keys = tuple(values(rows[0]))
        groups[cell['name']] = {'seed_count': 5,
            'joint_pass_seeds': sum(r['joint_checks']['all_five_pass'] for r in rows),
            'metric_medians': {k: statistics.median(v) if v else None for k in keys
                for v in [[values(r)[k] for r in rows if values(r)[k] is not None]]}}
    def gaps(row):
        g = row['joint_checks']['values']
        return {'mean': g['absolute_mean_gap'], 'volatility': abs(g['volatility_ratio'] - 1),
            'zero_fraction': g['zero_fraction_gap'], 'stock_correlation': g['stock_correlation_gap'],
            'portfolio_volatility': abs(g['portfolio_volatility_ratio'] - 1)}
    for seed in seeds:
        for anchor in ('initial_inventory', 'current_inventory'):
            for label, scale in (('full', 1.0), ('both_off', 0.0)):
                prefix = anchor + '_feedback_' + label + '_risk_'
                for treatment, reference, pair in (
                    ('market_independent_delivery_public', 'market_independent_delivery_masked', 'public_coverage_independent'),
                    ('market_shared_delivery_public', 'market_shared_delivery_masked', 'public_coverage_shared'),
                    ('market_shared_delivery_masked', 'market_independent_delivery_masked', 'coupling_masked'),
                    ('market_shared_delivery_public', 'market_independent_delivery_public', 'coupling_public')):
                    treated, baseline = lookup[seed['seed_id'], prefix + treatment], lookup[seed['seed_id'], prefix + reference]
                    change = difference(gaps(treated), gaps(baseline))
                    paired.append({'seed_id': seed['seed_id'], 'background_anchor': anchor, 'feedback_scale': scale,
                        'pair': pair, 'treatment': treatment, 'reference': reference,
                        'metric_changes': difference(values(treated), values(baseline)), 'gap_changes': change,
                        'all_five_nonworse': all(v is not None and v <= 1e-12 for v in change.values())})
    return {'seed_summary': groups, 'paired_effects': paired,
        'joint_pass_conditions': sum(r['joint_checks']['all_five_pass'] for r in variants),
        'joint_pass_by_delivery': dict(Counter(r['delivery'] for r in variants if r['joint_checks']['all_five_pass']))}
