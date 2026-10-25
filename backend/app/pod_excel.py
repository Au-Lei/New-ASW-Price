"""Local POD / 20' / 40' quotation sheets, including workbook port dictionaries."""
import re,sqlite3
from uuid import uuid4
from .excel_format import read_xlsx,column
from .quote_enrichment import validity_range,equal_40,reference_ports,enrich_port
from .quotation import Draft,validation,decorate,dictionaries
from .ai_extract import number

class NotPOD(ValueError):pass

def header_row(sheet):
 c=sheet['cells']
 for r in range(1,21):
  a,b,d=[c.get(f'{column(i)}{r}','').strip().upper().replace('’',"'").replace('′',"'") for i in (1,2,3)]
  if a in ('POD','DESTINATION','目的港') and b in ('20',"20'",'20GP') and d in ('40',"40'",'40GP','40HQ','40HC','40GP/HQ'):return r
 return None

def can_parse(sheets):return any(header_row(s) is not None for s in sheets)

def parse_pod(db,content,max_file):
 sheets=read_xlsx(content,max_file)
 if not can_parse(sheets):raise NotPOD()
 db.row_factory=sqlite3.Row;lookup=dictionaries(db);reference=reference_ports(sheets);rows=[]
 default=db.execute("SELECT value FROM application_meta WHERE key='default_origin_port_id'").fetchone()[0]
 for sheet in sheets:
  header=header_row(sheet)
  if header is None:continue
  c=sheet['cells'];title=' '.join(v for a,v in c.items() if int(re.search(r'\d+',a)[0])<header);start,end=validity_range(title)
  box40='40HQ' if c.get(f'C{header}','').strip().upper() in ('40HQ','40HC') else '40GP'
  price_columns=[('20GP','B'),(box40,'C')]
  fourth=c.get(f'D{header}','').strip().upper()
  if fourth in ('40GP','40HQ','40HC') and ('40HQ' if fourth=='40HC' else fourth)!=box40:price_columns.append(('40HQ' if fourth=='40HC' else fourth,'D'))
  origin='青岛' if re.search(r'青[岛島]|QINGDAO',sheet['name'],re.I) else ''
  date_cells=[a for a,v in c.items() if int(re.search(r'\d+',a)[0])<header and validity_range(v)==(start,end) and start]
  for r in sorted({int(re.search(r'\d+',a)[0]) for a in c}):
   text=c.get(f'A{r}','').strip();v20=c.get(f'B{r}','').strip();v40=c.get(f'C{r}','').strip()
   if r<=header or not text or not any(c.get(f'{col}{r}','').strip() for _,col in price_columns):continue
   notes=[];codes=re.findall(r'(?<![A-Z0-9])[A-Z]{2}[A-Z0-9]{3}(?![A-Z0-9])',text.upper());special_origin=''
   if re.match(r'日照[-－]',text):
    codes=[code for alias,code in [('宿霧','PHCEB'),('達沃','PHDVA'),('巴達燕','PHBTG'),('蘇比克','PHZUB'),('將軍港','PHGTC'),('卡嘉顏','PHKGY')] if alias in text]
    special_origin='日照';notes.append('此行为日照起运，已与青岛报价分开；中文多港口按文件对照表展开，须核对')
   codes=list(dict.fromkeys(codes or [text]))
   if len(codes)>1:notes.append(f'原文一行{len(codes)}个港口，按同一箱价展开；重叠代码不去重覆盖')
   base={};blocked=[]
   for box,col in price_columns:
    address=f'{col}{r}';value=c.get(address,'').strip()
    if address in sheet['formulas']:notes.append(f'{address}为公式，未采信缓存价格');continue
    parsed=number(value) if value else ''
    if parsed:base[box]=parsed
    elif value:notes.append(f'{address}价格无法解析，保留原文待核对');blocked.append(box)
   prices,copied=equal_40(base)
   for box in blocked:prices.pop(box,None)
   notes+=copied
   if c.get(f'C{header}','').strip() in ('40',"40'",'40GP/HQ'):notes.append('原表40尺共价，按用户同价规则同时用于40GP和40HQ')
   for code in codes:
    enriched=enrich_port(db,code,reference);all_notes=[*notes,*enriched['notes']]
    data=Draft(origin_port_id='' if special_origin else default,destination_port_id=enriched['port_id'],route_id=enriched['route_id'],prices=prices,valid_from=start,valid_to=end,validity_original=title,manual_note=f'本机读取：{sheet["name"]} A{r}:D{r}；有效期来自共享表头').model_dump()
    if not origin and not special_origin:all_notes.append('原表起运港未明确；默认青岛待人工确认')
    if not start:all_notes.append('共享表头未能唯一解析完整有效期')
    if special_origin:enriched['origin']={'name_en':'RIZHAO','name_cn':'日照','country_en':'CHINA','country_cn':'中国'}
    # An isolated USD in a barge-fee footnote does not establish currency of every freight row.
    all_notes.append('原表未明确全表币种、船公司和报价来源，保留待补充')
    remark=c.get(f'D{r}','').strip() if len(price_columns)==2 else '';data['remark']='；'.join(filter(None,[(f'原港口代码：{code}' if code!=enriched['fields'].get('portEn') else ''),('起运港：日照（非青岛）' if special_origin else ''),remark]))
    display=decorate(data,lookup)
    if not enriched['port_id']:display['destination']={**display['destination'],'name_en':enriched['fields'].get('portEn',''),'name_cn':enriched['fields'].get('portCn',''),'country':{'name_en':enriched['fields'].get('countryEn',''),'name_cn':enriched['fields'].get('countryCn','')}}
    if not enriched['route_id']:display['route']={**display['route'],'name':enriched['fields'].get('line','')}
    sources=[{'id':'row','worksheet':sheet['name'],'cell_range':f'A{r}:D{r}','page':None,'region':None,'excerpt':' | '.join(c.get(f'{column(i)}{r}','') for i in range(1,5))}]
    sources.append({'id':'price-header','worksheet':sheet['name'],'cell_range':f'A{header}:D{header}','page':None,'region':None,'excerpt':' | '.join(c.get(f'{column(i)}{header}','') for i in range(1,5))})
    for i,a in enumerate(date_cells):sources.append({'id':f'validity-{i}','worksheet':sheet['name'],'cell_range':a,'page':None,'region':None,'excerpt':c[a]})
    ref=enriched.get('reference',{})
    if ref.get('worksheet'):sources.append({'id':'port-reference','worksheet':ref['worksheet'],'cell_range':ref['cell_range'],'page':None,'region':None,'excerpt':' | '.join(str(ref.get(k,'')) for k in ('name_en','name_cn','country_cn'))})
    raw={'kind':'structured_excel','original_cells':{a:c.get(a,'') for a in [f'A{r}',f'B{r}',f'C{r}',f'D{r}']},'original_port_code':code,'validity_original':title,'enrichment':enriched,'sources':sources,'notes':all_notes}
    issues,_=validation(db,data);rows.append({'id':uuid4().hex,'data':data,'display':display,'raw':raw,'sources':sources,'issues':issues,'mapping_notes':all_notes,'importable':True})
 if not rows:raise ValueError('POD表内没有可提取的报价行')
 return {'kind':'structured_excel','provider':'本机解析','model':'local-pod-parser','prompt_version':'local-pod-1','rows':rows,'notes':['港口信息优先采用同文件对照表；40GP／40HQ按用户确认的同价规则补齐，原价格保留在原文中'],'calls':[],'counts':{'total':len(rows),'importable':len(rows),'failed':0,'pending':sum(bool(r['issues']) for r in rows)}}
