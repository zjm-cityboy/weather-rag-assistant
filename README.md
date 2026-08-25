# 气象 RAG 智能问答助手

基于 RAG（检索增强生成）的气象领域智能问答系统：回答科普/专业气象知识、实时天气播报、灾害预警防御指导。先内网部署验证，后续考虑上线。

## 核心架构思路：三种知识，三条通路

气象场景天然包含三种数据形态，各自走不同的技术通路——这是本项目最核心的架构设计：

| 数据类型 | 例子 | 通路 |
|---------|------|------|
| 静态知识 | 台风怎么形成、预警信号含义 | RAG（嵌入 + 向量检索） |
| 实时数据 | 今天天气、明天预报 | LLM 工具调用（天气 API，不进知识库） |
| 关联推理 | "台风红色预警该做什么"（跨多个知识点） | 知识图谱（二期，可降级） |

## 功能清单

**核心**：气象知识问答（混合检索 RAG）｜实时天气卡片+播报（工具调用）｜灾害关系链问答（知识图谱）｜多轮对话｜SSE 流式输出｜回答附来源引用｜**注册登录（bcrypt + JWT）**

**亮点**（已上线）：四路意图路由（知识/天气/图谱/闲聊）｜天气实况结构化卡片｜知识图谱力导向可视化（ECharts，随回答展示检索子图）｜回答元信息条（意图/检索块数/相关度/耗时）｜追问推荐（回答后预测 3 个后续问题）｜RAGAS 四指标评估（忠实度 0.877）

## 技术栈

```
前端   Vue 3 + Vite（原生 JS）｜Element Plus｜ECharts｜markdown-it｜EventSource
后端   FastAPI + Uvicorn｜SSE（StreamingResponse）｜Pydantic
编排   LangChain + LangGraph（意图路由状态图）
检索   pgvector 向量检索 + PG 全文检索 → RRF 融合 → Qwen3-Reranker 精排
       Neo4j 知识图谱（灾害关系链问答，LLM 三元组抽取构建，意图路由第四通路）
存储   PostgreSQL + pgvector（知识块 + 会话 + 业务数据一库）
       Neo4j 5.x 社区版（灾害预警知识图谱，890 节点/1018 关系）
模型   硅基流动 API：Chat LLM + BAAI/bge-m3（嵌入，实验 8 换代）+ Qwen3-Reranker
       （后期可切 Ollama 本地模型演示私有化部署）
数据   和风天气开发版 API（免费额度，实时天气/预报）
评估   RAGAS + 自建气象 QA 评测集（≥50 条）
可观测 Langfuse（自托管）
部署   Docker Compose（PG + Neo4j + backend + frontend）
```

**选型理由**：
- **pgvector**：气象知识库量级（万级块）远低于其性能拐点；知识、会话、用户数据同库，事务/备份/权限复用 PG 生态，运维成本最低。
- **FastAPI**：Python AI 服务事实标准，原生异步适合 SSE 流式与并发工具调用。
- **Vue3 + Element Plus + ECharts**：国内前端主流组合，图表能力契合气象可视化。
- **LangGraph**：LangChain 生态的生产级编排标准，适合多轮意图路由状态机。
- **混合检索 + Rerank**：气象问题常含专业术语与精确关键词（如具体云种、预警信号名），单路语义检索不够。

## 分期路线

| 期 | 内容 | 产出 | 状态 |
|----|------|------|------|
| 0 | 知识库构建：PDF/网页素材 → 清洗 → pgvector 入库 | 知识库 + 构建脚本 | ✅ 完成（844 块入库，冒烟检索通过） |
| 1 | FastAPI 后端：/ask 接口 + SSE + 会话记忆 + 引用溯源 + 查询改写 | 可调用的 API | ✅ 完成（多轮对话实测通过，单轮 3~4s） |
| 2 | Vue 前端：聊天界面 + SSE 流式渲染（打字机）+ 引用来源卡片 | 网页版可用 | ✅ 完成（fetch+ReadableStream 解析 SSE，Vite 代理联调） |
| 3 | LangGraph 意图路由 + 和风天气工具调用 | 天气播报功能 | ✅ 完成（三通路状态图；JWT 认证接入真实 API，实测上海 26.7°C 播报） |
| 4 | 混合检索：PG 全文检索 + 三路 RRF + Reranker 精排 | 检索质量升级 | ✅ 完成（消融实验 hit@1 3/5→5/5，见 docs/hybrid-search.md） |
| 5 | Neo4j 灾害预警知识图谱（LLM 三元组抽取 + 多跳查询 + 意图路由第四通路） | 关系链问答 | ✅ 完成（890 节点/1018 关系；关系类问题检索快 ~35 倍，见 docs/knowledge-graph.md） |
| 6 | RAGAS 评估 + 注册登录 + Docker Compose 部署 | 工程闭环 + 效果报告 | ✅ 完成（四指标评估忠实度 0.877；bcrypt+JWT 登录；compose 一键部署。Langfuse 未引入：单人项目评估+日志已覆盖，自托管观测栈超出必要复杂度） |

## 量化优化记录

> 每一项优化都有基线对照与计算依据，实验脚本在 `scripts/experiments/`、
> 原始数据与计算细节在 `docs/experiments.md` 与 `docs/pitfalls.md`。

| 优化项 | 基线 → 优化后 | 量化提升 | 计算依据 |
|--------|--------------|---------|---------|
| 数据清洗管线（实验 1） | 噪音块率 16.2% → 0% | 噪音块 100% 清除；断行密度 4.35 → 0.43 换行/百字（降 90.1%） | 同一 PDF 分清洗前/后两组切块入库，固定 4 个问题各取 top-1 余弦相似度，B 组平均高 +0.0236（4 问全部正向） |
| HNSW 向量索引（实验 3） | 检索延迟 12.49ms → 0.444ms | **快 28.1 倍**（延迟降 96.4% = (12.49−0.444)/12.49） | `EXPLAIN ANALYZE` 同一查询建索引前后对比；top-3 结果与精确扫描完全一致（无损） |
| 双语查询 + RRF（实验 4） | 中文问"台风怎么形成"：台风章 Ch16 在 top-10 占 0 席 → top-5 占 2 席 | 0 → 2 席（从召回不到到可用） | 检索结果按 source 前缀（Ch16-TropCycl）计数，修复前后对照 |
| 混合检索 + 精排（实验 5） | hit@1：三路 RRF 3/5 (60%) → 加精排 5/5 (100%) | **top-1 准确率绝对 +40 个百分点，相对提升 66.7%**（(5−3)/3）；对比纯向量基线 4/5 (80%) 为 +20pp；hit@5 18→22（相对 +22.2%） | 固定 5 题问题集（术语精确/语义改写/跨语言），每题人工预设期望关键词，统计各配置 top-5 命中块数与 top-1 命中数 |
| 知识图谱 vs 向量（实验 6） | 关系类问题素材覆盖 19/21 → 20/21；检索延迟 1.39s → 0.04s | **覆盖 +5.3%**（(20−19)/19）；**延迟降 97.1%（快 ~35 倍）**，图谱为本地索引遍历、向量路含 3 次网络往返 | 5 个关系类问题（次生灾害/防御措施），期望实体按图谱实体表述校准后做子串计数；耗时单次实测 |
| RAGAS 系统评估（实验 7→8） | 忠实度 **0.877 → 0.920**（+4.3pp）/ 相关性 0.679 / 上下文精确率 0.404 / 召回率 0.395（20 题均值） | 幻觉率 12%→8%；context 两项归因评估口径语言错位（中文参考答案判英文块覆盖保守，检索命中已由 Ch16 命中数 Hit@10 0→6 证实） | ragas 0.4 LLM-as-judge：20 题固定评测集，线上同链路生成，evaluator 独立 LLM 实例取均值 |
| 嵌入模型换代（实验 8） | Qwen3-Embedding-0.6B → **BAAI/bge-m3**（业界多语检索标杆） | 中文问台风英文 Ch16 命中数（Hit@10）**0/10 → 6/10**；忠实度 +4.3pp；重嵌入成本一次性 2675 调用/0.5 分钟 | 换模型后全库重嵌入（scripts/db/reembed_all.py 幂等），检索两侧同源；跨语相似度冒烟测试 0.611 vs 0.507 |
| 思考模式关闭（pitfalls 9） | 流式首字节 29~37s → 0.5s | **降 98.6%（快 74 倍）**（(37−0.5)/37） | `tests/smoke_llm_stream.py` A/B 对照：同一 prompt 思考开/关各计时 |

优化总结：**清洗让知识库干净（噪音块 −100%）、HNSW 让检索快 28 倍、双语 RRF 让英文教材可被中文问题召回（0→2 命中）、混合检索+精排让 top-1 准确率提升 66.7%、知识图谱让关系类问题检索快 ~35 倍、关思考让首字节快 74 倍、RAGAS 评估系统忠实度 0.877——每个数字都有实验脚本和计算公式可复现。**

## 知识库数据来源

- 气象书籍 PDF（内网学习用途；公开上线前需处理版权）
- 官方科普站点：中国天气网科普频道、中国气象局官网、《气象知识》杂志公开内容、WMO 公开资料
- 入库前人工筛选质量（Garbage In, Garbage Out）

## 数据库结构与访问（PostgreSQL + pgvector）

### 服务部署：weather-pg 容器

数据库跑在 Docker 容器里，不在宿主机安装任何 PG：

```
docker run -d --name weather-pg \
  -e POSTGRES_PASSWORD=weather_dev_2026 \
  -e POSTGRES_DB=weather \
  -p 5432:5432 \
  -v weather_pgdata:/var/lib/postgresql \
  pgvector/pgvector:pg18
```

| 要点 | 值 | 说明 |
|------|----|------|
| 镜像 | pgvector/pgvector:pg18 | PG18 + pgvector 扩展（检索依赖） |
| 端口 | 宿主 5432 → 容器 5432 | 应用/客户端统一连 `localhost:5432` |
| 数据卷 | weather_pgdata | 数据持久化在卷里，容器删了数据还在 |
| 挂载点 | /var/lib/postgresql | ⚠️ PG18 起必须挂父目录，旧的 /data 子路径拒绝启动（见 docs/pitfalls.md） |

### 服务部署：weather-neo4j 容器（第 5 期知识图谱）

```
docker run -d --name weather-neo4j \
  -e NEO4J_AUTH=neo4j/weather_graph_2026 \
  -p 7474:7474 -p 7687:7687 \
  -v weather_neo4jdata:/data \
  neo4j:5.26-community
```

| 要点 | 值 | 说明 |
|------|----|------|
| 端口 | 7474（HTTP 浏览器）/ 7687（Bolt 协议） | 浏览器开 http://localhost:7474 可可视化图谱 |
| 应用连接 | bolt://localhost:7687 | 密码在 backend/.env 的 NEO4J_PASSWORD |
| 数据卷 | weather_neo4jdata | 数据持久化；图谱重建跑 scripts/data/build_knowledge_graph.py（MERGE 幂等可重跑） |
| 密码 | weather_dev_2026 | 本地开发密码；对外部署必须更换 |

### 表结构：weather.knowledge_chunks

当前 public 模式下唯一的业务表，844 条知识块：

| 列 | 类型 | 约束/默认 | 说明 |
|----|------|-----------|------|
| id | BIGINT | 主键，自增 | |
| content | TEXT | NOT NULL | 知识块正文 |
| source | TEXT | | 来源（PDF 文件名 / web:网页标题） |
| page | INTEGER | | PDF 页码；网页条目为 0 |
| content_type | TEXT | | text（PDF直抽）/ ocr（扫描页）/ web（爬虫）/ web_llm（生成知识卡） |
| embedding | VECTOR(1024) | NOT NULL | BAAI/bge-m3 嵌入向量（实验 8 前为 Qwen3-Embedding-0.6B，同 1024 维） |
| created_at | TIMESTAMP | DEFAULT now() | 入库时间 |
| url | TEXT | | 网页来源的原始链接；PDF 来源为空字符串 |
| content_hash | TEXT | NOT NULL, UNIQUE | 内容 md5 哈希，exact dedup（数据库层去重约束） |

约束共 7 条（含 CHECK page>=0），索引 4 个：主键 B-tree(id)、唯一 B-tree(content_hash)、B-tree(source)、**HNSW(embedding, vector_cosine_ops, m=16, ef_construction=64)**——向量检索实测 12.49ms → 0.44ms（28 倍）且 top-3 与精确扫描完全一致（docs/experiments.md 实验 3）。数据分布：英文教材 6 章 2245 块（text）+ 网页/知识卡 398 块（web 253 / web_llm 145，14 张预警卡齐全）+ 公报 OCR 32 块，共 2675 条。检索为双语两路（中/英查询）+ RRF 融合，解决跨语言召回（实验 4）。

### 访问方式

**① 命令行（容器内自带的 psql 客户端，宿主机无需安装）**：

```
docker exec -it weather-pg psql -U postgres -d weather
```

逐段拆解：`exec` 在容器里执行命令；`-it` 保持交互终端；`weather-pg` 目标容器；`psql` PG 命令行客户端；`-U postgres` 以哪个用户登录；`-d weather` 连哪个库。进入后提示符为 `weather=#`，SQL 以分号结尾执行；`\dt` 列出所有表，`\d 表名` 看表结构，`\q` 退出。脚本场景可去掉 `-it` 改用 `-c '\dt'` 一次性执行后退出。

**② VSCode PostgreSQL 扩展（GUI）**：Host `localhost` / Port `5432` / User `postgres` / Password 同上 / Database `weather`，支持表数据浏览与 SQL 编辑器（Ctrl+Shift+P → PostgreSQL: New Query）。

**③ Python（psycopg2 参数绑定）**：入库脚本 `scripts/data/ingest_to_pgvector.py` 即此方式，检索接口沿用。

**④ 备份**（pg_dump 全量导出，恢复用 `psql weather < 备份文件`）：

```
docker exec weather-pg pg_dump -U postgres weather > backups/weather_backup_日期.sql
```

## 目录结构

```
气象RAG智能问答助手/
├── README.md          # 本文件
├── AGENTS.md          # 开发规范（仅本项目生效）
├── docs/              # 踩坑登记 pitfall 与对照实验记录
├── datas/             # 原始知识库素材
│   ├── books_pdf/     # 气象书籍 PDF
│   ├── processed/     # 清洗后的页级 JSONL（pages.jsonl）
│   └── web_pages/     # 科普网页抓取结果（含溯源 sources.txt）
├── scripts/           # 工具脚本（按职责分类）
│   ├── data/          # 数据管线：爬取 → PDF 构建/清洗 → 入库（按数据流顺序）
│   ├── db/            # 数据库管理：初始化/建表/约束/索引（幂等）与后续迁移脚本
│   ├── tools/         # 独立小工具（图片拼 PDF 等）
│   ├── experiments/   # 对照实验脚本（产出 docs/experiments.md 数据）
│   └── tests/         # 冒烟测试脚本（前提条件/一致性/接口连通性检查）
├── backend/           # FastAPI 后端
│   └── app/           # api/ core/ rag/ memory/ graph/（LangGraph 路由）/ weather/（和风客户端）
└── frontend/          # Vue 3 前端（Vite + Element Plus + markdown-it）
    └── src/           # App.vue 聊天界面 + api/ask.js SSE 客户端
```

**本地启动**见下方「快速开始」。

## 快速开始（完整启动流程）

### 0. 前置条件

| 依赖 | 说明 |
|------|------|
| Python 3.10+ | 后端运行时（建议 conda 独立环境） |
| Node.js 20+ | 前端构建 |
| Docker Desktop | 跑 PostgreSQL/Neo4j 两个数据库容器，安装后保持运行 |
| 硅基流动 API Key | 对话/嵌入/Reranker 三类模型共用一个 key（siliconflow.cn 注册） |
| 和风天气凭据 | 可选：不配置时天气路自动降级为演示数据（回答会标注【演示数据】） |

### 1. 安装依赖

```bash
# 后端（项目根目录）
pip install -r backend/requirements.txt

# 前端
cd frontend && npm install && cd ..
```

### 2. 配置密钥（backend/.env）

```bash
cp backend/.env.example backend/.env    # 然后按下表填写
```

| 配置项 | 必填 | 说明 |
|--------|------|------|
| API_KEY / API_BASE_URL / MODEL_NAME | ✅ | 硅基流动模型服务（对话+嵌入+Reranker） |
| NEO4J_PASSWORD | ✅ | 与第 3 步创建 Neo4j 容器时设置的密码一致 |
| JWT_SECRET | ✅ | 登录密钥，生成随机串：`python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| QWEATHER_CREDENTIAL_ID / DEV_ID / PROJECT_ID / HOST / PRIVATE_KEY_PATH | 可选 | 和风天气 JWT 五要素；私钥文件（Ed25519）放到 `backend/keys/`（目录已 gitignore） |

`.env` 与 `keys/` 均不入 git；密钥永不进代码。

### 3. 启动数据库容器（PostgreSQL + Neo4j）

```bash
# pgvector（知识库向量存储）
docker run -d --name weather-pg \
  -e POSTGRES_PASSWORD=weather_dev_2026 -e POSTGRES_DB=weather \
  -p 5432:5432 -v weather_pgdata:/var/lib/postgresql \
  pgvector/pgvector:pg18

# Neo4j（灾害知识图谱；密码自定，与 .env 的 NEO4J_PASSWORD 一致）
docker run -d --name weather-neo4j \
  -e NEO4J_AUTH=neo4j/你的neo4j密码 \
  -p 7474:7474 -p 7687:7687 \
  -v weather_neo4jdata:/data \
  neo4j:5.26-community
```

数据持久化在命名卷里（容器删了数据还在）；浏览器开 http://localhost:7474 可视化图谱。

### 4. 初始化数据（全部幂等，可重复执行）

```bash
# ① 建库/建表/约束/索引
python scripts/db/init_db.py

# ② 知识块清洗+分块+嵌入+入库（2675 块；调嵌入 API，约几分钟）
python scripts/data/ingest_to_pgvector.py

# ③ 灾害知识图谱构建（LLM 抽取三元组 → Neo4j，约 4 分钟，有逐块进度）
python scripts/data/build_knowledge_graph.py

# ④ 冒烟验证（哈希一致性 / 图谱链路）
python scripts/tests/smoke_hash_consistency.py
python scripts/tests/smoke_knowledge_graph.py
```

### 5. 启动服务（两个终端）

```bash
# 后端（终端 1；接口文档 http://localhost:8000/docs）
cd backend && uvicorn app.main:app --port 8000

# 前端（终端 2；5173 被占会自动切 5174）
cd frontend && npm run dev
```

### 6. 访问与验证

1. 浏览器开 **http://localhost:5173** → 注册账号（自动登录）
2. 四路各问一句验证：`台风是怎么形成的`（知识+引用）/ `北京今天多少度`（天气卡片）/ `台风会引发哪些次生灾害`（图谱可视化）/ `你能做什么`（闲聊）
3. 回答完成后有追问推荐胶囊（点击连续对话）

### 路线 B：Docker Compose 一键部署（数据已在卷里时最快）

```bash
# 首次部署前，移除手动创建的同名容器（数据在命名卷里不会丢）
docker rm -f weather-pg weather-neo4j

# 构建并启动全部四个服务（pg / neo4j / backend / frontend）
docker compose up -d --build

# 访问 http://localhost:5173（nginx 托管前端，/api 反代后端，SSE 已关缓冲）
```

| 要点 | 说明 |
|------|------|
| 数据卷复用 | `weather_pgdata` / `weather_neo4jdata` 与手动部署同名，已有知识块/图谱直接复用 |
| 表结构自建 | backend 容器启动命令先跑 `init_db.py`（幂等）再起服务 |
| 密钥注入 | 凭据经 `backend/.env`（env_file）与 `keys/` 只读挂载进容器，不进镜像 |
| 容器内寻址 | compose 文件里覆盖为服务名：`PG_DSN=host=db`、`NEO4J_URI=bolt://graph` |
| 全新环境 | 知识块/图谱重建命令同上「4. 初始化数据」（需模型 API） |

### 常见问题

- **前端 5173 打不开**：端口被占时 Vite 自动切 5174（后端 CORS 已包含两个端口）
- **数据库连不上**：Docker Desktop 未启动或容器停了——启动 Docker Desktop 后 `docker start weather-pg weather-neo4j`
- **Windows Git Bash 下 curl 测接口中文报错**：中文 JSON 写入 UTF-8 文件再 `-d @file`（终端直接传会编码错乱）
