<script setup>
import {ref,onMounted} from 'vue'
import {api,post} from './quotationApi'
const props=defineProps({user:Object}),emit=defineEmits(['logout','passwordChanged'])
const users=ref([]),backups=ref([]),error=ref(''),message=ref(''),busy=ref(false),temporary=ref(null),username=ref(''),role=ref('business'),currentPassword=ref(''),newPassword=ref(''),confirmPassword=ref('')
async function run(fn){if(busy.value)return;busy.value=true;error.value='';message.value='';try{await fn()}catch(e){error.value=e.message}finally{busy.value=false}}
async function load(){if(props.user.role==='admin'&&!props.user.must_change_password){users.value=(await api('/api/auth/users')).items;backups.value=(await api('/api/backups')).items}}
async function create(){temporary.value=await post('/api/auth/users',{username:username.value,role:role.value});username.value='';await load();message.value='账号已创建，首次登录须修改临时密码。'}
async function update(u){await post('/api/auth/users/'+u.id,{role:u.role,active:!!u.active},'PUT');message.value='已保存，该账号原有登录会话已失效。';await load()}
async function reset(u){temporary.value=await post(`/api/auth/users/${u.id}/reset-password`,{});message.value='原密码及登录会话已失效，请将临时密码交给本人修改。'}
async function change(){if(newPassword.value!==confirmPassword.value)throw new Error('两次新密码输入不一致');await post('/api/auth/password',{current_password:currentPassword.value,new_password:newPassword.value});currentPassword.value='';newPassword.value='';confirmPassword.value='';emit('passwordChanged')}
async function backup(){const result=await post('/api/backups',{});message.value=`备份完成，包含${result.file_count}个原件。请下载到另一存储位置保留。`;await load()}
onMounted(()=>run(load))
</script>
<template><section><h2>账号与备份</h2><p>当前账号 {{user.username}} · {{user.role==='admin'?'管理员':'业务人员'}}</p><p v-if="error" class="feedback error" role="alert">{{error}}</p><p v-if="message" class="feedback success" role="status">{{message}}</p>
<form class="catalog-form" @submit.prevent="run(change)"><h3>修改本人密码</h3><p v-if="user.must_change_password">首次登录须先设置本人密码，完成后重新登录。</p><label>当前密码<input type="password" v-model="currentPassword" autocomplete="current-password" required maxlength="128"></label><label>新密码（12～128位）<input type="password" v-model="newPassword" autocomplete="new-password" required minlength="12" maxlength="128"></label><label>确认新密码<input type="password" v-model="confirmPassword" autocomplete="new-password" required minlength="12" maxlength="128"></label><button class="small-button primary" :disabled="busy">修改并重新登录</button></form>
<template v-if="user.role==='admin'&&!user.must_change_password"><form class="catalog-form" @submit.prevent="run(create)"><h3>创建同事账号</h3><p>最多启用10个账号。业务人员可查询、导出并处理自己的草稿；管理员另可维护资料、审批、账号及备份。</p><label>用户名<input v-model="username" required maxlength="80"></label><label>角色<select v-model="role"><option value="business">业务人员</option><option value="admin">管理员</option></select></label><button class="small-button primary" :disabled="busy">创建并生成临时密码</button></form>
<aside v-if="temporary" class="quote-banner"><b>临时密码（仅本次显示）</b><p>{{temporary.temporary_password}}</p><button class="small-button" @click="temporary=null">已记录，关闭</button></aside>
<div class="catalog-table"><table><thead><tr><th>用户名</th><th>角色</th><th>启用</th><th>操作</th></tr></thead><tbody><tr v-for="u in users" :key="u.id"><td>{{u.username}}{{u.id===user.id?'（本人）':''}}</td><td><select v-model="u.role"><option value="business">业务人员</option><option value="admin">管理员</option></select></td><td><input type="checkbox" v-model="u.active" :true-value="1" :false-value="0" :aria-label="`启用${u.username}`"></td><td><button class="small-button" :disabled="busy" @click="run(()=>update(u))">保存角色及状态</button><button class="small-button" :disabled="busy" @click="run(()=>reset(u))">重置临时密码</button></td></tr></tbody></table></div>
<section class="catalog-form"><h3>数据库与原件完整备份</h3><p>备份包含账号、报价、审批、原件及校验清单。备份期间请暂停更新报价，查询仍可使用。恢复只在主机上操作，并写入新目录，不覆盖当前资料。</p><button class="small-button primary" :disabled="busy" @click="run(backup)">创建完整备份</button><p v-for="b in backups" :key="b.name"><a :href="`/api/backups/${b.name}`" download>{{b.name}}</a> · {{(b.size_bytes/1024/1024).toFixed(2)}}MB</p></section></template>
</section></template>
<style scoped>label{display:block;margin:12px 0}input,select{padding:9px;max-width:100%}.quote-banner p{overflow-wrap:anywhere}</style>
