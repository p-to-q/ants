"""Small wireframe survey of observed configurations, with explicit density geometry.

The surface smooths visit counts only. Score mode never interpolates objective
values: it draws independent observations above the same reference plane.
"""
import math


def draw(points, width, height, selected=0, mode='density', ascii_only=False, direction='min'):
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
    def project(x,y,z=0):return 4+ny*sx+(x-y)*sx, rise+2+(x+y)*sy-z*rise
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
        edges=0;child=start;visited=set()
        while edges<limit and child not in visited:
            visited.add(child);p=points[child]
            parents=[i for i in lookup.get((p['source'],p.get('parent_key')),[]) if points[i]['cpu_s']<p['cpu_s']]
            if not parents:break
            parent=max(parents,key=lambda i:points[i]['cpu_s'])
            line(projected[child],projected[parent],priority);edges+=1;child=parent
        return edges
    edges=trace(selected,20,4)
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
    for (x,y),(mask,rank) in cells.items():
        char=('+' if rank>=2 else ':' if mask.bit_count()>3 else '.') if ascii_only else chr(0x2800+mask)
        canvas[y][x]=(char,'bold' if rank==4 else 'ok' if rank==3 else 'accent' if rank==2 else 'dim')
    def label(x,y,text,style='dim'):
        for n,c in enumerate(text):
            if 0<=x+n<width and 0<=y<height:canvas[y][x+n]=(c,style)
    arms=list(dict.fromkeys(p['arm'] for p in points));marks=[]
    for n,arm in enumerate(arms):
        i=max((i for i,p in enumerate(points) if p['arm']==arm),key=lambda i:points[i]['cpu_s'])
        mark=chr(97+n) if n<26 else '+';marks.append((mark,arm))
        x,y=projected[i];label(round(x/2),int(y//4),mark,'ok')
    best=min(range(len(points)),key=lambda i:points[i]['value']*(1 if direction=='min' else -1))
    x,y=projected[best];label(round(x/2),int(y//4),'*','warn')
    x,y=projected[selected];label(round(x/2),int(y//4),'@','bold')
    label(1,height-1,'PC2  /  configuration projection  /  PC1')
    return canvas,{'edges':edges,'marks':marks,'best':best,'density_peak':peak}
