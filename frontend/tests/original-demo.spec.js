import {test,expect} from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
const root=path.resolve('..'),source=fs.readFileSync(process.env.ASW_DEMO_REFERENCE||path.join(root,'docs/demo-reference.html'),'utf8'),fixture=process.env.ASW_UI_FIXTURE
const style=html=>html.match(/<style>([\s\S]*?)<\/style>/)[1]
test('original CSS, home, query, upload, cell editing, frozen diff and real approval/export',async({page})=>{
 test.setTimeout(90000)
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 page.on('dialog',async d=>{if(d.type()==='prompt'){await d.accept(d.message().includes('服务名称')?'测试直达服务':'直达')}else await d.accept()})
 await page.goto('http://127.0.0.1:8004/')
 await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('Isolated-test-password-2026!');await page.getByRole('button',{name:'登录',exact:true}).click()
 const app=page.frameLocator('iframe');await expect(app.locator('#view-home')).toBeVisible()
 expect(style(fs.readFileSync(path.join(root,'frontend/public/quotation.html'),'utf8'))).toBe(style(source))
 await expect(app.locator('#view-home .entry')).toHaveCount(2)
 await page.screenshot({path:path.join(root,'docs/original-home-proof.png'),fullPage:true})
 await app.locator('#view-home .entry').first().click();await app.locator('#q-etd').fill('2026-11-15');await app.getByRole('button',{name:'🔍 查询报价',exact:true}).click();await expect(app.locator('#q-table tbody tr')).toContainText('1000.00')
 await expect(app.locator('#q-table th')).toHaveCount(18)
 await app.locator('#q-dest').fill('鹿特');await app.getByRole('button',{name:'🔍 查询报价',exact:true}).click();await expect(app.locator('#q-table tbody tr')).toContainText('鹿特丹')
 const dl=page.waitForEvent('download');await app.getByRole('button',{name:'📥 导出 Excel',exact:true}).click();expect((await dl).suggestedFilename()).toContain('.xlsx')
 await page.screenshot({path:path.join(root,'docs/original-query-proof.png'),fullPage:true})
 await app.locator('#view-query .back-link').click();await app.locator('#view-home .entry').last().click()
 await app.locator('#file-input').setInputFiles(path.join(fixture,'synthetic.xlsx'));await expect(app.locator('#filelist')).toContainText('✓ 已上传')
 await app.getByRole('button',{name:'✨ 提取报价',exact:true}).click();await expect(app.locator('#pane-edit')).toBeVisible({timeout:15000})
 await expect(app.locator('#edit-table tbody tr')).toHaveCount(1);await expect(app.locator('#update-tip')).toContainText('提取完成')
 await app.locator('input[data-r="0"][data-k="c20"]').fill('1050');await app.locator('input[data-r="0"][data-k="c20"]').blur()
 await app.getByRole('button',{name:'＋ 新增一行',exact:true}).click()
 const values={line:'欧洲基本港',countryEn:'GERMANY',countryCn:'德国',portEn:'HAMBURG',portCn:'汉堡',carrier:'测试船公司a',c20:'1200',c40:'2000',c40hq:'2200',currency:'USD',sailing:'周三',transit:'32',route:'青岛-汉堡',source:'测试代理a / 测试协议a',remark:'仅虚构测试',validFrom:'2026-11-01',validTo:'2026-11-30',feeBasis:'CY-CY'}
 for(const [key,value] of Object.entries(values)){const cell=app.locator(`input[data-r="1"][data-k="${key}"]`);await cell.fill(value);await cell.blur()}
 await app.getByRole('button',{name:'＋ 新增一行',exact:true}).click();await app.getByRole('button',{name:'移出',exact:true}).last().click();await expect(app.locator('#edit-table tbody tr')).toHaveCount(2)
 await page.screenshot({path:path.join(root,'docs/original-edit-proof.png'),fullPage:true})
 await app.getByRole('button',{name:'提交审批 →',exact:true}).click();await expect(app.locator('#pane-diff')).toBeVisible()
 await expect(app.locator('#diff-table .row-modify')).toHaveCount(1);await expect(app.locator('#diff-table .row-add')).toHaveCount(1)
 await expect(app.locator('#diff-table .row-modify .diff-cell')).not.toHaveCount(0)
 await expect(app.getByRole('button',{name:'✓ 审批通过并入库',exact:true})).toBeEnabled()
 await page.screenshot({path:path.join(root,'docs/original-diff-proof.png'),fullPage:true})
 const changes=page.waitForEvent('download');await app.getByRole('button',{name:'📥 导出变更明细',exact:true}).click();expect((await changes).suggestedFilename()).toContain('.xlsx')
 await app.getByRole('button',{name:'✓ 审批通过并入库',exact:true}).click();await expect(app.locator('#view-query')).toBeVisible()
 await app.locator('#q-dest').fill('');await app.getByRole('button',{name:'🔍 查询报价',exact:true}).click();await expect(app.locator('#q-table tbody tr')).toHaveCount(2);await expect(app.locator('#q-table tbody')).toContainText('1050.00');await expect(app.locator('#q-table tbody')).toContainText('汉堡')
 expect(errors).toEqual([])
 await page.reload();await expect(page.frameLocator('iframe').locator('#view-home')).toBeVisible()
})
