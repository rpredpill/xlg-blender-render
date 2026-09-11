from pathlib import Path
import json, shutil, sys
import numpy as np
from PIL import Image
import cv2

# V15 hidden-art completion for layered 2D rigging.
# Only transparent pixels that are covered by a known foreground occluder are changed.
# Existing visible pixels are preserved byte-for-byte.

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "neutral57")
MANIFEST = ROOT / "manifest.json"
PARTS = ROOT / "parts"
OUT = ROOT / "output"
OUT.mkdir(parents=True, exist_ok=True)
BACKUP = ROOT / "parts_hidden_backup"
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

def union_alpha(names):
    m=np.zeros((H,W),np.uint8)
    for n in names:
        if n not in info: continue
        a=to_canvas(n)[...,3]
        m=np.maximum(m,a)
    return m

def restrict_bbox(mask,bbox):
    x0,y0,x1,y1=bbox
    out=np.zeros_like(mask)
    out[y0:y1,x0:x1]=mask[y0:y1,x0:x1]
    return out

def feather_mask(binary, px=2):
    if not np.any(binary):
        return binary.astype(np.uint8)
    m=(binary.astype(np.uint8)*255)
    if px > 0:
        k=px*2+1
        m=cv2.GaussianBlur(m,(k,k),0)
    return m

def repair_part(target, occluders, max_distance, tag):
    rgba,bbox,path=load_crop(target)
    canvas=to_canvas(target)
    alpha=canvas[...,3]
    visible=(alpha>=8).astype(np.uint8)

    occ=union_alpha(occluders)
    occ=(occ>=8).astype(np.uint8)

    # Distance to nearest existing visible target pixel.
    inv=(1-visible).astype(np.uint8)
    dist=cv2.distanceTransform(inv,cv2.DIST_L2,5)

    # Crucial safety rule: only fill pixels that are currently transparent,
    # were hidden by a known foreground part, are near the target silhouette,
    # and lie inside the target's original crop bbox.
    hole=(alpha<=4)
    candidate=(hole & (occ>0) & (dist<=float(max_distance))).astype(np.uint8)
    candidate=restrict_bbox(candidate,bbox)

    # Remove isolated speckles and bridge 1-2px antialias gaps.
    candidate=cv2.morphologyEx(candidate,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
    candidate=cv2.morphologyEx(candidate,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))

    count=int(candidate.sum())
    if count == 0:
        return {"target":target,"tag":tag,"filled_pixels":0,"occluders":occluders}

    # Inpaint RGB strictly inside the hidden candidate.
    rgb=canvas[...,:3].copy()
    inpaint_mask=(candidate*255).astype(np.uint8)
    repaired_rgb=cv2.inpaint(rgb,inpaint_mask,5,cv2.INPAINT_TELEA)

    # Alpha interior becomes opaque; soften only the 2px edge.
    soft=feather_mask(candidate,2)
    out=canvas.copy()
    change=candidate.astype(bool)
    out[change,:3]=repaired_rgb[change,:3]
    out[...,3]=np.maximum(out[...,3],soft)

    # Preserve every pre-existing nontransparent pixel exactly.
    old_visible=alpha>4
    original=canvas
    out[old_visible]=original[old_visible]

    # Write the repaired crop only.
    x0,y0,x1,y1=bbox
    repaired_crop=out[y0:y1,x0:x1]
    backup=BACKUP/path.name
    if not backup.exists():
        shutil.copy2(path,backup)
    Image.fromarray(repaired_crop,"RGBA").save(path)

    # Diagnostic image: magenta = newly synthesized hidden region.
    dbg=np.zeros((H,W,4),np.uint8)
    dbg[change]=np.array([255,0,255,185],np.uint8)
    Image.fromarray(dbg,"RGBA").save(OUT/f"UNDERPAINT_MASK_{target}.png")

    return {
        "target":target,
        "tag":tag,
        "filled_pixels":count,
        "occluders":occluders,
        "max_distance":max_distance,
        "bbox":bbox,
    }

repairs=[]

# 1) Hair that should continue underneath the foreground arms.
repairs.append(repair_part(
    "TwinTail_L_Main",
    ["Sleeve_L","Cuff_L","Hand_L"],
    120,
    "hair_behind_left_arm"
))
repairs.append(repair_part(
    "TwinTail_R_Main",
    ["Sleeve_R","Cuff_R","Hand_R"],
    120,
    "hair_behind_right_arm"
))
repairs.append(repair_part(
    "SideHair_L",
    ["Sleeve_L","Cuff_L","Hand_L"],
    70,
    "sidehair_behind_left_arm"
))
repairs.append(repair_part(
    "SideHair_R",
    ["Sleeve_R","Cuff_R","Hand_R"],
    70,
    "sidehair_behind_right_arm"
))
repairs.append(repair_part(
    "Hair_Back",
    ["Sleeve_L","Sleeve_R","Shoulder"],
    55,
    "backhair_hidden_by_upper_body"
))

# 2) Clothing hidden under the two front side-hair strands.
front_hair=["SideHair_L","SideHair_R"]
repairs.append(repair_part(
    "Torso",
    front_hair,
    95,
    "torso_behind_front_hair"
))
repairs.append(repair_part(
    "Shoulder",
    front_hair,
    75,
    "shoulder_behind_front_hair"
))

# Combined QA visualization: repaired target canvases + magenta patch overlay.
qa=np.zeros((H,W,4),np.uint8)
def alpha_over(dst,src):
    sa=src[...,3:4].astype(np.float32)/255.0
    da=dst[...,3:4].astype(np.float32)/255.0
    oa=sa+da*(1-sa)
    rgb=np.where(oa>1e-6,(src[...,:3]*sa + dst[...,:3]*da*(1-sa))/np.maximum(oa,1e-6),0)
    dst[...,:3]=np.clip(rgb,0,255).astype(np.uint8)
    dst[...,3]=np.clip(oa[...,0]*255,0,255).astype(np.uint8)

# Put repaired clothing/hair in a common transparent canvas.
for n in ["Torso","Shoulder","Hair_Back","TwinTail_L_Main","TwinTail_R_Main","SideHair_L","SideHair_R"]:
    if n in info:
        alpha_over(qa,to_canvas(n))

# Overlay repair masks.
for r in repairs:
    p=OUT/f"UNDERPAINT_MASK_{r['target']}.png"
    if p.exists():
        alpha_over(qa,np.array(Image.open(p).convert("RGBA")))

Image.fromarray(qa,"RGBA").save(OUT/"UNDERPAINT_QA_COMBINED.png")
(OUT/"underpaint_report.json").write_text(json.dumps(repairs,ensure_ascii=False,indent=2),encoding="utf-8")

print("V15_UNDERPAINT_REPORT")
for r in repairs:
    print(r)
print("V15_UNDERPAINT_TOTAL",sum(r["filled_pixels"] for r in repairs))
