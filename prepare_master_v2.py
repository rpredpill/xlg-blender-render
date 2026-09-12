from pathlib import Path
from PIL import Image
import numpy as np, cv2, json, zipfile, shutil

ROOT=Path("neutral57")
OUT=Path("output")
OUT.mkdir(parents=True,exist_ok=True)
W,H=941,1672

def load(name):
    return np.array(Image.open(ROOT/"parts_cropped"/f"{name}.png").convert("RGBA")) if (ROOT/"parts_cropped"/f"{name}.png").exists() else np.array(Image.open(ROOT/"parts"/f"{name}.png").convert("RGBA"))

# Prefer full-canvas parts if present. If parts_cropped is cropped, rebuild from manifest.
manifest=json.loads((ROOT/"manifest.json").read_text(encoding="utf-8"))
meta={p["name"]:p for p in manifest["parts"]}

def full(name):
    p=ROOT/"parts"/f"{name}.png"
    arr=np.array(Image.open(p).convert("RGBA"))
    if arr.shape[1]==W and arr.shape[0]==H:
        return arr
    x0,y0,x1,y1=map(int,meta[name]["crop_bbox"])
    out=np.zeros((H,W,4),np.uint8)
    if arr.shape[1]!=(x1-x0) or arr.shape[0]!=(y1-y0):
        arr=np.array(Image.fromarray(arr).resize((x1-x0,y1-y0),Image.Resampling.LANCZOS))
    out[y0:y1,x0:x1]=arr
    return out

def over(dst,src):
    sa=src[...,3:4].astype(np.float32)/255
    da=dst[...,3:4].astype(np.float32)/255
    oa=sa+da*(1-sa)
    rgb=np.where(oa>1e-8,(src[...,:3]*sa+dst[...,:3]*da*(1-sa))/np.maximum(oa,1e-8),0)
    out=dst.copy()
    out[...,:3]=np.clip(rgb,0,255).astype(np.uint8)
    out[...,3]=np.clip(oa[...,0]*255,0,255).astype(np.uint8)
    return out

report={}
for side in ("L","R"):
    tail=np.zeros((H,W,4),np.uint8)
    for seg in ("Tip","Main","Root"):
        tail=over(tail,full(f"TwinTail_{side}_{seg}"))
    a=(tail[...,3]>8).astype(np.uint8)
    occ=np.zeros((H,W),np.uint8)
    for n in (f"Sleeve_{side}",f"Cuff_{side}",f"Hand_{side}"):
        occ=np.maximum(occ,(full(n)[...,3]>8).astype(np.uint8))
    occ=cv2.dilate(occ,np.ones((11,11),np.uint8),1)

    n,lab,stats,_=cv2.connectedComponentsWithStats(a,8)
    main=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
    clean=tail.copy()
    removed=[]
    for i in range(1,n):
        if i==main: continue
        m=lab==i
        area=int(m.sum()); ov=int((m&(occ>0)).sum())
        if area>=100 and ov/max(area,1)>=.4:
            clean[m]=0; removed.append({"label":i,"area":area,"overlap":ov})
    rgb=clean[...,:3].astype(np.int16)
    skin=(clean[...,3]>8)&(rgb[...,0]>120)&(rgb[...,1]>75)&(rgb[...,2]>65)&(rgb[...,0]>rgb[...,1]+12)&(rgb[...,1]>=rgb[...,2]-10)&(occ>0)
    clean[skin]=0

    ca=clean[...,3]>8
    ys,xs=np.where(ca)
    hull=np.zeros((H,W),np.uint8)
    cv2.fillConvexPoly(hull,cv2.convexHull(np.column_stack([xs,ys]).astype(np.int32)),1)
    dist=cv2.distanceTransform((~ca).astype(np.uint8),cv2.DIST_L2,5)
    mask=(occ>0)&(hull>0)&(dist<=140)

    ys2,xs2=np.where(ca|mask)
    x0=max(0,int(xs2.min())-40); x1=min(W,int(xs2.max())+41)
    y0=max(0,int(ys2.min())-40); y1=min(H,int(ys2.max())+41)
    crop=clean[y0:y1,x0:x1]
    m=mask[y0:y1,x0:x1]
    bg=np.full((crop.shape[0],crop.shape[1],3),128,np.uint8)
    al=crop[...,3:4].astype(np.float32)/255
    rgb=np.clip(crop[...,:3]*al+bg*(1-al),0,255).astype(np.uint8)
    Image.fromarray(rgb,"RGB").save(OUT/f"tail_{side}_inpaint_input.png",optimize=True)
    Image.fromarray((m.astype(np.uint8)*255),"L").save(OUT/f"tail_{side}_inpaint_mask.png",optimize=True)

    # save clean source and mask metadata for later compositor
    Image.fromarray(clean,"RGBA").save(OUT/f"tail_{side}_clean_source.png",optimize=True)
    np.savez_compressed(OUT/f"tail_{side}_meta.npz",mask=mask.astype(np.uint8),bbox=np.array([x0,y0,x1,y1]))

    report[side]={"bbox":[x0,y0,x1,y1],"removed":removed,"skin_removed":int(skin.sum()),"mask_pixels":int(mask.sum())}

(OUT/"prep_report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
(OUT/"index.html").write_text("""<!doctype html><html><body><h1>XLG Master V2 Prep</h1>
<ul>
<li><a href='/tail_L_inpaint_input.png'>L input</a></li>
<li><a href='/tail_L_inpaint_mask.png'>L mask</a></li>
<li><a href='/tail_R_inpaint_input.png'>R input</a></li>
<li><a href='/tail_R_inpaint_mask.png'>R mask</a></li>
<li><a href='/prep_report.json'>report</a></li>
</ul></body></html>""",encoding="utf-8")
print(report)
