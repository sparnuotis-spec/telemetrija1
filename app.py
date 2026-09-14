from __future__ import annotations
import csv, io, json, os, shutil, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, jsonify, render_template, request, send_from_directory
from betaflight_msp import ports as bf_ports, identify as bf_identify, download as bf_download

BASE=Path(__file__).resolve().parent
DATA=Path(os.getenv('FPV_DATA_DIR', BASE/'data')); RAW=DATA/'blackbox'; VIDEO=DATA/'video'; PART=DATA/'.partial'
for p in (DATA,RAW,VIDEO,PART): p.mkdir(parents=True,exist_ok=True)
DB=DATA/'ops.sqlite3'; LOCK=threading.RLock(); app=Flask(__name__); app.config['MAX_CONTENT_LENGTH']=80*1024**3
SCENARIOS={'SC-01':'Line A–B–A','SC-02':'Square','SC-03':'Oval','SC-04':'Freestyle / 3 steps','SC-05':'Slalom','SC-06':'Figure-8'}
MODES=('CALM','DYN'); WEATHER=('W1','W2'); REPS=('REP-01','REP-02','REP-03','REP-04')
STATUSES=('NAN','WAITING','FLYING','NEED_TRANSFER','TRANSFERRING','DONE','FALSE')

# NAN = pilot has not reported ready. A flight becomes WAITING when the pilot is ready.
def ts(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def today(): return datetime.now().date().isoformat()
def db(): c=sqlite3.connect(DB,timeout=30); c.row_factory=sqlite3.Row; return c
def clean(r):
 d=dict(r); d['complete']=d.get('status')=='DONE' or (d.get('raw_ok')=='TAIP' and d.get('video_ok')=='TAIP'); return d
def one(sql,args=()):
 with db() as c:
  r=c.execute(sql,args).fetchone(); return clean(r) if r else None
def many(sql,args=()):
 with db() as c:return [clean(x) for x in c.execute(sql,args).fetchall()]
def event(kind,payload):
 with LOCK,db() as c:c.execute('INSERT INTO sync_events(kind,payload,created_at) VALUES(?,?,?)',(kind,json.dumps(payload,ensure_ascii=False),ts()))
def safe(s): return ''.join(ch for ch in Path(s).name if ch.isalnum() or ch in '._-')

def init():
 with LOCK,db() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS flights(id INTEGER PRIMARY KEY,flight_code TEXT UNIQUE,pilot_id TEXT DEFAULT '',uav_id TEXT DEFAULT '',battery_id TEXT DEFAULT '',scenario TEXT DEFAULT '',mode TEXT DEFAULT '',weather TEXT DEFAULT '',repetition TEXT DEFAULT '',session_id TEXT DEFAULT '',date TEXT DEFAULT '',status TEXT DEFAULT 'NAN',pilot_tag TEXT DEFAULT 'REGULAR',flight_note TEXT DEFAULT '',started_at TEXT,landed_at TEXT,raw_path TEXT DEFAULT '',video_path TEXT DEFAULT '',raw_ok TEXT DEFAULT 'NEE',video_ok TEXT DEFAULT 'NEE',video_due INTEGER DEFAULT 0,source_device TEXT DEFAULT '',updated_at TEXT); CREATE TABLE IF NOT EXISTS pilots(pilot_id TEXT PRIMARY KEY,name TEXT DEFAULT '',tag TEXT DEFAULT 'REGULAR',active INTEGER DEFAULT 0,ready INTEGER DEFAULT 0); CREATE TABLE IF NOT EXISTS sync_events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,payload TEXT,created_at TEXT); CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT)''')
  for i in range(1,21): c.execute('INSERT OR IGNORE INTO pilots(pilot_id) VALUES(?)',(f'PILOT-{i:03d}',))
  c.execute("INSERT OR IGNORE INTO settings(k,v) VALUES('scenarios',?)",(json.dumps(list(SCENARIOS)),))

def setting(k,default=None):
 with db() as c:
  r=c.execute('SELECT v FROM settings WHERE k=?',(k,)).fetchone(); return json.loads(r['v']) if r else default
def set_setting(k,v):
 with LOCK,db() as c:c.execute('INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)',(k,json.dumps(v,ensure_ascii=False)))
def update(fid,fields):
 allowed={'status','pilot_id','uav_id','battery_id','scenario','mode','weather','repetition','session_id','date','pilot_tag','flight_note','raw_path','video_path','raw_ok','video_ok','video_due','source_device','started_at','landed_at'}; f={k:v for k,v in fields.items() if k in allowed}
 if not f:return
 f['updated_at']=ts(); sql=', '.join(k+'=?' for k in f)
 with LOCK,db() as c:c.execute('UPDATE flights SET '+sql+' WHERE id=?',(*f.values(),fid))
 event('flight_update',{'id':fid,**f})

def save_upload(fs,flight,kind,device):
 if not fs or not fs.filename:return ''
 ext=Path(fs.filename).suffix.lower() or ('.bbl' if kind=='raw' else '.mp4'); out=RAW if kind=='raw' else VIDEO
 name=f"{flight['flight_code']}_{flight['pilot_id']}_{flight['scenario']}_{flight['mode']}_{flight['weather']}_{flight['repetition']}{ext}"; tmp=PART/(uuid.uuid4().hex+'.part'); final=out/safe(name); fs.save(tmp); shutil.move(tmp,final); return str(final.relative_to(DATA))

def generate_flights(pilots,scenarios,session_id='Sesija 1'):
 with LOCK,db() as c:
  c.execute('DELETE FROM flights')
  rows=[]; n=1
  for pid in pilots:
   for sc in scenarios:
    for mode in MODES:
     for weather in WEATHER:
      for rep in REPS:
       rows.append((n,f'FL-{n:06d}',pid,f'UAV-{pid[-3:]}',f'BAT-{pid[-3:]}',sc,mode,weather,rep,session_id,today(),'NAN','REGULAR','','','','','','NEE','NEE',0,'',ts())); n+=1
  c.executemany('''INSERT INTO flights(id,flight_code,pilot_id,uav_id,battery_id,scenario,mode,weather,repetition,session_id,date,status,pilot_tag,flight_note,started_at,landed_at,raw_path,video_path,raw_ok,video_ok,video_due,source_device,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',rows)
  return len(rows)

def state():
 fs=many('SELECT * FROM flights ORDER BY id'); ps=many('SELECT * FROM pilots ORDER BY pilot_id');
 pilots=[]
 for p in ps:
  a=[x for x in fs if x['pilot_id']==p['pilot_id']]; live=next((x for x in a if x['status'] in ('WAITING','FLYING','NEED_TRANSFER','TRANSFERRING')),None); done=sum(x['status']=='DONE' for x in a)
  status='NAN' if not p['active'] or not p['ready'] else ('DONE' if a and done==len(a) else (live['status'] if live else 'WAITING'))
  pilots.append({'pilot_id':p['pilot_id'],'name':p['name'],'tag':p['tag'],'active':bool(p['active']),'ready':bool(p['ready']),'done':done,'total':len(a),'status':status,'current':live})
 return {'summary':{'total':len(fs),'done':sum(x['status']=='DONE' for x in fs),'flying':sum(x['status']=='FLYING' for x in fs),'need_transfer':sum(x['status']=='NEED_TRANSFER' for x in fs),'transferring':sum(x['status']=='TRANSFERRING' for x in fs),'false':sum(x['status']=='FALSE' for x in fs)},'pilots':pilots,'scenarios':SCENARIOS,'selected_scenarios':setting('scenarios',list(SCENARIOS)),'sessions':[f'Sesija {i}' for i in range(1,21)]}

@app.get('/')
def home(): return render_template('index.html',role='admin')
@app.get('/<role>')
def role(role): return render_template('index.html',role=role if role in ('admin','officer','collector') else 'admin')
@app.get('/api/state')
def api_state(): return jsonify(state())
@app.get('/api/setup')
def api_setup(): return jsonify(pilots=many('SELECT * FROM pilots ORDER BY pilot_id'),scenarios=SCENARIOS,selected_scenarios=setting('scenarios',list(SCENARIOS)),flight_count=one('SELECT COUNT(*) AS n FROM flights')['n'])
@app.post('/api/setup')
def api_setup_save():
 p=request.get_json() or {}; pilots=p.get('pilots',[]); scenarios=[x for x in p.get('scenarios',[]) if x in SCENARIOS]; session=p.get('session_id','Sesija 1')
 if not scenarios:return jsonify(error='Select at least one scenario'),400
 selected=[]
 with LOCK,db() as c:
  for item in pilots:
   pid=item.get('pilot_id',''); name=str(item.get('name','')).strip(); active=1 if item.get('active') else 0
   if pid.startswith('PILOT-'): c.execute('UPDATE pilots SET name=?,active=?,ready=0 WHERE pilot_id=?',(name,active,pid)); selected += [pid] if active else []
 set_setting('scenarios',scenarios); count=generate_flights(selected,scenarios,session)
 event('setup_saved',{'pilots':selected,'scenarios':scenarios,'session_id':session,'count':count}); return jsonify(ok=True,count=count)
@app.get('/api/flights')
def api_flights():
 q='SELECT * FROM flights WHERE 1=1'; a=[]
 for k in ('status','pilot_id','session_id','scenario'):
  if request.args.get(k): q+=' AND '+k+'=?'; a.append(request.args[k])
 q+=' ORDER BY id'; return jsonify(many(q,tuple(a)))
@app.post('/api/flights/<int:fid>/assign')
def api_assign_flight(fid):
 p=request.get_json() or {}; fields={k:p.get(k,'') for k in ('pilot_id','battery_id','scenario')}; f=one('SELECT * FROM flights WHERE id=?',(fid,))
 if not f:return jsonify(error='not found'),404
 if fields['scenario'] not in SCENARIOS:return jsonify(error='invalid scenario'),400
 fields.update(mode=p.get('mode') or f['mode'] or 'CALM',weather=p.get('weather') or f['weather'] or 'W1',repetition=p.get('repetition') or f['repetition'] or 'REP-01',date=today())
 update(fid,fields); return jsonify(ok=True,flight=one('SELECT * FROM flights WHERE id=?',(fid,)))
@app.post('/api/pilots/<pilot_id>/ready')
def api_ready(pilot_id):
 ready=1 if (request.get_json() or {}).get('ready',True) else 0
 with LOCK,db() as c:c.execute('UPDATE pilots SET ready=? WHERE pilot_id=?',(ready,pilot_id)); c.execute("UPDATE flights SET status=CASE WHEN status='NAN' AND ?=1 THEN 'WAITING' WHEN status='WAITING' AND ?=0 THEN 'NAN' ELSE status END,updated_at=? WHERE pilot_id=?",(ready,ready,ts(),pilot_id))
 event('pilot_ready',{'pilot_id':pilot_id,'ready':ready}); return jsonify(ok=True)
@app.post('/api/pilots/<pilot_id>/tag')
def api_tag(pilot_id):
 tag=(request.get_json() or {}).get('tag');
 if tag not in ('REGULAR','SDCARD'):return jsonify(error='invalid tag'),400
 with LOCK,db() as c:c.execute('UPDATE pilots SET tag=? WHERE pilot_id=?',(tag,pilot_id)); c.execute('UPDATE flights SET pilot_tag=?,updated_at=? WHERE pilot_id=?',(tag,ts(),pilot_id))
 event('pilot_tag',{'pilot_id':pilot_id,'tag':tag}); return jsonify(ok=True)
@app.post('/api/flights/<int:fid>/status')
def api_status(fid):
 st=(request.get_json() or {}).get('status');
 if st not in STATUSES:return jsonify(error='invalid status'),400
 f=one('SELECT * FROM flights WHERE id=?',(fid,));
 if not f:return jsonify(error='not found'),404
 fields={'status':st}
 if st=='FLYING':fields['started_at']=ts()
 if st=='NEED_TRANSFER':fields['landed_at']=ts(); fields['video_due']=1
 update(fid,fields); return jsonify(ok=True,flight=one('SELECT * FROM flights WHERE id=?',(fid,)))
@app.post('/api/flights/<int:fid>/intake')
def api_intake(fid):
 f=one('SELECT * FROM flights WHERE id=?',(fid,));
 if not f:return jsonify(error='not found'),404
 p=request.form; device=p.get('device','LAPTOP-1'); raw=save_upload(request.files.get('raw_file'),f,'raw',device); vid=save_upload(request.files.get('video_file'),f,'video',device); fields={'source_device':device,'status':'TRANSFERRING'}
 if raw:fields.update(raw_path=raw,raw_ok='TAIP')
 if vid:fields.update(video_path=vid,video_ok='TAIP',video_due=0)
 if (raw or f['raw_ok']=='TAIP') and (vid or f['video_ok']=='TAIP'):fields.update(status='DONE')
 update(fid,fields); return jsonify(ok=True,flight=one('SELECT * FROM flights WHERE id=?',(fid,)))
@app.post('/api/telemetry/assign')
def assign(): return api_intake(int(request.form['flight_id']))
@app.get('/api/betaflight/ports')
def betaflight_ports(): return jsonify(ports=bf_ports())
@app.post('/api/betaflight/identify')
def betaflight_identify():
 device=(request.get_json() or {}).get('device','')
 if not device:return jsonify(error='Select a Betaflight USB port'),400
 try:return jsonify(ok=True,device=bf_identify(device))
 except Exception as exc:return jsonify(error=str(exc)),400
@app.post('/api/betaflight/download/<int:fid>')
def betaflight_download(fid):
 f=one('SELECT * FROM flights WHERE id=?',(fid,)); device=(request.get_json() or {}).get('device','')
 if not f:return jsonify(error='flight not found'),404
 if not device:return jsonify(error='Select a Betaflight USB port'),400
 tmp=PART/(uuid.uuid4().hex+'.bbl.part'); final=RAW/safe(f"{f['flight_code']}_{f['pilot_id']}_{f['scenario']}_{f['mode']}_{f['weather']}_{f['repetition']}.bbl")
 update(fid,{'status':'TRANSFERRING','source_device':device})
 try:
  result=bf_download(device,tmp)
  if result.get('bytes_written',0)<=0:raise RuntimeError('No Blackbox bytes were downloaded')
  shutil.move(tmp,final); update(fid,{'raw_path':str(final.relative_to(DATA)),'raw_ok':'TAIP','source_device':device,'status':'DONE' if f['video_ok']=='TAIP' else 'TRANSFERRING'})
  return jsonify(ok=True,flight=one('SELECT * FROM flights WHERE id=?',(fid,)),download=result)
 except Exception as exc:
  tmp.unlink(missing_ok=True); update(fid,{'status':'NEED_TRANSFER'}); return jsonify(error=str(exc)),400
@app.get('/api/sync/events')
def sync_events():
 with db() as c: rows=[dict(x) for x in c.execute('SELECT * FROM sync_events WHERE id>? ORDER BY id LIMIT 500',(int(request.args.get('after','0')),)).fetchall()]
 for x in rows:x['payload']=json.loads(x['payload'])
 return jsonify(events=rows)
@app.post('/api/sync/push')
def sync_push():
 n=0
 for e in (request.get_json() or {}).get('events',[]):
  p=e.get('payload',{}); p=json.loads(p) if isinstance(p,str) else p
  if e.get('kind')=='flight_update':update(int(p['id']),p);n+=1
  elif e.get('kind')=='pilot_tag':
   with LOCK,db() as c:c.execute('UPDATE pilots SET tag=? WHERE pilot_id=?',(p['tag'],p['pilot_id']));n+=1
 return jsonify(ok=True,applied=n)
@app.get('/export/flights.csv')
def export():
 rows=many('SELECT * FROM flights ORDER BY id'); out=io.StringIO(); w=csv.DictWriter(out,fieldnames=rows[0].keys() if rows else ['flight_code']);w.writeheader();w.writerows(rows);return app.response_class(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=skrydziai.csv'})
@app.get('/files/<path:name>')
def files(name):return send_from_directory(DATA,name,as_attachment=True)
init()
if __name__=='__main__':app.run(host=os.getenv('FPV_HOST','0.0.0.0'),port=int(os.getenv('FPV_PORT','5050')),debug=False)
