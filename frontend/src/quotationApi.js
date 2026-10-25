let csrfToken=''
export function setSession(value){csrfToken=value?.csrf_token||''}
export function csrfHeaders(){return csrfToken?{'X-CSRF-Token':csrfToken}:{}}
export async function api(url, options = {}) {
  const response = await fetch(url, {...options,headers:{...csrfHeaders(),...options.headers},signal:options.signal||AbortSignal.timeout(15000)})
  const data = await response.json()
  if(response.status===401&&!url.endsWith('/login'))window.dispatchEvent(new Event('asw-session-expired'))
  if(!response.ok) throw new Error(data.error?.message || '请求失败，请重试')
  return data
}
export function post(url, body, method='POST') {return api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
export async function references() {
  const types=['ports','countries','routes','carriers','suppliers','source_series']
  const values=await Promise.all(types.map(type=>api(`/api/catalog/${type}`)))
  const result=Object.fromEntries(types.map((type,i)=>[type,values[i].items]))
  result.ports=result.ports.map(port=>({...port,country:result.countries.find(c=>c.id===port.country_id)}))
  return result
}
export function portLabel(port) {return port?[port.name_cn,port.name_en,port.country?.name_cn||port.country?.name_en,port.terminal].filter(Boolean).join(' · '):'—'}
export const boxes=['20GP','40GP','40HQ']
export function formatTimestamp(value){
  if(!value)return '—'
  const text=/Z$|[+-]\d{2}:\d{2}$/.test(value)?value:value.replace(' ','T')+'Z'
  const time=new Date(text)
  return Number.isNaN(time.getTime())?'时间格式待核对':new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(time)+'（北京时间）'
}
