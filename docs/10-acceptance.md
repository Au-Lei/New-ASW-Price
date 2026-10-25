# 任务十：端到端验收记录

2026-10-25，版本1.0.0，结构7。所有新增验收都使用临时独立数据目录和虚构报价；真实报价不外发，正式账号不改密，不向正式库写入测试报价。

## 实际检查

- 完整回归：180项通过，前端构建通过。仅存在原有Starlette/httpx测试客户端弃用提示，不影响本轮结果。
- `scripts/smoke_test.py`：真实HTTP、登录、模板上传／提取、无AI失败重试、模拟中断后真实重启、审批及跨进程持久化通过。
- `scripts/accept_accounts.py --lan-ip 10.103.108.35`：指定内网接口上的账号权限、停用、完整备份、CLI恢复及独立实例重新登录和原件字节核对通过。它不是第二台实体电脑的验收。
- `scripts/accept_delivery.py --live-ai`：实际HTTP服务及真实DeepSeek请求，通过四页虚构PDF的两段处理，验证共享表头／末页脚注关联到鹿特丹与汉堡报价；包含冒充指令的文档文字未获得审批权限。自动输出仍未确认起运港／条件，必须人工编辑后才可提交。
- 同一端到端脚本继续验证：固定模板真实后台提取、草稿不进入正式查询、人工校对、审批幂等、2×20GP＋1×40HQ=3900.50 USD、原价1000.25改1050、当前版本与两版历史、分页不限制全量导出、文本不作为Excel公式、10路并发读取共30次一致结果、原始AI预览不变、完整ZIP下载、CLI恢复到新目录及独立进程登录后核对报价／原件／AI证据。实际结果见 `task10-http-report.json`。
- 发布包通过清单和SHA256核对，并在临时目录新建Python虚拟环境、从依赖锁安装、npm ci及重新构建；环境测试及不联网AI的真实HTTP业务／恢复链检查结果见 `task10-clean-install-report.json`。

本轮修正：旧冒烟脚本继承正式 `.env` 的AI启用状态，使原“未配置AI”测试预期不稳定。已在非AI验收脚本显式关闭AI及限定允许主机；复测通过。端到端脚本开发中补齐Excel夹具元数据，并将导出金额按Decimal比较（1050与1050.00数值一致）；未把这些脚本断言失败记录成产品成功。

## 业务规格覆盖

以下对应任务一的42个验收场景。自动测试验证具体规则，真实HTTP检查验证跨模块组合；这不是42个独立人工浏览器测试。除T40的第二设备现场部分外，均有已执行的对应软件验证。

| 场景 | 已执行的证据 | 状态与范围 |
| --- | --- | --- |
| T01–T03 默认起运港、目录分组、别名／歧义 | test_quotation的defaults；test_catalog的import_counts、confirmed_alias、parser_group_boundaries | 软件通过 |
| T04–T06 箱价、日期及必填依据 | test_quotation的price_validation、required_fields；test_review的incomplete | 软件通过 |
| T07–T10 筛选、日期边界、参考日和状态 | test_quotation的date_boundaries、default_date_future_expired、filters | 软件通过 |
| T11–T15 箱量、缺价、币种、费用与条件 | test_quotation的mixed_quantities、invalid_quantities、filters；真实HTTP小计及PDF脚注 | 软件通过 |
| T16–T18 分页全量导出、多起运港、Excel类型 | test_excel的export_all_rows、grouping、all_origins；本轮HTTP修订后导出 | 软件通过 |
| T19–T25 并存、无变化、改价、新一期、重叠、显式目标 | test_review的distinct_identity、same_values、publish_modify、new_period、explicit_clear；本轮两目的港及改价 | 软件通过 |
| T26–T28 不暗删、明确清空、重复及冲突 | test_review的permission_and_disable、explicit_clear、batch_dedup、contradictory | 软件通过 |
| T29–T31 提交锁定、旧预览冲突、整批回滚及幂等 | test_review的reject_withdraw、concurrent、pending_freezes、two_distinct_rows_fail；HTTP重复审批 | 软件通过 |
| T32–T33 文件合法性、重复及中断恢复 | test_uploads全组；HTTP冒烟模拟中断后真实重启 | 软件通过 |
| T34 表头／脚注及扫描图 | test_excel的merge_inheritance；任务八四格式真实联调；本轮四页PDF两段真实联调 | 软件通过；AI准确率仅限所测样本 |
| T35 缺失／歧义、限制及文档指令 | test_ai的uncertain_values、untrusted_fields、missing_evidence；本轮含指令文字PDF与共享限制 | 软件通过；不承诺所有提示词攻击均被识别 |
| T36–T37 原始与编辑结果、服务异常 | test_ai的preview_commit_edit、transport_errors、bad_responses、truncated；本轮恢复后原始预览 | 软件通过 |
| T38–T39 权限、自批、未登录及驳回留痕 | test_accounts_backup；test_review的reject_withdraw、publish；账号与HTTP脚本 | 软件通过 |
| T40 重启、备份恢复、其他设备 | 三个真实HTTP脚本；恢复后独立进程读取报价及原件 | 本机及指定内网接口通过；第二实体设备待现场 |
| T41–T42 修订历史、内部成本导出 | test_review的publish_modify；test_excel的export；本轮修订后查询、导出及恢复 | 软件通过 |

## 交付及门禁结论

交付源代码、构建前端、依赖锁、可复跑测试、完整备份恢复工具、操作说明、验收记录及不含密钥／资料的ZIP包。安装与日常操作见 `10-delivery-guide.md`。版本为本地试用版，不宣称已完成公司生产环境部署。

软件端到端及本机交付验收通过。以下现场事项保留，不能虚构通过：第二台公司设备实际登录及文件访问、受信任HTTPS部署、真实业务允许范围内的脱敏样本质量检查；Windows、大数据量长期压力未测。没有执行真实报价外发或正式管理员改密。

用户现场建议顺序：启动主机→自行管理员首次改密→建立员工账号→同网另一设备访问启动地址→登录查询、上传虚构样本、管理员审批及导出→下载备份。由公司IT按网络环境完成HTTPS与连通性确认；若失败再按请求编号和主机日志排查。

## 试用反馈修正：发送确认提示

用户反馈上传文件反复“提取失败”。只读核对为AI_SCOPE_REQUIRED，逐文件确认尚未保存，并非供应商解析失败。页面改为“等待文件发送确认”，要求明确选择样本性质，提供“确认样本并提取”一次操作；不默认选虚构资料。保留仅保存声明选项。未更改后端外发权限，也未代用户确认或重发文件。前端构建及AI专项复测通过。
