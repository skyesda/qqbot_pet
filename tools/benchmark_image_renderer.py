"""Compare full-canvas vs panel screenshots on the same real card snapshots."""
import io
import json
import os
from pathlib import Path
import statistics
import sys
import time
from PIL import Image, ImageChops

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from petpark.image_renderer import ImageRenderer
from petpark.card_theme import crop_canvas

if os.name=='nt' and not os.environ.get('PETPARK_CHROME_PATH'):
    os.environ['PETPARK_CHROME_PATH']='C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
out=ROOT.parent/'output/image-render-optimization'
out.mkdir(parents=True,exist_ok=True)
inputs=[ROOT.parent/'output/cultivator_card_single.html',ROOT.parent/'output/image-performance/visual-check/pet.html']
report=[]
renderer=ImageRenderer()
try:
    for source in inputs:
        if not source.is_file(): continue
        html=source.read_text(encoding='utf-8')
        renderer._worker.submit(renderer._screenshot,html,900,5200,True).result()
        timings={False:[],True:[]}
        images={}
        for _ in range(3):
            for clipped in [False,True]:
                started=time.perf_counter()
                raw=renderer._worker.submit(renderer._screenshot,html,900,5200,clipped).result()
                timings[clipped].append((time.perf_counter()-started)*1000)
                images[clipped]=crop_canvas(Image.open(io.BytesIO(raw)))
        a,b=images[False],images[True]
        assert a.size==b.size,(source.name,a.size,b.size)
        delta=ImageChops.difference(a,b)
        assert delta.getbbox() is None,source.name
        b.save(out/(source.stem+'.png'))
        report.append({'card':source.name,'size':b.size,'before_median_ms':round(statistics.median(timings[False]),1),
                       'after_median_ms':round(statistics.median(timings[True]),1),'pixels_identical':True})
finally:
    renderer.close()
(out/'benchmark.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
