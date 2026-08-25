"""
英文块中文摘要：给英文教材块生成一句中文要点，拼进 content 重新嵌入入库

背景（实验 7 RAGAS 诊断）：
    ① 中文题的 top-5 被英文块污染（Saffir-Simpson 分级 ≠ 中国预警分级，
       rerank 跨语打分虚高）→ 摘要让 rerank 看懂英文块实际内容，正确降分；
    ② 评审判"英文块覆盖中文要点"保守 → 摘要把知识点显式中文化，覆盖判定变准。
    摘要拼在 content 开头一起嵌入，中文查询与英文块的向量对齐同时改善。

幂等设计：content_hash 基于原 content 计算（不变），已完成摘要的块靠
    content 以 "【中文摘要】" 开头识别跳过；断点重跑只处理剩余块。
"""

import sys
import time
from pathlib import Path

import jieba
import psycopg2

PROJECT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR / "backend"))

from app.core.config import (
    API_BASE_URL,
    API_KEY,
    CHAT_MODEL,
    EMBED_MODEL,
    PG_DSN,
)
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

SUMMARY_PROMPT = """用一句简体中文（40字以内）概括这段气象英文资料的具体知识点。
要求：点明专有名词和具体内容（如"萨菲尔-辛普森台风强度分级标准"而不是"关于台风的内容"），不要泛泛而谈。

英文资料：{text}

只输出这一句摘要，不要任何其他文字。"""

BATCH = 8                 # 嵌入批量（与入库管线一致）


def is_english(text: str) -> bool:
    """中文占比 < 15% 判为英文块（教材语料主体）。"""
    if not text:
        return False
    zh = sum(1 for ch in text if ord(ch) > 0x2E7F)   # CJK 区段及全角标点
    return zh < len(text) * 0.15


def main() -> None:
    llm = ChatOpenAI(model=CHAT_MODEL, api_key=API_KEY, base_url=API_BASE_URL,
                     temperature=0.2, timeout=30, max_retries=2, max_tokens=100,
                     extra_body={"enable_thinking": False})
    embeddings = OpenAIEmbeddings(
        model=EMBED_MODEL, api_key=API_KEY, base_url=API_BASE_URL,
        check_embedding_ctx_length=False, timeout=30, max_retries=2)   # pitfalls 第 1 条

    # ========================================================
    # 步骤 1：取待处理英文块（未做过摘要的：content 不以摘要标记开头）
    # ========================================================
    conn = psycopg2.connect(PG_DSN)
    with conn.cursor() as cur:
        cur.execute("SELECT id, content, source, page, content_type, url FROM knowledge_chunks;")
        rows = cur.fetchall()
    todo = [r for r in rows if is_english(r[1]) and not r[1].startswith("【中文摘要】")]
    print(f"[1] 英文块 {sum(1 for r in rows if is_english(r[1]))} 条，待摘要 {len(todo)} 条")

    # ========================================================
    # 步骤 2：逐块生成中文摘要 → 摘要+原文 拼接为新 content（进度每 10 块）
    # ========================================================
    t0 = time.time()
    rewritten: list[tuple] = []      # (id, new_content, old_content)
    for i, (rid, content, *_rest) in enumerate(todo, 1):
        try:
            summary = llm.invoke(SUMMARY_PROMPT.format(
                text=content[:1200])).content.strip().split("\n")[0][:60]
        except Exception as e:  # noqa: BLE001 —— 单块失败打印后跳过（幂等重跑可补）
            print(f"    块 {rid} 摘要失败（跳过）：{type(e).__name__}", flush=True)
            continue
        new_content = f"【中文摘要】{summary}\n{content}"
        rewritten.append((rid, new_content, content))
        if i % 10 == 0 or i == len(todo):
            print(f"[2] 进度 {i}/{len(todo)}（{(time.time()-t0)/60:.1f}min）", flush=True)
    print(f"[2] 摘要完成 {len(rewritten)} 条")

    # ========================================================
    # 步骤 3：新 content 重新嵌入 → UPDATE content/embedding/content_tokens（事务原子）
    # ========================================================
    texts = [nc for _rid, nc, _oc in rewritten]
    # 嵌入的是完整新 content（摘要+原文），检索两侧同源
    vectors: list[list[float]] = []
    for j in range(0, len(texts), BATCH):
        vectors.extend(embeddings.embed_documents(texts[j:j + BATCH]))
    print(f"[3] 重嵌入完成 {len(vectors)} 个向量")

    with conn.cursor() as cur:
        for (rid, new_content, _oc), vec in zip(rewritten, vectors):
            vec_literal = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
            cur.execute(
                "UPDATE knowledge_chunks SET content = %s, embedding = %s::vector WHERE id = %s;",
                (new_content, vec_literal, rid))
        # content_tokens 同步重算（新 content 含中文摘要 → 全文检索中文路也能命中英文块）
        for rid, new_content, _oc in rewritten:
            cur.execute("UPDATE knowledge_chunks SET content_tokens = %s WHERE id = %s;",
                        (" ".join(jieba.lcut(new_content)), rid))
    conn.commit()
    conn.close()
    print(f"[4] 入库完成：{len(rewritten)} 条英文块已带中文摘要（content_hash 未变，幂等可重跑）")


if __name__ == "__main__":
    main()
