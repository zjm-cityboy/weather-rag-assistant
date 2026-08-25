"""
气象 RAG · 知识库入库管线

消费两路语料并写入 pgvector：
    pages.jsonl      PDF 语料（上游 build_knowledge_base.py 已清洗）
    web_pages.jsonl  网页语料（crawl 产出、未清洗，本脚本内清洗）

执行顺序（自上而下即数据流，详见 docs/pipeline.md）：
    加载 → 清洗(网页) → 分块 → 精确去重 → 批量嵌入 → 入库 → 检索冒烟

前置：db/init_db.py 已执行（库/表/约束/索引就绪）。
"""

import hashlib
import json
import os
import sys
from pathlib import Path

import jieba
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

sys.path.insert(0, str(Path(__file__).parent))   # data/ 进模块搜索路径（cleaner 所在）
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.core.config import EMBED_MODEL
from cleaner import build_boilerplate, clean_text

# ============================================================
# 配置
# ============================================================
PROJECT_DIR = Path(__file__).parent.parent.parent                    # 项目根（scripts/data/ 上两层）
JSONL_PATH = PROJECT_DIR / "datas" / "processed" / "pages.jsonl"     # PDF 语料（已清洗）
WEB_JSONL = PROJECT_DIR / "datas" / "web_pages" / "web_pages.jsonl"  # 网页语料（未清洗）

CHUNK_SIZE = 500        # 单块最大字符数（与清洗实验一致，便于对照）
CHUNK_OVERLAP = 50      # 相邻块重叠字符数（防止句子被切断导致语义丢失）
MIN_CHUNK_LEN = 50      # 最小块长度：切分产生的过短块直接丢弃
EMBED_BATCH = 64        # 嵌入 API 每批条数（接口限速友好）

PG_DSN = "host=localhost port=5432 dbname=weather user=postgres password=weather_dev_2026"

WEB_TYPES = ("web", "web_llm")   # 未清洗的语料类型（text/ocr 已在上游过 cleaner）


def get_embeddings() -> OpenAIEmbeddings:
    """嵌入模型实例（嵌入与冒烟两处复用；密钥外置 .env）。"""
    load_dotenv(PROJECT_DIR / "backend" / ".env")
    return OpenAIEmbeddings(
        model=EMBED_MODEL,
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("API_BASE_URL"),
        check_embedding_ctx_length=False,   # 第三方接口必设，禁止移除（pitfalls 第 1 条）
    )


# ============================================================
# 步骤 1：加载语料（两个 JSONL 合并为 Document 列表）
#   pages.jsonl 一行 = 一页（source/page/content_type/text）
#   web_pages.jsonl 一行 = 一篇（多一个 url 字段，供溯源）
# ============================================================
docs: list[Document] = []
for line in JSONL_PATH.open(encoding="utf-8"):
    rec = json.loads(line)                          # 每行是一个独立 JSON 对象
    docs.append(Document(
        page_content=rec["text"],                   # 正文 → page_content
        metadata={"source": rec["source"], "page": rec["page"],
                  "content_type": rec["content_type"], "url": ""},   # PDF 无 url，占位空串
    ))
n_pdf = len(docs)                                   # 记下 PDF 条数，供打印统计

if WEB_JSONL.exists():
    for line in WEB_JSONL.open(encoding="utf-8"):
        rec = json.loads(line)
        docs.append(Document(
            page_content=rec["text"],
            metadata={"source": rec["source"], "page": rec["page"],
                      "content_type": rec["content_type"], "url": rec.get("url", "")},
        ))
print(f"[加载] PDF {n_pdf} 页 + 网页 {len(docs) - n_pdf} 篇 = {len(docs)} 条")

# ============================================================
# 步骤 2：清洗网页语料（PDF 已在上游清洗，直接透传）
#   复用 cleaner 模块：先跨文章统计模板行（同站导航残留），
#   再逐篇清洗；清洗后过短的（< MIN_CHUNK_LEN）视为无有效内容丢弃
# ============================================================
web_sources = [d.metadata["source"] for d in docs if d.metadata["content_type"] in WEB_TYPES]
boilerplate = build_boilerplate({                   # key=文章标识，value=待统计文本
    d.metadata["source"]: d.page_content
    for d in docs if d.metadata["content_type"] in WEB_TYPES
})
cleaned_docs: list[Document] = []
for d in docs:
    if d.metadata["content_type"] not in WEB_TYPES:
        cleaned_docs.append(d)                      # PDF 语料：上游已清洗，原样保留
        continue
    text = clean_text(d.page_content, "zh", boilerplate)
    if len(text) >= MIN_CHUNK_LEN:
        cleaned_docs.append(Document(page_content=text, metadata=d.metadata))
docs = cleaned_docs
print(f"[清洗] 网页语料 {len(web_sources)} 篇：模板行 {len(boilerplate)} 个，清洗后有效 "
      f"{sum(1 for d in docs if d.metadata['content_type'] in WEB_TYPES)} 篇")

# ============================================================
# 步骤 3：分块（PDF 与网页统一处理）
#   递归字符分块：优先按中文句末标点切，切不动逐级降级到字符
#   长度过滤：丢掉切分产生的过短块
# ============================================================
splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["。", "！", "？", "；", "\n\n", "\n", "，", " ", ""],   # 中文标点优先
)
all_chunks = splitter.split_documents(docs)         # metadata 会自动带进每个块
chunks = [c for c in all_chunks if len(c.page_content) >= MIN_CHUNK_LEN]
print(f"[分块] {len(all_chunks)} 块 → 长度过滤（<{MIN_CHUNK_LEN} 字）后 {len(chunks)} 块")

# ============================================================
# 步骤 4：连接 PG + 精确去重（exact dedup by content hash）
#   每块算 md5，与库内 content_hash 集合比对——已存在的块跳过，
#   且发生在嵌入之前：不产生任何 API 调用
# ============================================================
conn = psycopg2.connect(PG_DSN)
cur = conn.cursor()
cur.execute("SELECT content_hash FROM knowledge_chunks;")   # 取库内全部哈希
existing_hashes = {r[0] for r in cur.fetchall()}            # 转集合，成员判断 O(1)
for c in chunks:
    # 与 PG md5() 同算法（一致性由 tests/smoke_hash_consistency.py 验证）
    c.metadata["content_hash"] = hashlib.md5(c.page_content.encode("utf-8")).hexdigest()
new_chunks = [c for c in chunks if c.metadata["content_hash"] not in existing_hashes]
print(f"[去重] 库内 {len(existing_hashes)} 个 content hash，本次 {len(chunks)} 块，"
      f"待入库 {len(new_chunks)} 块（跳过已存在 {len(chunks) - len(new_chunks)}）")

if new_chunks:
    # ========================================================
    # 步骤 5：批量嵌入（64 条/批，分批调 API）
    # ========================================================
    embeddings = get_embeddings()
    texts = [c.page_content for c in new_chunks]    # 嵌入接口吃纯文本列表
    vectors: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):     # range(起点, 终点, 步长) 切批
        vectors.extend(embeddings.embed_documents(texts[i:i + EMBED_BATCH]))
        print(f"[嵌入] 进度 {min(i + EMBED_BATCH, len(texts))}/{len(texts)}")
    print(f"[嵌入] 完成：{len(vectors)} 个向量，维度 {len(vectors[0])}")

    # ========================================================
    # 步骤 6：入库（参数绑定批量 INSERT，SQL 一律不拼接）
    #   ON CONFLICT (content_hash) DO NOTHING：冲突行静默跳过，
    #   与 UNIQUE 约束配合，并发/边界场景由 DB 层兜底
    #   content_tokens：jieba 分词结果（空格分隔），tsv 生成列据此自动维护（第 4 期全文检索）
    # ========================================================
    # pgvector 列接受 '[0.1,0.2,...]' 字面量，显式 ::vector 转换
    INSERT_SQL = """
        INSERT INTO knowledge_chunks (content, source, page, content_type, url, content_hash, embedding, content_tokens)
        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)
        ON CONFLICT (content_hash) DO NOTHING
    """
    rows = [                                         # 每块一行八元组，与 %s 一一对应
        (c.page_content, c.metadata["source"], c.metadata["page"],
         c.metadata["content_type"], c.metadata.get("url", ""),
         c.metadata["content_hash"],
         "[" + ",".join(f"{x:.6f}" for x in vec) + "]",   # 向量 → pgvector 字面量
         " ".join(jieba.lcut(c.page_content)))            # 全文检索分词串
        for c, vec in zip(new_chunks, vectors)
    ]
    cur.execute("SELECT COUNT(*) FROM knowledge_chunks;")
    before = cur.fetchone()[0]                       # 入库前行数（用于算新增量）
    psycopg2.extras.execute_batch(cur, INSERT_SQL, rows)  # 批量执行，远快于逐条
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM knowledge_chunks;")
    after = cur.fetchone()[0]
    print(f"[入库] 新增 {after - before} 条，共 {after} 条")

# ============================================================
# 步骤 7：检索冒烟（验证库可用：3 问各取 top-3）
#   <=> 是余弦距离（1 - 余弦相似度），升序排列 = 越相似越靠前
# ============================================================
embeddings = get_embeddings()
QUERY_SQL = """
    SELECT source, page, LEFT(content, 60) AS preview,
           embedding <=> %s::vector AS distance
    FROM knowledge_chunks
    ORDER BY embedding <=> %s::vector
    LIMIT 3
"""
print("[冒烟] top-3，cosine 距离")
for query in ["台风是怎么形成的", "中国气温升高的趋势", "cloud formation types"]:
    qvec = embeddings.embed_query(query)            # 问题 → 查询向量
    vec_str = "[" + ",".join(f"{x:.6f}" for x in qvec) + "]"
    cur.execute(QUERY_SQL, (vec_str, vec_str))      # 同一向量用于 SELECT 与 ORDER BY
    print(f"\n  Q: {query}")
    for source, page, preview, dist in cur.fetchall():
        print(f"    {dist:.4f} | {Path(source).name[:28]:30} p{page:<3} | {preview}...")

conn.close()                                        # 释放连接
