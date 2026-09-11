from pathlib import Path
import json, shutil, sys
import numpy as np
from PIL import Image
import cv2

# V16 occlusion-safe underpaint.
# Goal:
# - Do NOT invent a huge blurry area with generic inpainting.
# - Replace every pixel hidden by the actual foreground occluder, including
#   contaminated pixels accidentally baked into a rear layer.
# - Hair uses a coherent clean donor texture from Hair_Back.
# - Clothing/skin use symmetry or nearest clean same-row pixels.
# - Pixels visible in the neutral pose are never changed.

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "neutral57")
MANIFEST = ROOT / "manifest.json"
PARTS = ROOT / "parts"
OUT = ROOT / "output"
OUT.mkdir(parents=True, exist_ok=True)
BACKUP = ROOT / "parts_hidden_backup_v16"
BACKUP.mkdir(parents=True, exist_ok=True)

manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
W, H = map(int, manifest["canvas"])
info = {p["name"]: p for p in manifest["parts"]}

def part_path(name):
    p = PARTS / f"{name}.png"
    if not p.exists():
        hits = list(ROOT.rglob(f"{name}.png"))
        if not hits:
            raise FileNotFoundError(name)
        p = hits[0]
    return p

def load_crop(name):
    p = part_path(name)
    im = Image.open(p).convert("RGBA")
    x0,y0,x1,y1 = map(int, info[name]["crop_bbox"])
    tw,th=x1-x0,y1-y0
    if im.size != (tw,th):
        im = im.resize((tw,th), Image.Resampling.LANCZOS)
    return np.array(im), (x0,y0,x1,y1), p

def to_canvas(name):
    crop,bbox,p = load_crop(name)
    x0,y0,x1,y1=bbox
    c=np.zeros((H,W,4),np.uint8)
    c[y0:y1,x0:x1]=crop
    return c

def save_canvas_back(name, canvas):
    crop,bbox,path=load_crop(name)
    x0,y0,x1,y1=bbox
    if not (BACKUP/path.name).exists():
        shutil.copy2(path, BACKUP/path.name)
    Image.fromarray(canvas[y0:y1,x0:x1],"RGBA").save(path)

def alpha_mask(name, threshold=8):
    return to_canvas(name)[...,3] >= threshold

def union_alpha(names):
    out=np.zeros((H,W),bool)
    for n in names:
        if n in info:
            out |= alpha_mask(n)
    return out

def convex_hull(mask):
    ys,xs=np.where(mask)
    out=np.zeros((H,W),np.uint8)
    if len(xs)>=3:
        pts=np.column_stack([xs,ys]).astype(np.int32)
        cv2.fillConvexPoly(out,cv2.convexHull(pts),1)
    return out.astype(bool)

def distance_from(mask):
    # distance of non-mask pixels to nearest mask pixel
    inv=(~mask).astype(np.uint8)
    return cv2.distanceTransform(inv,cv2.DIST_L2,5)

def tiny_edge_blend(canvas, changed):
    if not np.any(changed):
        return canvas
    edge=cv2.morphologyEx(changed.astype(np.uint8),cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8)).astype(bool)
    blur=cv2.GaussianBlur(canvas[...,:3],(3,3),0)
    canvas[edge,:3]=blur[edge]
    return canvas

# Build a coherent donor texture from the clean middle of Hair_Back.
hair_donor_full = to_canvas("Hair_Back")
hair_a = hair_donor_full[...,3] >= 8
ys,xs=np.where(hair_a)
hx0,hx1=xs.min(),xs.max()+1
hy0,hy1=ys.min(),ys.max()+1
dx0=int(hx0 + .18*(hx1-hx0))
dx1=int(hx0 + .82*(hx1-hx0))
dy0=int(hy0 + .08*(hy1-hy0))
dy1=int(hy0 + .66*(hy1-hy0))
HAIR_DONOR = hair_donor_full[dy0:dy1,dx0:dx1,:3].copy()

repairs=[]

def hair_underpaint(target, occluders, max_distance, tag):
    canvas=to_canvas(target)
    occ=union_alpha(occluders)
    visible=canvas[...,3] >= 8

    # Only trust target pixels that are not hidden by the front layer.
    # This prevents sleeve/skin fragments accidentally baked into the hair
    # part from shaping the reconstructed silhouette.
    clean_visible = visible & ~occ
    env = convex_hull(clean_visible)
    dist = distance_from(clean_visible)
    cover = occ & env & (dist <= float(max_distance))

    count=int(cover.sum())
    if count==0:
        return {"target":target,"tag":tag,"replaced_pixels":0,"occluders":occluders}

    ys,xs=np.where(cover)
    x0,y0,x1,y1=xs.min(),ys.min(),xs.max()+1,ys.max()+1
    tex=cv2.resize(HAIR_DONOR,(x1-x0,y1-y0),interpolation=cv2.INTER_CUBIC)

    # Match donor tone to genuine nearby hair, not to the hidden contaminated region.
    ring=cv2.dilate(cover.astype(np.uint8),np.ones((25,25),np.uint8)).astype(bool) & clean_visible
    rgb=canvas[...,:3]
    lum=rgb.mean(2)
    ring &= (lum>12) & (lum<155)
    trg=rgb[ring].astype(np.float32)
    src=tex.reshape(-1,3).astype(np.float32)
    if len(trg)>20:
        mt=np.median(trg,axis=0)
        st=np.std(trg,axis=0)+1.0
        ms=np.median(src,axis=0)
        ss=np.std(src,axis=0)+1.0
        tex=(tex.astype(np.float32)-ms)/ss*np.minimum(st,ss*1.20)+mt
        tex=np.clip(tex,0,255).astype(np.uint8)

    local=cover[y0:y1,x0:x1]
    # Replace ALL covered pixels, not only transparent ones. This removes
    # foreground contamination that would become visible when the arm moves.
    canvas[y0:y1,x0:x1,:3][local]=tex[local]
    canvas[y0:y1,x0:x1,3][local]=255
    canvas=tiny_edge_blend(canvas,cover)
    save_canvas_back(target,canvas)

    dbg=np.zeros((H,W,4),np.uint8)
    dbg[cover]=np.array([255,0,255,180],np.uint8)
    Image.fromarray(dbg,"RGBA").save(OUT/f"UNDERPAINT_MASK_{target}.png")

    return {
        "target":target,"tag":tag,"replaced_pixels":count,
        "occluders":occluders,"max_distance":max_distance,
        "replacement":"coherent Hair_Back donor texture"
    }

def mirror_underpaint(target, occluders, max_distance, tag, local_center=False):
    canvas=to_canvas(target)
    occ=union_alpha(occluders)
    visible=canvas[...,3]>=8
    clean=visible & ~occ
    env=convex_hull(clean)
    dist=distance_from(clean)
    cover=occ & env & (dist<=float(max_distance))

    count=int(cover.sum())
    if count==0:
        return {"target":target,"tag":tag,"replaced_pixels":0,"occluders":occluders}

    ys,xs=np.where(clean)
    if len(xs)==0:
        return {"target":target,"tag":tag,"replaced_pixels":0,"occluders":occluders}
    if local_center:
        cx=float((xs.min()+xs.max())/2)
    else:
        cx=float(np.median(xs))

    out=canvas.copy()
    changed=np.zeros((H,W),bool)

    for y in np.where(cover.any(axis=1))[0]:
        row_clean=np.where(clean[y])[0]
        if len(row_clean)==0:
            continue
        for x in np.where(cover[y])[0]:
            mx=int(round(2*cx-x))
            if 0<=mx<W and clean[y,mx]:
                sx=mx
            else:
                sx=int(row_clean[np.argmin(np.abs(row_clean-x))])
            out[y,x,:3]=canvas[y,sx,:3]
            out[y,x,3]=255
            changed[y,x]=True

    out=tiny_edge_blend(out,changed)
    save_canvas_back(target,out)

    dbg=np.zeros((H,W,4),np.uint8)
    dbg[changed]=np.array([0,220,255,180],np.uint8)
    Image.fromarray(dbg,"RGBA").save(OUT/f"UNDERPAINT_MASK_{target}.png")

    return {
        "target":target,"tag":tag,"replaced_pixels":int(changed.sum()),
        "occluders":occluders,"max_distance":max_distance,
        "replacement":"mirrored/nearest clean pixels"
    }

# Hair hidden by the arms: repair every tail segment that actually intersects.
for side in ("L","R"):
    arm=[f"Sleeve_{side}",f"Cuff_{side}",f"Hand_{side}"]
    for seg,dist in (("Root",70),("Main",120),("Tip",90)):
        n=f"TwinTail_{side}_{seg}"
        if n in info:
            repairs.append(hair_underpaint(n,arm,dist,f"{n}_behind_arm"))
    n=f"SideHair_{side}"
    if n in info:
        repairs.append(hair_underpaint(n,arm,55,f"{n}_behind_arm"))

# Back hair can also be briefly exposed around the upper sleeve/shoulder.
if "Hair_Back" in info:
    repairs.append(hair_underpaint(
        "Hair_Back",["Sleeve_L","Sleeve_R","Shoulder"],55,"Hair_Back_behind_upper_body"
    ))

# Clothing / neck hidden by the two front side-hair strands.
front_hair=["SideHair_L","SideHair_R"]
for n,d in (("Torso",95),("Shoulder",80),("Neck_Full",55)):
    if n in info:
        repairs.append(mirror_underpaint(n,front_hair,d,f"{n}_behind_front_hair"))

# The very top of each sleeve can also sit under its same-side front strand.
for side in ("L","R"):
    n=f"Sleeve_{side}"
    h=f"SideHair_{side}"
    if n in info and h in info:
        repairs.append(mirror_underpaint(n,[h],55,f"{n}_behind_{h}",local_center=True))

# QA composite: repaired rear layers only + colored repair overlay.
qa=np.zeros((H,W,4),np.uint8)
def alpha_over(dst,src):
    sa=src[...,3:4].astype(np.float32)/255.0
    da=dst[...,3:4].astype(np.float32)/255.0
    oa=sa+da*(1-sa)
    rgb=np.where(oa>1e-6,(src[...,:3]*sa + dst[...,:3]*da*(1-sa))/np.maximum(oa,1e-6),0)
    dst[...,:3]=np.clip(rgb,0,255).astype(np.uint8)
    dst[...,3]=np.clip(oa[...,0]*255,0,255).astype(np.uint8)

for n in [
    "Torso","Shoulder","Neck_Full",
    "Hair_Back",
    "TwinTail_L_Root","TwinTail_L_Main","TwinTail_L_Tip",
    "TwinTail_R_Root","TwinTail_R_Main","TwinTail_R_Tip",
    "SideHair_L","SideHair_R"
]:
    if n in info:
        alpha_over(qa,to_canvas(n))

for r in repairs:
    p=OUT/f"UNDERPAINT_MASK_{r['target']}.png"
    if p.exists():
        alpha_over(qa,np.array(Image.open(p).convert("RGBA")))

Image.fromarray(qa,"RGBA").save(OUT/"UNDERPAINT_QA_COMBINED.png")
(OUT/"underpaint_report.json").write_text(json.dumps(repairs,ensure_ascii=False,indent=2),encoding="utf-8")

print("V16_OCCLUSION_SAFE_REPORT")
for r in repairs:
    print(r)
print("V16_OCCLUSION_SAFE_TOTAL",sum(r["replaced_pixels"] for r in repairs))
