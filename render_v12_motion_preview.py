from pathlib import Path
import math
import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parent
NEUTRAL = ROOT / "neutral57"
SRC = NEUTRAL / "render_neutral57.py"
OUT = NEUTRAL / "output"
OUT.mkdir(parents=True, exist_ok=True)

src_text = SRC.read_text(encoding="utf-8")
src_text = src_text.replace(
    "sc.render.engine = 'BLENDER_EEVEE_NEXT'",
    "sc.render.engine = 'CYCLES'\nsc.cycles.samples = 1\nsc.cycles.use_denoising = False"
)
try:
    exec(compile(src_text, str(SRC), "exec"), {"__name__":"__main__", "__file__":str(SRC)})
except SystemExit:
    pass

sc = bpy.context.scene
sc.frame_start = 1
sc.frame_end = 120
sc.render.fps = 24
sc.render.resolution_x = 360
sc.render.resolution_y = 640
sc.render.resolution_percentage = 100
sc.render.film_transparent = False
sc.render.engine = 'CYCLES'
sc.cycles.samples = 1
sc.cycles.use_denoising = False
sc.cycles.max_bounces = 0
if sc.world:
    sc.world.color = (0.96, 0.96, 0.96)

def obj(name):
    return bpy.data.objects.get(name)

def bounds_of(o):
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    xs=[p.x for p in pts]; ys=[p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)

def group_bounds(names):
    bs=[bounds_of(obj(n)) for n in names if obj(n)]
    if not bs:
        return (0,0,1,1)
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))

def preserve_parent(child, parent):
    mw=child.matrix_world.copy()
    child.parent=parent
    child.matrix_world=mw

def make_ctrl(name, pivot):
    e=bpy.data.objects.new(name, None)
    e.empty_display_type='PLAIN_AXES'
    e.empty_display_size=.18
    sc.collection.objects.link(e)
    e.location=(pivot.x,pivot.y,0)
    return e

face_parts = [
"Face_Base_Full","Ear_L","Ear_R",
"Eye_L_White","Eye_L_Iris","Eye_L_Pupil","Eye_L_Highlight","Eye_L_LowerLid",
"Eye_L_UpperLid","Eye_L_LowerLash","Eye_L_UpperLash","Eye_L_Crease",
"Eye_R_White","Eye_R_Iris","Eye_R_Pupil","Eye_R_Highlight","Eye_R_LowerLid",
"Eye_R_UpperLid","Eye_R_LowerLash","Eye_R_UpperLash","Eye_R_Crease",
"Brow_L","Brow_R","Nose_Base","Mouth_Line"
]
head_hair = [
"Hair_Back","Hair_Crown","Bang_L","Bang_C","Bang_R",
"SideHair_L","SideHair_R","Hairpin_XLG","HairBow_L","HairBow_R"
]
body_parts = ["Neck_Full","Shoulder","Torso","Skirt","Leg_L","Leg_R","Sock_L","Sock_R","Shoe_L","Shoe_R"]
armL = ["Sleeve_L","Cuff_L","Hand_L"]
armR = ["Sleeve_R","Cuff_R","Hand_R"]

all_mesh=[o for o in sc.objects if o.type=='MESH']
all_bounds=[bounds_of(o) for o in all_mesh]
char_x0=min(b[0] for b in all_bounds); char_y0=min(b[1] for b in all_bounds)
char_x1=max(b[2] for b in all_bounds); char_y1=max(b[3] for b in all_bounds)
char_w=char_x1-char_x0; char_h=char_y1-char_y0

torso_b=group_bounds(["Torso","Shoulder"])
body_cx=(torso_b[0]+torso_b[2])/2
skirt_b=group_bounds(["Skirt"])
root_pivot=Vector((body_cx, skirt_b[3]-0.10*(skirt_b[3]-skirt_b[1]), 0))
neck_b=group_bounds(["Neck_Full"])
head_pivot=Vector(((neck_b[0]+neck_b[2])/2,
                   neck_b[1]+0.55*(neck_b[3]-neck_b[1]), 0))

def shoulder_pivot(sleeve_name):
    b=group_bounds([sleeve_name])
    cx=(b[0]+b[2])/2
    inner_x=b[0] if cx>body_cx else b[2]
    y=b[3]-0.10*(b[3]-b[1])
    return Vector((inner_x,y,0))

def tail_anchor(root_name):
    b=group_bounds([root_name])
    cx=(b[0]+b[2])/2
    inner_x=b[0] if cx>body_cx else b[2]
    y=b[3]-0.08*(b[3]-b[1])
    return Vector((inner_x,y,0))

def segment_anchor(name):
    b=group_bounds([name])
    return Vector(((b[0]+b[2])/2, b[3]-0.08*(b[3]-b[1]), 0))

ROOT_CTRL=make_ctrl("CTRL_ROOT_SEAMLOCK",root_pivot)
HEAD_CTRL=make_ctrl("CTRL_HEAD_SEAMLOCK",head_pivot)
FACE_CTRL=make_ctrl("CTRL_FACE_PARALLAX",head_pivot)
ARM_L_CTRL=make_ctrl("CTRL_ARM_L_SEAMLOCK",shoulder_pivot("Sleeve_L"))
ARM_R_CTRL=make_ctrl("CTRL_ARM_R_SEAMLOCK",shoulder_pivot("Sleeve_R"))

for n in body_parts:
    if obj(n): preserve_parent(obj(n),ROOT_CTRL)
preserve_parent(HEAD_CTRL,ROOT_CTRL)
preserve_parent(ARM_L_CTRL,ROOT_CTRL)
preserve_parent(ARM_R_CTRL,ROOT_CTRL)
for n in head_hair:
    if obj(n): preserve_parent(obj(n),HEAD_CTRL)
preserve_parent(FACE_CTRL,HEAD_CTRL)
for n in face_parts:
    if obj(n): preserve_parent(obj(n),FACE_CTRL)
for n in armL:
    if obj(n): preserve_parent(obj(n),ARM_L_CTRL)
for n in armR:
    if obj(n): preserve_parent(obj(n),ARM_R_CTRL)

def build_tail(side):
    r=f"TwinTail_{side}_Root"
    m=f"TwinTail_{side}_Main"
    t=f"TwinTail_{side}_Tip"
    c1=make_ctrl(f"CTRL_TAIL_{side}_01",tail_anchor(r))
    c2=make_ctrl(f"CTRL_TAIL_{side}_02",segment_anchor(m))
    c3=make_ctrl(f"CTRL_TAIL_{side}_03",segment_anchor(t))
    preserve_parent(c1,HEAD_CTRL)
    preserve_parent(c2,c1)
    preserve_parent(c3,c2)
    if obj(r): preserve_parent(obj(r),c1)
    if obj(m): preserve_parent(obj(m),c2)
    if obj(t): preserve_parent(obj(t),c3)
    return c1,c2,c3

TAIL_L=build_tail("L")
TAIL_R=build_tail("R")

controllers=[ROOT_CTRL,HEAD_CTRL,FACE_CTRL,ARM_L_CTRL,ARM_R_CTRL,*TAIL_L,*TAIL_R]
base_ctrl={o.name:(o.location.copy(),o.rotation_euler.copy(),o.scale.copy()) for o in controllers}

feature_names=[
"Eye_L_White","Eye_L_Iris","Eye_L_Pupil","Eye_L_Highlight","Eye_L_LowerLid","Eye_L_UpperLid","Eye_L_LowerLash","Eye_L_UpperLash","Eye_L_Crease",
"Eye_R_White","Eye_R_Iris","Eye_R_Pupil","Eye_R_Highlight","Eye_R_LowerLid","Eye_R_UpperLid","Eye_R_LowerLash","Eye_R_UpperLash","Eye_R_Crease",
"Brow_L","Brow_R","Nose_Base","Mouth_Line"
]
base_feat={}
for n in feature_names:
    o=obj(n)
    if o:
        base_feat[n]=(o.location.copy(),o.scale.copy(),o.rotation_euler.copy())

def kf(o,f,loc=None,rot=None,scale=None):
    if loc is not None:
        o.location=loc
        o.keyframe_insert("location",frame=f)
    if rot is not None:
        o.rotation_euler=rot
        o.keyframe_insert("rotation_euler",frame=f)
    if scale is not None:
        o.scale=scale
        o.keyframe_insert("scale",frame=f)

beats=[
(1,   0.00, 0.00,  0.0),
(24, -0.55, 0.12, -1.3),
(48,  0.65,-0.08,  1.5),
(72, -0.30, 0.10, -0.7),
(96,  0.42,-0.05,  0.8),
(120, 0.00, 0.00,  0.0),
]

root_loc0,root_rot0,_=base_ctrl[ROOT_CTRL.name]
head_loc0,head_rot0,_=base_ctrl[HEAD_CTRL.name]
face_loc0,face_rot0,_=base_ctrl[FACE_CTRL.name]
al_loc0,al_rot0,_=base_ctrl[ARM_L_CTRL.name]
ar_loc0,ar_rot0,_=base_ctrl[ARM_R_CTRL.name]

for f,yaw,pitch,roll in beats:
    rl=root_loc0.copy()
    rl.y += math.sin(f/18.0)*0.0018*char_h
    rr=root_rot0.copy()
    rr.z=math.radians(0.45*math.sin(f/31.0))
    kf(ROOT_CTRL,f,loc=rl,rot=rr)

    hl=head_loc0.copy()
    hl.y += pitch*0.0015*char_h
    hr=head_rot0.copy()
    hr.z=math.radians(roll)
    kf(HEAD_CTRL,f,loc=hl,rot=hr)

    fl=face_loc0.copy()
    fl.x += yaw*0.0028*char_w
    kf(FACE_CTRL,f,loc=fl,rot=face_rot0.copy())

    lar=al_rot0.copy(); rar=ar_rot0.copy()
    lar.z=math.radians(0.75*yaw)
    rar.z=math.radians(0.75*yaw)
    kf(ARM_L_CTRL,f,loc=al_loc0,rot=lar)
    kf(ARM_R_CTRL,f,loc=ar_loc0,rot=rar)

    for chain,sgn in ((TAIL_L,1.0),(TAIL_R,-1.0)):
        amps=(1.4,3.0,5.0)
        for i,c in enumerate(chain):
            loc0,rot0,_=base_ctrl[c.name]
            r=rot0.copy()
            lag=math.sin((f-i*5)/16.0)
            r.z=math.radians((-yaw*amps[i]*0.55) + sgn*amps[i]*0.35*lag)
            kf(c,f,loc=loc0,rot=r)

    for n in feature_names:
        o=obj(n)
        if not o or n not in base_feat: continue
        bl,bs,br=base_feat[n]
        loc=bl.copy(); scale=bs.copy()
        if n=="Nose_Base":
            loc.x += yaw*0.0018*char_w
            loc.y += pitch*0.0010*char_h
        elif n=="Mouth_Line":
            loc.x += yaw*0.0011*char_w
            loc.y += pitch*0.0010*char_h
        elif n.startswith("Eye_L_"):
            loc.x += yaw*0.00055*char_w
            scale.x *= (1.0+0.012*yaw)
        elif n.startswith("Eye_R_"):
            loc.x += yaw*0.00055*char_w
            scale.x *= (1.0-0.012*yaw)
        elif n.startswith("Brow_"):
            loc.x += yaw*0.00045*char_w
        kf(o,f,loc=loc,scale=scale)

iris_parts=["Eye_L_Iris","Eye_L_Pupil","Eye_L_Highlight","Eye_R_Iris","Eye_R_Pupil","Eye_R_Highlight"]
for f,gx,gy in [(1,0,0),(30,-.7,.2),(60,.8,-.1),(90,-.35,.15),(120,0,0)]:
    for n in iris_parts:
        o=obj(n)
        if not o: continue
        loc=o.location.copy()
        loc.x += gx*0.0032*char_w
        loc.y += gy*0.0022*char_h
        kf(o,f,loc=loc)

eye_open=[n for n in feature_names if n.startswith("Eye_L_") or n.startswith("Eye_R_")]
for cf in [22,61,99]:
    for n in eye_open:
        o=obj(n)
        if not o: continue
        s=o.scale.copy()
        kf(o,cf-2,scale=s)
        s2=s.copy(); s2.y=max(0.08,s.y*0.12)
        kf(o,cf,scale=s2)
        kf(o,cf+2,scale=s)

mouth=obj("Mouth_Line")
if mouth:
    for f in range(48,85,8):
        s=mouth.scale.copy()
        kf(mouth,f,scale=s)
        s2=s.copy(); s2.y*=1.45; s2.x*=0.96
        kf(mouth,f+4,scale=s2)
        kf(mouth,f+8,scale=s)

for action in bpy.data.actions:
    try:
        for layer in action.layers:
            for strip in layer.strips:
                bag=getattr(strip,'channelbag',None)
                if bag:
                    for fc in bag.fcurves:
                        for kp in fc.keyframe_points:
                            kp.interpolation='BEZIER'
    except Exception:
        try:
            for fc in action.fcurves:
                for kp in fc.keyframe_points:
                    kp.interpolation='BEZIER'
        except Exception:
            pass

frames_dir=OUT/"frames"
frames_dir.mkdir(parents=True,exist_ok=True)
sc.render.filepath=str(frames_dir/"frame_")
sc.render.image_settings.file_format='PNG'
sc.render.image_settings.color_mode='RGBA'
sc.render.image_settings.color_depth='8'

bpy.ops.wm.save_as_mainfile(filepath=str(OUT/"XLG_V13_SEAMLOCK_PREVIEW.blend"))
bpy.ops.render.render(animation=True)
print("V13_SEAMLOCK_DONE",frames_dir)
