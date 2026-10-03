"""Benchmark direct pet rendering from a saved HTML snapshot, without game actions."""
import argparse
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from petpark.pet_card_pillow import render_pet_card


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    html=args.snapshot.read_text(encoding='utf-8')
    result=[]
    for _ in range(3):
        wall,cpu=time.perf_counter(),time.process_time()
        image=render_pet_card(html,760)
        if image is None:
            raise ValueError('Not a supported pet snapshot')
        buffer=io.BytesIO()
        image.save(buffer,'JPEG',quality=88,optimize=True)
        result.append({'wall_ms':round((time.perf_counter()-wall)*1000,1),
                       'cpu_ms':round((time.process_time()-cpu)*1000,1)})
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_bytes(buffer.getvalue())
    print(json.dumps({'backend':'pillow','runs':result,'bytes':len(buffer.getvalue()),'size':image.size}))


if __name__=='__main__':
    main()
