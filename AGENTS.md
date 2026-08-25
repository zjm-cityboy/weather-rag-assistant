# AGENTS.md — 气象RAG智能问答助手 · 开发规范

> 本文件是本项目所有开发行为的最高约定，**仅对「气象RAG智能问答助手」项目生效**（AI 协作与人工开发均适用）。与本项目无关的内容不适用本规范。
>
> 制定参考：[PEP 8](https://github.com/tedyli/PEP8-Style-Guide-for-Python-Code) 与 Google 开源风格指南（Python 篇）、[腾讯代码安全指南 secguide](https://github.com/tencent/secguide)（安全编码思想）、[腾讯 IMWEB 前端规范](https://imweb.github.io/rule/)、[京东凹凸实验室前端规范](https://jdf2e.github.io/jdc_fe_guide/docs/index/)、字节跳动 Semi Design 组件规范理念、Conventional Commits。
>
> 规范原则：**每条都必须可执行；宁可少而严格，不要多而落空。**

---

## 0. 问题解决方法论（首要原则：先查业界，再动手）

遇到任何常见问题（性能优化、检索质量、架构选型、依赖冲突、报错排查）时，**第一动作是查业界标准方案，不是自己发明**：

1. **先调研**：联网查官方文档、大厂工程实践（Microsoft/NVIDIA/Google 等技术博客）、开源项目同类实现。工程问题优先采用业界验证过的标准解法。
2. **资源账先行**：对比候选方案的成本（API 调用次数/延迟/维护负担）——"解决了但太耗资源"同样是坏方案。一次性成本与持续成本分开算（反例：给每块语料生成 LLM 摘要 vs 换多语言嵌入模型，资源差一个量级）。
3. **业界确无方案才自创**：此时推导逻辑必须严谨可论证，并在文档中说明"为什么业界方案不适用"。
4. **落地必须量化闭环**：方案实施后复测对比（实验记录进 `docs/experiments.md`），没有前后数据的改进不算完成。

> 实例（本项目实验 8）：RAG 跨语言检索差 → 调研结论"换强多语言嵌入模型是根本解，查询翻译是过渡方案" → 换 BAAI/bge-m3 重嵌入 → Ch16 命中数（Hit@10）0/10→6/10、忠实度 +4.3pp，全程零自创组件。

---

## 1. 项目结构规范

| 目录 | 职责 | 约束 |
|------|------|------|
| `datas/books_pdf/` | 原始书籍 PDF | 只放原始素材，不加工 |
| `datas/web_pages/` | 抓取的网页原文 | 保留原始格式 + 来源 URL 记录 |
| `scripts/data/` | 数据管线脚本（按数据流：爬取 → 构建/清洗 → 入库） | 一次性数据脚本也放这，不混入 backend |
| `scripts/db/` | 数据库管理（初始化、迁移脚本按日期编号） | 全部幂等，可重复执行 |
| `scripts/tools/` | 独立小工具（不属于管线的一次性工具） | 命令行参数即文档 |
| `scripts/experiments/` | 对照实验脚本 | 产出数据必须落 `docs/experiments.md` |
| `scripts/tests/` | 冒烟测试脚本（smoke test：前提条件/一致性/外部接口连通性检查） | 可重复运行，输出结论明确 |
| `backend/` | FastAPI 应用 | 只放服务代码，按 `app/` 子结构组织 |
| `frontend/` | Vue 3 应用 | Vite 脚手架原生命名 |
| `docs/` | 项目文档（架构图、选型记录、实验数据） | 实验数据必须落文档 |
| `evals/` | 评测集与评估结果 | 评测集改动要有版本记录 |

**新增文件落位规则**：跑批/清洗/入库 → `scripts/data/`；建库/迁移 → `scripts/db/`；验证/实验 → `scripts/tests/`、`scripts/experiments/`；线上接口 → `backend/`；页面/组件 → `frontend/`；拿不准放哪 → 先放 `scripts/tools/`，迁走时用 git mv 保留历史。

**禁止**：在项目根目录散放 py/js 文件；把中间产物（向量库、缓存）提交进 git（写 `.gitignore`）。

---

## 2. Python 后端规范（backend/ 与 scripts/）

### 2.1 命名（PEP 8）

- 模块/文件/函数/变量：`snake_case`（如 `build_knowledge_base.py`、`get_weather`）
- 类：`PascalCase`（如 `WeatherService`）
- 常量：`UPPER_SNAKE_CASE`（如 `CHUNK_SIZE = 500`，数字必须带注释说明单位与取值原因）
- 布尔变量/函数：`is_`/`has_` 前缀（`is_raining`）
- **禁止**：拼音命名、无意义缩写（`a`、`tmp1`）、中英混杂

### 2.2 import 三段式（每组之间空一行）

```python
# 1. 标准库
import os
from pathlib import Path

# 2. 第三方库
from fastapi import FastAPI
from langchain_openai import ChatOpenAI

# 3. 本项目模块
from app.config import settings
```

### 2.3 类型标注（FastAPI/Pydantic 生态强制）

公开函数（会被别的模块调用/成为 API 层的）必须标注参数与返回类型；模块内部私有函数可省略。

```python
def format_docs(results: list[tuple[Document, float]]) -> str:
```

### 2.4 docstring 与注释

- 每个文件头写"职责说明块"（它是什么 / 在整个项目中的位置 / 怎么运行）
- 每个公开函数写 docstring：一句话职责 + 参数 + 返回值（延续现有中文注释风格）
- 注释解释**为什么**（Why），不复述代码在做什么（What）
- 魔法数字一律提取为带注释的常量（延续现有习惯）

### 2.5 异常与健壮性（腾讯 secguide 精神）

- 外部调用（LLM API / 天气 API / 数据库）必须设置 **timeout**，禁止无限等待
- 外部调用失败要有处理路径：重试 / 降级 / 明确报错，不允许裸 `except: pass`
- 捕获异常时用具体异常类型，日志要带上下文（哪一步、什么输入、什么错）

### 2.6 禁止硬编码（安全红线）

- API 密钥、数据库连接串、模型名 → 一律 `.env` + `os.getenv()`，**绝不写进代码/注释/笔记/截图**
- `.env` 模板提交 `.env.example`（只有键名没有值），真 `.env` 进 `.gitignore`
- 路径 → `pathlib.Path(__file__)` 相对定位，禁止绝对路径（`D:\...`）
- SQL 一律参数绑定，禁止 f-string 拼接 SQL

### 2.7 格式化工具

统一用 **ruff**（Python lint + format 二合一，业界主流）：`ruff check` 查、`ruff format` 格式化。提交前跑一遍。

---

## 3. 前端规范（frontend/，Vue 3）

- 组件文件名 `PascalCase.vue`（`ChatMessage.vue`、`WeatherCard.vue`）
- 一律组合式 API + `<script setup>` 语法糖，不写 Options API
- 目录组织：`views/`（页面）、`components/`（组件）、`api/`（后端接口封装）、`composables/`（可复用逻辑，`useXxx` 命名）
- **所有后端调用统一封装在 `api/` 层**，组件里禁止直接 fetch（换接口/加鉴权只改一处）
- 可复用状态逻辑优先抽 `composables/`，本项目**不引入** Pinia、TypeScript、Vue Router（单页 + 简单条件渲染足够；此为学习边界约定，非技术否定）
- ECharts 配置抽成独立文件（`charts/temperatureOption.js`），不堆在组件里

---

## 4. Git 规范

### 4.1 提交信息（Conventional Commits，大厂通行）

```
<type>: <一句话中文说明>

type 取值：
feat      新功能          fix       修复缺陷
docs      文档            refactor  重构（不改功能）
test      测试            chore     构建/工具/杂务
perf      性能优化        exp       实验代码（冒烟测试/对照实验）
```

示例：`feat: 气象知识问答接口支持 SSE 流式输出`、`exp: rerank 前后召回率对照实验脚本`

### 4.2 分支模型（单人项目简化版）

- `main`：随时可运行的稳定版（演示/发布用）
- `feature/xxx`：每期开发分支（`feature/pg-vector-ingest`），完成合并回 main
- 一次提交只做一件事；冒烟测试/实验脚本单独提交并标 `exp:`

---

## 5. AI / RAG 工程专项规范（本项目特色）

### 5.1 模型调用

- 所有嵌入调用保留 `check_embedding_ctx_length=False`（第三方接口已实测的坑，禁止移除）
- LLM 调用必设 `timeout` 与 `max_tokens`；temperature 按场景注释选值（问答 0.1~0.3 / 播报 0.7）
- 有成本意识：批量嵌入前先估算条数；已入库的数据不重复嵌入（入库前 `db.get()` 或 content-hash 去重检查）

### 5.2 已踩坑登记制（持续维护）

每次踩坑（报错排查、静默劣化、性能意外）必须在 `docs/pitfalls.md` 登记：现象 → 根因 → 修复 → 一句话规律。**登记后同类别错误不得再犯**——这是比规范本身更重要的经验资产。

### 5.3 冒烟测试要求（延续既有习惯）

每个新模块/新环节必须带可运行的验证输出（数量、类型、预览、分数），不允许"跑完没报错就算通"；对外部接口/关键链路的连通性验证脚本放 `scripts/tests/`（命名 `smoke_*.py`）。关键模块（检索、路由、rerank）额外保留对照实验脚本进 `scripts/experiments/`，实验数据进 `docs/`。

### 5.4 数据库

- pgvector 表结构变更写迁移脚本（`scripts/db/` 按日期编号），不直接手改生产表
- 知识库重建 = 删集合/表 → 全量重入，禁止在旧数据上补插（防重复污染）

---

## 6. 文档同步规范

### 6.1 文档先行（新功能开发流程）

开发新功能**前**，先让 AI 产出设计文档（放 `docs/design/`，一功能一文件）：流程设计、表结构、接口定义、异常处理、安全措施。**人工审查文档通过后才开始写代码**；开发完成后回写文档（实际实现与设计的差异）。文档不是给人看的注释，是给下一次开发（无论换什么 AI 工具）的接手蓝图——先审查设计再审查代码，分层把关。

### 6.2 文档同步

- 每期完成后：更新 `README.md` 的进度表 + `docs/` 补该期架构/实验记录
- 学习笔记（RAG实战流程.md）同步该期技术要点与踩坑，遵守笔记既有的独立性约定
- 对外文档（README/docs）不出现：密钥、本机绝对路径、内部文件夹名

---

## 7. 数据安全与不可逆操作规范（AI 协作安全绳）

> 来源：Vibe Coding 避坑实践 + 本项目误删数据卷事故（pitfalls 第 13 条）的教训固化。
> 核心原则：**Git 是代码的恢复点，备份是数据的恢复点；AI 加速之前先系安全绳。**

### 7.1 不可逆操作前置三问（强制）

删除卷/表/目录、TRUNCATE、DROP、覆盖写等操作执行前，**必须**依次确认：

1. **有没有备份？**——没有先备份（`pg_dump` / `docker run --rm -v 卷` 打包）
2. **能不能回滚？**——想清楚回滚路径；不能回滚的操作要用户明确确认
3. **操作的是不是在用的目标？**——`docker ps -a --filter volume=<名>` 查引用 + 进卷看内容。
   **"名字像垃圾"不等于垃圾**（本项目 weather_pgdata18 名字像升级残留，实际是生产数据卷）

### 7.2 备份制度化

- 数据量显著变化后（大批入库 / 换嵌入模型 / 迁移前）**立即** `pg_dump`，文件名带日期入 `backups/`
- 备份不是"上线前才想"——它是承认线上一定会出错的预案
- 恢复能力双重保险：备份 + 幂等重建管线（本项目事故实际靠后者 15 分钟恢复）

### 7.3 环境差异先行（部署/容器前）

写部署配置（Dockerfile/compose）前，先列**本地 vs 目标环境差异表**：数据库 host、配置来源、依赖清单、卷挂载、运行用户。本地能跑≠容器能跑（本项目容器首启三连炸的教训：PGHOST 未配 / requirements 漏依赖 / Compose 卷名前缀）。

### 7.4 AI 大规模改动的边界

- 大改前确认 `git status` 干净（不干净先提交或暂存）——**没有恢复点不开始大改**
- 改动后先 `git diff` 审查再验证再提交；禁止在一堆未提交改动上继续叠新需求
- 数据库操作只让 AI **生成脚本和风险说明**，高危命令人工确认后执行

## 8. 提交前检查清单（Checklist）

1. [ ] 密钥/绝对路径没有出现在代码、注释、文档中
2. [ ] 新增文件放对了目录，根目录没有散落文件
3. [ ] 魔法数字已提取为带注释的常量
4. [ ] 外部调用有 timeout 和失败处理
5. [ ] 新模块带验证输出（冒烟测试）
6. [ ] SQL 参数绑定、无 f-string 拼接
7. [ ] `ruff check` 通过
8. [ ] commit message 符合 Conventional Commits
9. [ ] README/docs 与代码状态一致
10. [ ] 踩过的坑已登记 `docs/pitfalls.md`
11. [ ] 涉及删数据/删卷/删目录的操作已过"前置三问"（7.1）
12. [ ] 大规模改动前 git status 干净，改动已 diff 审查（7.4）
