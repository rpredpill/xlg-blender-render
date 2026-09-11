from pathlib import Path
import json, shutil, sys
import numpy as np
from PIL import Image
import cv2

# V17 clean underpaint rebuild.
# 1) Remove disconnected foreign islands accidentally baked into hair textures.
# 2) Rebuild hair underpaint from the SAME tail's neighboring texture by reflection.
# 3) Rebuild clothing/neck underpaint from symmetric clean pixels.
# 4) Never modify visible neutral pixels except confirmed foreign islands.

ROOT=Path(sys.argv[1] if len(sys.argv)>1 else "neutral57")
MANIFEST=ROOT/"manifest.json"
PARTS=ROOT/"parts"
OUT=ROOT/"output"
OUT.mkdir(parents=True,exist_ok=True)
BACKUP=ROOT/"parts_hidden_backup_v17"
BACKUP.mkdir(parents=True,exist_ok=True)

manifest=json.loads(MANIFEST.read_text(encoding="utf-8"))
W,H=map(int,manifest["canvas"])
info={p["name"]:p for p in manifest["parts"]}

def ppath(name):
    p=PARTS/f"{name}.png"
    if not p.exists():
        hits=list(ROOT.rglob(f"{name}.png"))
        if not hits: raise FileNotFoundError(name)
        p=hits[0]
    return p

def load(name):
    p=ppath(name)
    arr=np.array(Image.open(p).convert("RGBA"))
    x0,y0,x1,y1=map(int,info[name]["crop_bbox"])
    if arr.shape[1]!=(x1-x0) or arr.shape[0]!=(y1-y0):
        arr=np.array(Image.fromarray(arr).resize((x1-x0,y1-y0),Image.Resampling.LANCZOS))
    return arr,(x0,y0,x1,y1),p

def canvas(name):
    crop,b,p=load(name)
    x0,y0,x1,y1=b
    out=np.zeros((H,W,4),np.uint8)
    out[y0:y1,x0:x1]=crop
    return out

def save_canvas(name,c):
    crop,b,p=load(name)
    x0,y0,x1,y1=b
    bp=BACKUP/p.name
    if not bp.exists(): shutil.copy2(p,bp)
    Image.fromarray(c[y0:y1,x0:x1],"RGBA").save(p)

def alpha(name,t=8):
    return canvas(name)[...,3]>=t

def union(names):
    m=np.zeros((H,W),bool)
    for n in names:
        if n in info: m|=alpha(n)
    return m

def hull(mask):
    ys,xs=np.where(mask)
    out=np.zeros((H,W),np.uint8)
    if len(xs)>=3:
        pts=np.column_stack([xs,ys]).astype(np.int32)
        cv2.fillConvexPoly(out,cv2.convexHull(pts),1)
    return out.astype(bool)

def dist_from(mask):
    return cv2.distanceTransform((~mask).astype(np.uint8),cv2.DIST_L2,5)

def cleanup_tail_islands(name):
    c=canvas(name)
    a=(c[...,3]>8).astype(np.uint8)
    n,lab,stats,cents=cv2.connectedComponentsWithStats(a,8)
    if n<=2: return 0
    areas=stats[1:,cv2.CC_STAT_AREA]
    keep=1+int(np.argmax(areas))
    removed=0
    for i in range(1,n):
        if i==keep: continue
        m=lab==i
        area=int(m.sum())
        # Main tail texture must be one continuous art island. Detached pieces
        # in the source were sleeve/hand/skin contamination.
        if area>0:
            c[m]=0
            removed+=area
    save_canvas(name,c)
    return removed

def nearest_clean_x(row_clean,x):
    if len(row_clean)==0: return None
    k=np.searchsorted(row_clean,x)
    cand=[]
    if k>0: cand.append(int(row_clean[k-1]))
    if k<len(row_clean): cand.append(int(row_clean[k]))
    return min(cand,key=lambda q:abs(q-x))

def reflected_source_x(row_clean,x):
    if len(row_clean)==0: return None
    left=row_clean[row_clean<x]
    right=row_clean[row_clean>x]
    choices=[]
    if len(left):
        b=int(left[-1])
        d=x-b
        # reflect into the clean side; if the reflection lands in a gap,
        # snap to the nearest actual clean hair pixel.
        target=b-d
        sx=nearest_clean_x(row_clean,target)
        if sx is not None: choices.append((d,sx))
    if len(right):
        b=int(right[0])
        d=b-x
        target=b+d
        sx=nearest_clean_x(row_clean,target)
        if sx is not None: choices.append((d,sx))
    if choices:
        return min(choices,key=lambda z:z[0])[1]
    return nearest_clean_x(row_clean,x)

def hair_underpaint(name,occluders,maxdist,tag):
    c=canvas(name)
    occ=union(occluders)
    vis=c[...,3]>=8
    env=hull(vis)
    d=dist_from(vis)
    cover=occ & env & (d<=float(maxdist))
    if not np.any(cover):
        return {"target":name,"tag":tag,"filled":0}

    out=c.copy()
    changed=np.zeros((H,W),bool)
    rows=np.where(cover.any(1))[0]
    for y in rows:
        row_clean=np.where(vis[y] & ~occ[y])[0]
        if len(row_clean)<2: continue
        for x in np.where(cover[y])[0]:
            sx=reflected_source_x(row_clean,int(x))
            if sx is None: continue
            out[y,x,:3]=c[y,sx,:3]
            out[y,x,3]=255
            changed[y,x]=True

    # Two-pass tiny vertical blend to avoid repeated horizontal stripes while
    # keeping the original tail's strand direction and contrast.
    if np.any(changed):
        sm=cv2.GaussianBlur(out[...,:3],(1,5),0)
        edge=cv2.morphologyEx(changed.astype(np.uint8),cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8)).astype(bool)
        out[edge,:3]=sm[edge,:3]
        save_canvas(name,out)

    dbg=np.zeros((H,W,4),np.uint8)
    dbg[changed]=np.array([255,0,255,185],np.uint8)
    Image.fromarray(dbg,"RGBA").save(OUT/f"UNDERPAINT_MASK_{name}.png")
    return {"target":name,"tag":tag,"filled":int(changed.sum()),"method":"same-tail reflected texture"}

def symmetric_underpaint(name,occluders,maxdist,tag,skin=False):
    c=canvas(name)
    occ=union(occluders)
    vis=c[...,3]>=8
    env=hull(vis)
    d=dist_from(vis)
    cover=occ & env & (d<=float(maxdist))
    out=c.copy()
    changed=np.zeros((H,W),bool)

    ys,xs=np.where(vis & ~occ)
    if not len(xs): return {"target":name,"tag":tag,"filled":0}
    cx=(xs.min()+xs.max())/2.0

    if skin:
        pix=c[vis & ~occ,:3]
        # robust local skin median
        med=np.median(pix,axis=0).astype(np.uint8)
        out[cover,:3]=med
        out[cover,3]=255
        changed=cover.copy()
    else:
        for y in np.where(cover.any(1))[0]:
            row=np.where(vis[y] & ~occ[y])[0]
            if not len(row): continue
            for x in np.where(cover[y])[0]:
                mx=int(round(2*cx-x))
                if 0<=mx<W and (vis[y,mx] and not occ[y,mx]):
                    sx=mx
                else:
                    sx=int(row[np.argmin(np.abs(row-x))])
                out[y,x,:3]=c[y,sx,:3]
                out[y,x,3]=255
                changed[y,x]=True

    if np.any(changed):
        edge=cv2.morphologyEx(changed.astype(np.uint8),cv2.MORPH_GRADIENT,np.ones((3,3),np.uint8)).astype(bool)
        sm=cv2.GaussianBlur(out[...,:3],(3,3),0)
        out[edge,:3]=sm[edge,:3]
        save_canvas(name,out)

    dbg=np.zeros((H,W,4),np.uint8)
    dbg[changed]=np.array([0,220,255,185],np.uint8)
    Image.fromarray(dbg,"RGBA").save(OUT/f"UNDERPAINT_MASK_{name}.png")
    return {"target":name,"tag":tag,"filled":int(changed.sum()),"method":"same-part symmetry/median"}

report=[]
# FIRST: purge source contamination before creating any underpaint.
for s in ("L","R"):
    for seg in ("Root","Main","Tip"):
        n=f"TwinTail_{s}_{seg}"
        if n in info:
            rem=cleanup_tail_islands(n)
            report.append({"target":n,"action":"remove_foreign_islands","removed":rem})

# SECOND: rebuild hidden hair from its own texture.
for s in ("L","R"):
    arm=[f"Sleeve_{s}",f"Cuff_{s}",f"Hand_{s}"]
    for seg,md in (("Root",65),("Main",125),("Tip",75)):
        n=f"TwinTail_{s}_{seg}"
        if n in info:
            report.append(hair_underpaint(n,arm,md,f"{n}_behind_arm"))

# Clothing hidden by front side hair.
front=["SideHair_L","SideHair_R"]
for n,md in (("Torso",85),("Shoulder",70)):
    if n in info:
        report.append(symmetric_underpaint(n,front,md,f"{n}_behind_front_hair"))
if "Neck_Full" in info:
    report.append(symmetric_underpaint("Neck_Full",front,45,"Neck_behind_front_hair",skin=True))

# Side hair should not expose empty arm/torso pixels at its root.
for s in ("L","R"):
    n=f"Sleeve_{s}"; h=f"SideHair_{s}"
    if n in info and h in info:
        report.append(symmetric_underpaint(n,[h],45,f"{n}_behind_{h}"))

# QA image: neutral rear parts + repair masks.
qa=np.zeros((H,W,4),np.uint8)
def over(dst,src):
    sa=src[...,3:4].astype(np.float32)/255
    da=dst[...,3:4].astype(np.float32)/255
    oa=sa+da*(1-sa)
    rgb=np.where(oa>1e-6,(src[...,:3]*sa+dst[...,:3]*da*(1-sa))/np.maximum(oa,1e-6),0)
    dst[...,:3]=np.clip(rgb,0,255).astype(np.uint8)
    dst[...,3]=np.clip(oa[...,0]*255,0,255).astype(np.uint8)

for n in [
    "Torso","Shoulder","Neck_Full",
    "TwinTail_L_Root","TwinTail_L_Main","TwinTail_L_Tip",
    "TwinTail_R_Root","TwinTail_R_Main","TwinTail_R_Tip"
]:
    if n in info: over(qa,canvas(n))
for p in OUT.glob("UNDERPAINT_MASK_*.png"):
    over(qa,np.array(Image.open(p).convert("RGBA")))
Image.fromarray(qa,"RGBA").save(OUT/"UNDERPAINT_QA_COMBINED.png")

(OUT/"underpaint_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print("V17_CLEAN_REBUILD")
for x in report: print(x)
