<script setup>
import {ref,computed} from 'vue'
import {portLabel} from './quotationApi'
const props=defineProps({modelValue:{type:String,default:''},ports:{type:Array,default:()=>[]},label:String,allowAll:Boolean})
const emit=defineEmits(['update:modelValue'])
const term=ref('')
const candidates=computed(()=>{
  const normalized=term.value.normalize('NFKC').trim().toUpperCase()
  return props.ports.filter(port=>port.id===props.modelValue||(port.active&&(!normalized||[port.name_en,port.name_cn,port.port_code,...(port.aliases||[]).filter(a=>a.active&&a.confirmed).map(a=>a.name)].some(text=>text&&text.normalize('NFKC').toUpperCase().includes(normalized)))))
})
defineExpose({hasUnresolvedSearch:()=>Boolean(term.value.trim()&&!props.modelValue),clearSearch:()=>{term.value=''}})
</script>
<template><div class="port-picker"><label>{{label}}检索<input v-model="term" @input="emit('update:modelValue','')" placeholder="输入中英文或已确认别名"></label><label>{{label}}<select :value="modelValue" @change="emit('update:modelValue',$event.target.value)"><option value="">{{allowAll&&!term.trim()?'全部港口':'请选择明确港口'}}</option><option v-for="port in candidates" :key="port.id" :value="port.id">{{portLabel(port)}}{{port.active?'':'（停用）'}}</option></select></label><small v-if="term&&!modelValue">{{candidates.length?`找到${candidates.length}个候选，请在列表中选择；未选定前不能查询。`:'没有匹配港口，请调整名称或维护别名。'}}</small></div></template>
