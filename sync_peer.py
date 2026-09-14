from __future__ import annotations
import json, os, sqlite3, time, urllib.request
from pathlib import Path

LOCAL=os.environ.get('FPV_LOCAL_URL','http://127.0.0.1:5050')
PEER=os.environ.get('FPV_PEER_URL','http://192.168.1.10:5050')
STATE=Path(os.environ.get('FPV_SYNC_STATE','data/.sync_cursor'))

def get(url):
 try:
  with urllib.request.urlopen(url,timeout=8) as r:return json.load(r)
 except Exception as exc:
  raise RuntimeError(f'Cannot reach {url}. Check that app.py is running, port 5050 is allowed, and both laptops are on the same network. Original error: {exc}') from exc
def post(url,obj):
 b=json.dumps(obj).encode(); req=urllib.request.Request(url,data=b,headers={'Content-Type':'application/json'},method='POST')
 try:
  with urllib.request.urlopen(req,timeout=15) as r:return json.load(r)
 except Exception as exc:
  raise RuntimeError(f'Cannot send to {url}. Check that app.py is running and the network allows port 5050. Original error: {exc}') from exc
def cursor(): return int(STATE.read_text()) if STATE.exists() else 0
def main():
 global LOCAL, PEER
 if not LOCAL.startswith(('http://','https://')): LOCAL='http://'+LOCAL
 if not PEER.startswith(('http://','https://')): PEER='http://'+PEER
 print('Local:',LOCAL)
 print('Peer: ',PEER)
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
