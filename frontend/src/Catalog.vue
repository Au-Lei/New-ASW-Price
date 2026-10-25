<script setup>
import { ref, computed, onMounted } from 'vue'

const names = { ports:'港口与交付点', port_aliases:'港口别名', carriers:'船公司', carrier_aliases:'船公司别名', suppliers:'报价供应商', source_series:'协议／报价系列', routes:'航线', countries:'国家／地区', regions:'航线区域' }
const specifications = {
  ports:[['name_en','英文名称'],['name_cn','中文名称'],['country_id','国家／地区','countries'],['terminal','码头／交付点区分'],['port_code','已核对的港口代码'],['route_ids','所属航线','routes','multiple']],
  port_aliases:[['port_id','对应港口','ports'],['name','别名'],['confirmed','已人工核对，允许检索','checkbox']],
  carriers:[['name','标准名称']], carrier_aliases:[['carrier_id','对应船公司','carriers'],['name','别名']],
  suppliers:[['name','供应商名称'],['kind','来源类型','kinds']],
  source_series:[['supplier_id','所属供应商','suppliers'],['name','协议／报价系列名称']],
  routes:[['region_id','所属区域','regions'],['name','航线名称']],
  countries:[['name_en','英文名称'],['name_cn','中文名称']], regions:[['name','区域名称']],
}
const dictionaries = ref({})
const summary = ref(null)
const kind = ref('ports')
const search = ref('')
const showInactive = ref(false)
const message = ref('')
const error = ref('')
const loading = ref(false)
const saving = ref(false)
const form = ref(null)
const editId = ref(null)
const editRevision = ref(null)
const evidence = ref(null)
const fieldSpecs = computed(() => specifications[kind.value])
const kindOptions = [{id:'agent', name:'代理'}, {id:'carrier',name:'船公司'}, {id:'other',name:'其他'}]

function label(row) {
  return row ? [row.name_cn || row.name, row.name_en, row.terminal].filter(Boolean).join(' · ') : '—'
}
function options(source) {
  const rows = source === 'kinds' ? kindOptions : dictionaries.value[source] || []
  return rows.filter(row => row.active !== false || (form.value && Object.values(form.value).flat().includes(row.id)))
}
function relation(source, id) { return label((dictionaries.value[source] || []).find(row => row.id === id)) }
const rows = computed(() => {
  const term = search.value.normalize('NFKC').trim().toUpperCase()
  return (dictionaries.value[kind.value] || []).filter(row => {
    if (!showInactive.value && !row.active) return false
    const texts = [row.name, row.name_en, row.name_cn, row.terminal, row.port_code,
      ...(row.aliases || []).filter(a => a.active && a.confirmed !== 0).map(a => a.name)]
    return !term || texts.some(t => t && t.normalize('NFKC').toUpperCase().includes(term))
  })
})
async function request(url, options) {
  const response = await fetch(url, { ...options, signal: AbortSignal.timeout(12000) })
  const data = await response.json()
  if (!response.ok) throw new Error(data.error?.message || '请求失败，请稍后重试')
  return data
}
async function reload() {
  loading.value = true
  error.value = ''
  try {
    const keys = Object.keys(names)
    const results = await Promise.all(keys.map(key => request(`/api/catalog/${key}`)))
    dictionaries.value = Object.fromEntries(keys.map((key, i) => [key, results[i].items]))
    summary.value = await request('/api/catalog/summary')
  } catch (e) { error.value = e.message } finally { loading.value = false }
}
function changeKind() { form.value = null; evidence.value = null; search.value = ''; message.value = ''; error.value = '' }
function edit(row = null) {
  editId.value = row?.id || null
  editRevision.value = row?.revision || null
  form.value = {active: row?.active ?? true}
  for (const [key, , source, mode] of fieldSpecs.value) {
    const fallback = mode === 'multiple' ? [] : source === 'checkbox' ? false : ''
    form.value[key] = row?.[key] ?? fallback
    if (source === 'checkbox') form.value[key] = Boolean(form.value[key])
  }
  message.value = ''; error.value = ''; evidence.value = null
}
async function save() {
  if (saving.value) return
  saving.value = true; error.value = ''; message.value = ''
  try {
    const body = {...form.value}
    if ('port_code' in body && !body.port_code) body.port_code = null
    const url = `/api/catalog/${kind.value}${editId.value ? `/${editId.value}?expected_revision=${editRevision.value}` : ''}`
    await request(url, {method: editId.value ? 'PUT' : 'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
    form.value = null
    await reload()
    message.value = '已保存。资料编号保持稳定，修改已记录。'
  } catch(e) {error.value = e.message} finally {saving.value = false}
}
async function viewEvidence(row) {
  error.value = ''
  try { evidence.value = {name:label(row), ...await request(`/api/catalog/ports/${row.id}/evidence`)} }
  catch(e) {error.value = e.message}
}
onMounted(reload)
</script>

<template>
  <section class="catalog">
    <div class="catalog-heading"><div><h2>基础资料</h2><p>维护港口、船公司和报价来源，让不同文件中的名称对应到同一份资料。</p></div><button class="small-button" :disabled="loading" @click="reload">刷新资料</button></div>
    <div v-if="summary" class="catalog-summary"><span>港口／交付点 <b>{{ summary.counts.ports }}</b></span><span>航线 <b>{{ summary.counts.routes }}</b></span><span>区域 <b>{{ summary.counts.regions }}</b></span><span>正式报价 <b>{{ summary.quotes }}</b></span></div>
    <p class="catalog-note">默认起运港：青岛。目录包含模板中的内陆交付点；名称按原表保留，不代表已核验国际港口代码。</p>
    <p v-if="summary?.imports.length" class="catalog-source">已导入 {{ summary.imports[0].source_name }} · {{ summary.imports[0].rows_count }} 条目录记录。船公司和供应商请按实际业务添加。</p>
    <div class="catalog-tools"><label>资料类型<select v-model="kind" @change="changeKind"><option v-for="(name,key) in names" :key="key" :value="key">{{name}}</option></select></label><label class="search-field">检索<input v-model="search" placeholder="输入中文、英文或已确认别名"></label><label class="check-label"><input type="checkbox" v-model="showInactive">包含停用资料</label><button class="small-button primary" :disabled="loading || saving" @click="edit()">新增资料</button></div>
    <p class="feedback error" role="alert" v-if="error">{{error}}</p><p class="feedback success" role="status" v-if="message">{{message}}</p>
    <p v-if="loading" role="status">正在加载资料…</p>
    <form v-if="form" class="catalog-form" @submit.prevent="save">
      <h3>{{ editId ? '编辑' : '新增' }}{{ names[kind] }}</h3>
      <div class="form-fields"><label v-for="[key,title,source,mode] in fieldSpecs" :key="key" :class="{'check-label':source === 'checkbox'}">
        <template v-if="source === 'checkbox'"><input type="checkbox" v-model="form[key]">{{title}}</template>
        <template v-else>{{title}}
          <select v-if="source" v-model="form[key]" :multiple="mode === 'multiple'" :size="mode === 'multiple' ? 5 : undefined" :required="mode !== 'multiple'"><option v-if="mode !== 'multiple'" value="" disabled>请选择</option><option v-for="option in options(source)" :key="option.id" :value="option.id">{{label(option)}}{{option.active === false ? '（已停用）' : ''}}</option></select>
          <input v-else v-model="form[key]" :maxlength="key === 'port_code' ? 5 : 160" :required="!['name_cn','terminal','port_code'].includes(key)" :placeholder="key === 'port_code' ? '无核对依据请留空' : ''">
        </template>
      </label></div>
      <p v-if="kind === 'ports'" class="catalog-note">航线可多选（按住 Command／Ctrl）。新增港口请核对国家与码头，同名地点不自动合并。</p>
      <p v-if="kind === 'port_aliases'" class="catalog-note">仅勾选“已人工核对”且启用的别名参与检索；同一别名可以对应多个港口，查询时保留候选。</p>
      <label class="check-label"><input type="checkbox" v-model="form.active">启用此资料</label>
      <div class="form-actions"><button type="submit" class="small-button primary" :disabled="saving">{{saving ? '保存中…' : '保存资料'}}</button><button type="button" class="small-button" :disabled="saving" @click="form = null">取消</button></div>
    </form>
    <p class="catalog-count">共 {{ rows.length }} 条{{ search && rows.length > 1 ? '匹配资料，请结合国家和码头选择' : '' }}</p>
    <div class="catalog-table"><table><thead><tr><th>名称</th><th v-if="kind === 'ports'">国家／地区</th><th v-if="kind === 'ports'">航线</th><th v-if="kind === 'ports'">码头／代码</th><th v-if="['routes','source_series','port_aliases','carrier_aliases','suppliers'].includes(kind)">关联／类型</th><th>状态</th><th>操作</th></tr></thead><tbody>
      <tr v-for="row in rows" :key="row.id"><td><b>{{row.name_cn || row.name || row.name_en}}</b><span v-if="row.name_en && row.name_cn" class="secondary">{{row.name_en}}</span><span v-if="row.id === summary?.default_origin_port_id" class="badge">默认起运港</span></td>
        <td v-if="kind === 'ports'">{{relation('countries',row.country_id)}}</td><td v-if="kind === 'ports'">{{row.route_ids.map(id => relation('routes',id)).join('、') || '未分配'}}</td><td v-if="kind === 'ports'">{{row.terminal || '—'}}<span class="secondary">{{row.port_code || '代码未核验'}}</span></td>
        <td v-if="kind === 'routes'">{{relation('regions',row.region_id)}}</td><td v-if="kind === 'source_series'">{{relation('suppliers',row.supplier_id)}}</td><td v-if="kind === 'port_aliases'">{{relation('ports',row.port_id)}}<span class="secondary">{{row.confirmed ? '已人工核对' : '待核对，不参与检索'}}</span></td><td v-if="kind === 'carrier_aliases'">{{relation('carriers',row.carrier_id)}}</td><td v-if="kind === 'suppliers'">{{kindOptions.find(o => o.id === row.kind)?.name}}</td>
        <td>{{row.active ? '启用' : '已停用'}}</td><td><button class="table-button" :disabled="saving" @click="edit(row)">编辑</button><button v-if="kind === 'ports'" class="table-button" @click="viewEvidence(row)">来源</button></td>
      </tr><tr v-if="!rows.length"><td colspan="7">{{loading ? '正在加载…' : '暂无资料，请添加或调整检索条件。'}}</td></tr>
    </tbody></table></div>
    <aside v-if="evidence" class="catalog-form"><h3>{{evidence.name}} · 目录来源</h3><p v-for="(item,index) in evidence.items" :key="index">{{item.source_name}} · {{item.worksheet}} · 第{{item.row_number}}行</p><p v-if="!evidence.items.length">此资料由本机维护添加，无模板行记录。</p><button class="small-button" @click="evidence = null">关闭来源</button></aside>
  </section>
</template>
