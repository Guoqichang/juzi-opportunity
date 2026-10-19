import json,subprocess
from pathlib import Path
from compute_policy.base_sync import SCHEMAS
p=Path('deploy/base-config.json')
cfg=json.loads(p.read_text())
base=cfg['base_token']
def call(cmd,*args):
 r=subprocess.run(['lark-cli','base','+'+cmd,'--base-token',base,'--as','user','--format','json',*args],capture_output=True,text=True,timeout=60)
 if r.returncode: raise RuntimeError(cmd+': '+r.stderr[:300])
 d=json.loads(r.stdout)
 if not d.get('ok'): raise RuntimeError(cmd+' failed')
 return d['data']
def field(n):
 options={'跟进状态':['待核实','值得跟进','不适合','已处理'],'关系判断':['待核对','同批次转载','同批次解读','补充更正','不同事项'],'确认状态':['未知','估算','已确认'],'资料状态':['参考','已核验','停用']}
 if n in options: return {'name':n,'type':'select','multiple':False,'options':[{'name':o} for o in options[n]]}
 return {'name':n,'type':'text',**({'style':{'type':'url'}} if n in {'原文链接','工作台链接','来源一','来源二','完整报告'} else {})}
for key,(name,_,fields,_) in SCHEMAS.items():
 if key not in cfg['tables']:
  result=call('table-create','--name',name,'--fields',json.dumps([field(n) for n in fields],ensure_ascii=False))
  print(key,json.dumps(result,ensure_ascii=False),flush=True)
  cfg['tables'][key]=result['table']['id'] if 'table' in result else result.get('table_id',result.get('id'))
  assert cfg['tables'][key]
  p.write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
 else:
  existing={f['name'] for f in call('field-list','--table-id',cfg['tables'][key])['fields']}
  for name in fields:
   if name not in existing: call('field-create','--table-id',cfg['tables'][key],'--json',json.dumps(field(name),ensure_ascii=False))
  print(key,'fields configured',flush=True)
p.write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
