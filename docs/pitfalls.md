# 踩坑登记册（pitfalls.md）

> 按项目规范 5.2 条维护：每次踩坑登记"现象 → 根因 → 修复 → 一句话规律"。
> 同类别错误登记后不得再犯。此文档同时是项目的经验资产库。

## 1. OpenAIEmbeddings 的 tiktoken 编码坑（继承自 RAG demo 阶段，本项目直接适用）

- **现象**：langchain 的 OpenAIEmbeddings 接第三方服务（硅基流动），中文语义相似度区分度从 0.30 塌缩到 0.04，且**不报错**（静默劣化）。
- **根因**：默认 `check_embedding_ctx_length=True` 会先用 tiktoken（OpenAI 分词器）把文本编成 token ID 再发送；第三方服务按自己的词表解读这些 ID，中文被"串味"成另一段文字，向量全错。
- **修复**：`OpenAIEmbeddings(..., check_embedding_ctx_length=False)`，发送原始字符串由服务端分词。
- **规律**：接第三方 OpenAI 兼容服务时，凡是 langchain 默认行为涉及"OpenAI 专属预处理"的参数都要审一遍；不报错的错误最危险，新接入必须用已知样例做相似度 sanity check。

## 2. 异步方法误用于同步链（继承自 RAG demo 阶段）

- **现象**：`chain.invoke()` 抛 `TypeError: Cannot invoke a coroutine function synchronously`。
- **根因**：方法名带 `a` 前缀（如 `db.asimilarity_search_with_score`）是异步版，返回协程；同步 `invoke` 无法执行。
- **修复**：普通 `invoke` 链一律用不带 `a` 的同步版。
- **规律**：LangChain 生态 `a` 前缀 = async；写 LCEL 链前先确认每个方法的同步/异步形态一致。

## 3. 向量库重复入库（继承自 RAG demo 阶段）

- **现象**：RAG 脚本每运行一次 `add_documents` 就重复插入全部文档块，实测积到 18 条（6 块 × 3 次），检索 top-k 全是重复内容，回答质量悄悄下降。
- **根因**：入库前没有检查库内已有数据。
- **修复**：入库前 `if db.get()["ids"]: 跳过`；知识库重建走"删表全量重入"，不做增量补插。
- **规律**：任何"写库"操作都要幂等（重复执行结果不变），否则调试期间的数据污染会以"效果变差"而非报错的形式出现。

## 4. PDF 下载静默截断（Linearized PDF 假"0 页扫描型"）

- **现象**：curl -sL 下载教材分章 PDF，curl 不报错、文件开头是真 `%PDF-1.4`，但 PyMuPDF 打开显示 **0 页**——第一反应容易误判成"扫描型 PDF"。
- **根因**：UBC 服务器对本网络的大响应（约 >1.2MB）中途断流，文件被截断；这些 PDF 是 **Linearized（线性化）格式**，页对象总数（N 34）和总长（L 2112040）写在文件头元数据里，但主 xref 表在**文件尾**——尾部缺失时 PyMuPDF 解析不出任何页。wget -c 断点续传也拿回同样大小（每次断点位置相同，疑似中间层对大响应的一致性截断，Range 续传无效）。
- **修复/规避**：① 小文件（<1MB）一次就能下完整（Ch01/Ch06 成功）；② 判定"0 页"前先看文件头 `%PDF` 和文件大小 vs Linearized 头里的 `/L 总长`——大小对不上=截断，不是扫描件；③ 截断文件直接删除，勿混入知识库；④ 同内容改从 LibreTexts 网页版取 HTML 文本（已验证可达），比 PDF 管线更干净。
- **规律**：`curl -s` 静默失败 + 文件头正常 = 大概率截断/错误页，**下载后必须校验完整性**（PDF 看 `%PDF` 头 + 能解析页数；有 Content-Length/总长元数据就比对大小）。

## 5. PyMuPDF 导入警告（2026-08-23）

- `import fitz` 出 DeprecationWarning：`The fitz API is deprecated and will be removed in future. Use import pymupdf instead`——新版建议 `import pymupdf`（fitz 别名将移除）。现有代码仍能跑，新代码统一用 `import pymupdf`。

## 6. 缓存孤儿：源文件删除后 OCR 缓存残留（2026-08-23）

- **现象**：删除 2023 旧报告、换入 2025 公报后，`ocr_cache/` 里仍残留旧报告的 4 个 OCR 缓存 txt——数据"删不干净"。
- **根因**：管线只管"写缓存"（避免重复调 API），没有管"缓存生命周期"——缓存与源文件的对应关系断了也不会被发现。
- **修复**：管线开头加孤儿清理——遍历缓存，凡缓存名对应的源 PDF 不在当前 `books_pdf/` 里就删除。重跑时自动清掉 4 个孤儿，且其余 OCR 全部命中缓存、零 API 消耗。
- **规律**：**加缓存时必须同时设计缓存失效**（源删/源变/参数变）。凡是"加速优化"引入的状态，都是新的清理责任。

## 7. PostgreSQL 18 官方镜像挂载点变更（2026-08-24 实测）

- **现象**：用 pgvector/pgvector:pg18 镜像按老习惯挂载 `-v 卷:/var/lib/postgresql/data`，容器启动即退出，日志报 `Error: in 18+, these Docker images are configured to store database data in a format which is compatible with "pg_ctlcluster"...`。
- **根因**：PG18+ 官方镜像改了数据目录布局——数据存进**版本化子目录**（配合 pg_ctlcluster，方便将来 `pg_upgrade --link` 跨版本升级），因此要求挂载点从 `/var/lib/postgresql/data` 改为**父目录 `/var/lib/postgresql`**；挂在老位置一律拒绝启动（与卷是否为空无关）。
- **修复**：`docker run ... -v 卷名:/var/lib/postgresql pgvector/pgvector:pg18`。PG17 及以下仍用老的 `/var/lib/postgresql/data`。
- **规律**：大版本升级先看官方镜像的 BREAKING CHANGES；教程代码的挂载路径跨大版本不保证兼容。另：PG 数据目录跨大版本不能直接复用（高版本拒启低版本数据目录），升级要么 pg_upgrade 要么 pg_dump 导出导入——本项目数据可再生，选择了"新卷+重跑管线"的重建路线（全程约 3 分钟，顺带验证了灾备能力）。

## 8. ADD CONSTRAINT 幂等化的异常码是 duplicate_table（2026-08-25）

- **现象**：init_db.py 用 `DO $$ BEGIN ALTER TABLE ... ADD CONSTRAINT ...; EXCEPTION WHEN duplicate_object THEN NULL; END $$;` 做约束幂等，在已有同名约束的库上重跑，异常未被捕获，直接抛 `psycopg2.errors.DuplicateTable: relation "uq_content" already exists`。
- **根因**：UNIQUE/CHECK 约束底层由索引实现，约束已存在时 PG 报的 SQLSTATE 是 42P07（`duplicate_table`，"relation already exists"），不是直觉上的 `duplicate_object`（42710）。
- **修复**：EXCEPTION 分支同时捕获两种：`WHEN duplicate_object THEN NULL; WHEN duplicate_table THEN NULL;`。
- **规律**：凡"约束/索引已存在"的幂等场景，优先用原生 `IF NOT EXISTS`（表、扩展、索引都支持）；ADD CONSTRAINT 不支持，吞异常时按 SQLSTATE 42P07 捕获 duplicate_table。

## 9. 思考型模型流式首字节延迟 ≈ 思考时长（2026-08-25 实测）

- **现象**：/ask 接口偶发"挂死"——SSE 一个字节都不返回，curl 100s 超时；服务日志显示检索 0.3s 完成、卡死在"开始流式生成"之后。且带会话历史的请求挂死率明显更高（疑似长 prompt 思考更久）。
- **根因**：Qwen3.5-35B-A3B 是思考型模型，流式调用时思考过程在服务端生成、不下发给客户端——首字节延迟 = 思考耗时。tests/smoke_llm_stream.py 隔离实测：思考开（默认）首字节 29~37s；`enable_thinking=False` 后 0.5s（快 74 倍）。当思考超过 60s timeout 触发 SDK 重试（再 60s），总时长超观察窗 = 表象"挂死"。
- **修复**：ChatOpenAI 加 `extra_body={"enable_thinking": False}`。RAG 问答场景检索内容已喂给模型，不需要长思考；若未来要开思考，前端必须配"思考中"状态提示，并把 timeout 与重试预算算上思考时长。
- **规律**：SSE/流式场景选模型先问两件事——是否思考型（首字节延迟）+ 网关是否透传思考段；用冒烟测试测首字节延迟再上线。

## 10. 和风天气新版 GeoAPI 路径是 /geo/v2 不是 /geo/v1（2026-08-25 实测）

- **现象**：JWT 签名通过（无 401），但城市查找请求 404：`/geo/v1/city/search?location=北京`。
- **根因**：新版 API v1 的版本号在 **服务名**（weather）上，GeoAPI 自身仍是 v2——城市查找实为 `/geo/v2/city/lookup`（响应数组字段为 `location`），把它想当然拼成 `/geo/v1/city/search` 是错的。凭据权限正常，纯属路径拼错。
- **修复**：写冒烟测试 `tests/smoke_qweather.py` 对 4 个候选路径逐一实测（200/404 一测便知），确认 `/geo/v2/city/lookup` 与 `/weather/v1/current/{lat}/{lon}`；client.py 改用实测路径。另注意新版响应结构为嵌套式（`temperature.value`、`condition.text`、`humidity` 为 0-1 小数），整体透传给播报 prompt 时补充了"湿度换算百分比"的说明。
- **规律**：第三方 API 路径不要凭版本号推断，用带认证的冒烟测试把候选路径实测一遍（一次 HTTP 请求的成本换确定性）；版本号挂在哪个段（服务名 or 路径）各家 API 习惯不同。

## 11. PG 全文检索 AND 语义：查询里的疑问词让匹配全军覆没（2026-08-25 实测）

- **现象**：`search_lexical("台风预警信号分几级")` 返回空，但手动 SQL 用"台风 预警 信号"三个词能正常命中。
- **根因**：`plainto_tsquery` 把分词串按 **AND** 连接，要求文档包含**每一个**词。整句分词含"几级"，而知识卡文档里没有"几级"二字 → 所有文档被一个虚词一票否决。用户自然语言提问必然带疑问词（什么/几级/怎么），此路径必踩。
- **修复**：两级——① 停用词过滤（疑问词/虚词不进查询，`_tokenize` + `STOPWORDS`）；② AND 空结果自动降级 OR（`to_tsquery` 构造 `词 | 词`，`ts_rank_cd` 排序仍偏向命中词多的文档）。
- **规律**：倒排索引的 AND 匹配是"全有或全无"语义，任何一颗老鼠屎（无区分度的虚词）都会毁掉整锅查询；自然语言进全文检索前必须过停用词，并保留宽松降级路径。

## 12. Docker Compose 卷名自动加项目前缀，与 docker run 的同名卷不是同一个（2026-08-26 实测）

- **现象**：compose 部署后 users 表"丢了"（登录失败），进容器一查只有旧数据——`docker run -v weather_pgdata:...` 与 `docker compose`（ volumes 定义 `weather_pgdata`）挂的是**两个不同的卷**（compose 实际创建 `rag_weather_pgdata`）。
- **根因**：Compose 会对**非 external** 的卷名自动加 `<项目名>_` 前缀做隔离；同名裸卷不会被接管而是新建空卷。
- **修复**：卷声明加 `external: true` + `name: weather_pgdata` 显式接管现有卷。
- **规律**：从 docker run 迁到 compose 时，逐个 `docker volume inspect` 确认挂载点；"数据丢了"先查卷名前缀再怀疑数据。

## 13. 误删生产数据卷：不可逆操作前必须盘点目标（2026-08-26，真实事故）

- **现象**：清理 compose 前缀空卷时，把 `weather_pgdata18` 一并删除——它是 PG18 迁移后的**生产数据卷**（2675 条知识块），名字像残留实际是在用。
- **根因**：只凭名字猜测卷的用途（"带 18 后缀=升级残留"），没有先确认它是否被任何容器引用、里面是什么数据；删卷操作不可逆且无备份（8.3MB 备份是 844 条时代的）。
- **恢复**：语料源（datas/）与幂等管线完整——重跑 ingest（BGE-M3 重嵌入 2675 次）+ 图谱重抽（168 块 LLM）+ 用户重注册，约 10 分钟完整重建。**教训三层**：①删任何卷/容器前先 `docker ps -a --filter volume=<名>` 查引用 + 进卷看内容；②"名字像垃圾"不等于垃圾；③重建能力（幂等管线+源数据）是最后一道保险——这次能恢复全靠当初把管线做成可重跑的。
- **规律**：不可逆操作（rm/drop/覆盖）前，"看一眼目标"是硬性步骤，猜测不算检查。
