import {test,expect} from '@playwright/test'
import path from 'node:path'
import fs from 'node:fs'
test('actual POD file enriches ports, inherits shared year and duplicates only missing 40 price locally',async({page})=>{
 test.skip(!process.env.ASW_POD_FILE,'Explicit local file required; never bundled in release')
 const errors=[],dialogs=[];page.on('pageerror',e=>errors.push(e.message));page.on('dialog',async d=>{dialogs.push(d.message());await d.dismiss()})
 await page.goto('http://127.0.0.1:8004/');await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill('Isolated-test-password-2026!');await page.getByRole('button',{name:'登录',exact:true}).click()
 const app=page.frameLocator('iframe');await app.locator('#view-home .entry').last().click()
 await app.locator('#file-input').setInputFiles(process.env.ASW_POD_FILE);await expect(app.locator('#filelist')).toContainText(/✓ 已上传|✓ 已提取/)
 await app.getByRole('button',{name:'✨ 提取报价',exact:true}).click();await expect(app.locator('#pane-edit')).toBeVisible({timeout:20000})
 await expect(app.locator('#edit-table tbody tr')).toHaveCount(57)
 await expect(app.locator('input[data-r="0"][data-k="portEn"]')).toHaveValue('HONG KONG')
 await expect(app.locator('input[data-r="0"][data-k="portCn"]')).toHaveValue('香港')
 await expect(app.locator('input[data-r="0"][data-k="countryEn"]')).toHaveValue('CHINA')
 await expect(app.locator('input[data-r="0"][data-k="line"]')).toHaveValue('中国港台航线')
 await expect(app.locator('input[data-r="0"][data-k="validFrom"]')).toHaveValue('2026-10-18')
 await expect(app.locator('input[data-r="0"][data-k="validTo"]')).toHaveValue('2026-10-24')
 const value=await app.locator('input[data-r="0"][data-k="c40"]').inputValue();await expect(app.locator('input[data-r="0"][data-k="c40hq"]')).toHaveValue(value)
 await expect(app.locator('#edit-table tbody')).toContainText('同价补齐');await expect(app.locator('#edit-table tbody')).toContainText('港口待核对')
 expect(errors).toEqual([]);expect(dialogs).toEqual([])
 const out=path.resolve('../data/verification');fs.mkdirSync(out,{recursive:true});await page.screenshot({path:path.join(out,'pod-enrichment-preview.png'),fullPage:true})
})
