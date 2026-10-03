"""Read-only benchmark: never instantiate a store or write player data."""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from petpark.store import PetStore, _fast_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path)
    parser.add_argument('--runs', type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error('--runs must be positive')
    snapshot = json.loads(args.path.read_bytes())
    results = {name: {'wall_ms': [], 'cpu_ms': []} for name in ('before', 'after')}
    for _ in range(args.runs):
        for name, encode in (
            ('before', lambda: json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')).encode('utf-8')),
            ('after', lambda: PetStore._encode_snapshot(snapshot)),
        ):
            wall, cpu = time.perf_counter(), time.process_time()
            raw = encode()
            results[name]['cpu_ms'].append((time.process_time() - cpu) * 1000)
            results[name]['wall_ms'].append((time.perf_counter() - wall) * 1000)
            if json.loads(raw) != snapshot:
                raise AssertionError('Snapshot round trip changed data')
    report = {'engine': 'orjson' if _fast_json else 'stdlib', 'runs': args.runs,
              'bytes': args.path.stat().st_size, 'roundtrip_equal': True}
    for name, metrics in results.items():
        report[name] = {key: round(statistics.median(values), 2) for key, values in metrics.items()}
    print(json.dumps(report))


if __name__ == '__main__':
    main()
