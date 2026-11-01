"""Read-only reference comparison; never creates or replaces a DOM regression baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

SLUGS = ['overview','market','account','recommend','paper','run','create','history','backtest','report','connections','diagnostics']

def fit_reference(image: Image.Image, width: int, height: int) -> tuple[Image.Image, dict[str, str | int]]:
    scaled_height = round(image.height * width / image.width)
    scaled = image.resize((width, scaled_height), Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', (width, height), '#101721')
    offset = (height - scaled_height) // 2
    canvas.paste(scaled, (0, offset))
    return canvas, {'method':'proportional-width then centered vertical crop/pad','scaledHeightRounded':scaled_height,'verticalOffset':offset,'resampling':'LANCZOS'}

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--captures',required=True,type=Path)
    parser.add_argument('--manifest',type=Path,default=Path(__file__).resolve().parents[2]/'docs/ui-reference/manifest.json')
    parser.add_argument('--prefix',default='final')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or args.captures / ('reference-comparison-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
    if output.exists() and any(output.iterdir()):
        parser.error('Output must be new or empty. Existing comparison evidence will not be overwritten.')
    output.mkdir(parents=True,exist_ok=True)
    manifest=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    results=[]
    for row in manifest['pages']:
        number=int(row['pageId'].split('-')[0]); surface=row['pageId'].split('-')[1]
        stem=f'{args.prefix}-{surface}-{number:02d}_{SLUGS[number-1]}'
        candidates=[args.captures/(stem+suffix) for suffix in ['-viewport.png','-viewport.jpg','.png','.jpg']]
        capture=next((p for p in candidates if p.is_file()),None)
        result={'pageId':row['pageId'],'route':row['route'],'fixtureId':row['fixtureId'],'capture':str(capture) if capture else None,'reference':row['file'],'originalSha':row['originalSha']}
        if capture is None:
            result['state']='MISSING_CAPTURE';results.append(result);continue
        original=args.manifest.parent/row['file']
        if hashlib.sha256(original.read_bytes()).hexdigest()!=row['originalSha']:
            raise ValueError(f'Original reference hash mismatch: {original}')
        actual=Image.open(capture).convert('RGB');source=Image.open(original).convert('RGB')
        width,height=row['viewport']['width'],row['viewport']['height']
        reference,policy=fit_reference(source,width,height)
        result.update(actualSize=list(actual.size),viewport=[width,height],transform=policy)
        if actual.width!=width or actual.height<height-1:
            result['state']='CAPTURE_DIMENSION_MISMATCH';results.append(result);continue
        if actual.height==height-1:
            height=actual.height
            reference=reference.crop((0,0,width,height))
            result['captureCrop']='Browser viewport API omitted the final 1px row; reference bottom row cropped to match. Actual pixels were not resized or padded.'
        actual=actual.crop((0,0,width,height))
        diff=ImageChops.difference(reference,actual)
        side=Image.new('RGB',(width*2,height));side.paste(reference,(0,0));side.paste(actual,(width,0))
        side.save(output/(row['pageId']+'-side-by-side.png'))
        Image.blend(reference,actual,0.5).save(output/(row['pageId']+'-overlay.png'))
        diff.save(output/(row['pageId']+'-difference.png'))
        red,green,blue=diff.split()
        maximum=ImageChops.lighter(ImageChops.lighter(red,green),blue)
        result.update(state='REVIEW_REQUIRED',meanAbsoluteChannelDifference=round(sum(ImageStat.Stat(diff).mean)/3,4),differentPixelCount=width*height-maximum.histogram()[0])
        results.append(result)
    report={'purpose':'Mockup alignment evidence only; not automatic pass/fail or an approved regression baseline. Inspect boundaries, typography, chart, values, CTA and warnings against documented exceptions. The 2 CSS px boundary target has not been proven by these aggregate image metrics.','clock':manifest['clock'],'prefix':args.prefix,'captures':results}
    (output/'comparison-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'screens':len(results),'readyForReview':sum(r['state']=='REVIEW_REQUIRED' for r in results),'missing':sum(r['state']=='MISSING_CAPTURE' for r in results),'dimensionMismatch':sum(r['state']=='CAPTURE_DIMENSION_MISMATCH' for r in results)},ensure_ascii=True))

if __name__=='__main__':
    main()
