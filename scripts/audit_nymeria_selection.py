"""Read-only production selection audit: no downloads, uploads or user-state changes."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    from run_tests import OFFLINE_GUARD
    exec(OFFLINE_GUARD, {})
    from moneymin import ego4d, nymeria
    counts = Counter()
    recovered = set()
    rejected = []
    for seq in sorted(args.root.iterdir()):
        if not (seq / 'narration/atomic_action.csv').is_file():
            continue
        stats = {}
        try:
            rows, _ = nymeria._annotation_rows(seq, stats=stats)
        except (OSError, ValueError, UnicodeError) as exc:
            rejected.append({'sequence': seq.name, 'reason': str(exc)})
            continue
        counts['usable_recordings'] += 1
        counts['usable_rows'] += len(rows)
        counts['excluded_invalid_rows'] += stats['invalid_rows']
        counts['excluded_overlapping_rows'] += stats['removed_overlapping_rows']
        if stats['invalid_rows']:
            recovered.add(seq.name)
            counts['recovered_rows'] += len(rows)
    counts['recovered_recordings'] = len(recovered)
    print(json.dumps(dict(counts)), flush=True)
    names = json.loads((project / 'test/current_minute_task_names.json').read_text(encoding='utf8'))
    started = time.monotonic()
    ranges = []
    with ego4d.selection_operation():
        for minimum in (60, 180):
            tasks = []
            for name in names:
                plans = nymeria.planned_candidates(task_name=name, min_dur_s=minimum,
                                                   max_dur_s=1800, root=args.root)
                new_plans = [plan for plan in plans if plan['seq_id'] in recovered]
                if new_plans:
                    nymeria.revalidate_planned_candidate(new_plans[0], root=args.root)
                row = {'task': name, 'candidates': len(plans),
                       'candidates_from_recovered_recordings': len(new_plans),
                       'annotation_seconds_upper_bound': sum(plan['dur_s'] for plan in plans)}
                tasks.append(row)
                print(json.dumps({'minimum': minimum, **row}, ensure_ascii=False), flush=True)
            ranges.append({'minimum_seconds': minimum, 'tasks': tasks,
                           'candidates': sum(row['candidates'] for row in tasks),
                           'tasks_with_candidates': sum(bool(row['candidates']) for row in tasks)})
    report = {'algorithm': nymeria._ALGORITHM, 'parser': dict(counts),
              'rejected_recordings': rejected, 'ranges': ranges,
              'duration_seconds': round(time.monotonic() - started, 2),
              'external_network_blocked': True, 'media_downloaded': False,
              'note': 'Plans are annotation estimates; measured video/IMU and account policy gate delivery.'}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps({'finished': True, 'ranges': [{key: value for key, value in row.items()
                     if key != 'tasks'} for row in ranges], 'seconds': report['duration_seconds']}), flush=True)


if __name__ == '__main__':
    main()
