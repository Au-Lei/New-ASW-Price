# 在另一台 Codex 继续开发

## 当前状态

仓库：https://github.com/Au-Lei/New-ASW-Price ，主分支 main。

这是可本地运行的国际海运报价系统：FastAPI + SQLite，Vue 登录外壳，业务页面沿用用户演示 HTML。供公司少量内部用户使用。数据库迁移截至 **011**。最近验收：**275 项后端测试通过**；后台提取页面验收及4项校对审批回归通过。测试仅使用虚构资料、模拟AI响应，没有真实报价外发。

代码已经包含上传及历史记录删除、批次隔离、文件名展示、冲突拦截、有效期必填与批量日期、状态筛选、过期清理、同港多船公司补齐、国家港口排序、航线合并、后台提取进度及完成提醒。具体规则以 docs/21～29 为准。此前提出的“部分成功保留/失败分段单独重试、自动重试”等只是讨论，尚未实现。

## 新电脑安装

需要 Git、Python3.12、uv、Node22.12+或24及npm。macOS/Linux：

```bash
git clone https://github.com/Au-Lei/New-ASW-Price.git
cd New-ASW-Price
bash scripts/setup.sh
ASW_HOST=127.0.0.1 ASW_PORT=8001 bash scripts/start.sh
```

访问 http://127.0.0.1:8001/ ，健康检查 `/api/health`，正常返回schema_version 11。默认端口8000，如被其他项目占用，使用上面的8001启动。前端改动必须重新 `npm --prefix frontend run build`，然后刷新网页；后端改动须重启。Vue开发代理默认8000，若用Vite开发需调整后端代理端口。

首次启动创建独立本机数据库和管理员，初始凭据保存在 `data/admin-initial.txt`。原电脑/云端的账号、报价、上传原件和AI Key不会跟随Git。AI不是启动必需项；需要时本机执行 `.venv/bin/python scripts/configure_ai.py`，不要把密钥贴入聊天或代码。模型/接口配置以当前代码和示例环境文件为准。

## 主要入口

- backend/app/main.py：启动、认证中间件、静态页面。
- quotation.py：草稿、查询、有效期筛选及过期清理。
- review.py：身份匹配、去重/冲突、预览、审批及版本记录。
- uploads.py / extraction_progress.py：持久任务、文件、后台进度接口。
- ai_extract.py / prompts/shipping_rules.txt：AI提取与证据校验；port_metadata.py/quote_enrichment.py补齐港口。
- pod_excel.py / excel_api.py / legacy_excel.py：本机Excel解析、导入导出和xls支持。
- frontend/public/quotation-app.js：校对、查询、审批、任务轮询、提醒。网页脚本版本在quotation.html及App.vue中，改版需同步。
- migrations/010：软删除及允许删除后重新上传；011：进度字段。迁移只能追加，通过服务启动执行，勿手改正式库。

## 验证

```bash
.venv/bin/python -m pytest backend/tests -q
npm --prefix frontend run build
```

Playwright在frontend目录运行，使用本机浏览器依赖（必要时 `npx playwright install chromium`）。测试固定监听8004，使用隔离库，不能指向业务实例。

后台监控端到端：

```bash
.venv/bin/python scripts/serve_progress_fixture.py /private/tmp/asw-progress-fixture-browser1
# 另开终端
cd frontend
npx playwright test tests/background-extraction.spec.js --workers=1
```

fixture目录必须全新；该测试读取上述目录的虚构xlsx。已存在时选择新目录并同步测试路径，或确认仅是测试数据后清理原目录。服务人为延迟本机解析7秒，不调用AI。

其他测试使用 `scripts/serve_original_fixture.py /private/tmp/asw-original-ui-新名称`；国家港口合并测试加 `--port-groups`。部分测试包含临时xlsx路径，按各spec准备对应fixture。不能不准备fixture就把全部浏览器测试一起跑；不同fixture场景应分别启动、停服。后端275项可直接整体运行。

## 云端信息与交付边界

服务器47.122.105.169；项目 `/home/admin/asw-price`，服务 `asw-price.service`，后端127.0.0.1:8001，Nginx以HTTPS转发。另有ETD、旧ASW项目，不要误操作。

云端systemd覆盖使用 `--proxy-headers --forwarded-allow-ips=127.0.0.1`；不要直接换回本机start.sh的no-proxy-headers方式，否则HTTPS登录可能报来源不匹配。保留云端 `.env`、`.env.ai`、data及现有Nginx/systemd配置。

用户要求手动部署。最近提供20261028离线zip及安装脚本，仅做了本地打包/校验；**没有获得云端执行成功证据**。不要把“已提供更新包”说成“云端已更新”。安装包位于原电脑被Git忽略的deliverables目录，不随克隆带到新电脑；源码交付可运行scripts/package_release.py重新生成（先构建前端），服务器更新必须先备份并明确保留环境和业务数据。

## 给新Codex的起始指令

“这是国际海运报价项目，请先阅读AGENTS.md和docs/30-development-handoff.md，检查当前分支与工作区，安装并启动本地8001，先验证现状再根据我的新要求修改。不要部署云端，不要向外部AI发送真实报价，不要覆盖我的未提交改动。”
