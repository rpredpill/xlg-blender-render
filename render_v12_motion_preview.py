from pathlib import Path
import math
import bpy

ROOT = Path(__file__).resolve().parent
NEUTRAL = ROOT / "neutral57"
SRC = NEUTRAL / "render_neutral57.py"
OUT = NEUTRAL / "output"
OUT.mkdir(parents=True, exist_ok=True)

# Build the exact 57-part character using the already verified neutral57 source.
# Patch the older renderer's EEVEE enum for Blender 5.2 before executing it.
src_text = SRC.read_text(encoding="utf-8").replace("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")
try:
    exec(compile(src_text, str(SRC), "exec"), {"__name__":"__main__", "__file__":str(SRC)})
except SystemExit:
    pass

sc = bpy.context.scene
sc.frame_start = 1
sc.frame_end = 240
sc.render.fps = 24
sc.render.resolution_x = 540
sc.render.resolution_y = 960
sc.render.resolution_percentage = 100
sc.render.film_transparent = False
sc.render.engine = 'BLENDER_EEVEE'
try:
    sc.eevee.taa_render_samples = 12
except Exception:
    pass

# Neutral white background.
if sc.world:
    sc.world.color = (0.96, 0.96, 0.96)

def obj(name):
    return bpy.data.objects.get(name)

# Full bounds for scale-aware controls.
renderables = [o for o in sc.objects if o.type == 'MESH']
xs, ys = [], []
for o in renderables:
    try:
        for c in o.bound_box:
            wc = o.matrix_world @ __import__('mathutils').Vector(c)
            xs.append(wc.x); ys.append(wc.y)
    except Exception:
        pass
char_w = max(xs)-min(xs) if xs else 10.0
char_h = max(ys)-min(ys) if ys else 18.0

def center(names):
    pts = [obj(n).matrix_world.translation.copy() for n in names if obj(n)]
    if not pts:
        return (0,0,0)
    x=sum(p.x for p in pts)/len(pts); y=sum(p.y for p in pts)/len(pts); z=sum(p.z for p in pts)/len(pts)
    return (x,y,z)

def ctrl(name, names, pivot=None):
    e=bpy.data.objects.new(name,None)
    sc.collection.objects.link(e)
    if pivot is None: pivot=center(names)
    e.location=pivot
    for n in names:
        o=obj(n)
        if not o: continue
        mw=o.matrix_world.copy()
        o.parent=e
        o.matrix_world=mw
    return e

face_names = [
"Face_Base_Full","Ear_L","Ear_R",
"Eye_L_White","Eye_L_Iris","Eye_L_Pupil","Eye_L_Highlight","Eye_L_LowerLid","Eye_L_UpperLid","Eye_L_LowerLash","Eye_L_UpperLash","Eye_L_Crease",
"Eye_R_White","Eye_R_Iris","Eye_R_Pupil","Eye_R_Highlight","Eye_R_LowerLid","Eye_R_UpperLid","Eye_R_LowerLash","Eye_R_UpperLash","Eye_R_Crease",
"Brow_L","Brow_R","Nose_Base","Mouth_Line",
"Bang_L","Bang_C","Bang_R","Hair_Crown","Hairpin_XLG","HairBow_L","HairBow_R",
"SideHair_L","SideHair_R","Hair_Back"
]
body_names=["Neck_Full","Shoulder","Torso","Skirt","Leg_L","Leg_R","Sock_L","Sock_R","Shoe_L","Shoe_R"]
armL=["Sleeve_L","Cuff_L","Hand_L"]
armR=["Sleeve_R","Cuff_R","Hand_R"]
tailL=["TwinTail_L_Root","TwinTail_L_Main","TwinTail_L_Tip"]
tailR=["TwinTail_R_Root","TwinTail_R_Main","TwinTail_R_Tip"]

body=ctrl("CTRL_BODY", body_names)
head=ctrl("CTRL_HEAD", face_names)
arm_l=ctrl("CTRL_ARM_L", armL)
arm_r=ctrl("CTRL_ARM_R", armR)
tail_l=ctrl("CTRL_TAIL_L", tailL)
tail_r=ctrl("CTRL_TAIL_R", tailR)

# Hierarchy
for c in (head,arm_l,arm_r):
    mw=c.matrix_world.copy(); c.parent=body; c.matrix_world=mw
for c in (tail_l,tail_r):
    mw=c.matrix_world.copy(); c.parent=head; c.matrix_world=mw

# Baselines for local feature animation.
all_feature_names = [
"Eye_L_White","Eye_L_Iris","Eye_L_Pupil","Eye_L_Highlight","Eye_L_LowerLid","Eye_L_UpperLid","Eye_L_LowerLash","Eye_L_UpperLash","Eye_L_Crease",
"Eye_R_White","Eye_R_Iris","Eye_R_Pupil","Eye_R_Highlight","Eye_R_LowerLid","Eye_R_UpperLid","Eye_R_LowerLash","Eye_R_UpperLash","Eye_R_Crease",
"Brow_L","Brow_R","Nose_Base","Mouth_Line","Ear_L","Ear_R","Face_Base_Full"
]
base={}
for n in all_feature_names:
    o=obj(n)
    if o:
        base[n]=(o.location.copy(),o.scale.copy(),o.rotation_euler.copy())

def kf(o, frame, loc=None, rot=None, scale=None):
    if loc is not None:
        o.location=loc; o.keyframe_insert("location",frame=frame)
    if rot is not None:
        o.rotation_euler=rot; o.keyframe_insert("rotation_euler",frame=frame)
    if scale is not None:
        o.scale=scale; o.keyframe_insert("scale",frame=frame)

# Main motion beats.
beats=[
(1,   0.00, 0.00,  0.0),
(36, -0.75, 0.18, -2.0),
(72,  0.85,-0.10,  2.5),
(108, 0.25, 0.22, -1.0),
(144,-0.45,-0.12,  1.5),
(180, 0.60, 0.10, -1.0),
(216,-0.20, 0.00,  0.5),
(240, 0.00, 0.00,  0.0),
]
for f,yaw,pitch,roll in beats:
    # Body and head follow.
    b=body.rotation_euler.copy(); b.z=math.radians(-yaw*1.4)
    kf(body,f,loc=(body.location.x,body.location.y+pitch*0.015*char_h,body.location.z),rot=b)
    h=head.rotation_euler.copy(); h.z=math.radians(roll + yaw*2.0)
    kf(head,f,loc=(head.location.x+yaw*0.010*char_w,head.location.y+pitch*0.012*char_h,head.location.z),rot=h)

    # Facial pseudo-3D parallax.
    for n in all_feature_names:
        o=obj(n)
        if not o or n not in base: continue
        bl,bs,br=base[n]
        factor=0.0
        sx=1.0
        if n=="Nose_Base": factor=0.030
        elif n=="Mouth_Line": factor=0.020
        elif n.startswith("Eye_L"): factor=0.012; sx=1.0+0.035*yaw
        elif n.startswith("Eye_R"): factor=0.012; sx=1.0-0.035*yaw
        elif n.startswith("Brow_"): factor=0.010
        elif n.startswith("Ear_"): factor=-0.008
        elif n=="Face_Base_Full": factor=0.004; sx=1.0-0.018*abs(yaw)
        loc=bl.copy(); loc.x += yaw*factor*char_w; loc.y += pitch*0.007*char_h
        scale=bs.copy(); scale.x *= sx
        kf(o,f,loc=loc,scale=scale)

    # Hair delay.
    for c,phase in ((tail_l,1.0),(tail_r,-1.0)):
        r=c.rotation_euler.copy()
        r.z=math.radians((-yaw*5.5 + phase*2.0*math.sin(f/26.0)))
        kf(c,f,rot=r)

    # Arms subtly counter-sway.
    rl=arm_l.rotation_euler.copy(); rr=arm_r.rotation_euler.copy()
    rl.z=math.radians(yaw*1.8); rr.z=math.radians(yaw*1.8)
    kf(arm_l,f,rot=rl); kf(arm_r,f,rot=rr)

# Eye gaze.
iris_parts=["Eye_L_Iris","Eye_L_Pupil","Eye_L_Highlight","Eye_R_Iris","Eye_R_Pupil","Eye_R_Highlight"]
for f,gx,gy in [(1,0,0),(55,-1,.3),(90,1,-.2),(130,.5,.35),(170,-.6,-.1),(210,.3,.15),(240,0,0)]:
    for n in iris_parts:
        o=obj(n)
        if not o or n not in base: continue
        bl,bs,br=base[n]
        # layer on top of yaw keyframes with another key at same frame where possible
        loc=o.location.copy()
        loc.x += gx*0.006*char_w
        loc.y += gy*0.004*char_h
        kf(o,f,loc=loc)

# Blinks by vertically compressing eye parts around their own origins.
eye_open_parts=[n for n in all_feature_names if n.startswith("Eye_L_") or n.startswith("Eye_R_")]
for centerf in [28,84,151,205]:
    for n in eye_open_parts:
        o=obj(n)
        if not o: continue
        s0=o.scale.copy()
        kf(o,centerf-2,scale=s0)
        sm=s0.copy(); sm.y=max(0.08,s0.y*0.10)
        kf(o,centerf,scale=sm)
        kf(o,centerf+2,scale=s0)

# Speech-like mouth movement.
m=obj("Mouth_Line")
if m:
    for f in range(100,177,6):
        s=m.scale.copy()
        kf(m,f,scale=s)
        s2=s.copy()
        s2.x*=0.90 if ((f//6)%2) else 1.08
        s2.y*=1.7
        kf(m,f+3,scale=s2)
        kf(m,f+6,scale=s)

# Make interpolation smooth.
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
        # Blender 4.x legacy actions
        try:
            for fc in action.fcurves:
                for kp in fc.keyframe_points:
                    kp.interpolation='BEZIER'
        except Exception:
            pass

# Render actual Blender animation.
sc.render.filepath=str(OUT/"XLG_V12_REAL_BLENDER_PREVIEW.mp4")
sc.render.image_settings.file_format='FFMPEG'
sc.render.ffmpeg.format='MPEG4'
sc.render.ffmpeg.codec='H264'
sc.render.ffmpeg.constant_rate_factor='MEDIUM'
sc.render.ffmpeg.ffmpeg_preset='GOOD'
sc.render.ffmpeg.audio_codec='NONE'
bpy.ops.render.render(animation=True)

# Save the Blender scene too.
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/"XLG_V12_REAL_BLENDER_PREVIEW.blend"))
print("DONE", sc.render.filepath)
