from __future__ import annotations
import json, os, sqlite3, time, urllib.request
from pathlib import Path

LOCAL=os.environ.get('FPV_LOCAL_URL','http://127.0.0.1:5050')
PEER=os.environ.get('FPV_PEER_URL','http://192.168.1.10:5050')
STATE=Path(os.environ.get('FPV_SYNC_STATE','data/.sync_cursor'))

def get(url):
 with urllib.request.urlopen(url,timeout=8) as r:return json.load(r)
def post(url,obj):
 b=json.dumps(obj).encode(); req=urllib.request.Request(url,data=b,headers={'Content-Type':'application/json'},method='POST')
 with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
def cursor(): return int(STATE.read_text()) if STATE.exists() else 0
def main():
 cur=cursor(); incoming=get(PEER+'/api/sync/events?after='+str(cur))['events']
 if incoming:
  print('Applying',len(incoming),'events from peer')
  post(LOCAL+'/api/sync/push',{'events':incoming}); STATE.write_text(str(max(x['id'] for x in incoming)))
 outgoing=get(LOCAL+'/api/sync/events?after=0')['events']
 if outgoing:
  print('Sending',len(outgoing),'events to peer')
  post(PEER+'/api/sync/push',{'events':outgoing})
 print('sync ok')
if __name__=='__main__':main()
