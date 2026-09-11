import bpy, json, math
from pathlib import Path
from mathutils import Vector

ROOT=Path(__file__).resolve().parent
PARTS=ROOT/'parts'
MANIFEST=json.load(open(ROOT/'manifest.json',encoding='utf-8'))
W,H=MANIFEST['canvas']
PPU=100.0
Z_STEP=0.002

bpy.ops.wm.read_factory_settings(use_empty=True)

def make_mesh(name,w,h,cols,rows):
    verts=[]; faces=[]; uvs=[]
    for j in range(rows+1):
        v=j/rows; y=-h/2+h*v
        for i in range(cols+1):
            u=i/cols; x=-w/2+w*u
            verts.append((x,y,0)); uvs.append((u,v))
    s=cols+1
    for j in range(rows):
        for i in range(cols):
            a=j*s+i; faces.append((a,a+1,a+1+s,a+s))
    me=bpy.data.meshes.new(name+'_Mesh'); me.from_pydata(verts,[],faces); me.update()
    uv=me.uv_layers.new(name='UVMap')
    for poly in me.polygons:
        for li in poly.loop_indices:
            vi=me.loops[li].vertex_index; uv.data[li].uv=uvs[vi]
    return me

def mat_for(name,path):
    m=bpy.data.materials.new('MAT_'+name); m.use_nodes=True
    if hasattr(m,'surface_render_method'): m.surface_render_method='DITHERED'
    nt=m.node_tree; nt.nodes.clear()
    out=nt.nodes.new('ShaderNodeOutputMaterial'); mix=nt.nodes.new('ShaderNodeMixShader')
    tr=nt.nodes.new('ShaderNodeBsdfTransparent'); em=nt.nodes.new('ShaderNodeEmission'); tx=nt.nodes.new('ShaderNodeTexImage')
    tx.interpolation='Linear'; tx.extension='CLIP'; tx.image=bpy.data.images.load(str(path),check_existing=True)
    nt.links.new(tx.outputs['Color'],em.inputs['Color']); nt.links.new(tx.outputs['Alpha'],mix.inputs[0]); nt.links.new(tr.outputs[0],mix.inputs[1]); nt.links.new(em.outputs[0],mix.inputs[2]); nt.links.new(mix.outputs[0],out.inputs['Surface'])
    return m

def gridres(n,w,h):
    cols=max(2,min(16,math.ceil(w/28))); rows=max(2,min(16,math.ceil(h/28)))
    if n=='Face_Base_Full': cols=max(cols,9); rows=max(rows,11)
    if n.startswith('Eye_'): cols=max(cols,5); rows=max(rows,4)
    if n.startswith('Bang_'): cols=max(cols,5); rows=max(rows,6)
    if n.startswith('SideHair_'): cols=max(cols,5); rows=max(rows,10)
    return cols,rows

def coll(name):
    c=bpy.data.collections.new(name); bpy.context.scene.collection.children.link(c); return c

ART=coll('XLG_ART'); DEF=coll('XLG_DEFORMERS')
objs={}
for p in MANIFEST['parts']:
    n=p['name']; l,t,r,b=p['crop_bbox']; wp=r-l; hp=b-t; cols,rows=gridres(n,wp,hp)
    me=make_mesh(n,wp/PPU,hp/PPU,cols,rows); o=bpy.data.objects.new(n,me); ART.objects.link(o)
    o.location=(((l+r)/2-W/2)/PPU,(H/2-(t+b)/2)/PPU,p['z_index']*Z_STEP)
    o.data.materials.append(mat_for(n,PARTS/f'{n}.png')); objs[n]=o

def empty(name,parent=None,size=.4):
    o=bpy.data.objects.new(name,None); o.empty_display_type='PLAIN_AXES'; o.empty_display_size=size; DEF.objects.link(o)
    if parent:
        mw=o.matrix_world.copy(); o.parent=parent; o.matrix_world=mw
    return o
ctrl=empty('CTRL_Head',None,.6); ctrl['head_yaw']=0.0
try: ctrl.id_properties_ui('head_yaw').update(min=-20,max=20,soft_min=-20,soft_max=20)
except: pass
head=empty('DEF_HEAD_ROOT',None,.55); face_space=empty('DEF_FACE_SPACE',head,.35); hair_space=empty('DEF_HAIR_SPACE',head,.35)

def parent_preserve(o,p):
    mw=o.matrix_world.copy(); o.parent=p; o.matrix_world=mw

def many(prefix): return [o for n,o in objs.items() if n.startswith(prefix)]
face_names=['Face_Base_Full','Nose_Base','Mouth_Line','Brow_L','Brow_R','Ear_L','Ear_R']
for n in face_names:
    if n in objs: parent_preserve(objs[n],face_space)
for o in many('Eye_L_')+many('Eye_R_'): parent_preserve(o,face_space)
for n in ['Hair_Back','Hair_Crown','Bang_L','Bang_C','Bang_R','SideHair_L','SideHair_R','Hairpin_XLG','HairBow_L','HairBow_R']:
    if n in objs: parent_preserve(objs[n],hair_space)
if 'Neck_Full' in objs: parent_preserve(objs['Neck_Full'],head)

for sp,scale in [(face_space,0.006),(hair_space,0.004)]:
    fc=sp.driver_add('location',0); d=fc.driver; d.type='SCRIPTED'; v=d.variables.new(); v.name='yaw'; v.type='SINGLE_PROP'; v.targets[0].id=ctrl; v.targets[0].data_path='["head_yaw"]'; d.expression=f'yaw/20.0*{scale}'

def bbox_world(targets):
    xs=[]; ys=[]
    for o in targets:
        for c in o.bound_box:
            p=o.matrix_world@Vector(c); xs.append(p.x); ys.append(p.y)
    return min(xs),min(ys),max(xs),max(ys)

def shape_driver(k,neg):
    fc=k.driver_add('value'); d=fc.driver; d.type='SCRIPTED'; v=d.variables.new(); v.name='yaw'; v.type='SINGLE_PROP'; v.targets[0].id=ctrl; v.targets[0].data_path='["head_yaw"]'; d.expression='max(0,min(1,-yaw/20))' if neg else 'max(0,min(1,yaw/20))'

def lattice(name,targets,u,v,margin,parent):
    targets=[x for x in targets if x]
    x0,y0,x1,y1=bbox_world(targets); m=margin/PPU; x0-=m;x1+=m;y0-=m;y1+=m
    cx=(x0+x1)/2;cy=(y0+y1)/2;w=x1-x0;h=y1-y0
    ld=bpy.data.lattices.new(name+'_DATA'); ld.points_u=u;ld.points_v=v;ld.points_w=2;ld.interpolation_type_u='KEY_BSPLINE';ld.interpolation_type_v='KEY_BSPLINE'
    la=bpy.data.objects.new(name,ld);DEF.objects.link(la);la.location=(cx,cy,.5)
    xs=[p.co_deform.x for p in ld.points]; ys=[p.co_deform.y for p in ld.points]; la.scale=(w/max(max(xs)-min(xs),1e-6),h/max(max(ys)-min(ys),1e-6),1)
    parent_preserve(la,parent);la.display_type='WIRE';la.hide_render=True
    for o in targets:
        md=o.modifiers.new('LAT_'+name,'LATTICE');md.object=la
    la.shape_key_add(name='Basis'); kn=la.shape_key_add(name='Yaw_N20');kp=la.shape_key_add(name='Yaw_P20');shape_driver(kn,True);shape_driver(kp,False)
    return la

def keyreset(la,n):
    kb=la.data.shape_keys.key_blocks;b=kb['Basis'];k=kb[n]
    for i,p in enumerate(b.data):k.data[i].co=p.co.copy()
    return b,k

def bounds(key):
    xs=[p.co.x for p in key.data];ys=[p.co.y for p in key.data];x0,x1=min(xs),max(xs);y0,y1=min(ys),max(ys);return (x0+x1)/2,(y0+y1)/2,max((x1-x0)/2,1e-6),max((y1-y0)/2,1e-6)

def facewarp(la,d):
    b,k=keyreset(la,'Yaw_P20' if d>0 else 'Yaw_N20');cx,cy,hw,hh=bounds(b);th=math.radians(11)*d
    for i,p in enumerate(b.data):
        x,y,z=p.co;nx=max(-1,min(1,(x-cx)/hw));ny=max(-1,min(1,(y-cy)/hh));nz=math.sqrt(max(0,1-nx*nx))*.28*(1-.18*abs(ny));nx2=nx*math.cos(th)+nz*math.sin(th);nx2=.88*nx2+.12*nx;k.data[i].co=Vector((cx+nx2*hw,y,z))

def groupwarp(la,d,tx,sx,edge=0):
    b,k=keyreset(la,'Yaw_P20' if d>0 else 'Yaw_N20');cx,cy,hw,hh=bounds(b);tx=tx/PPU*d
    for i,p in enumerate(b.data):
        x,y,z=p.co;nx=(x-cx)/hw;ls=sx*(1+edge*nx*d);k.data[i].co=Vector((cx+(x-cx)*ls+tx,y,z))

def hairwarp(la,d,tx,tip,bend):
    b,k=keyreset(la,'Yaw_P20' if d>0 else 'Yaw_N20');cx,cy,hw,hh=bounds(b)
    for i,p in enumerate(b.data):
        x,y,z=p.co;ny=(y-cy)/hh;rw=max(0,min(1,(ny+1)/2));follow=tip+(1-tip)*rw;mid=1-abs(2*rw-1);dx=(tx*follow+bend*mid)/PPU*d;k.data[i].co=Vector((x+dx,y,z))

L={}
L['FACE']=lattice('DEF_FACE_CONTOUR',[objs['Face_Base_Full']],6,7,8,face_space)
for side in 'LR':
    L['EYE_'+side]=lattice('DEF_EYE_'+side,many('Eye_'+side+'_'),5,4,4,face_space)
    L['BROW_'+side]=lattice('DEF_BROW_'+side,[objs['Brow_'+side]],4,3,3,face_space)
    L['EAR_'+side]=lattice('DEF_EAR_'+side,[objs['Ear_'+side]],3,4,3,face_space)
L['NOSE']=lattice('DEF_NOSE',[objs['Nose_Base']],3,3,3,face_space);L['MOUTH']=lattice('DEF_MOUTH',[objs['Mouth_Line']],4,3,4,face_space)
for n in ['Bang_L','Bang_C','Bang_R']:L[n]=lattice('DEF_'+n,[objs[n]],4,5,4,hair_space)
for n in ['SideHair_L','SideHair_R']:L[n]=lattice('DEF_'+n,[objs[n]],4,7,5,hair_space)
L['CROWN']=lattice('DEF_CROWN',[objs['Hair_Crown']],5,5,5,hair_space);L['BACK']=lattice('DEF_BACK',[objs['Hair_Back']],5,5,5,hair_space)

def far(side,d):return (d>0 and side=='L') or (d<0 and side=='R')
for d in (-1,1):
    facewarp(L['FACE'],d)
    for side in 'LR':
        f=far(side,d);tx=2 if side=='L' else 1.6;groupwarp(L['EYE_'+side],d,tx,.955 if f else 1.018,.01);groupwarp(L['BROW_'+side],d,tx*.85,.97 if f else 1.012,.006);groupwarp(L['EAR_'+side],d,.55,.92 if f else 1.05)
    groupwarp(L['NOSE'],d,5,.985);groupwarp(L['MOUTH'],d,2.7,.992)
    hairwarp(L['BACK'],d,.35,.92,.05);hairwarp(L['CROWN'],d,.45,.95,.05)
    for n in ['Bang_L','Bang_C','Bang_R']:hairwarp(L[n],d,.55,.82,.10)
    for n in ['SideHair_L','SideHair_R']:hairwarp(L[n],d,.65,.74,.12)

sc=bpy.context.scene;sc.render.engine='BLENDER_EEVEE_NEXT';sc.render.resolution_x=460;sc.render.resolution_y=700;sc.render.resolution_percentage=100;sc.render.film_transparent=False
if sc.world is None:
    sc.world=bpy.data.worlds.new('World')
sc.world.color=(1,1,1)
cd=bpy.data.cameras.new('Camera');cam=bpy.data.objects.new('Camera',cd);bpy.context.scene.collection.objects.link(cam);cam.location=(0,5.0,20);cam.rotation_euler=(0,0,0);cd.type='ORTHO';cd.ortho_scale=7.0;sc.camera=cam
cam.location.x=0.0;cam.location.y=5.25
bpy.context.scene.render.image_settings.file_format='PNG';bpy.context.scene.render.image_settings.color_mode='RGBA'

sc.frame_start=1;sc.frame_end=73
for f,val in [(1,-20),(19,0),(37,20),(55,0),(73,-20)]:ctrl['head_yaw']=val;ctrl.keyframe_insert(data_path='["head_yaw"]',frame=f)
if ctrl.animation_data and ctrl.animation_data.action:
    for fc in ctrl.animation_data.action.fcurves:
        for kp in fc.keyframe_points:kp.interpolation='BEZIER'

OUT=ROOT/'output';OUT.mkdir(exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'XLG_HeadYaw_V4_REAL.blend'))
for f,n in [(1,'Yaw_N20_REAL'),(19,'Yaw_0_REAL'),(37,'Yaw_P20_REAL')]:
    sc.frame_set(f);sc.render.filepath=str(OUT/(n+'.png'));bpy.ops.render.render(write_still=True)
print('RENDER_DONE_STILLS',OUT)
