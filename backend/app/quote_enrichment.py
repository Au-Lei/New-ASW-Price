"""Source-backed port enrichment and explicit business rules, independent of AI."""
from datetime import date
import re,unicodedata
from .quotation import dictionaries

def key(text):return re.sub(r'[^A-Z0-9\u3400-\u9fff]','',unicodedata.normalize('NFKC',str(text)).upper())
def validity_range(text):
 pattern=r'(?<!\d)(\d{4})[./年-](\d{1,2})[./月-](\d{1,2})日?\s*[~～至到—–-]\s*(?:(\d{4})[./年-])?(\d{1,2})[./月-](\d{1,2})日?(?!\d)'
 values=set()
 for m in re.finditer(pattern,text):
  y,month,day,ey,em,ed=m.groups();y,month,day,em,ed=map(int,(y,month,day,em,ed));endyear=int(ey) if ey else y+(em<month)
  try:
   start,end=date(y,month,day),date(endyear,em,ed)
   if start<=end:values.add((start.isoformat(),end.isoformat()))
  except ValueError:pass
 return next(iter(values)) if len(values)==1 else ('','')

def equal_40(prices):
 result=dict(prices);notes=[]
 for a,b in [('40GP','40HQ'),('40HQ','40GP')]:
  if result.get(a) not in (None,'') and result.get(b) in (None,''):
   result[b]=result[a];notes.append(f'{b}未单列，按已确认的40GP／40HQ同价规则由{a}补齐；请核对')
 return result,notes

COUNTRIES={'HK':('CHINA','中国','中国港台航线'),'MO':('CHINA','中国','中国港台航线'),'TW':('CHINA','中国','中国港台航线'),'SG':('SINGAPORE','新加坡','新马航线'),'MY':('MALAYSIA','马来西亚','新马航线'),'BN':('BRUNEI','文莱','文莱航线'),'ID':('INDONESIA','印度尼西亚','印尼航线'),'TH':('THAILAND','泰国','泰国航线'),'VN':('VIETNAM','越南','越南航线'),'KH':('CAMBODIA','柬埔寨','柬埔寨航线'),'PH':('PHILIPPINES','菲律宾','菲律宾航线'),'MM':('MYANMAR','缅甸','缅甸航线'),'JP':('JAPAN','日本','日韩航线')}
COUNTRY_NAMES={key(cn):(en,cn,route) for en,cn,route in COUNTRIES.values()}
COUNTRY_NAMES.update({key(n):COUNTRIES[c] for n,c in [('中国香港','HK'),('中国澳门','MO'),('中国台湾','TW')]})
EQUIVALENTS={key(a):key(b) for a,b in [('PORT KLANG WEST PORT','PORT KELANG(W)'),('PORT KLANG NORTH PORT','PORT KELANG(N)'),('TANJUNG PELEPAS, MALAYSIA','TANJONG PELEPAS'),('PASIR GUDANG, MALAYSIA','PASIR GUDANG'),('HAIPHONG, VIETNAM','HAIPHONG'),('MANILA NORTH PORT','MANILA N'),('MANILA SOUTH PORT','MANILA S'),('BATAM','BATAM ISLAND'),('SUBIC BAY','SUBIC'),('CAGAYAN DE ORO','CAGAYAN'),('GENERAL SANTOS CITY','GENERAL SANTOS'),('CIKARANG DRY PORT','CIKARANG')]}
# This one additional mapping is verified against the port operator, not guessed from the code.
VERIFIED={'IDJBK':{'name_en':'CIKARANG DRY PORT','name_cn':'西卡朗','country_cn':'印度尼西亚','source_url':'https://cikarangdryport.com/id/direct-multimodal-export-import/'}}


VERIFIED.update({
 'THSGZ':{'name_en':'SONGKHLA','name_cn':'宋卡','country_cn':'泰国','source_url':'https://www.cnc-line.com/ebusiness/schedules/port/details?ActualPOLDescription=SONGKHLA+PORT+%3B+TH+%3B+THSGZ&POLDescription=SONGKHLA+PORT+%3B+TH+%3B+THSGZ'},
 'IDMKR':{'name_en':'MAKASSAR','name_cn':'锡江','country_cn':'印度尼西亚','source_url':'https://www.evergreen-shipping.us/servlet/LoginPage2?FmcIndex=EGLV-603&RuleIndex=010-O10&jspname=JspFmcRevision'},
})

def reference_ports(sheets):
 entries={}
 for sheet in sheets:
  cells=sheet['cells'];header=None
  for r in range(1,21):
   if cells.get(f'A{r}','').strip()=='港口代码' and '英文' in cells.get(f'B{r}','') and '中文' in cells.get(f'C{r}',''):header=r;break
  if not header:continue
  rows=sorted({int(re.search(r'\d+',a)[0]) for a in cells})
  for r in rows:
   code=cells.get(f'A{r}','').strip().upper()
   if r<=header or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}',code):continue
   value={'name_en':cells.get(f'B{r}','').strip(),'name_cn':cells.get(f'C{r}','').strip(),'country_cn':cells.get(f'D{r}','').strip(),'worksheet':sheet['name'],'cell_range':f'A{r}:E{r}'}
   if code in entries and entries[code]!=value:
    if any(entries[code].get(k)!=value.get(k) for k in ('name_en','name_cn','country_cn')):entries[code]={'ambiguous':True}
   else:entries[code]=value
 return entries

def canonical_country(db,text):
 value=key(text)
 if not value:return ''
 countries=[dict(r) for r in db.execute('SELECT * FROM countries WHERE active=1')]
 exact=[c for c in countries if value in (key(c['name_en']),key(c['name_cn']),key(c['name_cn']+c['name_en']),key(c['name_en']+c['name_cn']))]
 if len(exact)==1:return exact[0]['name_en']
 return text


def port_candidates(db,text,country='',terminal=''):
 """Resolve bilingual spelling and carrier suffixes against existing ports only."""
 country=canonical_country(db,country);lookup=dictionaries(db);value=key(text);matches=[]
 extras={'INCHON':'INCHEON','PYSONGTAEK':'PYEONGTAEK','SUBIBAY':'SUBIC','MANILASOUTH':'MANILAS','MANILANORTH':'MANILAN','PORTKLANGW':'PORTKELANGW','PORTKLANGN':'PORTKELANGN','KWANGYANG':'GWANGYANG','CHULAI':'CHULAI','DANANG':'DANANG'}
 for old,new in extras.items():value=value.replace(old,new)
 value=extras.get(value,value)
 aliases=[dict(a) for a in db.execute('SELECT * FROM port_aliases WHERE active=1 AND confirmed=1')]
 for port in lookup['ports'].values():
  c=lookup['countries'].get(port['country_id'],{})
  if not port['active'] or not c.get('active'):continue
  if country and key(country) not in (key(c['name_en']),key(c['name_cn'])):continue
  if terminal and key(terminal)!=key(port['terminal']):continue
  en,cn=key(port['name_en']),key(port['name_cn']);names={en,cn,key(port['port_code'] or ''),en+cn,cn+en}
  names|={key(a['name']) for a in aliases if a['port_id']==port['id']}
  if value in names or any(value==n+suffix for n in names if n for suffix in ('CMV2','CKV2','CBX2')):matches.append(port)
 return matches


def supplemental_entry(text):
 value=key(re.sub(r'[（(][^）)]*中转[^）)]*[）)]','',text))
 return next((v for k,v in SUPPLEMENTAL.items() if value in (key(k),key(v['name_en']),key(v['name_cn']))),{})


def expand_destination(db,text,country='',terminal=''):
 direct=port_candidates(db,text,country,terminal)
 if len(direct)==1:return [direct[0]['name_en']]
 pieces=[p.strip() for p in re.split(r'[/／、;；]',text) if p.strip()]
 if len(pieces)>1:
  resolved=[port_candidates(db,p,country,terminal) for p in pieces]
  if any(len(m)==1 or supplemental_entry(piece) for m,piece in zip(resolved,pieces)):
   return list(dict.fromkeys(m[0]['name_en'] if len(m)==1 else supplemental_entry(piece).get('name_en',piece) for m,piece in zip(resolved,pieces)))
 return [text]


SUPPLEMENTAL={
 'ILOILO':{'name_en':'ILOILO','name_cn':'伊洛伊洛','country_cn':'菲律宾','source_url':'https://directory.ppa.net.ph/'},
 'YANGON (AWPT)':{'name_en':'YANGON (AWPT)','name_cn':'仰光（AWPT）','country_cn':'缅甸','source_url':'https://www.mpa.gov.mm/my/ports/asia-world-port-terminal-2/'},
 'YANGON (MITT)':{'name_en':'YANGON (MITT)','name_cn':'仰光（MITT）','country_cn':'缅甸','source_url':'https://www.mpa.gov.mm/'},
 'CHATTOGRAM':{'name_en':'CHATTOGRAM','name_cn':'吉大港','country_cn':'孟加拉','source_url':'https://cpa.gov.bd/'},
}
SUPPLEMENTAL.update({
 'HACHINOHE':{'name_en':'HACHINOHE','name_cn':'八户','country_cn':'日本','source_url':'https://www.mlit.go.jp/kankocho/cruise/detail/077/index.html'},
 'KAMAISHI':{'name_en':'KAMAISHI','name_cn':'釜石','country_cn':'日本','source_url':'https://www.pa.thr.mlit.go.jp/tohoku_cruise/en/010/020/030/20200101182000.html'},
 '桂武港':{'name_en':'QUE VO','name_cn':'桂武港','country_cn':'越南','source_url':'https://saigonnewport.com.vn/cang-cong-ty/tan-cang-que-vo-icd'},
 'QUE VO':{'name_en':'QUE VO','name_cn':'桂武港','country_cn':'越南','source_url':'https://saigonnewport.com.vn/cang-cong-ty/tan-cang-que-vo-icd'},
 '莎哈太':{'name_en':'SAHATHAI TERMINAL','name_cn':'莎哈太','country_cn':'泰国','source_url':'https://sahathaiterminal.com/th/contact/'},
 '胡志明(PHUC AN) CMV2':{'name_en':'HO CHI MINH (PHUC AN)','name_cn':'胡志明（PHUC AN，码头拼写待核对）','country_cn':'越南','unconfirmed':True,'source_url':'https://www.phuocanport.com/en/'},
})
SUPPLEMENTAL.update({
 'CHORNOMORSK':{'name_en':'CHORNOMORSK','name_cn':'切尔诺莫尔斯克','country_cn':'乌克兰','route':'欧洲航线','source_url':'https://cpsp.com.ua/'},
 'CONSTANTA':{'name_en':'CONSTANTA','name_cn':'康斯坦察','country_cn':'罗马尼亚','route':'欧洲航线','source_url':'https://www.portofconstantza.com/'},
 'VARNA':{'name_en':'VARNA','name_cn':'瓦尔纳','country_cn':'保加利亚','route':'欧洲航线','source_url':'https://www.mtc.government.bg/en/category/12/presentation-bulgarian-ports-and-airports-which-ministry-transport-information-technologies-and-communications-will-offer-concession'},
 'BURGAS':{'name_en':'BURGAS','name_cn':'布尔加斯','country_cn':'保加利亚','route':'欧洲航线','source_url':'https://port-burgas.bg/'},
 'POTI':{'name_en':'POTI','name_cn':'波季','country_cn':'格鲁吉亚','route':'欧洲航线','source_url':'https://www.apmterminals.com/en/poti'},
})
SUPPLEMENTAL['DERINCE (SAFI)']={'name_en':'DERINCE (SAFI)','name_cn':'代林杰（SAFI码头）','country_cn':'土耳其','route':'地东港口','source_url':'https://www.safiport.com.tr/'}
for code,info in [('UA',('UKRAINE','乌克兰','欧洲航线')),('RO',('ROMANIA','罗马尼亚','欧洲航线')),('BG',('BULGARIA','保加利亚','欧洲航线')),('GE',('GEORGIA','格鲁吉亚','欧洲航线'))]:COUNTRIES[code]=info
COUNTRIES['BD']=('BANGLADESH','孟加拉','孟加拉航线')
COUNTRY_NAMES.update({key(n):v for v in COUNTRIES.values() for n in v[:2]})

def enrich_port(db,text,reference=None,country='',terminal=''):
 country=canonical_country(db,country)
 candidates=port_candidates(db,text,country,terminal)
 if len(candidates)==1:text=candidates[0]['name_en']
 lookup=dictionaries(db);reference=reference or {};code=text.strip().upper();entry=reference.get(code) or VERIFIED.get(code) or SUPPLEMENTAL.get(code) or supplemental_entry(text);notes=[]
 if not entry:
  try:
   cached=db.execute('SELECT metadata_json FROM port_metadata_cache WHERE name_key=?',(key(text),)).fetchone()
   if cached:
    import json
    entry=json.loads(cached[0])
  except __import__('sqlite3').OperationalError:pass
 if entry.get('ambiguous'):return {'port_id':'','route_id':'','fields':{'portEn':code},'notes':['文件中此代码有多个港口定义，须人工确认'],'port_code':code,'status':'unresolved'}
 en=entry.get('name_en','');cn=entry.get('name_cn','');country_info=COUNTRY_NAMES.get(key(entry.get('country_cn','')))
 if not country_info and entry.get('country_cn'):
  known=[c for c in lookup['countries'].values() if key(c['name_cn'])==key(entry['country_cn'])]
  if len(known)==1:country_info=(known[0]['name_en'],known[0]['name_cn'],entry.get('route',''))
 if not country_info and country:
  matched=[c for c in lookup['countries'].values() if key(country) in (key(c['name_en']),key(c['name_cn']))]
  if len(matched)==1:
   c=matched[0];route_name=next((r for en,cn,r in COUNTRIES.values() if key(en)==key(c['name_en'])),'')
   country_info=(c['name_en'],c['name_cn'],route_name)
 known_name=any(key(text) in (key(p['name_en']),key(p['name_cn'])) for p in lookup['ports'].values())
 if not country_info and not known_name and re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}',code):country_info=COUNTRIES.get(code[:2])
 names={key(v) for v in (text,en,cn) if v};names|={EQUIVALENTS[n] for n in list(names) if n in EQUIVALENTS}
 aliases=[dict(r) for r in db.execute('SELECT * FROM port_aliases WHERE active=1 AND confirmed=1')]
 matches=[]
 for p in lookup['ports'].values():
  c=lookup['countries'].get(p['country_id'],{})
  if not p['active'] or not c.get('active'):continue
  candidates={key(v) for v in (p['name_en'],p['name_cn'],p.get('port_code') or '') if v}|{key(a['name']) for a in aliases if a['port_id']==p['id']}
  if not names&candidates:continue
  if country and key(country) not in {key(c['name_en']),key(c['name_cn']),key(entry.get('country_cn',''))}:continue
  if country_info and key(c['name_en'])!=key(country_info[0]):continue
  if terminal and key(terminal)!=key(p['terminal']):continue
  matches.append(p)
 selected=matches[0] if len(matches)==1 else None
 if selected:
  en,cn=selected['name_en'],selected['name_cn'];c=lookup['countries'][selected['country_id']];cen,ccn=c['name_en'],c['name_cn']
  routes=[lookup['routes'][r[0]] for r in db.execute('SELECT route_id FROM port_routes WHERE port_id=?',(selected['id'],)) if lookup['routes'].get(r[0],{}).get('active')]
  route=routes[0] if len(routes)==1 else None;route_name=route['name'] if route else ''
  status='catalog';notes.append('国家、港口中文名和航线按基础资料补齐')
 else:
  cen,ccn,route_name=country_info or ('',entry.get('country_cn',''),'');route=next((r for r in lookup['routes'].values() if r['active'] and r['name']==route_name),None)
  if entry.get('country_en') and entry.get('country_cn'):cen,ccn=entry['country_en'].upper(),entry['country_cn']
  route_name=entry.get('route') or route_name
  route=next((r for r in lookup['routes'].values() if r['active'] and r['name']==route_name),None)
  status='source_reference' if en and cn and len(matches)<2 and not entry.get('unconfirmed') else 'unresolved'
  notes.append(('按AI公开地理信息候选补全，提交前请核对' if entry.get('ai_candidate') else '按公开港口资料补全，来源链接已保留' if entry.get('source_url') else '按文件港口对照表补全；航线按现有国家分类，提交前可修改') if status=='source_reference' else '港口名称或代码未能唯一核实，保留原文待确认，不自动猜港名')
  if not en:en=code
 if entry.get('ai_candidate'):notes.append('国家和港口译名为AI公开地理信息候选，已缓存，提交前请核对')
 return {'port_id':selected['id'] if selected else '', 'route_id':route['id'] if route else '', 'port_code':code if not known_name and re.fullmatch(r'[A-Z]{2}[A-Z0-9]{3}',code) else '', 'fields':{'portEn':en,'portCn':cn,'countryEn':cen,'countryCn':ccn,'line':route_name},'status':status,'notes':notes,'reference':entry}
