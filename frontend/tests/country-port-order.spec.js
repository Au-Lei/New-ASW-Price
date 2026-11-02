import {test,expect} from '@playwright/test'
test('draft and approval rows keep same country and port together without losing row identities',async({page})=>{
 await page.goto('http://127.0.0.1:8004/')
 await page.getByLabel('账号',{exact:true}).fill('admin')
 await page.getByLabel('密码',{exact:true}).fill('Isolated-test-password-2026!')
 await page.getByRole('button',{name:'登录',exact:true}).click()
 const app=page.frameLocator('iframe')
 await app.locator('#view-home .entry').nth(1).click()
 const frame=page.frames().find(f=>f.url().includes('quotation.html'))
 await frame.evaluate(async()=>{
  activeBatch=(await request('/api/uploads/batches',{})).id
  const rot=refs.ports.find(p=>p.name_en==='ROTTERDAM'),ham=refs.ports.find(p=>p.name_en==='HAMBURG')
  for(const [p,carrier] of [[rot,'carrier-b'],[ham,'carrier-a'],[rot,'carrier-a']]){
   const d=await request('/api/manual-drafts',{...defaults,destination_port_id:p.id,route_id:p.route_ids[0],carrier_id:carrier,prices:{'20GP':'1600'},valid_from:'2026-11-01',valid_to:'2026-11-30'})
   await request('/api/review/rows/'+d.id+'/move',{batch_id:activeBatch,revision:d.revision})
  }
  await loadEditRows();pane('edit',2)
 })
 const ports=app.locator('#edit-table input[data-k="portEn"]')
 expect(await ports.evaluateAll(items=>items.map(i=>i.value))).toEqual(['HAMBURG','ROTTERDAM','ROTTERDAM'])
 await app.locator('#edit-table input[data-r="0"][data-k="remark"]').fill('分组后仍修改汉堡这一行')
 await app.locator('#edit-table input[data-r="0"][data-k="remark"]').blur()
 page.once('dialog',d=>d.accept())
 await app.getByRole('button',{name:'提交审批 →',exact:true}).click()
 await expect(app.locator('#pane-diff')).toBeVisible()
 const diffPorts=app.locator('#diff-table tbody tr').locator('td').filter({hasText:/^(HAMBURG|ROTTERDAM)$/})
 await expect(diffPorts).toHaveText(['HAMBURG','ROTTERDAM','ROTTERDAM'])
 const saved=await frame.evaluate(()=>review.preview.items.find(i=>i.data.remark==='分组后仍修改汉堡这一行').display.destination.name_en)
 expect(saved).toBe('HAMBURG')
 const order=await frame.evaluate(()=>groupedQuoteOrder([{countryEn:'JAPAN',portEn:'TOKYO'},{countryEn:'CHINA',portEn:'SHANGHAI'},{countryEn:'JAPAN',portEn:'OSAKA'},{countryEn:'',portEn:''}]).map(r=>r.portEn))
 expect(order).toEqual(['SHANGHAI','OSAKA','TOKYO',''])
})
