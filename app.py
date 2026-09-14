from __future__ import annotations
import csv, io, json, os, shutil, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory

BASE=Path(__file__).resolve().parent
DATA=Path(os.getenv('FPV_DATA_DIR', BASE/'data')); RAW=DATA/'blackbox'; VIDEO=DATA/'video'; PART=DATA/'.partial'
for p in (DATA,RAW,VIDEO,PART): p.mkdir(parents=True,exist_ok=True)
DB=DATA/'ops.sqlite3'; LOCK=threading.RLock(); app=Flask(__name__); app.config['MAX_CONTENT_LENGTH']=80*1024**3
SCENARIOS={'SC-01':'Line A–B–A','SC-02':'Square','SC-03':'Oval','SC-04':'Freestyle / 3 steps','SC-05':'Slalom','SC-06':'Figure-8'}
PILOT_NAMES={'PILOT-001':'Markas','PILOT-002':'Gustas','PILOT-003':'Simas','PILOT-004':'Rokas','PILOT-005':'Valdemaras','PILOT-006':'Darius','PILOT-007':'Tautvydas'}
MODES=('CALM','DYN'); WEATHER=('W1','W2'); REPS=('REP-01','REP-02','REP-03','REP-04')

def ts(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def db(): c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; return c
def init():
 with LOCK,db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS flights(id INTEGER PRIMARY KEY,flight_code TEXT UNIQUE,pilot_id TEXT,uav_id TEXT,battery_id TEXT,scenario TEXT,mode TEXT,weather TEXT,repetition TEXT,session_id TEXT,date TEXT,status TEXT DEFAULT 'FUTURE',pilot_tag TEXT DEFAULT 'REGULAR',flight_note TEXT DEFAULT '',started_at TEXT,landed_at TEXT,raw_path TEXT DEFAULT '',video_path TEXT DEFAULT '',raw_ok TEXT DEFAULT 'NEE',video_ok TEXT DEFAULT 'NEE',video_due INTEGER DEFAULT 0,source_device TEXT DEFAULT '',updated_at TEXT); CREATE TABLE IF NOT EXISTS pilots(pilot_id TEXT PRIMARY KEY,name TEXT,tag TEXT DEFAULT 'REGULAR',active INTEGER DEFAULT 1); CREATE TABLE IF NOT EXISTS sync_events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,payload TEXT,created_at TEXT); CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)''')
  if c.execute('SELECT COUNT(*) FROM flights').fetchone()[0]==0:
   rows=[]; n=1
   for pi in range(1,21):
    pid=f'PILOT-{pi:03d}'; uid=f'UAV-{pi:03d}'; bid=f'BAT-{pi:03d}'
    for sc in SCENARIOS:
     for mode in MODES:
      for weather in WEATHER:
       for rep in REPS:
        # Workbook provides 20 session slots; leave assignment explicit but usable.
        session=f'Sesija {((n-1)//96)%20+1}'
        rows.append((n,f'FL-{n:06d}',pid,uid,bid,sc,mode,weather,rep,session,datetime.now().date().isoformat(),'FUTURE','REGULAR','','','','','','NEE','NEE',0,'',ts())); n+=1
   c.executemany('''INSERT INTO flights(id,flight_code,pilot_id,uav_id,battery_id,scenario,mode,weather,repetition,session_id,date,status,pilot_tag,flight_note,started_at,landed_at,raw_path,video_path,raw_ok,video_ok,video_due,source_device,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',rows)
   c.executemany('INSERT OR IGNORE INTO pilots VALUES(?,?,?,1)',[(f'PILOT-{i:03d}',PILOT_NAMES.get(f'PILOT-{i:03d}',''), 'REGULAR') for i in range(1,21)])
   c.execute("INSERT OR REPLACE INTO settings VALUES('source_workbook','Viskas.xlsx; ledger=SKRYDzIAI; sessions=Sesija 1..20')")

def clean(r):
 d=dict(r); d['complete']=d['raw_ok']=='TAIP' and d['video_ok']=='TAIP'; return d
def get(sql,args=()):
 with db() as c:return [clean(x) for x in c.execute(sql,args).fetchall()]
def one(sql,args=()):
 with db() as c:
  x=c.execute(sql,args).fetchone(); return clean(x) if x else None
def event(kind,payload):
 with LOCK,db() as c:c.execute('INSERT INTO sync_events(kind,payload,created_at) VALUES(?,?,?)',(kind,json.dumps(payload,ensure_ascii=False),ts()))
def update(fid,fields):
 allowed={'status','pilot_tag','flight_note','session_id','raw_path','video_path','raw_ok','video_ok','video_due','source_device','started_at','landed_at'}; f={k:v for k,v in fields.items() if k in allowed};
 if not f:return
 f['updated_at']=ts(); sql=', '.join(k+'=?' for k in f)
 with LOCK,db() as c:c.execute('UPDATE flights SET '+sql+' WHERE id=?',(*f.values(),fid))
 event('flight_update',{'id':fid,**f})
def safe(s): return ''.join(ch for ch in Path(s).name if ch.isalnum() or ch in '._-')
def save_upload(fs,flight,kind,device):
 if not fs or not fs.filename:return ''
 ext=Path(fs.filename).suffix.lower() or ('.bbl' if kind=='raw' else '.mp4'); out=RAW if kind=='raw' else VIDEO
 name=f"{flight['flight_code']}_{flight['pilot_id']}_{flight['scenario']}_{flight['mode']}_{flight['weather']}_{flight['repetition']}{ext}"; tmp=PART/(uuid.uuid4().hex+'.part'); final=out/safe(name); fs.save(tmp); shutil.move(tmp,final); return str(final.relative_to(DATA))

def state():
 fs=get('SELECT * FROM flights ORDER BY id'); pilots=[]
 for p in range(1,21):
  pid=f'PILOT-{p:03d}'; a=[x for x in fs if x['pilot_id']==pid]; live=next((x for x in a if x['status'] in ('FLYING','LANDED','TRANSFERRING')),None); done=sum(x['complete'] for x in a); pilots.append({'pilot_id':pid,'name':PILOT_NAMES.get(pid,''),'tag':next((x['pilot_tag'] for x in a), 'REGULAR'),'done':done,'total':96,'status':live['status'] if live else ('DONE' if done==96 else 'READY'),'current':live})
 return {'summary':{'total':len(fs),'complete':sum(x['complete'] for x in fs),'flying':sum(x['status']=='FLYING' for x in fs),'landed':sum(x['status']=='LANDED' for x in fs),'transferring':sum(x['status']=='TRANSFERRING' for x in fs),'video_due':sum(x['video_due'] and not x['video_ok']=='TAIP' for x in fs)},'pilots':pilots,'scenarios':SCENARIOS,'sessions':[f'Sesija {i}' for i in range(1,21)]}

@app.get('/')
def home(): return render_template('index.html')
@app.get('/<role>')
def role(role): return render_template('index.html',role=role if role in ('admin','officer','collector') else 'admin')
@app.get('/api/state')
def api_state(): return jsonify(state())
@app.get('/api/flights')
def api_flights():
 q='SELECT * FROM flights WHERE 1=1'; a=[]
 for k in ('status','pilot_id','session_id','pilot_tag'):
  if request.args.get(k):q+=' AND '+k+'=?';a.append(request.args[k])
 q+=' ORDER BY id'; return jsonify(get(q,tuple(a)))
@app.post('/api/flights/<int:fid>/status')
def api_status(fid):
 p=request.get_json() or {}; st=p.get('status');
 if st not in ('FUTURE','READY','FLYING','LANDED','TRANSFERRING','COMPLETE','BLOCKED'):return jsonify(error='invalid status'),400
 f=one('SELECT * FROM flights WHERE id=?',(fid,));
 if not f:return jsonify(error='not found'),404
 fields={'status':st};
 if st=='FLYING': fields['started_at']=ts()
 if st=='LANDED':
  fields['landed_at']=ts()
  with db() as c: landed=c.execute("SELECT COUNT(*) FROM flights WHERE pilot_id=? AND status IN ('LANDED','TRANSFERRING','COMPLETE')",(f['pilot_id'],)).fetchone()[0]+1
  fields['video_due']=1 if landed%4==0 else f['video_due']
 update(fid,fields); return jsonify(ok=True,flight=one('SELECT * FROM flights WHERE id=?',(fid,)))
@app.post('/api/pilots/<pilot_id>/tag')
def api_tag(pilot_id):
 tag=(request.get_json() or {}).get('tag');
 if tag not in ('REGULAR','SDCARD'):return jsonify(error='invalid tag'),400
 with LOCK,db() as c:c.execute('UPDATE pilots SET tag=? WHERE pilot_id=?',(tag,pilot_id)); c.execute('UPDATE flights SET pilot_tag=?,updated_at=? WHERE pilot_id=?',(tag,ts(),pilot_id))
 event('pilot_tag',{'pilot_id':pilot_id,'tag':tag}); return jsonify(ok=True)
@app.post('/api/flights/<int:fid>/intake')
def api_intake(fid):
 f=one('SELECT * FROM flights WHERE id=?',(fid,));
 if not f:return jsonify(error='not found'),404
 p=request.form; device=p.get('device','LAPTOP-1'); raw=save_upload(request.files.get('raw_file'),f,'raw',device); vid=save_upload(request.files.get('video_file'),f,'video',device); fields={'source_device':device,'status':'TRANSFERRING'}
 if raw:fields.update(raw_path=raw,raw_ok='TAIP')
 if vid:fields.update(video_path=vid,video_ok='TAIP',video_due=0)
 if (raw or f['raw_ok']=='TAIP') and (vid or f['video_ok']=='TAIP'):fields.update(status='COMPLETE')
 update(fid,fields); return jsonify(ok=True,flight=one('SELECT * FROM flights WHERE id=?',(fid,)))
@app.post('/api/telemetry/assign')
def assign():
 p=request.form; fid=int(p['flight_id']); return api_intake(fid)
@app.post('/api/claim')
def claim():
 p=request.get_json() or {}; pid=p.get('pilot_id');
 f=one("SELECT * FROM flights WHERE pilot_id=? AND status IN ('FUTURE','READY') ORDER BY id LIMIT 1",(pid,)) if pid else one("SELECT * FROM flights WHERE status IN ('FUTURE','READY') ORDER BY id LIMIT 1")
 if not f:return jsonify(flight=None)
 update(f['id'],{'status':'FLYING','pilot_tag':p.get('tag',f['pilot_tag'])}); return jsonify(flight=one('SELECT * FROM flights WHERE id=?',(f['id'],)))
@app.get('/api/sync/events')
def sync_events():
 with db() as c:
  after=int(request.args.get('after','0'))
  rows=[dict(x) for x in c.execute('SELECT * FROM sync_events WHERE id>? ORDER BY id LIMIT 500',(after,)).fetchall()]
 for x in rows: x['payload']=json.loads(x['payload'])
 return jsonify({'events':rows})
@app.post('/api/sync/push')
def sync_push():
 payload=request.get_json() or {}; n=0
 for e in payload.get('events',[]):
  p=e.get('payload',{})
  if isinstance(p,str): p=json.loads(p)
  if e.get('kind')=='flight_update': update(int(p['id']),p); n+=1
  elif e.get('kind')=='pilot_tag':
   with LOCK,db() as c: c.execute('UPDATE pilots SET tag=? WHERE pilot_id=?',(p['tag'],p['pilot_id'])); c.execute('UPDATE flights SET pilot_tag=?,updated_at=? WHERE pilot_id=?',(p['tag'],ts(),p['pilot_id']))
   n+=1
 return jsonify(ok=True,applied=n)
@app.get('/export/flights.csv')
def export():
 rows=get('SELECT * FROM flights ORDER BY id'); out=io.StringIO(); w=csv.DictWriter(out,fieldnames=rows[0].keys() if rows else ['flight_code']);w.writeheader();w.writerows(rows);return app.response_class(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=skrydziai.csv'})
@app.get('/files/<path:name>')
def files(name): return send_from_directory(DATA,name,as_attachment=True)
init()
if __name__=='__main__':app.run(host=os.getenv('FPV_HOST','0.0.0.0'),port=int(os.getenv('FPV_PORT','5050')),debug=False)
