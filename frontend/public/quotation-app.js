const COLS = [
  {k:'line',       t:'航线'},
  {k:'countryEn',  t:'国家英文名'},
  {k:'countryCn',  t:'国家中文名'},
  {k:'portEn',     t:'英文港口名'},
  {k:'portCn',     t:'中文港口名'},
  {k:'carrier',    t:'船公司'},
  {k:'c20',        t:'20GP',  num:true},
  {k:'c40',        t:'40GP',  num:true},
  {k:'c40hq',      t:'40HQ',  num:true},
  {k:'currency',   t:'货币'},
  {k:'sailing',    t:'船期'},
  {k:'transit',    t:'航程天数'},
  {k:'route',      t:'途径路径'},
  {k:'source',     t:'价格来源'},
  {k:'remark',     t:'备注（注意事项）'},
  {k:'validFrom',  t:'有效期起', date:true},
  {k:'validTo',    t:'有效期止', date:true},
  {k:'feeBasis',   t:'费用口径'}
];

let queryFreezeObserver;
function updateQueryFrozenOffsets(){
  const table=document.getElementById('q-table');
  let left=0;
  table.querySelectorAll('thead th').forEach((cell,index)=>{
    if(index<5){table.style.setProperty(`--q-frozen-${index}`,`${left}px`);left+=cell.getBoundingClientRect().width;}
  });
}

function renderQuery(rows, etd){
  const groups=new Map();
  rows.forEach((row,index)=>{
    const d=row._data||{};
    const key=d.destination_port_id?JSON.stringify([d.origin_port_id,d.destination_port_id,d.route_id,row.line,row.countryEn,row.countryCn,row.portEn,row.portCn]):`unknown-${index}`;
    if(!groups.has(key))groups.set(key,[]);
    groups.get(key).push(row);
  });
  rows=Array.from(groups.values()).flatMap(group=>group.map((r,i)=>({...r,_portGroupStart:i===0,_portGroupSize:group.length})));
  for(let start=0;start<rows.length;){
    const row=rows[start],data=row._data||{};let end=start+1;
    const key=JSON.stringify([data.origin_port_id,data.route_id,row.line]);
    while(row.line&&end<rows.length&&JSON.stringify([rows[end]._data?.origin_port_id,rows[end]._data?.route_id,rows[end].line])===key)end++;
    rows[start]._routeSpan=end-start;for(let i=start+1;i<end;i++)rows[i]._routeSpan=0;start=end;
  }
  const thead=document.querySelector('#q-table thead');
  const tbody=document.querySelector('#q-table tbody');
  thead.innerHTML='<tr>'+COLS.map((c,i)=>{
    let cls=i<5?`q-frozen q-frozen-${i}`:'';
    return `<th class="${cls}">${c.t}</th>`;
  }).join('')+'</tr>';

  if(rows.length===0){
    tbody.innerHTML=`<tr><td colspan="${COLS.length}"><div class="empty-state"><div class="ic">🚢</div>没有符合条件的报价</div></td></tr>`;
  } else {
    tbody.innerHTML=rows.map(r=>{
      const vs=validStatus(r);
      return '<tr>'+COLS.map((c,i)=>{
        if(i===0&&!r._routeSpan)return '';
        if(i>=1&&i<=4&&!r._portGroupStart)return '';
        let cls=i<5?`q-frozen q-frozen-${i}`:'';
        let val=r[c.k];
        let content=esc(val);
        if(c.num) content = (val!==''&&val!==null&&val!==undefined)?`<span class="price">${val}</span>`:'<span class="price-empty">—</span>';
        if(c.k==='validFrom' && validityMissing(r)) content = `<span class="tag tag-miss">有效期缺失</span>`;
        if(c.k==='validTo'){
          content = esc(val)+` <span class="tag ${vs.cls}">${vs.txt}</span>`;
        }
        return `<td class="${cls}"${i===0?` rowspan="${r._routeSpan}"`:i>=1&&i<=4?` rowspan="${r._portGroupSize}"`:""}>${content}</td>`;
      }).join('')+'</tr>';
    }).join('');
  }

  const stat=document.getElementById('q-stat');
  const states=[['','全部'],['有效','有效期内'],['即将到期','即将到期'],['已过期','已过期'],['有效期未完整','有效期缺失']];
  let statHtml=states.map(([key,label])=>`<button class="stat" aria-pressed="${queryStatus===key}" style="cursor:pointer;${queryStatus===key?'background:#dceeff;border-color:#1c70a4;':''}" onclick="filterValidity('${key}')">${label} <b>${key?(queryStatusCounts[key]||0):Object.values(queryStatusCounts).reduce((a,b)=>a+b,0)}</b></button>`).join('');
  if(user?.role==='admin')statHtml+='<button class="btn-danger" onclick="deleteExpiredQuotes()">一键删除过期报价</button>';
  if(etd){
    statHtml+=`<div class="stat" style="border-color:var(--sea2)">ETD <b>${esc(etd)}</b> · 仅校验是否在有效期内</div>`;
  }
  stat.innerHTML=statHtml;
  updateQueryFrozenOffsets();
  if(!queryFreezeObserver){queryFreezeObserver=new ResizeObserver(updateQueryFrozenOffsets);queryFreezeObserver.observe(document.getElementById('q-table'));}
}


function groupedQuoteOrder(rows,getRow=r=>r){
  const value=v=>String(v??'').normalize('NFKC').trim().toLocaleUpperCase('en');
  const keys=row=>[value(row.countryEn||row.countryCn),value(row.portEn||row.portCn),value(row.carrier)];
  return rows.map((item,index)=>({item,index,keys:keys(getRow(item))})).sort((a,b)=>{
    for(let i=0;i<a.keys.length;i++){const x=a.keys[i],y=b.keys[i];if(!x&&y)return 1;if(x&&!y)return -1;const order=x.localeCompare(y,'zh-CN',{numeric:true});if(order)return order;}
    return a.index-b.index;
  }).map(entry=>entry.item);
}

function renderEdit(){
  const thead=document.querySelector('#edit-table thead');
  const tbody=document.querySelector('#edit-table tbody');
  thead.innerHTML='<tr>'+COLS.map((c,i)=>{
    let cls=i===0?'frz1':(i===1?'frz2':'');
    if(['validFrom','validTo'].includes(c.k)){
      const values=[...new Set(editRows.map(r=>r[c.k]||''))];
      const shared=values.length===1?values[0]:'';
      return `<th class="${cls}"><label for="batch-${c.k}" style="display:block;cursor:pointer">${c.t}</label><input id="batch-${c.k}" type="date" aria-label="统一${c.t}" title="选择后覆盖本批次所有行的${c.t}" value="${esc(shared)}" style="display:block;margin-top:6px;width:145px;padding:5px;border:1px solid #b8d0e4;border-radius:5px;color:#16385a;background:white" onchange="applyBatchValidity('${c.k}',this.value)"><small style="font-weight:normal">选择后统一填入所有行</small></th>`;
    }
    return `<th class="${cls}">${c.t}</th>`;
  }).join('')+'<th>校验</th><th>操作</th></tr>';

  tbody.innerHTML=editRows.map((r,ri)=>{
    const miss=validityMissing(r);
    return `<tr class="${r._problems?.length?'quote-conflict':''}">`+COLS.map((c,i)=>{
      const cls=i===0?'frz1':(i===1?'frz2':'');
      const empty=(r[c.k]===undefined||r[c.k]===''||r[c.k]===null);
      return `<td class="${cls}">
        <input class="cell-edit ${empty?'cell-empty':''}" value="${esc(r[c.k])}" title="${esc((r._notes||[]).join('；'))}" data-r="${ri}" data-k="${c.k}" onchange="onEditChange(this)">
      ${((c.k==='c40hq'&&(r._notes||[]).some(n=>n.startsWith('40HQ未单列')))||(c.k==='c40'&&(r._notes||[]).some(n=>n.startsWith('40GP未单列'))))?'<small style="font-size:10px;color:var(--muted)">同价补齐</small>':''}</td>`;
    }).join('')+`<td>${r._problems?.length?`<span class="tag tag-miss" title="${esc(r._problems.join('；'))}">${validityMissing(r)?'有效期缺失':'报价冲突'}</span>`:''}${miss?'<span class="tag tag-miss">有效期缺失</span>':''}${r._enrichment?.status==='unresolved'?'<span class="tag tag-miss">港口待核对</span>':''}</td>`+
    `<td><button class="btn-danger" style="padding:5px 10px;font-size:12px" onclick="delRow(${ri})">移出</button></td></tr>`;
  }).join('');
}

function renderDiff(){
  const onlyDiff=document.getElementById('chk-only-diff').checked;
  const thead=document.querySelector('#diff-table thead');
  const tbody=document.querySelector('#diff-table tbody');
  thead.innerHTML='<tr><th style="min-width:90px">变更类型</th>'+COLS.map((c,i)=>{
    let cls=i===0?'frz1':(i===1?'frz2':'');
    return `<th class="${cls}">${c.t}</th>`;
  }).join('')+'</tr>';

  let rows=groupedQuoteOrder(diffRows,r=>r.row);
  if(onlyDiff) rows=rows.filter(r=>r.type!=='same');

  if(rows.length===0){
    tbody.innerHTML=`<tr><td colspan="${COLS.length+1}"><div class="empty-state"><div class="ic">✅</div>没有变更</div></td></tr>`;
    return;
  }
  const typeCls={modify:'row-modify',add:'row-add'};
  const badge={modify:'<span class="badge-diff badge-modify">修改</span>',add:'<span class="badge-diff badge-add">新增</span>'};
  tbody.innerHTML=rows.map(r=>{
    const rowCls=typeCls[r.type]||'';
    const miss=validityMissing(r.row);
    return `<tr class="${rowCls}"><td>${badge[r.type]||''}${miss?'<br><span class="tag tag-miss" style="margin-top:4px;display:inline-block">有效期缺失</span>':''}</td>`+COLS.map((c,i)=>{
      const cls=i===0?'frz1':(i===1?'frz2':'');
      let extra='';
      if(r.type==='modify' && r.changedKeys && r.changedKeys.includes(c.k)) extra='diff-cell';
      let val=r.row[c.k];
      let content=esc(val);
      if(c.num) content = (val!==''&&val!==null&&val!==undefined)?`<span class="price">${val}</span>`:'<span class="price-empty">—</span>';
      if(r.type==='modify' && extra){
        const oldVal=r.old[c.k];
        content = `<span title="原值：${esc(oldVal)}">${content}</span>`;
      }
      return `<td class="${cls} ${extra}">${content}</td>`;
    }).join('')+'</tr>';
  }).join('');
}

// The supplied demonstration is the view; every mutation below uses the authenticated backend.
let queryStatus='',queryStatusCounts={};
let updateEpoch=0;
let token='',user=null,refs={},defaults={},currentQueryRows=[],editRows=[],diffRows=[],uploadedFiles=[],activeBatch='',review=null,lastSearch=null,pollTimer=null,working=false,booted=false;
const statusCodes={new:'add',modified:'modify',unchanged:'same',duplicate:'same'};
const columnFields={line:'route_id',countryEn:'destination_port_id',countryCn:'destination_port_id',portEn:'destination_port_id',portCn:'destination_port_id',carrier:'carrier_id',c20:'prices.20GP',c40:'prices.40GP',c40hq:'prices.40HQ',currency:'currency',sailing:'sailing',transit:'transit_days',route:'path_text',source:'source_series_id',remark:'remark',validFrom:'valid_from',validTo:'valid_to',feeBasis:'fee_basis'};
const norm=v=>String(v??'').normalize('NFKC').trim().toUpperCase();
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function toast(text,type=''){const t=document.getElementById('toast');t.textContent=text;t.className='show '+type;clearTimeout(t._tm);t._tm=setTimeout(()=>t.className='',6500)}
async function request(path,data,method){const r=await fetch(path,{method:method||(data===undefined?'GET':'POST'),headers:{'X-CSRF-Token':token,...(data!==undefined?{'Content-Type':'application/json'}:{})},...(data!==undefined?{body:JSON.stringify(data)}:{})});const value=await r.json();if(r.status===401){parent.postMessage({type:'quotation-session-expired'},location.origin);throw new Error('登录已过期，请重新登录')}if(!r.ok)throw new Error(value.error?.message||'操作失败，请重试');return value}
async function action(fn){if(working)return;working=true;document.body.setAttribute('aria-busy','true');try{await fn()}catch(e){toast(e.message,'warn')}finally{working=false;document.body.removeAttribute('aria-busy')}}
function todayStr(){return new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai'}).format(new Date())}
function validityMissing(r){return !r.validFrom||!r.validTo}
function validStatus(r){const map={'有效':['tag-valid','有效'],'即将到期':['tag-soon','即将到期'],'未生效':['tag-miss','未生效'],'已过期':['tag-exp','已过期']};if(r._status&&map[r._status]){const [cls,txt]=map[r._status];return {cls,txt}}if(validityMissing(r))return {cls:'tag-miss',txt:'有效期缺失'};const t=todayStr();if(r.validFrom>t)return {cls:'tag-miss',txt:'未生效'};if(r.validTo<t)return {cls:'tag-exp',txt:'已过期'};return (new Date(r.validTo)-new Date(t))/86400000<=10?{cls:'tag-soon',txt:'即将到期'}:{cls:'tag-valid',txt:'有效'}}
function fromData(d,display={}){const port=display.destination||refs.ports.find(p=>p.id===d.destination_port_id)||{},country=port.country||refs.countries.find(c=>c.id===port.country_id)||{},carrier=display.carrier||refs.carriers.find(c=>c.id===d.carrier_id)||{},series=display.series||refs.source_series.find(s=>s.id===d.source_series_id)||{},supplier=display.supplier||refs.suppliers.find(s=>s.id===series.supplier_id)||{},route=display.route||refs.routes.find(r=>r.id===d.route_id)||{};return {line:route.name||'',countryEn:country.name_en||'',countryCn:country.name_cn||'',portEn:port.name_en||'',portCn:port.name_cn||'',carrier:carrier.name||'',c20:d.prices?.['20GP']??'',c40:d.prices?.['40GP']??'',c40hq:d.prices?.['40HQ']??'',currency:d.currency||'',sailing:d.sailing||'',transit:d.transit_days||'',route:d.path_text||'',source:[supplier.name,series.name].filter(Boolean).join(' / '),remark:d.remark||'',validFrom:d.valid_from||'',validTo:d.valid_to||'',feeBasis:d.fee_basis||'',_data:structuredClone(d)}}
async function loadReferences(){const types=['ports','countries','routes','carriers','suppliers','source_series'];const result=await Promise.all(types.map(t=>request('/api/catalog/'+t)));refs=Object.fromEntries(types.map((t,i)=>[t,result[i].items]));defaults=(await request('/api/manual-drafts/defaults')).data}
function matchPort(name,country='',other=''){if(!String(name).trim()&&!String(other).trim())return null;const names=[name,other].filter(Boolean).map(norm);const candidates=refs.ports.filter(p=>p.active&&names.every(n=>[p.name_en,p.name_cn,p.port_code,...(p.aliases||[]).filter(a=>a.active&&a.confirmed).map(a=>a.name)].filter(Boolean).map(norm).includes(n))&&(!country||[refs.countries.find(c=>c.id===p.country_id)?.name_en,refs.countries.find(c=>c.id===p.country_id)?.name_cn].map(norm).includes(norm(country))));if(candidates.length!==1)throw new Error(`港口“${name||other}”${candidates.length?'有多个候选，请补齐国家及明确港口名':'未匹配，请核对中英文名称'}`);return candidates[0]}
function resolveOrigin(name){if(!String(name).trim())return '';if(['青岛','青岛港','QINGDAO','QINGDAO PORT'].includes(norm(name)))return defaults.origin_port_id;return matchPort(name).id}
async function go(view){updateEpoch++;clearTimeout(pollTimer);document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-'+view).classList.add('active');window.scrollTo(0,0);if(view==='query')await runQuery();if(view==='update')await resetUpdate()}
function initQuery(){const select=document.getElementById('q-carrier');select.innerHTML='<option value="">全部</option>'+refs.carriers.filter(c=>c.active).map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}
async function resetQuery(){queryStatus='';document.getElementById('q-origin').value='青岛港';for(const id of ['q-dest','q-carrier','q-ctype','q-etd'])document.getElementById(id).value='';await runQuery()}
function queryFilters(){const origin=resolveOrigin(document.getElementById('q-origin').value),destText=document.getElementById('q-dest').value.trim(),dest=destText;return {validity_status:queryStatus,origin_port_id:origin,destination_text:dest,carrier_id:document.getElementById('q-carrier').value,etd:document.getElementById('q-etd').value,date_scope:document.getElementById('q-etd').value?'applicable':'all',container_type:document.getElementById('q-ctype').value,quantities:{},page:1,page_size:100};}
async function runQuery(){return action(async()=>{const query=queryFilters();let result=await request('/api/quotes/search',query),all=[...result.items];for(let page=2;all.length<result.total;page++){const next=await request('/api/quotes/search',{...query,page});if(!next.items.length)break;all.push(...next.items)}const box=document.getElementById('q-ctype').value;if(box)all=all.filter(r=>r.prices[box]!==undefined&&r.prices[box]!==null);queryStatusCounts=result.status_counts;lastSearch={...query,container_type:box};currentQueryRows=all.map(r=>({...fromData(r,r),_status:r.date_status}));renderQuery(currentQueryRows,query.etd)})}
function setStep(n){for(let i=1;i<=3;i++){document.getElementById('step'+i).classList.toggle('on',i===n);document.getElementById('step'+i).classList.toggle('done',i<n)}}
function pane(name,step){for(const p of ['upload','edit','diff'])document.getElementById('pane-'+p).style.display=p===name?'block':'none';setStep(step)}
async function resetUpdate(){updateEpoch++;clearTimeout(pollTimer);activeBatch='';review=null;removedRows=[];relatedFiles=[];editRows=[];diffRows=[];uploadedFiles=[];pane('upload',1);document.getElementById('update-tip').textContent='';document.getElementById('btn-extract').disabled=true;renderFiles();await renderRecent()}
function chooseFiles(){document.getElementById('file-input').click()}
async function uploadFiles(files){return action(async()=>{if(!files.length)return;const limits=await request('/api/uploads/limits');if(files.length>limits.max_batch_files)throw new Error(`每次最多上传${limits.max_batch_files}个文件`);if(files.some(f=>f.size>limits.max_file_bytes))throw new Error('文件超出允许大小');if(!activeBatch)activeBatch=(await request('/api/uploads/batches',{})).id;const target=activeBatch,duplicates=[];let added=0;for(const f of files){const r=await fetch(`/api/uploads/batches/${target}/files?filename=${encodeURIComponent(f.name)}`,{method:'POST',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':token},body:f});const value=await r.json();if(!r.ok)throw new Error(value.error?.message||'上传失败');if(value.duplicate)duplicates.push(value);else added++}await reloadFiles();toast(duplicates.length?'已跳过本次列表中的重复文件':'上传完成，点击“提取报价”','ok');await renderRecent()})}
async function reloadFiles(){const bid=activeBatch,epoch=updateEpoch,related=[...relatedFiles];let files=bid?(await request('/api/uploads/batches/'+bid)).files:[];for(const other of new Set(related.map(f=>f.batch_id))){const b=await request('/api/uploads/batches/'+other);if(!['draft','rejected','withdrawn'].includes(b.batch.status))continue;for(const f of b.files)if(related.some(x=>x.id===f.id)&&!files.some(x=>x.id===f.id))files.push(f)}if(bid!==activeBatch||epoch!==updateEpoch)return false;uploadedFiles=files;renderFiles();return true}

function renderFiles(){document.getElementById('filelist').innerHTML=uploadedFiles.map(f=>{const code=f.task?.error_code,status=f.task?.status;const text=code==='AI_SCOPE_REQUIRED'?'待确认AI发送':status==='processing'||status==='waiting'?taskProgressText(f.task):status==='success'?'✓ 已提取':status==='failed'?'需重试':'✓ 已上传';return `<div class="fileitem"><div class="fi-ic">${f.metadata?.kind==='xlsx'?'📊':f.metadata?.kind==='pdf'?'📄':'🖼️'}</div><div class="fi-name">${esc(f.original_name)}<div class="fi-meta">${(f.size_bytes/1024).toFixed(1)} KB</div></div><a class="fi-meta" href="/api/uploads/files/${f.id}/original" download>原件</a><span class="fi-status ${status==='processing'?'st-parsing':status==='failed'?'st-err':'st-done'}">${text}</span><button class="btn-ghost" onclick="deleteUploadedFile('${f.id}')">删除</button></div>`}).join('');document.getElementById('btn-extract').textContent=uploadedFiles.some(staleLocalResult)?'✨ 按新规则重新提取':'✨ 提取报价';document.getElementById('btn-extract').disabled=!uploadedFiles.length||uploadedFiles.some(f=>['waiting','processing'].includes(f.task?.status))}
async function allowAI(file){if(file.ai_consent)return;const ok=confirm(`“${file.original_name}”需要使用DeepSeek提取。\n仅允许虚构或已脱敏资料，真实报价暂不外发。\n确认此文件符合上述范围，并允许发送至 api.deepseek.com？`);if(!ok)throw new Error('未发送文件，可继续使用固定模板或人工录入');await request('/api/ai/files/'+file.id+'/consent',{classification:'redacted',confirmed:true});file.ai_consent=true}
function staleLocalResult(f){return f.metadata.local_parseable===true&&f.task?.result?.kind==='ai'&&f.task?.result?.model!=='local-pod-parser'&&!f.metadata.fixed_template}
async function extractQuotes(){if(uploadedFiles.some(staleLocalResult))return reextractQuotes();return action(async()=>{const epoch=updateEpoch;for(const f of [...uploadedFiles]){if(f.task?.status==='success')continue;if(f.metadata.kind!=='xlsx'||(f.metadata.local_parseable!==true&&(f.metadata.fixed_template===false||f.task?.error_code==='AI_SCOPE_REQUIRED')))await allowAI(f);const task=await request('/api/uploads/files/'+f.id+'/extract',{});if(task.status!=='success')watchExtractionTask(task.id)}refreshExtractionMonitor();if(epoch!==updateEpoch)return;await reloadFiles();document.getElementById('update-tip').textContent='正在后台提取，可返回主页；完成后会显示提醒。';pollTimer=setTimeout(pollExtraction,1000)})}

async function pollExtraction(){const epoch=updateEpoch,bid=activeBatch;const stale=()=>epoch!==updateEpoch||bid!==activeBatch;try{if(!await reloadFiles()||stale())return;if(uploadedFiles.some(f=>['waiting','processing'].includes(f.task?.status))){pollTimer=setTimeout(pollExtraction,1300);return}const scope=uploadedFiles.find(f=>f.task?.error_code==='AI_SCOPE_REQUIRED');if(scope){toast('此表需要AI解析。点击“提取报价”后确认文件已脱敏。','warn');return}const failure=uploadedFiles.find(f=>f.task?.status==='failed');if(failure)throw new Error(failure.task.error);for(const f of uploadedFiles){const r=f.task?.result,base=r?.ai_import_id?'/api/ai/imports/':r?.import_id?'/api/excel/imports/':null,id=r?.ai_import_id||r?.import_id;if(!base)continue;const preview=await request(base+id);if(stale())return;if(preview.status!=='committed'){const ids=preview.rows.filter(r=>r.importable).map(r=>r.id);if(ids.length)await request(base+id+'/commit',{row_ids:ids})}}if(stale())return;await loadEditRows();if(stale())return;if(!editRows.length)throw new Error('未识别到报价，请核对原文件');review=await request('/api/review/batches/'+bid);if(stale())return;if(review.preview.items.some(i=>i.problems.length)){showEditProblems();return}pane('edit',2);document.getElementById('update-tip').textContent=`提取完成，共${editRows.length}条，等待校对`;toast(`提取完成，共${editRows.length}条，请直接在表格中校对`,'ok')}catch(e){if(!stale())toast(e.message,'warn')}}
function editableRow(r){const row=fromData(r.data),raw=r.original_import||{};if(r.origin_kind==='excel'){for(const c of COLS)if((row[c.k]===''||row[c.k]===undefined)&&!c.num&&c.k!=='source'&&raw[c.t])row[c.k]=raw[c.t]}else if(r.origin_kind==='ai'){const facts=raw.facts||{};if(!row.portEn&&!row.portCn&&facts.destination_port)row[/[\u3400-\u9fff]/.test(facts.destination_port)?'portCn':'portEn']=facts.destination_port;const map={countryEn:'destination_country',carrier:'carrier',line:'route'};for(const [k,f] of Object.entries(map))if(!row[k]&&facts[f])row[k]=facts[f];}const geo=r.enrichment||raw.enrichment;if(geo?.fields){for(const [k,v] of Object.entries(geo.fields))if(v&&(!row[k]||['countryEn','countryCn','line'].includes(k)))row[k]=v}return {...row,_enrichment:geo,_notes:r.mapping_notes||[],_id:r.id,_revision:r.revision,_original:structuredClone(r.data),_keep:true}}
async function loadEditRows(){const bid=activeBatch,epoch=updateEpoch,fileIds=new Set(uploadedFiles.map(f=>f.id));const stale=()=>bid!==activeBatch||epoch!==updateEpoch;const rows=(await request('/api/manual-drafts')).items;if(stale())return false;for(const r of rows){if(r.batch_id!==bid&&['draft','rejected','withdrawn'].includes(r.batch_status)&&r.evidence.some(e=>fileIds.has(e.file_id))){const moved=await request('/api/review/rows/'+r.id+'/move',{batch_id:bid,revision:r.revision});if(stale())return false;r.batch_id=bid;r.revision=moved.revision}}if(stale())return false;editRows=rows.filter(r=>r.batch_id===bid).reverse().sort((a,b)=>(a.source_order??1e9)-(b.source_order??1e9)).map(editableRow);editRows=groupedQuoteOrder(editRows);renderEdit();return true}

function onEditChange(input){const row=editRows[Number(input.dataset.r)];row[input.dataset.k]=input.value;input.classList.toggle('cell-empty',!input.value);if(['validFrom','validTo'].includes(input.dataset.k))renderEdit()}
function addRow(){const blank=Object.fromEntries(COLS.map(c=>[c.k,'']));editRows.push({...blank,_data:structuredClone(defaults),_original:structuredClone(defaults),_keep:true});renderEdit()}
function delRow(i){const row=editRows[i];if(row._id)removedRows.push(row);editRows.splice(i,1);renderEdit()}
let removedRows=[],relatedFiles=[];
async function named(kind,name,extra={}){const existing=refs[kind].filter(x=>x.active&&norm(x.name)===norm(name)&&(!extra.supplier_id||x.supplier_id===extra.supplier_id));if(existing.length===1)return existing[0].id;if(!String(name).trim())return '';if(user.role!=='admin')throw new Error(`“${name}”尚未建档，请管理员补充基础资料`);const value=await request('/api/catalog/'+kind,{name,...extra});refs[kind].push(value);return value.id}
async function ensurePort(row){try{return matchPort(row.portEn,row.countryEn||row.countryCn,row.portCn)}catch(error){const geo=row._enrichment;if(!geo||geo.status==='unresolved'||user.role!=='admin')throw error;let country=refs.countries.find(c=>norm(c.name_en)===norm(row.countryEn)&&norm(c.name_cn)===norm(row.countryCn));if(!country){country=await request('/api/catalog/countries',{name_en:row.countryEn,name_cn:row.countryCn});refs.countries.push(country)}let route=refs.routes.find(r=>r.active&&norm(r.name)===norm(row.line));if(!route){const regions=(await request('/api/catalog/regions')).items;const region=regions.find(r=>r.name===(geo.reference?.region||row.line))||regions.find(r=>r.name==='亚洲航线');if(!region)throw new Error('请先维护航线区域');route=await request('/api/catalog/routes',{name:row.line,region_id:region.id});refs.routes.push(route)}const port=await request('/api/catalog/ports',{name_en:row.portEn,name_cn:row.portCn,country_id:country.id,route_ids:[route.id]});refs.ports.push(port);return port}}
async function rememberPortCode(port,geo){if(user.role!=='admin'||!geo?.port_code||geo.status==='unresolved')return;if(norm(port.port_code)===norm(geo.port_code)||(port.aliases||[]).some(a=>a.confirmed&&norm(a.name)===norm(geo.port_code)))return;const other=refs.ports.find(p=>p.id!==port.id&&(norm(p.port_code)===norm(geo.port_code)||(p.aliases||[]).some(a=>a.confirmed&&norm(a.name)===norm(geo.port_code))));if(other)throw new Error('此代码另有已确认的港口，请先核对：'+geo.port_code);const alias=await request('/api/catalog/port_aliases',{port_id:port.id,name:geo.port_code,confirmed:true});port.aliases=[...(port.aliases||[]),alias]}
async function toDraft(row){const data=structuredClone(row._data||defaults),prior=row._original||defaults;const p=await ensurePort(row);if(p)await rememberPortCode(p,row._enrichment);data.destination_port_id=p?.id||'';const routes=refs.routes.filter(r=>r.active&&(!p||p.route_ids.includes(r.id))&&norm(r.name)===norm(row.line));if(row.line&&routes.length!==1)throw new Error(`请核对 ${row.portEn||row.portCn} 的航线分类`);data.route_id=row.line?routes[0].id:'';data.carrier_id=await named('carriers',row.carrier);const originalSource=fromData(prior).source;if(row.source!==originalSource||!data.source_series_id){const pieces=row.source.split(/\s+\/\s+|\s+·\s+/);const supplierName=pieces.length>1?pieces.shift().trim():row.source.trim(),seriesName=pieces.length?pieces.join(' / ').trim():row.source.trim();if(!supplierName||!seriesName)data.source_series_id='';else{const supplier=await named('suppliers',supplierName,{kind:'agent'});data.source_series_id=await named('source_series',seriesName,{supplier_id:supplier})}}
 data.prices=Object.fromEntries([['20GP','c20'],['40GP','c40'],['40HQ','c40hq']].filter(([,k])=>row[k]!==''&&row[k]!==null&&row[k]!==undefined).map(([b,k])=>[b,String(row[k])]));for(const [key,col] of Object.entries({currency:'currency',sailing:'sailing',transit_days:'transit',path_text:'route',remark:'remark',valid_from:'validFrom',valid_to:'validTo',fee_basis:'feeBasis'}))data[key]=String(row[col]??'');if(row._enrichment?.origin){const o=row._enrichment.origin;let origin=refs.ports.find(p=>norm(p.name_cn)===norm(o.name_cn));if(!origin){const country=refs.countries.find(c=>norm(c.name_en)===norm(o.country_en));if(!country)throw new Error('请核对日照起运港');origin=await request('/api/catalog/ports',{name_en:o.name_en,name_cn:o.name_cn,country_id:country.id,route_ids:[]});refs.ports.push(origin)}data.origin_port_id=origin.id}else data.origin_port_id=data.origin_port_id||defaults.origin_port_id;data.origin_confirmed=true;data.conditions_confirmed=true;data.zero_confirmed=Object.entries(data.prices).filter(([,v])=>/^0(?:\.0{1,2})?$/.test(v.trim())).map(([k])=>k);data.manual_note=data.manual_note||'用户对照上传原件逐格校对';return data}
async function goStep3(){return action(async()=>{if(!editRows.length)throw new Error('没有待提交报价');const missing=editRows.filter(validityMissing);if(missing.length){for(const row of editRows)row._problems=validityMissing(row)?['有效期缺失，请补齐有效期起和有效期止']:[];pane('edit',2);renderEdit();const message=`有${missing.length}条报价缺失有效期，请补齐有效期起和有效期止后再提交审批。`;document.getElementById('update-tip').textContent=message;toast(message,'warn');return}if(!confirm('你确定要提交吗，有些字段仍为空。'))return;for(const row of editRows){const data=await toDraft(row);let saved;if(row._id)saved=await request(`/api/manual-drafts/${row._id}?expected_revision=${row._revision}`,data,'PUT');else{saved=await request('/api/manual-drafts',data);if(activeBatch){const moved=await request('/api/review/rows/'+saved.id+'/move',{batch_id:activeBatch,revision:saved.revision});saved.revision=moved.revision}else activeBatch=saved.batch_id;row._id=saved.id}row._revision=saved.revision;row._data=data;row._original=structuredClone(data)}for(const row of removedRows){await request('/api/review/rows/'+row._id,{revision:row._revision,included:false},'PUT')}removedRows=[];review=await request('/api/review/batches/'+activeBatch);const included=review.rows.filter(r=>editRows.some(e=>e._id===r.id));for(const r of included)if(!r.included)await request('/api/review/rows/'+r.id,{revision:r.revision,included:true},'PUT');review=await request('/api/review/batches/'+activeBatch);if(!review.preview.can_submit){showEditProblems();return}for(const row of editRows)row._problems=[];renderEdit();if(review.preview.can_submit){await request('/api/review/batches/'+activeBatch+'/submit',{token:review.preview.token});review=await request('/api/review/batches/'+activeBatch);await showComparison();toast('已提交，请核对变化后审批入库','ok')}else showEditProblems()})}
async function showComparison(){if(review.preview.items.some(i=>i.problems.length)&&review.batch.status!=='approved'){showEditProblems();return}diffRows=review.preview.items.map(i=>{const row=fromData(i.data,i.display),old=i.target?fromData(i.target.data):{};return {type:statusCodes[i.kind]||'same',row,old,changedKeys:COLS.filter(c=>String(row[c.k]??'')!==String(old[c.k]??'')).map(c=>c.k),problems:i.problems}});const mods=diffRows.filter(r=>r.type==='modify').length,adds=diffRows.filter(r=>r.type==='add').length;document.getElementById('diff-summary').innerHTML=`<div class="item">本次变更</div><div class="item"><span class="swatch sw-modify"></span>修改 <b>${mods}</b></div><div class="item"><span class="swatch sw-add"></span>新增 <b>${adds}</b></div><div class="legend">黄色＝修改 · 绿色＝新增 · 主表只修改与新增</div>`;const problems=[...new Set(review.preview.items.flatMap(i=>i.problems))];document.getElementById('update-tip').textContent=problems.join('；')||'请核对变更后审批入库';const button=document.querySelector('[onclick="approveCommit()"]');button.disabled=user.role!=='admin'||review.batch.status!=='pending';button.textContent=user.role==='admin'?'✓ 审批通过并入库':'等待管理员审批';pane('diff',3);renderDiff()}
async function goStep2(){return action(async()=>{if(review?.batch.status==='pending')await request('/api/review/batches/'+activeBatch+'/withdraw',{});review=null;const current=(await request('/api/manual-drafts')).items;for(const row of editRows){const saved=current.find(r=>r.id===row._id);if(saved)row._revision=saved.revision}pane('edit',2);renderEdit()})}
async function approveCommit(){return action(async()=>{if(!review||review.batch.status!=='pending')throw new Error('请先补齐问题并提交审批');await request('/api/review/submissions/'+review.submissions[0].id+'/decision',{operation_key:crypto.randomUUID(),decision:'approved',reason:''});toast('审批通过，已更新报价表','ok');document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-query').classList.add('active');setTimeout(runQuery,0)})}
async function download(path,body,name){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'X-CSRF-Token':token,...(body!==undefined?{'Content-Type':'application/json'}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});if(!r.ok){const value=await r.json();throw new Error(value.error?.message||'导出失败')}const blob=await r.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
async function exportExcel(rows,name){return action(async()=>{if(name==='报价变更明细'){if(!activeBatch)throw new Error('没有可导出的变更');await download('/api/review/batches/'+activeBatch+'/export',undefined,name+'.xlsx')}else{const query=queryFilters();const filtered=!!(query.origin_port_id||query.destination_text||query.carrier_id||query.etd||query.container_type||query.validity_status);await download('/api/excel/export',{...query,export_layout:filtered?'simple':'template'},name+'.xlsx')}toast('已导出Excel','ok')})}
async function renderRecent(){const host=document.getElementById('recent-uploads');const [uploads,batches]=await Promise.all([request('/api/uploads/batches'),request('/api/review/batches')]);const items=new Map(uploads.items.map(b=>[b.id,b]));for(const b of batches.items)items.set(b.id,{...items.get(b.id),...b});host.innerHTML=[...items.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at)).slice(0,10).map(b=>`<div style="display:flex;align-items:center;gap:8px"><button class="btn-ghost" style="text-align:left;white-space:normal;overflow-wrap:anywhere;max-width:560px" onclick="resumeBatch('${b.id}')"><strong style="display:block;margin-bottom:5px">${esc(b.file_names?.join('、')||'人工录入报价')}</strong>${esc(b.created_at.slice(0,10))} · ${b.file_count?b.file_count+'份文件':b.row_count+'条报价'} · ${esc(({draft:'待处理',pending:'待审批',approved:'已入库',rejected:'已驳回',withdrawn:'已撤回'})[b.status])}</button><button class="btn-ghost" style="color:#c0392b" onclick="deleteUploadedBatch('${b.id}','${b.status}')">删除</button></div>`).join('')||'暂无历史上传'}
async function resumeBatch(id){return action(async()=>{updateEpoch++;clearTimeout(pollTimer);removedRows=[];relatedFiles=[];activeBatch=id;await reloadFiles();await loadEditRows();if(editRows.length){review=await request('/api/review/batches/'+id);if(['pending','approved'].includes(review.batch.status)){await showComparison()}else if(review.preview.items.some(i=>i.problems.length))showEditProblems();else pane('edit',2)}else{pane('upload',1);if(uploadedFiles.some(f=>['waiting','processing','success'].includes(f.task?.status)))pollTimer=setTimeout(pollExtraction,1000)}})}
async function boot(){if(booted)return;booted=true;try{const session=await request('/api/auth/me');token=session.csrf_token;user=session.user;await loadReferences();initQuery();const file=document.getElementById('file-input');file.addEventListener('change',e=>{uploadFiles(Array.from(e.target.files));e.target.value='' });const zone=document.getElementById('dropzone');zone.addEventListener('dragover',e=>{e.preventDefault();zone.classList.add('drag')});zone.addEventListener('dragleave',()=>zone.classList.remove('drag'));zone.addEventListener('drop',e=>{e.preventDefault();zone.classList.remove('drag');uploadFiles(Array.from(e.dataTransfer.files))});document.querySelectorAll('input[id^="q-"]').forEach(i=>i.addEventListener('keydown',e=>{if(e.key==='Enter')runQuery()}));document.getElementById('pane-upload').insertAdjacentHTML('beforeend','<details style="margin-top:24px;color:var(--muted);font-size:13px"><summary>历史上传与待审批</summary><div id="recent-uploads" class="toolbar" style="margin-top:12px"></div></details>');document.querySelector('.topbar .logo').style.cursor='pointer';document.querySelector('.topbar .logo').onclick=()=>go('home');startExtractionMonitor();}catch(e){toast(e.message,'warn')}}
document.addEventListener('DOMContentLoaded',boot);

async function reextractQuotes(){let created=false;await action(async()=>{if(!uploadedFiles.length)throw new Error('没有可重新提取的原件');if(!confirm('将保留现有校对内容和原件，另建一版重新提取。继续？'))return;const copies=[];for(const f of uploadedFiles){if(f.metadata.fixed_template)continue;copies.push(await request('/api/uploads/files/'+f.id+'/reextract',{}))}if(!copies.length)throw new Error('固定模板可以直接编辑当前结果');activeBatch=copies[0].batch_id;relatedFiles=copies.slice(1);removedRows=[];editRows=[];review=null;await reloadFiles();pane('upload',1);created=true});if(created)await extractQuotes()}

async function deleteUploadedFile(id){return action(async()=>{if(!confirm('确定删除此文件？'))return;await request('/api/uploads/files/'+id,undefined,'DELETE');relatedFiles=relatedFiles.filter(f=>f.id!==id);await reloadFiles();await renderRecent();document.getElementById('update-tip').textContent='';toast('文件已删除','ok')})}
async function deleteUploadedBatch(id,status){return action(async()=>{const message=status==='approved'?'确定删除这条上传记录？已入库报价不受影响。':status==='pending'?'确定删除此批次？删除后将不能继续审批。':'确定删除此批次及其待处理报价？';if(!confirm(message))return;await request('/api/uploads/batches/'+id,undefined,'DELETE');if(activeBatch===id)await resetUpdate();else await renderRecent();toast('记录已删除','ok')})}

function showEditProblems(){const items=review.preview.items;for(const row of editRows)row._problems=[...new Set(items.find(i=>i.row_id===row._id)?.problems||[])];const problems=[...new Set(items.flatMap(i=>i.problems))];const count=editRows.filter(r=>r._problems.length).length;const message=count?`有${count}条报价需要调整，已用浅黄色标出。${problems.join('；')}`:'没有可发布的变更';pane('edit',2);renderEdit();document.getElementById('update-tip').textContent=count?`有${count}条报价需要调整，请检查浅黄色行。`:'没有可发布的变更';toast(message,'warn');document.querySelector('#edit-table .quote-conflict')?.scrollIntoView({block:'nearest'})}

async function filterValidity(status){queryStatus=status;await runQuery()}
async function deleteExpiredQuotes(){let done=false;await action(async()=>{const preview=await request('/api/quotes/expired/preview',{});if(!preview.count){toast('没有可删除的过期报价','ok');return}if(!confirm(`确定删除全部${preview.count}条已过期报价？按今天判断，不限当前查询条件；删除后不再显示在报价查询中，历史记录保留。`))return;const result=await request('/api/quotes/expired/delete',{token:preview.token});toast(`已删除${result.deleted}条过期报价`,'ok');done=true});if(done)await runQuery()}

function applyBatchValidity(key,value){if(!['validFrom','validTo'].includes(key)||!value)return;for(const row of editRows){row[key]=value;if(!validityMissing(row))row._problems=(row._problems||[]).filter(p=>!p.includes('有效期缺失'))}renderEdit();document.getElementById('update-tip').textContent=`已将本批次${editRows.length}条报价的${key==='validFrom'?'有效期起':'有效期止'}统一设为${value}，仍可逐行修改。`;toast(`已填入${editRows.length}条报价`,'ok')}

// Background monitor is independent of the currently open batch and never navigates automatically.
let extractionTimer=null,extractionMonitorBusy=false,monitoredTasks=[],extractionWatch={},extractionDismissed=[],extractionConnectionIssue=false;
function extractionStorageKey(){return 'asw-extraction-monitor-v1:'+user.id}
function saveExtractionWatch(){try{localStorage.setItem(extractionStorageKey(),JSON.stringify({watched:extractionWatch,dismissed:extractionDismissed.slice(-200)}))}catch{}}
function watchExtractionTask(id){extractionWatch[id]=true;saveExtractionWatch()}
function extractionNoticeKey(task){return `${task.id}:${task.attempts}:${task.status}`}
function taskProgressText(task){const p=task.progress||{},labels={reading:'读取文件',local_parsing:'本机解析表格',extracting:'AI识别报价',organizing:'整理报价',enriching:'补齐港口信息',saving:'保存提取结果'};let text=task.status==='waiting'?'排队等待':labels[p.stage]||'正在处理';if(task.status==='processing'&&p.total>0)text+=` · 已完成${p.completed}/${p.total}个分段`;return text+` · 已耗时${Math.max(0,task.elapsed_seconds||0)}秒`}
function startExtractionMonitor(){try{const saved=JSON.parse(localStorage.getItem(extractionStorageKey())||'{}');extractionWatch=saved.watched||{};extractionDismissed=saved.dismissed||[]}catch{}const host=document.createElement('div');host.id='extraction-monitor';host.setAttribute('aria-live','polite');host.style.cssText='display:none;margin:16px 24px;padding:14px 18px;border:1px solid #b9d3e8;border-radius:10px;background:#f0f7fc;color:#163b5a;font-size:14px';document.querySelector('.topbar').insertAdjacentElement('afterend',host);refreshExtractionMonitor();document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshExtractionMonitor()})}
async function refreshExtractionMonitor(){if(extractionMonitorBusy||!user)return;clearTimeout(extractionTimer);extractionMonitorBusy=true;try{const result=await request('/api/uploads/tasks');monitoredTasks=result.items;for(const task of monitoredTasks)if(['waiting','processing'].includes(task.status))extractionWatch[task.id]=true;const existing=new Set(monitoredTasks.map(t=>t.id));for(const id of Object.keys(extractionWatch))if(!existing.has(id))delete extractionWatch[id];saveExtractionWatch();extractionConnectionIssue=false;renderExtractionMonitor()}catch{extractionConnectionIssue=true;renderExtractionMonitor()}finally{extractionMonitorBusy=false;extractionTimer=setTimeout(refreshExtractionMonitor,monitoredTasks.some(t=>['waiting','processing'].includes(t.status))?2500:7000)}}
function renderExtractionMonitor(){const host=document.getElementById('extraction-monitor');if(!host)return;const visible=monitoredTasks.filter(t=>['waiting','processing'].includes(t.status)||(extractionWatch[t.id]&&!extractionDismissed.includes(extractionNoticeKey(t))&&t.error_code!=='DELETED'));host.style.display=visible.length||extractionConnectionIssue?'block':'none';host.innerHTML=(extractionConnectionIssue?'<div>进度暂时无法刷新，正在重连；服务正常运行时后台任务会继续。</div>':'')+visible.map(t=>{const running=['waiting','processing'].includes(t.status),count=t.result?.counts?.importable??t.result?.counts?.total;const text=running?taskProgressText(t):t.status==='success'?`提取完成${Number.isInteger(count)?`，${count}条结果`:''}，请校对`:`提取未完成：${t.error||'请查看任务后重试'}`;return `<div style="display:flex;align-items:center;flex-wrap:wrap;gap:10px;margin:5px 0"><strong style="overflow-wrap:anywhere">${esc(t.original_name)}</strong><span>${esc(text)}</span><button class="btn-ghost" onclick="openExtractionBatch('${t.batch_id}')">${running?'查看进度':'查看结果'}</button>${running?'':`<button class="btn-ghost" aria-label="关闭${esc(t.original_name)}提醒" onclick="dismissExtractionNotice('${t.id}')">关闭提醒</button>`}</div>`}).join('')+(visible.some(t=>['waiting','processing'].includes(t.status))?'<small>可返回主页或查询报价，提取会在后台继续。关闭网页后，重新登录也可查看进度及结果。</small>':'')}
function dismissExtractionNotice(id){const task=monitoredTasks.find(t=>t.id===id);if(!task)return;extractionDismissed.push(extractionNoticeKey(task));saveExtractionWatch();renderExtractionMonitor()}
async function openExtractionBatch(id){if(working)return;document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));document.getElementById('view-update').classList.add('active');window.scrollTo(0,0);await resumeBatch(id)}
