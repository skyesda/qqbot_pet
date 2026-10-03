"""Measure all direct layouts on offline HTML snapshots, with optional artifacts."""
import argparse
import io
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from petpark.raster_cards import render_card, card_kind
from card_samples import samples


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runs',type=int,default=3)
    args=parser.parse_args()
    if args.runs<1:parser.error('--runs must be positive')
    inputs={p.stem:(p.read_text(encoding='utf-8'),int(p.with_suffix('.width').read_text())) for p in args.snapshots.glob('*.html')} if args.snapshots else samples()
    args.output.mkdir(parents=True,exist_ok=True)
    reports=[]
    for name,(html,width) in inputs.items():
        times=[];cpus=[]
        for _ in range(args.runs):
            w,c=time.perf_counter(),time.process_time()
            image=render_card(html,width)
            if image is None:raise ValueError('Unsupported built-in: '+name)
            raw=io.BytesIO();image.save(raw,'JPEG',quality=88,optimize=True)
            times.append((time.perf_counter()-w)*1000);cpus.append((time.process_time()-c)*1000)
        (args.output/(name+'.jpg')).write_bytes(raw.getvalue())
        (args.output/(name+'.html')).write_text(html,encoding='utf-8')
        (args.output/(name+'.width')).write_text(str(width))
        report={'card':name,'kind':card_kind(html),'size':image.size,'bytes':len(raw.getvalue()),
                'median_ms':round(statistics.median(times),1),'cpu_ms':round(statistics.median(cpus),1)}
        reports.append(report);print(json.dumps(report,ensure_ascii=False),flush=True)
    (args.output/'benchmark.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
