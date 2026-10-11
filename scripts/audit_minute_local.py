"""Read-only APK trace and on-demand Nymeria catalog audit; never sends content."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--minute-root', type=Path, required=True)
    parser.add_argument('--library-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--minimum-seconds', type=int, default=300)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    os.environ['QMONEY_LIBRARY_ROOT'] = str(args.library_root)
    from moneymin import config, nymeria, nymeria_library, ego4d
    bundle = args.minute_root / 'bundle/decompiled.js'
    text = bundle.read_text(encoding='utf-8')
    witnesses = {
        'version_header': 'X-App-Version', 'device_header': 'X-Device-Id',
        'appcheck_header': 'X-Firebase-AppCheck', 'upload_sidecar_name': '.data.zip',
        'last_chunk_completion': 'session_complete', 'suppress_chunk_evaluation': 'suppress_per_chunk_catbear',
        'sas_schema': 'signed_urls', 'sas_expiry': 'expires_at',
        'task_audio_requirement': 'requiresAudio', 'evaluation_schema': 'evaluationResultSchema',
    }
    trace = {key: next((n for n, line in enumerate(text.splitlines(), 1) if literal in line), None)
             for key, literal in witnesses.items()}
    native = args.minute_root / 'apktool/base/smali/l2.1'
    native_trace = {}
    for literal in ('t,ax,ay,az,wx,wy,wz', 'i,ptsNs,dtNs,tNs,key',
                    'android_elapsedRealtimeNanos', 'gyro_anchored_v1'):
        matches = []
        for source in native.glob('*.smali'):
            for n, line in enumerate(source.read_text(encoding='utf-8').splitlines(), 1):
                if literal in line:
                    matches.append({'file': str(source), 'line': n})
        native_trace[literal] = matches
    if any(v is None for v in trace.values()) or any(not v for v in native_trace.values()):
        raise RuntimeError('APK contract witness missing; review the mapping before publishing a conclusion.')
    root = args.library_root / 'data/nymeria'
    summary = nymeria_library.summary(root)
    names = json.loads((project / 'test/current_minute_task_names.json').read_text(encoding='utf-8'))
    started = time.monotonic()
    rows = []
    for name in names:
        candidates = nymeria.automatic_candidates(task_name=name, min_dur_s=args.minimum_seconds,
            max_dur_s=1800, root=root, include_planned=True, catalog_only=True)
        if candidates:
            nymeria.revalidate_planned_candidate(candidates[0], task_name=name, root=root)
        rows.append({'task': name, 'candidates': len(candidates),
                     'annotation_seconds_upper_bound': sum(row['dur_s'] for row in candidates)})
        print(json.dumps({'task': name, 'candidates': len(candidates)}, ensure_ascii=False), flush=True)
    result = {
        'contract': {'version': config.APP_VERSION, 'android_version_code': config.ANDROID_VERSION_CODE,
                     'apk_bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
                     'bundle': str(bundle), 'js_witness_lines': trace, 'native_witnesses': native_trace},
        'catalog': {k: summary[k] for k in ('sequence_count','by_state','atomic_action_count')},
        'nymeria_tasks': rows, 'tasks_with_candidates': sum(row['candidates'] > 0 for row in rows),
        'catalog_duration_seconds': round(time.monotonic() - started, 2),
        'duration_bounds_seconds': [args.minimum_seconds, 1800],
        'ego4d_catalog_present': all((args.library_root / 'data/ego4d' / name).is_file()
                                    for name in ('ego4d.json', 'clips.csv')),
        'media_downloaded_by_audit': False,
        'note': 'Annotation counts are acquisition plans; measured video/IMU, live account policy and capacity still gate every delivery.',
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'apk_contract': 'verified', 'sequences': summary['sequence_count'],
                      'tasks_with_candidates': result['tasks_with_candidates'],
                      'ego4d_catalog_present': result['ego4d_catalog_present']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
