# 港口补全、共享有效期与40尺同价

2026-10-25。按用户新确认的业务规则实施，保持原演示18列界面。

## 解析规则

- 优先采用同一文件的港口代码对照表，其次使用本机已确认的港口名、代码及别名。补全国家英文／中文、港口英文全称／中文及模板航线分类；不以代码直接充当已确认的英文港名。新港口的字段先保留在可编辑草稿，用户校对提交时才纳入基础资料。确认过的代码可作为别名供以后复用。
- 来源表的私有码不是通用UN/LOCODE，保留来源代码和查表位置；同名不同码头不盲目合并，不能唯一确定的代码标“港口待核对”。
- `WK42 2026/10/18~10/24`按明确日期识别为2026-10-18～2026-10-24，继承同一范围左侧年份，不按WK编号猜日期。支持明确跨年范围；不为完全缺失年份的范围补猜。
- 40GP／40HQ只提供一种价格时，另一种按用户规则补齐并显示“同价补齐”。原始值留在原件及提取快照中；两种价格明确不同则各自保留，不强制覆盖。不把无效数字、非数字询价文字或公式缓存当成已确认价格。
- POD／20尺／40尺这类表格及同文件港口对照表可本机读取，不需要发送真实报价到AI服务。AI处理其他格式时也使用相同字典、有效期和同价规则；提示词版本shipping-extract-3。
- 一行多个港口展开成多条报价，保留共价来源；同代码出现在组价与单列中且价格不同，分别保留，后续差异校验提示冲突，不用最后一行覆盖。
- “日照起步”的组价保留日照起运信息，并在备注中显示，不默认为青岛。无明确全表币种、船公司、来源时仍待人工补齐，不能拿某条驳船费的USD说明套到全表。

## 已有提取结果

新增“重新提取”：创建新原件版本及新批次，原有提取快照、原件和人工编辑内容保留。已上传的POD表如果旧结果来自AI，会提示“按新规则重新提取”，避免反复复用旧结果。只有点击后才建立新版本，不自动覆写人工校对稿。

## 实际验收

- 用用户提供的Excel在独立本机实例解析，展开57条待校对记录，有效期均来自共享表头；54条可匹配港口或使用附表明确名称，3条IDPJA／IDXID／JPNHA不自动定名。
- 浏览器以真实文件验证上传、无AI发送确认弹窗、本机提取、57行显示、港口全称／中文／国家／航线／日期及同价标记，运行时错误为零。测试不写正式库；含实际价格的截图只在主机data/verification下，不进入源码包。
- 完整回归200项通过；其后补充AI路径复用文件对照表与日期规则的专项，补全专项共16项通过。覆盖不猜年份、跨年、无效日期、区间冲突、明确不同40尺价格不覆盖、重复重新提取不改旧稿、原件字节一致、确认别名复用及无AI调用。
- 用户原文件未修改；未向AI服务发送真实报价原件或价格。

## 港口资料核对

附表外补充的公开资料仅涉及港口标识，不含用户报价：

- IDJBK：港口运营方[Cikarang Dry Port](https://cikarangdryport.com/id/direct-multimodal-export-import/)。
- THSGZ：[CNC船公司港口目录](https://www.cnc-line.com/ebusiness/schedules/port/details?ActualPOLDescription=SONGKHLA+PORT+%3B+TH+%3B+THSGZ&POLDescription=SONGKHLA+PORT+%3B+TH+%3B+THSGZ)。
- IDMKR：[长荣公开港口附加资料](https://www.evergreen-shipping.us/servlet/LoginPage2?FmcIndex=EGLV-603&RuleIndex=010-O10&jspname=JspFmcRevision)。
- JPNHA在[日本横滨港代码文件](https://www.city.yokohama.lg.jp/city-info/yokohamashi/yokohamako/kkihon/tokei/link.files/07_DomesticPortCodes2025-04.pdf)与[越南港口协会代码表](https://www.vpa.org.vn/vn/port-code/)中有不同对应；IDPJA在[马来西亚官方口岸列表](https://www.miti.gov.my/miti/resources/Preferential%20Certificate%20of%20Origin/ATIGA%20e-Form%20D/Port_of_Discharge_-_Appendix_A.12_Updated_MIG_Version_3_.1_.6_.pdf)列为Penajam，但原附表没有相应定义；IDXID未能核实。不将候选或可能不同的供应商私有码当成唯一事实。
