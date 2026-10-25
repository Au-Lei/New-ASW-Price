# 青岛港国际海运报价管理系统

当前版本：1.0.0 本地试用版，业务界面按用户《最终版.html》还原。任务十的软件端到端及本机交付验收通过，提供源码／构建前端／依赖锁／测试／交付包。AI仅允许虚构或脱敏样本，真实报价暂不外发；公司第二设备、HTTPS及真实业务脱敏样本质量仍需现场确认。

## 环境与安装

macOS／Linux，Python 3.12（由 uv 管理）、Node.js 22.12+ 或24、npm和uv。首次安装需要联网。

```bash
bash scripts/setup.sh
bash scripts/start.sh
```

当前实例打开 http://127.0.0.1:8000/ 。停止：在启动终端按 Ctrl+C。重新运行启动脚本即重启，数据保留。示例配置默认只监听127.0.0.1。
当前同时支持本机127.0.0.1及指定内网IP 192.168.31.25；首次管理员凭据见主机 data/admin-initial.txt，首次登录须自行修改密码。此文件权限0600，修改成功后移除。完整说明见 docs/09-accounts-backup-lan.md。

## 配置和目录

`.env.example` 是示例，复制为 `.env`；已有环境变量优先。数据／日志的相对路径基于项目目录，与启动终端位置无关。

- `ASW_DATA_DIR`：默认 `data`；SQLite在 `data/asw.sqlite3`，原件目录 `data/uploads`。
- `ASW_LOG_DIR`：默认 `logs`；`app.log`自动轮转，不记录请求体和密钥。
- `ASW_TIMEZONE`：默认 `Asia/Shanghai`。
- `ASW_PORT`：默认8000，端口被占用可修改。
- `backend/app`：接口、配置及版本化迁移；结构版本7，包含基础资料、报价、审批、账号、上传任务及AI结果表。
- `backend/resources/port-directory.json`：从用户模板提取的295条目录快照；初次启动自动导入，重复启动不会覆盖人工编辑。
- `frontend/src`：Vue首页；构建文件由同一后端提供，不依赖外部CDN。
- `docs`：业务规则、验收用例及开发记录。

升级时先备份，安装依赖并重新构建，再启动；迁移在服务启动时按版本执行，重复启动不会重复迁移。禁止手动修改正式数据库结构。

## 开发与验证

```bash
npm --prefix frontend run build
.venv/bin/python -m pytest backend/tests -q
.venv/bin/python scripts/smoke_test.py
bash scripts/start.sh
```

前端热更新可另开终端执行 `npm --prefix frontend run dev`，浏览器打开5173端口，开发代理默认连接8000端口。使用非默认后端端口时同步调整 `frontend/vite.config.js`。
后端健康检查 `/api/health`，系统信息 `/api/system`，接口文档 `/docs`。
后端异常返回中文错误及请求编号，详细原因见日志；启动失败在终端显示。

依赖更新：使用 `uv pip compile backend/requirements.in -o backend/requirements.txt` 更新Python锁定版本；前端提交 `package-lock.json`。用户资料、日志和密钥不纳入Git。

任务三的目录统计、表关系和API见 `docs/03-data-model.md`，验收见 `docs/03-catalog-verification.md`。
如需重新生成固定目录快照（不是报价上传功能）：`.venv/bin/python scripts/build_port_directory.py /绝对路径/模板.xlsx`。生成前核对文件与用途，更新后运行测试；不要随意替换已导入版本。原始微信临时文件后续无需留在原路径。
基础资料维护仅管理员可操作；业务人员可读取字典用于录入和查询。

## 人工录入与试查

1. 首页下方维护基础资料，先建立实际船公司、报价供应商和对应协议／报价系列。
2. 进入“更新报价”新增人工报价，明确港口、航线、来源、有效期、币种及箱价，填写手工依据并确认必要条件。
3. 点击“保存草稿并校验”。缺失内容也可保存，按提示补齐；未补齐草稿不参与试查。
4. 点击“进入草稿试查”，按ETD或“全部有效期”查看。普通“正式报价”范围始终不含草稿，需进入“差异对比与审批”提交并审批通过。
5. 勾选箱型并输入整数箱量，点击查询计算海运费小计；另计／待确认费用不计入全包价。

人工草稿接口：`GET /api/manual-drafts/defaults`、`GET/POST /api/manual-drafts`、`PUT /api/manual-drafts/{id}?expected_revision=N`。
查询接口：`POST /api/quotes/search`（正式），`POST /api/manual-drafts/search`（独立未审批试查）。筛选使用港口／船公司ID，未选定的歧义名称不直接用于查询。
任务四验收及输入约定见 `docs/04-manual-query-verification.md`。数据库没有自动注入演示报价，浏览器验收的虚构数据仅存于隔离临时目录。

## Excel导入与导出

更新报价→Excel导入→下载填写模板，或选择已有18列模板xlsx。每个文件不超过20MB，识别前20行内的完整表头；表头和列顺序须一致。
上传后先预览。原文件空白字段可通过页面显式补充条件；未知名称、缺日期及不合法价格留待人工修正。勾选行后保存为草稿，再从草稿页校对。空价格目录不生成报价，公式行不可导入。
同内容文件会打开已有批次；提交选行后锁定原始导入快照，后续直接编辑草稿，不重复提交增加行。原文件位于data/uploads，数据库保留来源关系，不能单独删除原件。

价格查询先完成一次正式查询，再点“导出全部查询结果”。按上次查询条件与参考日重新读取全量正式记录，不受分页影响。其他条件空时默认青岛到全部目的港、参考日适用；全部起运港／全部有效期需主动选择。
未审批草稿不导出；正式报价由审批入库产生。导出成本报表保留来源和费用说明，不是客户售价报表。

接口：`GET /api/excel/template`，`POST /api/excel/imports/preview?filename=...&options_json=...`（请求体为xlsx原始字节），`GET /api/excel/imports`，`GET /api/excel/imports/{id}`，`POST /api/excel/imports/{id}/commit`（row_ids），`POST /api/excel/export`（同正式查询筛选）。
验收详见 `docs/05-excel-verification.md`。程序的固定模板读写只用Python标准库，不要求另外安装Excel或使用Codex自带运行时。


任务六操作：更新报价 → 人工录入／Excel导入并校对 → 差异对比与审批 → 选择批次 → 核对新增、修改、原件与费用条件 → 确认差异，提交审批 → 整批审批通过并入库。问题行取消勾选，提交时自动移到独立草稿；有效期重叠须明确修订目标或调整日期。空白默认保留原值，明确清空须逐字段选择。

现已使用真实账号登录与会话，管理员可自批；业务人员只处理本人草稿及批次，不能审批或停用报价。待审批内容不可编辑，可撤回；驳回必须说明理由。审批前数据变化会返回冲突，需撤回重新对比提交。

验收记录：[任务六](docs/06-review-verification.md)。


任务七操作：更新报价 → 上传原件与提取报价 → 选择本批原件 → 保存本批原件 → 逐文件点击“提取报价”。固定模板成功后进入“校对提取结果，选择生成草稿”，选择报价行保存，补齐确认后再提交审批。任务在服务端执行，关闭页面不会丢失任务。失败可重试，“任务结果与重试记录”保留每次处理状态。原件支持下载，图片可在页面查看，草稿／审批／历史记录中也可下载对应原件核对。

默认限制为单文件20MB、每批10个、PDF50页，支持在后端`.env`配置`ASW_UPLOAD_MAX_MB`、`ASW_UPLOAD_MAX_FILES`、`ASW_PDF_MAX_PAGES`并重启。允许范围分别为1～100MB、1～100个、1～500页；图片最多4000万像素，Excel仍受既定工作表、行列及解压体积限制。独立的“固定Excel模板导入”入口继续采用20MB上限。

当前采用单进程本机服务和一个后台提取线程，SQLite队列持久化，不需要额外队列服务。请使用既定启动脚本，不开启多进程workers；当前监听范围及登录要求见任务九说明。原件和提取记录保存在本机；用户逐文件确认并点击AI提取后，样本文本和必要图像才会发送至DeepSeek。固定模板解析不调用AI。

验收记录：[任务七](docs/07-upload-task-verification.md)。


任务九操作、备份恢复命令、账号权限及局域网配置见 [账号／备份／局域网使用与验收](docs/09-accounts-backup-lan.md)。当前HTTP试用不加密传输，受信任HTTPS证书可通过启动配置接入；公司另一台真实设备的连通性仍需现场确认。

## AI报价提取

使用本机配置命令 `.venv/bin/python scripts/configure_ai.py` 隐藏输入密钥；密钥只保存在被Git忽略的 `.env.ai`。新环境在 `.env` 设置 `ASW_AI_ENABLED=true` 并重启后启用。

操作：更新报价 → 上传原件 → 确认文件为虚构或脱敏样本及发送目的地 → 提取报价 → 校对AI结果 → 选行生成草稿 → 人工编辑与确认 → 差异对比及审批入库。

配置、错误处理、数据边界及180项测试和四种格式实际联调记录见 [任务八验收](docs/08-ai-extraction.md)。AI结果须对照原件人工核对，当前尚未允许真实报价外发。

## 交付与验收

[日常使用、安装及备份恢复](docs/10-delivery-guide.md) · [任务十验收及42项规格覆盖](docs/10-acceptance.md)。

交付包：`deliverables/ASW-v1.0.0.zip`，校验文件 `deliverables/ASW-v1.0.0.zip.sha256`。包内不含真实数据与密钥。重建包：`.venv/bin/python scripts/package_release.py`；全新临时目录安装验证：`.venv/bin/python scripts/verify_release.py`（使用依赖缓存，不调用AI）。

端到端离线AI验收：`.venv/bin/python scripts/accept_delivery.py`（人工＋固定模板真实HTTP流程，无AI外发）；真实AI流程需显式运行 `.venv/bin/python scripts/accept_delivery.py --live-ai`，仅发送脚本生成的虚构PDF，会使用DeepSeek服务额度。所有验收使用临时数据目录，不写入正式报价。

## 原演示界面

当前首页、查询和三步更新页面直接采用原演示HTML／CSS，已连接真实服务。操作说明在顶部“📖 使用说明”。还原与测试记录见 [原演示界面还原](docs/11-original-ui-restoration.md)。浏览器如仍显示旧界面，使用Cmd+Shift+R强制刷新。

浏览器验收：先在临时目录启动 `scripts/serve_original_fixture.py /private/tmp/asw-original-ui-任意标识`，然后在frontend目录设置同一 `ASW_UI_FIXTURE` 路径，执行 `./node_modules/.bin/playwright test tests/original-demo.spec.js --workers=1`；首次需安装Playwright Chromium。测试资料为虚构报价，AI关闭。

港口代码及共享有效期补全现已接入，POD／20尺／40尺表可本机解析，40GP／40HQ按用户同价规则补齐。已提取的旧文件请点“重新提取”，保留旧校对稿并创建新版本。详见[补全规则与验收](docs/12-quote-enrichment.md)。
