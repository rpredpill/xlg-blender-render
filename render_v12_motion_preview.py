from pathlib import Path
import math
import bpy
from mathutils import Vector

ROOT=Path(__file__).resolve().parent
NEUTRAL=ROOT/"neutral57"
SRC=NEUTRAL/"render_neutral57.py"
OUT=NEUTRAL/"output"
OUT.mkdir(parents=True,exist_ok=True)

# Build the exact 57-part source.
src=SRC.read_text(encoding="utf-8")
src=src.replace(
    "sc.render.engine = 'BLENDER_EEVEE_NEXT'",
    "sc.render.engine = 'CYCLES'\nsc.cycles.samples=1\nsc.cycles.use_denoising=False"
)
try:
    exec(compile(src,str(SRC),"exec"),{"__name__":"__main__","__file__":str(SRC)})
except SystemExit:
    pass

sc=bpy.context.scene
sc.frame_start=1
sc.frame_end=120
sc.render.fps=24
sc.render.resolution_x=360
sc.render.resolution_y=640
sc.render.resolution_percentage=100
sc.render.engine='CYCLES'
sc.cycles.samples=1
sc.cycles.use_denoising=False
sc.cycles.max_bounces=0
sc.render.film_transparent=False
if sc.world: sc.world.color=(0.96,0.96,0.96)

def O(n): return bpy.data.objects.get(n)

def wb(o):
    pts=[o.matrix_world@Vector(c) for c in o.bound_box]
    xs=[p.x for p in pts]; ys=[p.y for p in pts]
    return min(xs),min(ys),max(xs),max(ys)

def gb(names):
    bs=[wb(O(n)) for n in names if O(n)]
    return min(x[0] for x in bs),min(x[1] for x in bs),max(x[2] for x in bs),max(x[3] for x in bs)

def subdiv(o,cuts=7):
    if not o or o.type!='MESH': return
    if len(o.data.vertices)>180: return
    bpy.ops.object.select_all(action='DESELECT')
    o.select_set(True); bpy.context.view_layer.objects.active=o
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.mesh.subdivide(number_cuts=cuts,smoothness=0)
    bpy.ops.object.mode_set(mode='OBJECT')
    o.select_set(False)

def clear_groups(o):
    while o.vertex_groups:
        o.vertex_groups.remove(o.vertex_groups[0])

def put(o,weights):
    # weights: {group: {vertex_index: weight}}
    for g,m in weights.items():
        vg=o.vertex_groups.get(g) or o.vertex_groups.new(name=g)
        for i,w in m.items():
            if w>1e-5: vg.add([i],float(w),'REPLACE')

def rigid(o,bone):
    clear_groups(o)
    vg=o.vertex_groups.new(name=bone)
    vg.add(list(range(len(o.data.vertices))),1.0,'REPLACE')

def norm_y(o,v):
    b=wb(o); p=o.matrix_world@v.co
    return (b[3]-p.y)/max(1e-6,b[3]-b[1]) # 0 top, 1 bottom

def sleeve_weights(o,side):
    clear_groups(o)
    C="CHEST"; U=f"UPPER_ARM_{side}"; F=f"FOREARM_{side}"
    m={C:{},U:{},F:{}}
    for v in o.data.vertices:
        t=norm_y(o,v)
        if t<=0.10:
            m[C][v.index]=1
        elif t<0.30:
            a=(t-.10)/.20; m[C][v.index]=1-a; m[U][v.index]=a
        elif t<0.58:
            m[U][v.index]=1
        elif t<0.78:
            a=(t-.58)/.20; m[U][v.index]=1-a; m[F][v.index]=a
        else:
            m[F][v.index]=1
    put(o,m)

def cuff_weights(o,side):
    clear_groups(o)
    F=f"FOREARM_{side}"; H=f"HAND_{side}"
    m={F:{},H:{}}
    for v in o.data.vertices:
        t=norm_y(o,v)
        a=max(0,min(1,(t-.35)/.50))
        m[F][v.index]=1-a; m[H][v.index]=a
    put(o,m)

def torso_weights(o,name):
    clear_groups(o)
    if name=="Shoulder":
        rigid(o,"CHEST"); return
    if name=="Neck_Full":
        m={"NECK":{},"HEAD":{}}
        for v in o.data.vertices:
            t=norm_y(o,v)
            a=max(0,min(1,(.55-t)/.45))
            m["NECK"][v.index]=1-a; m["HEAD"][v.index]=a
        put(o,m); return
    if name=="Skirt":
        rigid(o,"PELVIS"); return
    m={"PELVIS":{},"SPINE":{},"CHEST":{}}
    for v in o.data.vertices:
        t=norm_y(o,v)
        if t<.34:
            m["CHEST"][v.index]=1
        elif t<.68:
            a=(t-.34)/.34
            m["CHEST"][v.index]=1-a; m["SPINE"][v.index]=a
        else:
            a=(t-.68)/.32
            m["SPINE"][v.index]=1-a; m["PELVIS"][v.index]=a
    put(o,m)

def tail_weights(o,side,segment):
    clear_groups(o)
    b1=f"TAIL_{side}_01"; b2=f"TAIL_{side}_02"; b3=f"TAIL_{side}_03"
    m={b1:{},b2:{},b3:{}}
    for v in o.data.vertices:
        t=norm_y(o,v)
        if segment=="Root":
            a=max(0,min(1,(t-.35)/.55))
            m[b1][v.index]=1-a; m[b2][v.index]=a
        elif segment=="Main":
            if t<.50:
                a=t/.50; m[b1][v.index]=1-a; m[b2][v.index]=a
            else:
                a=(t-.50)/.50; m[b2][v.index]=1-a; m[b3][v.index]=a
        else:
            a=max(0,min(1,t/.60))
            m[b2][v.index]=1-a; m[b3][v.index]=a
    put(o,m)

def leg_weights(o,side,kind):
    clear_groups(o)
    if kind=="Leg": rigid(o,f"THIGH_{side}")
    elif kind=="Sock":
        m={f"THIGH_{side}":{},f"SHIN_{side}":{}}
        for v in o.data.vertices:
            t=norm_y(o,v); a=max(0,min(1,(t-.08)/.28))
            m[f"THIGH_{side}"][v.index]=1-a; m[f"SHIN_{side}"][v.index]=a
        put(o,m)
    else: rigid(o,f"FOOT_{side}")

def join(names,newname):
    obs=[O(n) for n in names if O(n)]
    bpy.ops.object.select_all(action='DESELECT')
    for o in obs: o.select_set(True)
    bpy.context.view_layer.objects.active=obs[0]
    bpy.ops.object.join()
    a=obs[0]; a.name=newname
    return a

# Dense meshes where bending matters.
dense=[
"Torso","Shoulder","Neck_Full","Skirt",
"Sleeve_L","Cuff_L","Hand_L","Sleeve_R","Cuff_R","Hand_R",
"Leg_L","Sock_L","Shoe_L","Leg_R","Sock_R","Shoe_R",
"TwinTail_L_Root","TwinTail_L_Main","TwinTail_L_Tip",
"TwinTail_R_Root","TwinTail_R_Main","TwinTail_R_Tip"
]
for n in dense: subdiv(O(n),9 if "TwinTail" in n or "Sleeve" in n else 7)

# Weight each source island BEFORE joining, so provenance is preserved.
for n in ["Torso","Shoulder","Neck_Full","Skirt"]:
    torso_weights(O(n),n)
for s in "LR":
    sleeve_weights(O(f"Sleeve_{s}"),s)
    cuff_weights(O(f"Cuff_{s}"),s)
    rigid(O(f"Hand_{s}"),f"HAND_{s}")
    leg_weights(O(f"Leg_{s}"),s,"Leg")
    leg_weights(O(f"Sock_{s}"),s,"Sock")
    leg_weights(O(f"Shoe_{s}"),s,"Shoe")
    for seg in ["Root","Main","Tip"]:
        tail_weights(O(f"TwinTail_{s}_{seg}"),s,seg)

upper=join(["Torso","Shoulder","Neck_Full","Skirt",
            "Sleeve_L","Cuff_L","Hand_L","Sleeve_R","Cuff_R","Hand_R"],"MESH_UPPER_BODY_SKIN")
legL=join(["Leg_L","Sock_L","Shoe_L"],"MESH_LEG_L_SKIN")
legR=join(["Leg_R","Sock_R","Shoe_R"],"MESH_LEG_R_SKIN")
tailL=join(["TwinTail_L_Root","TwinTail_L_Main","TwinTail_L_Tip"],"MESH_TAIL_L_SKIN")
tailR=join(["TwinTail_R_Root","TwinTail_R_Main","TwinTail_R_Tip"],"MESH_TAIL_R_SKIN")

# -----------------------------------------------------------------
# Armature built from actual art bounds.
# -----------------------------------------------------------------
tor=gb(["MESH_UPPER_BODY_SKIN"])
cx=(tor[0]+tor[2])/2
sk=wb(upper)
pelvis_y=gb(["MESH_UPPER_BODY_SKIN"])[1]+0.39*(gb(["MESH_UPPER_BODY_SKIN"])[3]-gb(["MESH_UPPER_BODY_SKIN"])[1])
# Use original head/face objects for the upper landmarks.
faceb=gb(["Face_Base_Full"])
head_center=Vector(((faceb[0]+faceb[2])/2,(faceb[1]+faceb[3])/2,0))
neck_y=gb(["Face_Base_Full"])[1]-0.015
chest_y=gb(["MESH_UPPER_BODY_SKIN"])[3]-0.20*(gb(["MESH_UPPER_BODY_SKIN"])[3]-gb(["MESH_UPPER_BODY_SKIN"])[1])
spine_y=(pelvis_y+chest_y)/2

arm_data=bpy.data.armatures.new("XLG_ARMATURE_DATA")
arm=bpy.data.objects.new("XLG_ARMATURE",arm_data)
sc.collection.objects.link(arm)
bpy.context.view_layer.objects.active=arm
arm.select_set(True)
bpy.ops.object.mode_set(mode='EDIT')

def bone(name,h,t,parent=None,connect=False):
    b=arm.data.edit_bones.new(name)
    b.head=(h.x,h.y,0); b.tail=(t.x,t.y,0)
    if parent:
        b.parent=arm.data.edit_bones[parent]; b.use_connect=connect
    return b

pel=Vector((cx,pelvis_y,0)); spi=Vector((cx,spine_y,0)); che=Vector((cx,chest_y,0))
nec=Vector((cx,neck_y,0)); hea=Vector((head_center.x,head_center.y,0))
root=Vector((cx,pelvis_y-.45,0))
bone("ROOT",root,pel)
bone("PELVIS",pel,spi,"ROOT",True)
bone("SPINE",spi,che,"PELVIS",True)
bone("CHEST",che,nec,"SPINE",True)
bone("NECK",nec,Vector((cx,(nec.y+hea.y)/2,0)),"CHEST",True)
bone("HEAD",Vector((cx,(nec.y+hea.y)/2,0)),hea,"NECK",True)

def arm_points(side):
    sb=gb([f"MESH_UPPER_BODY_SKIN"])
    sleeve_b=gb([f"MESH_UPPER_BODY_SKIN"])
    # derive shoulder/wrist from original-side x sign using surviving hand bounds unavailable after join:
    sign=1 if side=="L" else -1
    shoulder=Vector((cx+sign*0.16*(tor[2]-tor[0]),chest_y-.015,0))
    # hands in source are now part of upper mesh; use proportional anatomy.
    elbow=Vector((cx+sign*0.235*(tor[2]-tor[0]), chest_y-.28*(tor[3]-tor[1]),0))
    wrist=Vector((cx+sign*0.27*(tor[2]-tor[0]), chest_y-.52*(tor[3]-tor[1]),0))
    hand=Vector((wrist.x,wrist.y-.10*(tor[3]-tor[1]),0))
    return shoulder,elbow,wrist,hand

for s in "LR":
    sh,el,wr,ha=arm_points(s)
    bone(f"UPPER_ARM_{s}",sh,el,"CHEST",False)
    bone(f"FOREARM_{s}",el,wr,f"UPPER_ARM_{s}",True)
    bone(f"HAND_{s}",wr,ha,f"FOREARM_{s}",True)

def leg_points(side,mesh):
    b=wb(mesh)
    x=(b[0]+b[2])/2
    hip=Vector((x,b[3]-.04*(b[3]-b[1]),0))
    knee=Vector((x,b[1]+.58*(b[3]-b[1]),0))
    ankle=Vector((x,b[1]+.18*(b[3]-b[1]),0))
    foot=Vector((x,b[1],0))
    return hip,knee,ankle,foot

for s,mesh in (("L",legL),("R",legR)):
    h,k,a,f=leg_points(s,mesh)
    bone(f"THIGH_{s}",h,k,"PELVIS",False)
    bone(f"SHIN_{s}",k,a,f"THIGH_{s}",True)
    bone(f"FOOT_{s}",a,f,f"SHIN_{s}",True)

def tail_chain(side,mesh):
    b=wb(mesh); xmid=(b[0]+b[2])/2
    # Root is closer to the head; tip follows the tail silhouette downwards.
    sign=1 if xmid>cx else -1
    a=Vector((cx+sign*.16*(faceb[2]-faceb[0]),faceb[3]-.12*(faceb[3]-faceb[1]),0))
    p1=Vector((xmid,b[3]-.28*(b[3]-b[1]),0))
    p2=Vector((xmid,b[3]-.62*(b[3]-b[1]),0))
    p3=Vector((xmid,b[1]+.05*(b[3]-b[1]),0))
    bone(f"TAIL_{side}_01",a,p1,"HEAD",False)
    bone(f"TAIL_{side}_02",p1,p2,f"TAIL_{side}_01",True)
    bone(f"TAIL_{side}_03",p2,p3,f"TAIL_{side}_02",True)

tail_chain("L",tailL); tail_chain("R",tailR)

bpy.ops.object.mode_set(mode='POSE')
for pb in arm.pose.bones:
    pb.rotation_mode='XYZ'
bpy.ops.object.mode_set(mode='OBJECT')
arm.show_in_front=True

def arm_mod(o):
    md=o.modifiers.new("ARMATURE_DEFORM","ARMATURE")
    md.object=arm
    md.use_vertex_groups=True
    md.use_bone_envelopes=False
    md.use_deform_preserve_volume=False

for o in [upper,legL,legR,tailL,tailR]: arm_mod(o)

# Head art uses the same HEAD bone, so face/hair cannot drift apart.
head_names=[
"Face_Base_Full","Ear_L","Ear_R",
"Eye_L_White","Eye_L_Iris","Eye_L_Pupil","Eye_L_Highlight","Eye_L_LowerLid","Eye_L_UpperLid","Eye_L_LowerLash","Eye_L_UpperLash","Eye_L_Crease",
"Eye_R_White","Eye_R_Iris","Eye_R_Pupil","Eye_R_Highlight","Eye_R_LowerLid","Eye_R_UpperLid","Eye_R_LowerLash","Eye_R_UpperLash","Eye_R_Crease",
"Brow_L","Brow_R","Nose_Base","Mouth_Line","Hair_Back","Hair_Crown","Bang_L","Bang_C","Bang_R",
"SideHair_L","SideHair_R","Hairpin_XLG","HairBow_L","HairBow_R"
]
for n in head_names:
    o=O(n)
    if not o: continue
    rigid(o,"HEAD")
    arm_mod(o)

# Stress-test animation: body/arms/tails all deform through weights.
def pk(name,f,z):
    pb=arm.pose.bones[name]
    pb.rotation_euler=(0,0,math.radians(z))
    pb.keyframe_insert("rotation_euler",frame=f)

beats=[(1,0,0),(24,-2.5,-6),(48,2.8,7),(72,-1.8,-5),(96,2.0,5),(120,0,0)]
for f,bodyz,armz in beats:
    pk("SPINE",f,bodyz*.35)
    pk("CHEST",f,bodyz)
    pk("HEAD",f,-bodyz*.40)
    pk("UPPER_ARM_L",f,armz)
    pk("UPPER_ARM_R",f,armz)
    pk("FOREARM_L",f,-armz*.28)
    pk("FOREARM_R",f,-armz*.28)
    for s,sgn in (("L",1),("R",-1)):
        pk(f"TAIL_{s}_01",f,-bodyz*1.1 + sgn*1.0)
        pk(f"TAIL_{s}_02",f,-bodyz*1.8 + sgn*2.0)
        pk(f"TAIL_{s}_03",f,-bodyz*2.5 + sgn*3.2)

# Minimal eye motion/blink remains local inside HEAD deformation.
eye_open=[n for n in head_names if n.startswith("Eye_L_") or n.startswith("Eye_R_")]
for cf in [20,62,100]:
    for n in eye_open:
        o=O(n)
        if not o: continue
        s=o.scale.copy()
        o.scale=s; o.keyframe_insert("scale",frame=cf-2)
        q=s.copy(); q.y=max(.10,s.y*.12)
        o.scale=q; o.keyframe_insert("scale",frame=cf)
        o.scale=s; o.keyframe_insert("scale",frame=cf+2)

# Smooth interpolation.
for action in bpy.data.actions:
    try:
        for layer in action.layers:
            for strip in layer.strips:
                bag=getattr(strip,'channelbag',None)
                if bag:
                    for fc in bag.fcurves:
                        for kp in fc.keyframe_points: kp.interpolation='BEZIER'
    except Exception:
        try:
            for fc in action.fcurves:
                for kp in fc.keyframe_points: kp.interpolation='BEZIER'
        except Exception: pass

frames=OUT/"frames"; frames.mkdir(exist_ok=True)
sc.render.filepath=str(frames/"frame_")
sc.render.image_settings.file_format='PNG'
sc.render.image_settings.color_mode='RGBA'
sc.render.image_settings.color_depth='8'

bpy.ops.wm.save_as_mainfile(filepath=str(OUT/"XLG_V12_REAL_BLENDER_PREVIEW.blend"))
bpy.ops.render.render(animation=True)
print("V14_MESH_DEFORM_DONE")
