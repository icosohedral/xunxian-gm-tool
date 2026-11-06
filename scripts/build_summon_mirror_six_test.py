from pathlib import Path
from scripts.unpack import DpkReader
from scripts.repack import replace_resource,batch_repacking
names=('skill/skill_data_npc_1.txt','skill/method_data_npc_1.txt','skill/trigger_npc_1.txt','skill/methodunit_data.txt')
spec=[('壬鱼',101764,60001,990011),('勾陈',101779,60002,990012),('风雷',101782,60003,990013),('腾蛇',101769,60004,990014),('无妄',101787,60005,990015)]
base={'main':Path('data/server_mb_main.dpk'),'dynamic':Path('data/server_mb_dynamic.dpk'),'client':Path('data/client_mb_work.dpk')}
outdir=Path('/tmp/gmtool-mirror-five-stage');outdir.mkdir(exist_ok=True)
for label,path in base.items():
 with DpkReader(path) as d:
  allent={e.path.lower():e for e in d.read_entries()}; rows={n:[l.split('\t') for l in d.extract(allent[n]).decode('gb18030').splitlines()] for n in names}
  def lookup(n,col,key):
   hits=[f for f in rows[n] if len(f)>col and f[col]==str(key)]
   if not hits and label!='main':hits=[f.copy() for f in masterrows[n] if len(f)>col and f[col]==str(key)]
   if len(hits)!=1:raise ValueError((label,n,key,len(hits)))
   return hits[0]
  skill=lookup(names[0],1,60000); method_template=lookup(names[1],1,990001)
  allunits=[]
  for path2,entry in allent.items():
   if path2.startswith('skill/methodunit_data') and path2.endswith('.txt'):
    allunits.extend(l.split('\t') for l in d.extract(entry).decode('gb18030').splitlines()[1:])
  for eid in (9900001,9900002):lookup(names[2],0,eid)[3]='12'
  if label=='main':
   masterrows={n:[r.copy() for r in v] for n,v in rows.items()}; masterunits=[r.copy() for r in allunits]
  elif label!='main':
   allunits=masterunits
  nexttrigger=9900100; nextunit=60100
  for labelname,source,id,mid in spec:
   sf=skill.copy();sf[0]='签系同步'+labelname;sf[1]=str(id);sf[2]=str(mid);rows[names[0]].append(sf)
   original=lookup(names[1],1,source)
   m=method_template.copy();m[0]=str(id);m[1]=str(mid);m[3]='同步'+labelname
   m[10]=original[10];m[11]=original[11]
   events=[]
   for old in original[2].split('*'):
    if not old:continue
    ev=lookup(names[2],0,old).copy()
    if ev[1] in ('4','5'):continue
    if ev[2] not in ('10001','10002','10010','20018','30005','30007','30010'):continue
    ev[0]=str(nexttrigger);nexttrigger+=1
    if ev[2]=='30010':
     found=[x for x in allunits if len(x)>1 and x[1]==ev[4]];
     if len(found)!=1:raise ValueError((label, 'unit',ev[4],len(found)))
     unit=found[0].copy();unit[0]='签系同步'+labelname+'特效';unit[1]=str(nextunit);nextunit+=1;unit[5]='skill_1';rows[names[3]].append(unit);ev[4]=unit[1]
    if len(ev)>8 and ev[8]=='skill_20':ev[8]='skill_1'
    ev[3]='12';rows[names[2]].append(ev);events.append(ev[0])
   # Ensure server-side hit processing is present even when the source uses another event.
   if not any(lookup(names[2],0,x)[2]=='20018' for x in events):
    ev=lookup(names[2],0,9900002).copy();ev[0]=str(nexttrigger);nexttrigger+=1;ev[3]='12';rows[names[2]].append(ev);events.append(ev[0])
   m[2]='*'.join(events);rows[names[1]].append(m)
  replacements={n:('\r\n'.join('\t'.join(f) for f in rows[n])+'\r\n').encode('gb18030') for n in names}
 current=path
 for i,(name,data) in enumerate(replacements.items()):
  dest=outdir/f'{label}-{i}.dpk';dest.unlink(missing_ok=True)
  with batch_repacking():replace_resource(current,name,None,dest,line_edit=None,replacement_data=data,compression_level=6,zlib_mem_level=8,zlib_strategy='default',auto_zlib_parameters=True,allow_chain_growth=True,allow_chain_shrink=True,overwrite_output=False,overwrite_report=False,max_input_size=2*1024*1024*1024,report_path=None)
  if i and current.parent==outdir:current.unlink()
  current=dest
 print(label,current,flush=True)
