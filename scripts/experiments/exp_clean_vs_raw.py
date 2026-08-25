"""
对照实验：原始 PDF 直接切块 vs 清洗后切块（量化清洗收益）

实验设计（A/B 对照，切块参数两组一致）：
    A 组：PyMuPDF 原样抽取（含断行/页眉/页码）→ 递归分块
    B 组：经 build_knowledge_base.py 清洗后的文本 → 同参数递归分块
指标：
    结构：短块率 / 断行密度（换行/百字）/ 噪音块率 / 平均块长
    检索：固定 4 问的 top-1 余弦相似度（块与问题的语义匹配度）

注：素材为中国适应气候变化进展报告2023.pdf（已下线换 2025 公报），
    本脚本为历史实验记录，结果存档于 docs/experiments.md 实验 1。
"""

import json
import os
import re
from pathlib import Path

import pymupdf
from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ============================================================
# 配置
# ============================================================
PROJECT_DIR = Path(__file__).parent.parent.parent     # 项目根（scripts/experiments/ 上两层）
import sys

sys.path.insert(0, str(PROJECT_DIR / "backend"))
from app.core.config import EMBED_MODEL

PDF_PATH = PROJECT_DIR / "datas" / "books_pdf" / "中国适应气候变化进展报告2023.pdf"
JSONL_PATH = PROJECT_DIR / "datas" / "processed" / "pages.jsonl"    # B 组数据源

CHUNK_SIZE = 500      # 与主管线一致，保证两组可比
CHUNK_OVERLAP = 50
MIN_MEANINGFUL = 50   # 小于 50 字视为过短块

QUERIES = [           # 4 条针对报告内容的代表性问题
    "中国气温升高的趋势怎么样",
    "适应气候变化的政策体系建设有哪些",
    "海平面上升的情况如何",
    "农业如何适应气候变化",
]

# 噪音块判定模式：块内命中任一模式即计为噪音块
NOISE_PATTERNS = [re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE),           # 纯数字页码行
                  re.compile(r"Copyright.*Stull", re.MULTILINE)]           # 版权页眉


def structure_metrics(chunks: list[Document]) -> dict:
    """计算块列表的结构指标：短块率/断行密度/噪音块率/平均块长。"""
    n = len(chunks)
    frag = sum(1 for c in chunks if len(c.page_content) < MIN_MEANINGFUL)
    newlines = sum(c.page_content.count("\n") for c in chunks)
    total_chars = sum(len(c.page_content) for c in chunks)
    noise = sum(1 for c in chunks if any(p.search(c.page_content) for p in NOISE_PATTERNS))
    return {
        "块数": n,
        "短块率": f"{frag * 100 / n:.1f}%",
        "断行密度(个/百字)": f"{newlines * 100 / total_chars:.2f}",
        "噪音块率": f"{noise * 100 / n:.1f}%",
        "平均块长": f"{total_chars // n} 字",
    }


def cosine(a: list[float], b: list[float]) -> float:
    """两向量的余弦相似度（点积 / 模长乘积）。"""
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (sum(x * x for x in a) ** 0.5 * sum(y * y for y in b) ** 0.5)


def main() -> None:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["。", "！", "？", "；", "\n\n", "\n", "，", " ", ""],   # 中文标点优先
    )

    # ---- A 组：原始 PDF 直接抽取（不清洗）----
    raw_docs = pymupdf.open(PDF_PATH)
    a_docs = []
    for i, page in enumerate(raw_docs):
        t = page.get_text().strip()
        if len(t) >= MIN_MEANINGFUL:                     # 与主管线同口径：跳过纯图页
            a_docs.append(Document(page_content=t, metadata={"page": i + 1}))
    raw_docs.close()
    chunks_a = splitter.split_documents(a_docs)

    # ---- B 组：清洗后文本（主管线产物 pages.jsonl）----
    b_docs = []
    for line in JSONL_PATH.open(encoding="utf-8"):
        rec = json.loads(line)
        if (rec["source"] == PDF_PATH.name and rec["content_type"] == "text"
                and len(rec["text"]) >= MIN_MEANINGFUL):
            b_docs.append(Document(page_content=rec["text"], metadata={"page": rec["page"]}))
    chunks_b = splitter.split_documents(b_docs)

    # ---- 结构指标对比 ----
    print("=" * 62)
    print(f"【结构指标】（chunk_size={CHUNK_SIZE}, overlap={CHUNK_OVERLAP}）")
    print(f"{'':12}{'A组 原始':>16}{'B组 清洗后':>16}")
    ma, mb = structure_metrics(chunks_a), structure_metrics(chunks_b)
    for k in ma:
        print(f"{k:12}{ma[k]:>16}{mb[k]:>16}")

    # ---- 检索指标：固定 4 问，比 top-1 余弦相似度 ----
    load_dotenv(PROJECT_DIR / "backend" / ".env")
    embeddings = OpenAIEmbeddings(
        model=EMBED_MODEL,
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("API_BASE_URL"),
        check_embedding_ctx_length=False,   # 第三方接口必设（pitfalls 第 1 条）
    )
    print("\n【检索指标】固定 4 问的 top-1 余弦相似度（越高=块与问题越匹配）")
    print(f"{'问题':24}{'A组 原始':>12}{'B组 清洗后':>12}{'提升':>10}")

    q_vecs = embeddings.embed_documents(QUERIES)          # 4 个问题 → 向量
    vec_a = embeddings.embed_documents([c.page_content for c in chunks_a])   # 两组块批量嵌入
    vec_b = embeddings.embed_documents([c.page_content for c in chunks_b])

    gains = []
    for q, qv in zip(QUERIES, q_vecs):
        sim_a = max(cosine(qv, v) for v in vec_a)         # 问题与全部块比相似度，取最高
        sim_b = max(cosine(qv, v) for v in vec_b)
        gains.append(sim_b - sim_a)
        print(f"{q:24}{sim_a:>12.4f}{sim_b:>12.4f}{sim_b - sim_a:>+10.4f}")

    print(f"\n平均提升：{sum(gains) / len(gains):+.4f}")


if __name__ == "__main__":
    main()
