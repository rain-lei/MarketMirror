"""Read-only verification of archived disclosure bytes for platform onboarding."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

COLLECTIONS = ('issuer_q3_reports_raw_2019_v1', 'issuer_half_reports_raw_2019_v1')


def verify_file(root, original):
    relative = original.get('archive_path')
    if not isinstance(relative, str) or not relative:
        return {'verification': 'NO_SOURCE'}
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        return {'verification': 'OUTSIDE_ROOT'}
    try:
        digest = hashlib.sha256()
        size = 0
        with path.open('rb') as stream:
            header = stream.read(5)
            digest.update(header)
            size += len(header)
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
                size += len(block)
    except OSError as error:
        return {'verification': 'UNREADABLE', 'error_type': type(error).__name__}
    checks = {'sha256_matches': digest.hexdigest() == original.get('sha256'),
              'size_matches': size == original.get('bytes'), 'pdf_header_matches': header == b'%PDF-'}
    return {'verification': 'BYTES_VERIFIED' if all(checks.values()) else 'MISMATCH',
            'actual_sha256': digest.hexdigest(), 'actual_bytes': size, **checks}


def inventory(root):
    rows, manifests = [], []
    for collection in COLLECTIONS:
        manifest = root / 'research_outputs' / collection / 'manifest.json'
        raw = manifest.read_bytes()
        manifests.append({'path': manifest.relative_to(root).as_posix(),
                          'sha256': hashlib.sha256(raw).hexdigest()})
        for code, record in sorted(json.loads(raw)['reports'].items()):
            original = record.get('original_pdf') or {}
            metadata = record.get('report_metadata') or {}
            rows.append({'collection': collection, 'stock_code': code,
                         'name': record.get('historical_short_name'),
                         'source_status': record.get('status'),
                         'archive_path': original.get('archive_path'),
                         'expected_sha256': original.get('sha256'),
                         'metadata': metadata, **verify_file(root, original)})
    verified = [r for r in rows if r['verification'] == 'BYTES_VERIFIED']
    coverage = Counter(r['stock_code'] for r in verified)
    return {'schema_version': 1, 'verified_at': datetime.now(timezone.utc).isoformat(),
            'scope': 'Disclosure archive byte integrity only; not PDF parsing, financial correctness, Q&A coverage or multi-hop readiness.',
            'source_manifests': manifests,
            'summary': {'records': len(rows), 'states': dict(Counter(r['verification'] for r in rows)),
                        'verified_bytes': sum(r['actual_bytes'] for r in verified),
                        'companies_with_both_reports': sum(n == 2 for n in coverage.values())},
            'records': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    result = inventory(root)
    output = Path(args.output).resolve()
    # A fresh output avoids replacing any frozen research or previous verification.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result['summary'], ensure_ascii=False))
