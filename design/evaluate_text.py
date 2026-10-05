"""Repeatable live extraction probe using explicitly fictional messages."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from .text_analysis import PROMPT, validate_facts, validate_source
from .text_prompts import PROMPT_V2, PROMPT_V3
from research.semantic.local_credential import load_api_key
from research.semantic.run_model import request_completion, endpoint_url, DEFAULT_BASE_URL, DEFAULT_MODEL

ROOT = Path(__file__).resolve().parents[1]

def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    version = parser.add_mutually_exclusive_group()
    version.add_argument('--candidate', action='store_true', help='Legacy alias for --prompt-version v2')
    version.add_argument('--prompt-version', choices=('current', 'v2', 'v3'), default='current')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    prompt = {'current': PROMPT, 'v2': PROMPT_V2, 'v3': PROMPT_V3}['v2' if args.candidate else args.prompt_version]
    cases = json.loads((ROOT / 'design/text_eval_cases.json').read_text(encoding='utf-8'))
    output = args.output or ROOT / 'research_outputs/platform_workspace/verification' / ('text_probe_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=True)
    settings = {'schema_version': 2, 'cases': cases, 'prompt': prompt, 'prompt_sha256': digest(prompt),
                'model': DEFAULT_MODEL, 'base_url': DEFAULT_BASE_URL, 'repeats': 2,
                'temperature': 0, 'timeout': 45, 'retries': 1}
    manifest = output / 'manifest.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8')) != settings:
            raise ValueError('Evaluation configuration changed; use a new output directory')
    else:
        manifest.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    key = load_api_key()
    for case in cases['cases']:
        name, source = case['id'], validate_source(case['source'])
        for repeat in range(settings['repeats']):
            target = output / f'{name}_{repeat}.json'
            if target.exists():
                prior = json.loads(target.read_text(encoding='utf-8'))
                if prior['source'] != source or prior['prompt_sha256'] != digest(prompt):
                    raise ValueError('Existing output does not match manifest')
                print(name, repeat, 'already recorded', flush=True)
                continue
            record = {'case': name, 'repeat': repeat, 'source': source, 'source_sha256': digest(source),
                      'prompt_sha256': digest(prompt), 'model': DEFAULT_MODEL,
                      'review_status': 'pending_semantic_review', 'started_at': datetime.now(timezone.utc).isoformat()}
            try:
                raw = request_completion(endpoint_url(DEFAULT_BASE_URL), key, DEFAULT_MODEL,
                                         [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': source}],
                                         timeout=45, retries=1, temperature=0)
                record['raw_response'] = raw
                record['facts'] = validate_facts(source, raw)
                record['status'] = 'extracted'
            except Exception as error:
                record.update(status='failed', error_type=type(error).__name__)
            record['finished_at'] = datetime.now(timezone.utc).isoformat()
            with target.open('x', encoding='utf-8') as stream:
                json.dump(record, stream, ensure_ascii=False, indent=2)
            print(name, repeat, record['status'], flush=True)
    print(output, flush=True)

if __name__ == '__main__':
    main()
