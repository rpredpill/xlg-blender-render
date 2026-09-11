#!/usr/bin/env bash
set -e
rm -rf neutral57 output
mkdir -p neutral57 output
python -c "import zipfile; zipfile.ZipFile('neutral57.zip').extractall('neutral57')"
python - <<'PY'
from pathlib import Path
p=Path('neutral57/render_neutral57.py')
s=p.read_text()
s=s.replace("tx.interpolation = 'Linear'","tx.interpolation = 'Closest'")
s=s.replace("m.surface_render_method = 'BLENDED'","m.surface_render_method = 'DITHERED'")
s=s.replace("sc.render.resolution_x = 471","sc.render.resolution_x = 941")
s=s.replace("sc.render.resolution_y = 836","sc.render.resolution_y = 1672")
s=s.replace("cd.ortho_scale = max(canvas_h, canvas_w/aspect) * 1.01","cd.ortho_scale = max(canvas_h, canvas_w/aspect)")
# The visible horizontal Brow_L line is rejected for this character's neutral look.
# Keep the object in the .blend for future expression rigging, but do not render it in neutral.
s += "\n# neutral brow cleanup\nb=bpy.data.objects.get('Brow_L')\nif b: b.hide_render=True\n"
needle="sc.render.image_settings.color_depth = '8'"
insert = needle + "\nsc.view_settings.view_transform = 'Standard'\nsc.view_settings.look = 'NONE'\nsc.view_settings.exposure = 0.0\nsc.view_settings.gamma = 1.0"
s=s.replace(needle, insert)
p.write_text(s)
PY
pip install --no-cache-dir bpy==4.5.13 pillow
python neutral57/render_neutral57.py
python - <<'PY'
from PIL import Image, ImageChops, ImageStat
from pathlib import Path
root=Path('neutral57')
out=root/'output'
ren=Image.open(out/'Neutral_REAL_FULL57.png').convert('RGBA')
ref=Image.open(root/'XLG_Neutral_Reference.png').convert('RGBA')
def white(im):
    bg=Image.new('RGBA', im.size, (255,255,255,255))
    bg.alpha_composite(im)
    return bg.convert('RGB')
rw=white(ren); fw=white(ref)
rw.save(out/'Neutral_REAL_FULL57_WHITE.png')
d=ImageChops.difference(rw,fw)
stat=ImageStat.Stat(d)
mae=sum(stat.mean)/3
mx=max(e[1] for e in d.getextrema())
print('PIXEL_QA_MAE', round(mae,4), 'MAX', mx, 'SIZE', ren.size)
d.point(lambda x:min(255,x*4)).save(out/'Neutral_DIFF_x4.png')
PY
cp -f neutral57/output/* output/
python - <<'PY'
from pathlib import Path
p=Path('output')
links=''.join(f'<li><a href="/{x.name}">{x.name}</a></li>' for x in sorted(p.iterdir()) if x.name!='index.html')
(p/'index.html').write_text('<html><body><h1>XLG Neutral 57 — Full Resolution Standard View</h1><ul>'+links+'</ul></body></html>')
PY
