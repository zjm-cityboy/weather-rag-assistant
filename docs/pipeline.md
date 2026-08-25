# 知识库管线设计说明（pipeline.md）

> 代码注释只保留实现说明，设计依据与流程讲解集中在本文件。
> 坑的完整记录见 pitfalls.md，量化数据见 experiments.md。

## 数据流七步总览

| 步骤 | 做什么 | 输入 → 输出 | 代码位置 |
|------|--------|------------|----------|
| 1. 爬取数据 | 爬中国天气网科普文章（带 URL 溯源） | 入口页 → web_pages.jsonl + sources.txt + raw_html/ | `scripts/data/crawl_weather_articles.py` |
| 2. 加载数据 | PDF 逐页文本层检测：文字页直抽，扫描页 OCR（带缓存） | books_pdf/*.pdf → pages.jsonl（页级） | `scripts/data/build_knowledge_base.py` |
| 3. 清洗数据 | two-pass boilerplate 检测 + 六环节清洗；PDF 在步骤 2 内联完成，网页在入库管线内完成 | 原始文本 → 干净文本 | `scripts/data/cleaner.py`（两处共用） |
| 4. 数据分块 | 递归字符分块（500 字/重叠 50，中文标点分隔符）+ 长度过滤 | 99 条语料 → 844 块 | `scripts/data/ingest_to_pgvector.py` 步骤 3 |
| 5. 数据嵌入 | Qwen3-Embedding-0.6B，64 条/批（嵌入前先 content-hash 精确去重） | 844 块 → 844×1024 维向量 | `scripts/data/ingest_to_pgvector.py` 步骤 4~5 |
| 6. 建立向量数据库 | 建库、pgvector 扩展、表、约束、HNSW 索引（全部幂等可重跑） | — → weather.knowledge_chunks | `scripts/db/init_db.py` |
| 7. 向量入库 | 参数绑定批量 INSERT + ON CONFLICT 兜底 | 块+向量 → 表内 844 条 | `scripts/data/ingest_to_pgvector.py` 步骤 6 |
| 8. 验证 | 三问检索冒烟（<=> 余弦距离） | 查询 → top-3 命中检查 | `scripts/data/ingest_to_pgvector.py` 步骤 7 |

```
中国天气网 ──①爬取──▶ web_pages.jsonl ──────────────────┐
                                                        ├─③清洗(网页)─┐
书籍 PDF ──②加载──▶ pages.jsonl（内联③清洗）───────────┴──────────────┤
                                                                      ├─④分块→⑤去重+嵌入→⑦入库─▶ knowledge_chunks
                                          ⑥建库/表/约束/索引（init_db）─┘
                                                                     ⑧检索冒烟 ←─┘
```

**执行顺序**：`data/crawl_weather_articles.py` → `data/build_knowledge_base.py`
→ `db/init_db.py` → `data/ingest_to_pgvector.py`（均在 scripts/ 下，每个脚本幂等可重复）。

**`data/ingest_to_pgvector.py` 为线性脚本，自上而下即数据流**——七个分段注释块
（步骤 1 加载 → 步骤 2 清洗(网页) → 步骤 3 分块 → 步骤 4 去重 →
步骤 5 嵌入 → 步骤 6 入库 → 步骤 7 检索冒烟），每段开头注明输入→输出，
段内关键行带简要行尾注释；除 `get_embeddings()`（嵌入与冒烟两处复用）外
不做函数封装，顺序读代码即顺序读流程。

辅助脚本：`tools/images_to_pdf.py`（图片拼 PDF）、`experiments/exp_clean_vs_raw.py`
（清洗对照实验）、`tests/smoke_hash_consistency.py`（Python/PG md5 一致性冒烟测试）。

## 关键设计决策

### 1. 基于文本层检测的 OCR 路由（不做全量 OCR）

每页先 `get_text()` 直抽，文字量 ≥ 50 字符判定为文字页直接使用（无损、毫秒级）；
不足则判定扫描/图表页，渲染 PNG 走 DeepSeek-OCR（有损、秒级、耗免费额度）。
理由：对文字页过 OCR 等于把无损数据变有损。OCR 结果落盘缓存，
重跑不重复调 API；缓存孤儿随源文件删除而清理。

### 2. Two-pass 清洗（先统计后清洗）

第一遍跨页/跨文章统计短行的文档频率：出现在 ≥ 30% 文档中的行
= 页眉/页脚/版权/同站导航等模板行（boilerplate）。第二遍逐文档执行六环节：
NFKC 规范化 → 剔模板行/页码行 → 断行合并（中文按句末标点，英文含连字符断词还原）
→ 空白压缩 → 低质量过滤（有效字符占比 < 50% 判废）→ 报告累计。
相比硬编码特定书的版权行，统计式检测对任意语料通用。

### 3. 分块放在入库脚本，且 PDF 与网页统一处理

清洗的单位是"页"（pages.jsonl 一页一条，保留页码溯源），入库的单位才是"块"。
先清洗后切块：脏数据（页眉/断行）若先切碎就混进块内无法识别；
最短长度过滤（< 50 字）放在切完后兜底。

分块覆盖全部语料，不只 PDF：分块步骤的输入同时包含 pages.jsonl
与清洗后的 web_pages.jsonl。数据佐证：原始网页语料 36 篇，
入库后 web 类 249 块（平均每篇 6~7 块）。

### 4. 幂等入库的两级实现（application-level + DB-level）

- 应用层（pre-check）：每块计算内容哈希（content hash，md5），嵌入**之前**
  与库内 content_hash 集合比对，已存在块直接跳过——不产生嵌入 API 调用；
- DB 层（兜底）：INSERT 带 `ON CONFLICT (content_hash) DO NOTHING`，
  配合 UNIQUE 约束，并发/边界场景下冲突行静默跳过。

Python `hashlib.md5` 与 PG `md5()` 对同一文本产出一致
（smoke_hash_consistency.py 验证 5/5），两侧哈希算法一致。
新增语料时直接重跑入库脚本即可增量入库，无需 TRUNCATE 重建。

### 5. check_embedding_ctx_length=False

OpenAIEmbeddings 默认先用 tiktoken 把文本编码为 token ID 再发给嵌入接口——
这是 OpenAI 官方接口的行为；第三方兼容接口收到 token 序列会产生语义劣化
（中文语料实测区分度 0.30 → 0.04，静默发生）。设为 False 后直接发送原始文本。
本参数禁止移除（pitfalls 第 1 条）。

### 6. 检索度量与索引选型

- 度量：余弦距离 `<=>`（= 1 − 余弦相似度，越小越相似）。
  文本嵌入看语义方向不看模长，且 Qwen3-Embedding 按余弦训练评测，
  全链路（Chroma 实验期 → pgvector）统一余弦，实验数据可比。
- 索引：HNSW（`vector_cosine_ops`，m=16，ef_construction=64）。
  844 块实测 12.49ms → 0.44ms（28 倍）且 top-3 与精确扫描完全一致
  （experiments.md 实验 3）。
- 匹配规则：查询操作符必须与索引 opclass 一致——用 `<->`（欧氏）查询会
  退回 Seq Scan（实测验证）。HNSW 是近似索引，精度旋钮为 `hnsw.ef_search`
  （默认 40），数据量增大后按"top-k 一致性比对"重标定。

### 7. init_db.py 全幂等 DDL

库（查 pg_database 后建）、表/扩展（IF NOT EXISTS）、索引（IF NOT EXISTS）、
约束（SET NOT NULL 天然幂等；ADD CONSTRAINT 用 DO $$ 异常吞并）。
新环境跑一次即建齐，已有环境重复执行零副作用。
坑：约束重名抛的异常是 `duplicate_table`（42P07）而非 `duplicate_object`，
EXCEPTION 分支两者都要捕获（pitfalls 第 8 条）。
