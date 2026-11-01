"""Small wireframe survey of observed configurations, with explicit density geometry.

The surface smooths visit counts only. Score mode never interpolates objective
values: it draws independent observations above the same reference plane.
"""
import math


def draw(points, width, height, selected=0, mode='density', ascii_only=False, direction='min', angle=0, colours=True):
    width, height = max(30, width), max(7, height)
    nx, ny = 16, 12
    us, vs = [p['u'] for p in points], [p['v'] for p in points]
    u0, u1, v0, v1 = min(us), max(us), min(vs), max(vs)
    coords = [((p['u']-u0)/(u1-u0 or 1)*nx, (p['v']-v0)/(v1-v0 or 1)*ny) for p in points]
    counts = [[0.0]*(ny+1) for _ in range(nx+1)]
    for x,y in coords:
        counts[min(nx,round(x))][min(ny,round(y))] += 1
    # A visual KDE of OBSERVATION COUNTS, never a surrogate objective surface.
    surface = [[sum(counts[a][b]*math.exp(-((a-x)**2+(b-y)**2)/2)
                    for a in range(max(0,x-2),min(nx,x+2)+1)
                    for b in range(max(0,y-2),min(ny,y+2)+1))
                for y in range(ny+1)] for x in range(nx+1)]
    peak=max(max(row) for row in surface) or 1
    surface=[[math.log1p(v)/math.log1p(peak) for v in row] for row in surface]
    def density(x,y):
        a,b=min(nx-1,int(x)),min(ny-1,int(y));fx,fy=x-a,y-b
        return ((1-fx)*(1-fy)*surface[a][b]+fx*(1-fy)*surface[a+1][b]
                +(1-fx)*fy*surface[a][b+1]+fx*fy*surface[a+1][b+1])
    # Draw at braille subcell resolution, then resolve labels at terminal cells.
    pw,ph=2*(width-2),4*(height-1)
    rise=ph*.42; sx=(pw-10)/(nx+ny);sy=(ph-rise-8)/(nx+ny)
    angle%=4
    def project(x,y,z=0):
        x,y=((x,y),(ny-y,x),(nx-x,ny-y),(y,nx-x))[angle]
        return 4+(ny if angle%2==0 else nx)*sx+(x-y)*sx, rise+2+(x+y)*sy-z*rise
    pixels={}
    def dot(x,y,priority=0):
        x,y=round(x),round(y)
        if 0<=x<pw and 0<=y<ph:pixels[x,y]=max(priority,pixels.get((x,y),-1))
    def line(a,b,priority=0):
        steps=max(1,math.ceil(max(abs(a[0]-b[0]),abs(a[1]-b[1]))))
        for t in range(steps+1):dot(a[0]+(b[0]-a[0])*t/steps,a[1]+(b[1]-a[1])*t/steps,priority)
    for a,b in [((0,0),(nx,0)),((nx,0),(nx,ny)),((nx,ny),(0,ny)),((0,ny),(0,0))]:
        line(project(*a),project(*b))
    if mode=='density':
        for x in range(nx+1):
            for y in range(ny):line(project(x,y,surface[x][y]),project(x,y+1,surface[x][y+1]),1)
        for y in range(ny+1):
            for x in range(nx):line(project(x,y,surface[x][y]),project(x+1,y,surface[x+1][y]),1)
    else:
        for x in range(0,nx+1,4):line(project(x,0),project(x,ny))
        for y in range(0,ny+1,3):line(project(0,y),project(nx,y))
    low,high=min(p['value'] for p in points),max(p['value'] for p in points)
    projected=[]
    for i,(x,y) in enumerate(coords):
        z=density(x,y) if mode=='density' else ((high-points[i]['value']) if direction=='min' else (points[i]['value']-low))/(high-low or 1)
        q=project(x,y,z);projected.append(q);dot(*q,2)
    # No nearest-neighbour path inference: only recorded same-source parents.
    lookup={}
    for i,p in enumerate(points):lookup.setdefault((p['source'],p['spec_key']),[]).append(i)
    def trace(start,limit,priority):
        edges=0;child=start;visited=set();chain=[start]
        while edges<limit and child not in visited:
            visited.add(child);p=points[child]
            parents=[i for i in lookup.get((p['source'],p.get('parent_key')),[]) if points[i]['cpu_s']<p['cpu_s']]
            if not parents:break
            parent=max(parents,key=lambda i:points[i]['cpu_s'])
            line(projected[child],projected[parent],priority);edges+=1;child=parent;chain.append(parent)
        return edges,chain
    edges,chain=trace(selected,20,4)
    arms=list(dict.fromkeys(p['arm'] for p in points))
    for arm in arms[:4]:
        if arm==points[selected]['arm']:continue
        i=min((i for i,p in enumerate(points) if p['arm']==arm),key=lambda i:points[i]['value']*(1 if direction=='min' else -1))
        trace(i,12,3)
    bits=((1,8),(2,16),(4,32),(64,128))
    cells={}
    for (x,y),priority in pixels.items():
        cell=(x//2,y//4);old,rank=cells.get(cell,(0,0))
        cells[cell]=(old|bits[y%4][x%2],max(rank,priority))
    canvas=[[(' ','none') for _ in range(width)] for _ in range(height)]
    arm_cells={}
    for i,(x,y) in enumerate(projected):arm_cells.setdefault((round(x)//2,round(y)//4),set()).add(points[i]['arm'])
    for (x,y),(mask,rank) in cells.items():
        char=('+' if rank>=2 else ':' if mask.bit_count()>3 else '.') if ascii_only else chr(0x2800+mask)
        style='bold' if rank==4 else 'ok' if rank==3 else 'accent' if rank==2 else 'dim'
        if colours and rank==2:
            ids=arm_cells.get((x,y),set())
            style=('accent','ok','bold','dim')[arms.index(next(iter(ids)))%4] if len(ids)==1 else 'bold'
        if not colours and rank<4:style='dim'
        canvas[y][x]=(char,style)
    def label(x,y,text,style='dim'):
        for n,c in enumerate(text):
            if 0<=x+n<width and 0<=y<height:canvas[y][x+n]=(c,style)
    occupied=set();locations={}
    def place_label(i,text,style):
        px,py=projected[i];x,y=round(px/2),int(py//4)
        for dx,dy in [(0,0),(3,-1),(-3,-1),(4,1),(-4,1),(2,0),(-2,0),(6,-2),(-6,-2),(0,2)]:
            xx,yy=x+dx,y+dy
            if not(0<=xx<width and 0<=yy<height-1) or (xx,yy) in occupied:continue
            steps=max(abs(dx),abs(dy),1)
            if dx or dy:
                for t in range(steps):
                    ax,ay=round(x+dx*t/steps),round(y+dy*t/steps)
                    if 0<=ax<width and 0<=ay<height-1 and (ax,ay) not in occupied:label(ax,ay,'.','dim')
            label(xx,yy,text,style);occupied.add((xx,yy));locations[text]=(xx,yy);return
    best=min(range(len(points)),key=lambda i:points[i]['value']*(1 if direction=='min' else -1))
    place_label(selected,'@','bold');place_label(best,'*','warn')
    marks=[]
    for n,arm in enumerate(arms):
        i=max((i for i,p in enumerate(points) if p['arm']==arm),key=lambda i:points[i]['cpu_s'])
        glyph=chr(97+n) if n<26 else '+';marks.append((glyph,arm))
        place_label(i,glyph,'ok')
    ancestors=list(reversed(chain))[:-1];node_labels=[]
    count=min(4,len(ancestors))
    for n in range(count):
        i=ancestors[round(n*(len(ancestors)-1)/max(1,count-1))]
        glyph=str(n+1);place_label(i,glyph,'bold');node_labels.append((glyph,i))
    from collections import Counter
    collisions=Counter((round(x/2),int(y//4)) for x,y in projected)
    for cell,_ in [kv for kv in collisions.most_common() if kv[1]>1][:2]:
        i=next(i for i,(x,y) in enumerate(projected) if (round(x/2),int(y//4))==cell)
        place_label(i,'+','accent')
    label(1,height-1,f'PC1 / PC2 fixed projection · view {angle*90} deg')
    return canvas,{'edges':edges,'marks':marks,'best':best,'density_peak':peak,'locations':locations,'nodes':node_labels}
