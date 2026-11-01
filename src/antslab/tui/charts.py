"""Small deterministic terminal plots: explicit axes, units and missingness."""
import math


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def number(x):
    if x == 0:return '0'
    if abs(x)>=10000 or abs(x)<.01:return f'{x:.1g}'
    return f'{x:.2f}'.rstrip('0').rstrip('.')


def scatter(series,width=80,height=7,xlim=None,ylim=None,xlabel='x',ylabel='y',ascii_only=False):
    """series = [(label, marker, [(x,y), ...])]. Missing points are not joined."""
    points=[(x,y) for _,_,ps in series for x,y in ps if finite(x) and finite(y)]
    if not points:return [ylabel, 'No observations in this window.']
    if width<34:return [ylabel,'Widen the terminal to show axes.']
    xs,ys=zip(*points)
    xmin,xmax=xlim or (min(xs),max(xs));ymin,ymax=ylim or (min(ys),max(ys))
    if not all(finite(v) for v in (xmin,xmax,ymin,ymax)) or xmin>xmax or ymin>ymax:
        raise ValueError('invalid plot limits')
    if xmin==xmax:xmin-=.5;xmax+=.5
    if ymin==ymax:
        pad=max(abs(ymin)*.1,1);ymin-=pad;ymax+=pad
    h=max(3,min(height,16));pw=width-12
    cells=[[' ']*pw for _ in range(h)]
    for _,marker,ps in series:
        for x,y in ps:
            if not finite(x) or not finite(y) or not(xmin<=x<=xmax and ymin<=y<=ymax):continue
            col=round((x-xmin)/(xmax-xmin)*(pw-1));row=h-1-round((y-ymin)/(ymax-ymin)*(h-1))
            cells[row][col]=marker[:1] if cells[row][col]==' ' else '+'
    vert,corner,horiz=('|','+','-') if ascii_only else ('│','└','─')
    lines=[ylabel]
    for i,c in enumerate(cells):
        tick=number(ymax-i/(h-1)*(ymax-ymin)) if i in (0,h//2,h-1) else ''
        lines.append(f'{tick:>8} {vert}'+''.join(c))
    lines.append(' '*9+corner+horiz*pw)
    left,right=number(xmin),number(xmax)
    lines.append(' '*10+left+' '*max(1,pw-len(left)-len(right))+right)
    lines.append(' '*10+xlabel)
    lines.append(' '.join(f'{marker} {label}' for label,marker,_ in series)+'   + overlap')
    return [line[:width] for line in lines]


def role_bars(roles,width=80,ascii_only=False):
    """Horizontal count axes. Presence of a path is not integrity verification."""
    if width<40:return ['Dataset entries by role','Widen the terminal to show axes.']
    entries=[]
    for role,items in roles.items():
        total=len(items)
        present=None if role=='synthetic' else sum(bool(ok) for _,ok in items)
        entries.append((role,total,present))
    if not entries:return ['No catalog entries.']
    maximum=max(n for _,n,_ in entries) or 1;pw=width-32
    full,empty=('X','.') if ascii_only else ('■','░')
    lines=['Dataset entries by role (y); count (x)']
    for role,total,present in entries:
        n=round(total/maximum*pw)
        if present is None:bar='~'*n;label=f'{total} generated'
        else:
            have=round(present/maximum*pw);bar=full*have+empty*max(0,n-have);label=f'{present}/{total}'
        lines.append(f'{role:>10} |{bar:<{pw}} {label}')
    lines+=[' '*11+'+'+'-'*pw,' '*12+'0'+' '*max(1,pw-len(str(maximum))-1)+str(maximum),
            f'{full} file present  {empty} unavailable  ~ generated',
            'Presence is not a hash check.']
    return [line[:width] for line in lines]
