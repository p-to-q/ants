"""Small deterministic terminal plots: explicit axes, units and missingness."""
import math


def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)


def number(x):
    if x == 0:return '0'
    if abs(x)>=10000 or abs(x)<.01:return f'{x:.1g}'
    return f'{x:.2f}'.rstrip('0').rstrip('.')


def scatter(series,width=80,height=7,xlim=None,ylim=None,xlabel='x',ylabel='y',ascii_only=False,connect=False):
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
    def paint(col,row,marker):
        previous=cells[row][col]
        cells[row][col]=marker[:1] if previous in (' ',marker[:1]) else '+'
    for _,marker,ps in series:
        previous=None
        for x,y in ps:
            if not finite(x) or not finite(y) or not(xmin<=x<=xmax and ymin<=y<=ymax):
                previous=None
                continue
            col=round((x-xmin)/(xmax-xmin)*(pw-1));row=h-1-round((y-ymin)/(ymax-ymin)*(h-1))
            if connect and previous is not None:
                pc,pr=previous;steps=max(abs(col-pc),abs(row-pr),1)
                for t in range(steps+1):paint(round(pc+(col-pc)*t/steps),round(pr+(row-pr)*t/steps),marker)
            paint(col,row,marker);previous=(col,row)
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


def activity_grid(data,width=80,days=7,metric='cpu',ascii_only=False):
    """Styled date/hour grid. CPU is logged evaluation time, never host utilisation."""
    import datetime
    if width<62:
        return [[(' Research activity','accent')],[(' Widen to 62 columns for the 24-hour grid.','dim')]]
    now=data.get('now',0);today=datetime.datetime.fromtimestamp(now).replace(hour=0,minute=0,second=0,microsecond=0)
    days=max(1,min(days,7));started=data.get('started_at')
    dates={(today-datetime.timedelta(days=d)).strftime('%Y-%m-%d') for d in range(days)}
    cells={(c['day'],c['hour']):c for c in data.get('cells',[]) if c['day'] in dates}
    unit='CPU minutes' if metric=='cpu' else 'completed trials'
    peak=max([c['cpu_s']/60 if metric=='cpu' else c['done'] for c in cells.values()] or [0])
    lines=[[(' Research activity · '+unit+f' · {data.get("sources",0)} ledgers · c changes metric','accent')],
           [(' local hr  '+''.join(f'{h:02d}    ' for h in range(0,24,3)),'dim')]]
    total=0.;missing=sum(c['terminal']-c['cpu_known'] for c in cells.values())
    for delta in range(days-1,-1,-1):
        day=today-datetime.timedelta(days=delta);row=[(' '+day.strftime('%m-%d %a')+' ','dim')]
        for h in range(24):
            begin=day.replace(hour=h).timestamp();end=(day.replace(hour=h)+datetime.timedelta(hours=1)).timestamp()
            cell=cells.get((day.strftime('%Y-%m-%d'),h));unknown=not finite(started) or end<=started or begin>now
            value=(cell['cpu_s']/60 if metric=='cpu' else cell['done']) if cell else 0
            if metric=='cpu' and cell and not cell['cpu_known']:unknown=True
            if unknown:mark,style='- ','dim'
            elif value<=0:mark,style='. ' if ascii_only else '· ','dim'
            else:
                level=min(3,max(0,math.ceil(4*math.sqrt(value/(peak or 1)))-1))
                mark=('1234'[level] if ascii_only else '░▒▓█'[level])+' ';style=('ok','ok','accent','bold')[level];total+=value
            row.append((mark,style))
        lines.append(row)
    lines.append([(f' {days} dates · {data.get("timezone","local time")} · peak {number(peak)} {unit}/hour · total {number(total)}','dim')])
    lines.append([(' · zero recorded · - outside coverage · CPU attributed to finish hour','dim')])
    if missing or data.get('unreadable'):
        lines.append([(f' {missing} missing CPU records · {data.get("unreadable",0)} unreadable sources','warn')])
    return lines
