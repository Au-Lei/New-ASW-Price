import {test,expect} from '@playwright/test'
test('completed geography renders and new Europe routes stay in Europe',async({page})=>{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept())
 await page.goto('http://127.0.0.1:8004/')
 await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('Isolated-test-password-2026!');await page.getByRole('button',{name:'登录',exact:true}).click()
 const app=page.frameLocator('iframe');await expect(app.locator('#view-home')).toBeVisible();await app.locator('#view-home .entry').last().click()
 const frame=page.frames().find(f=>f.url().includes('quotation.html'))
 await frame.evaluate(()=>{pane('edit',2);addRow();Object.assign(editRows[0],{line:'欧洲航线',countryEn:'ROMANIA',countryCn:'罗马尼亚',portEn:'CONSTANTA',portCn:'康斯坦察',c20:'1000',c40:'2000',c40hq:'2000',remark:'虚构UI回归数据',_enrichment:{status:'source_reference',fields:{line:'欧洲航线'},reference:{source_url:'https://www.portofconstantza.com/'}}});renderEdit()})
 await expect(app.locator('input[data-k="countryEn"]')).toHaveValue('ROMANIA');await expect(app.locator('input[data-k="portCn"]')).toHaveValue('康斯坦察')
 await app.getByRole('button',{name:'提交审批 →',exact:true}).click();await expect(app.locator('#pane-diff')).toBeVisible()
 const geography=await frame.evaluate(async()=>{const routes=(await request('/api/catalog/routes')).items,regions=(await request('/api/catalog/regions')).items;return {route:routes.find(r=>r.name==='欧洲航线'),region:regions.find(r=>r.name==='欧洲航线')}})
 expect(geography.route.region_id).toBe(geography.region.id)
 await expect(app.getByRole('button',{name:'✓ 审批通过并入库',exact:true})).toBeEnabled();expect(errors).toEqual([])
})
