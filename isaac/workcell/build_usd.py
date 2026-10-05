"""Deterministic procedural USD geometry; no third-party mesh redistribution."""
import math

# Own compact geometric glyphs, three columns by five rows. No font dependency.
GLYPHS = {
 'A':['010','101','111','101','101'],'B':['110','101','110','101','110'],
 'C':['011','100','100','100','011'],'D':['110','101','101','101','110'],
 'E':['111','100','110','100','111'],'F':['111','100','110','100','100'],
 'G':['011','100','101','101','011'],'H':['101','101','111','101','101'],
 'I':['111','010','010','010','111'],'L':['100','100','100','100','111'],
 'N':['101','111','111','111','101'],'P':['110','101','110','100','100'],
 'R':['110','101','110','101','101'],'S':['011','100','010','001','110'],
 'T':['111','010','010','010','010'],'U':['101','101','101','101','111'],
 'Y':['101','101','010','010','010'],'0':['111','101','101','101','111'],
 '1':['010','110','010','010','111'],
}


def build_workcell(stage, layout):
    from pxr import Gf, Sdf, UsdGeom, UsdShade, UsdLux, UsdPhysics
    from .layout import neutral_state
    from .state import StateAccessors
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.Xform.Define(stage, '/World/Workcell')
    materials = {}
    for name, color in sorted(layout['materials'].items()):
        path = '/World/Workcell/Materials/' + name
        material = UsdShade.Material.Define(stage, path)
        shader = UsdShade.Shader.Define(stage, path + '/Shader')
        shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(.7)
        material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
        materials[name] = material
    for data in layout['lights']:
        light = UsdLux.DomeLight.Define(stage, '/World/Workcell/Lights/' + data['id'])
        light.CreateIntensityAttr(data['intensity'])
        light.CreateColorAttr(Gf.Vec3f(*data['color']))

    def decorate(prim, material):
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(materials[material])
        UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(False)
    def cube(path, dimensions, position=(0.,0.,0.), material='neutral'):
        geometry = UsdGeom.Cube.Define(stage,path); geometry.CreateSizeAttr(1.)
        geometry.AddTranslateOp().Set(Gf.Vec3d(*position))
        geometry.AddScaleOp().Set(Gf.Vec3f(*dimensions))
        decorate(geometry.GetPrim(), material)
        return geometry
    def text(path, label, position, pixel=.002, horizontal=False):
        # Front view faces +X, horizontal face is +Z. Text left->right is +Y.
        width = (len(label)*4-1)*pixel
        for index, letter in enumerate(label):
            for row, bits in enumerate(GLYPHS[letter]):
                for col, bit in enumerate(bits):
                    if bit == '1':
                        u=(index*4+col+.5)*pixel-width/2; v=(2-row)*pixel
                        offset=(v,u,.0005) if horizontal else (.0005,u,v)
                        size=(pixel*.84,pixel*.84,.0007) if horizontal else (.0007,pixel*.84,pixel*.84)
                        cube(f'{path}/c{index}_r{row}_p{col}',size,
                             [position[i]+offset[i] for i in range(3)],'ink')
    def shell(path, dimensions, wall=.004):
        x,y,z=dimensions
        cube(path+'/Bottom',(x,y,wall),(0,0,-z/2+wall/2))
        cube(path+'/Back',(wall,y,z),(-x/2+wall/2,0,0))
        cube(path+'/Front',(wall,y,z),(x/2-wall/2,0,0))
        cube(path+'/Left',(x,wall,z),(0,-y/2+wall/2,0))
        cube(path+'/Right',(x,wall,z),(0,y/2-wall/2,0))
    def washer(path, size):
        r=size[0]/2; inner=r*.5; height=size[2]/2; segments=32
        points=[]
        for z in (-height,height):
            for radius in (inner,r):
                points += [(radius*math.cos(i*2*math.pi/segments),radius*math.sin(i*2*math.pi/segments),z) for i in range(segments)]
        faces=[]
        for i in range(segments):
            j=(i+1)%segments
            faces += [[i,j,segments+j,segments+i],[2*segments+i,3*segments+i,3*segments+j,2*segments+j],
                      [i,2*segments+i,2*segments+j,j],[segments+i,segments+j,3*segments+j,3*segments+i]]
        mesh=UsdGeom.Mesh.Define(stage,path)
        mesh.CreatePointsAttr(points); mesh.CreateFaceVertexCountsAttr([4]*len(faces))
        mesh.CreateFaceVertexIndicesAttr([i for face in faces for i in face])
        mesh.CreateSubdivisionSchemeAttr('none'); decorate(mesh.GetPrim(),'washer')

    for data in layout['objects']:
        path=data['prim_path']; root=UsdGeom.Xform.Define(stage,path)
        root.AddTranslateOp(); root.AddOrientOp(precision=UsdGeom.XformOp.PrecisionDouble)
        root.GetPrim().CreateAttribute('workcell:id',Sdf.ValueTypeNames.String).Set(data['id'])
        root.GetPrim().CreateAttribute('workcell:enabled',Sdf.ValueTypeNames.Bool).Set(True)
        root.GetPrim().CreateAttribute('workcell:linearVelocity',Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(0))
        root.GetPrim().CreateAttribute('workcell:angularVelocity',Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(0))
        for key,value in data['state'].items():
            dtype={bool:Sdf.ValueTypeNames.Bool,int:Sdf.ValueTypeNames.Int,float:Sdf.ValueTypeNames.Double,str:Sdf.ValueTypeNames.String}[type(value)]
            root.GetPrim().CreateAttribute('workcell:'+key,dtype).Set(value)
        x,y,z=data['dimensions_m']; kind=data['kind']; material=data['material']
        visual=UsdGeom.Xform.Define(stage,path+'/Visual')
        vp=str(visual.GetPath())
        if kind=='washer': washer(vp+'/Ring',(x,y,z))
        elif kind in ('tray','container','cup'):
            shell(vp,(x,y,z))
            if data['label']:
                pixel=(layout['label_style']['primary_label_pixel_m'] if kind=='tray' else
                       min(layout['label_style']['secondary_cup_max_pixel_m'], y*.85/(len(data['label'])*4+1)))
                label_z=data.get('label_offset_z_m',0.)
                cube(vp+'/Label',(.001,y*.90,.020),(x/2+.0007,0,label_z),'label')
                if label_z: cube(vp+'/LabelPost',(.003,.008,label_z),(x/2,0,label_z/2),'neutral')
                text(vp+'/Text',data['label'],(x/2+.0013,0,label_z),pixel)
        elif kind=='surface':
            cube(vp+'/Top',(x,y,z),material=material)
            for i,xx in enumerate((-x*.4,x*.4)):
                for j,yy in enumerate((-y*.4,y*.4)):
                    cube(vp+f'/Leg{i}{j}',(.035,.035,.79),(xx,yy,-.41),'surface')
        elif kind=='card':
            visual.AddRotateXOp().Set(0.)
            cube(vp+'/Card',(x,y,z),material='card')
            text(vp+'/Face0','0',(0,0,z/2+.0005),layout['label_style']['card_face_pixel_m'],True)
            # Face1 label faces downward until the card flips.
            back=UsdGeom.Xform.Define(stage,vp+'/BackFace'); back.AddRotateXOp().Set(180.)
            text(vp+'/BackFace/Text','1',(0,0,z/2+.0005),layout['label_style']['card_face_pixel_m'],True)
        elif kind=='arrow':
            # Upright slot is fixed along +X; arrow rotates around +Z.
            cube(path+'/MarkedSlot',(x,.006,.001),(0,0,-.002),'label')
            visual.AddRotateZOp().Set(0.)
            cube(vp+'/Shaft',(x*.60,.006,z),(-x*.1,0,0),'arrow')
            for i,sign in enumerate((-1,1)):
                bar=cube(vp+f'/Head{i}',(x*.35,.006,z),(x*.25,sign*x*.10,0),'arrow')
                bar.AddRotateZOp().Set(-sign*45.)
        elif kind=='lid':
            visual.AddRotateYOp().Set(0.)
            cube(vp+'/Panel',(x,y,z),(x/2,0,0),material)
        elif kind=='code':
            cube(vp+'/Plate',(x,y,z),material='label')
            cube(vp+'/Post',(.002,.006,.055),(0,0,-.038),'neutral')
            text(vp+'/Text',data['label'],(x/2+.0005,0,0),layout['label_style']['primary_label_pixel_m'])
        elif kind=='quarantine':
            # Marked perimeter, no coloured per-target differences.
            for i,yy in enumerate((-y/2,y/2)): cube(vp+f'/EdgeY{i}',(x,.003,z),(0,yy,0),'quarantine')
            for i,xx in enumerate((-x/2,x/2)): cube(vp+f'/EdgeX{i}',(.003,y,z),(xx,0,0),'quarantine')
            text(vp+'/Text',data['label'],(0,0,z/2+.0005),layout['label_style']['primary_label_pixel_m'],True)
        elif kind=='tag':
            cube(vp+'/Plate',(x,y,z),material='tag')
            cube(vp+'/Clip',(.005,y*.6,z*1.5),(-x/2,0,.003),'washer')
        else: raise ValueError('Unsupported geometry kind: '+kind)
    accessors=StateAccessors(stage,layout)
    accessors.apply_state(neutral_state(layout))
    return accessors

