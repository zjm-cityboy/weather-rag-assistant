"""实验 10：BM25（pg_search）vs ts_rank_cd（PG 原生全文检索）同场对照（2026-08-25）。

背景：词法路从 PG 原生全文检索（ts_rank_cd，tf-idf 家族，无 k1/b）升级为
pg_search 的真 BM25（Tantivy 实现，带 k1/b 饱和参数，jieba 内置分词）。

设计：与实验 5 同款方法（预设关键词 hit@5/hit@1），同一批 10 题，
两个词法实现各跑一遍 —— 同题同分母，差异只来自打分函数。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))   # 复用同目录实验的题库与判定
from app.rag.retriever import (  # noqa: E402
    TOP_K,
    _as_chunk,
    _fetch,
    _tokenize,
    search_lexical,
)
from scripts.experiments.exp_hybrid_vs_single import (  # noqa: E402
    QUESTIONS,
    hit_count,
)

# v1 词法路 SQL（ts_rank_cd + OR 降级语义的最终形态），作为对照组原样保留
TS_RANK_SQL = """
    SELECT id, content, source, page, url,
           ts_rank_cd(tsv, to_tsquery('simple', %(tokens)s)) AS score
    FROM knowledge_chunks
    WHERE tsv @@ to_tsquery('simple', %(tokens)s)
    ORDER BY score DESC
    LIMIT %(top_k)s
"""


def lexical_v1(zh_query: str, top_k: int = TOP_K) -> list[dict]:
    """v1 词法路：jieba 分词 → OR 查询 → ts_rank_cd 排序。"""
    tokens = _tokenize(zh_query)
    if not tokens:
        return []
    rows = _fetch(TS_RANK_SQL, {"tokens": " | ".join(tokens), "top_k": top_k})
    return [_as_chunk(r, "lex_score") for r in rows]


def main() -> None:
    print(f"{'问题':<18}{'ts_rank_cd':>12}{'BM25':>8}")
    totals = {"v1": [0, 0], "v2": [0, 0]}   # [hit@5, hit@1]
    for zh, _en, keywords, _qtype in QUESTIONS:
        cells = []
        for name, fn in (("v1", lexical_v1), ("v2", search_lexical)):
            n, top1 = hit_count(fn(zh, 5), keywords)
            totals[name][0] += n
            totals[name][1] += int(top1)
            cells.append(f"{n}/{'1' if top1 else '0'}")
        print(f"{zh:<18}{cells[0]:>12}{cells[1]:>8}")

    n = len(QUESTIONS)
    v1, v2 = totals["v1"], totals["v2"]
    print(f"\n合计（{n} 题，满分 {5 * n}/{n}）")
    print(f"  ts_rank_cd: hit@5={v1[0]}  hit@1={v1[1]}")
    print(f"  BM25      : hit@5={v2[0]}  hit@1={v2[1]}")

    # 延迟对照（单路词法，各跑 3 次取平均，量级参考）
    for name, fn in (("ts_rank_cd", lexical_v1), ("BM25", search_lexical)):
        times = []
        for _ in range(3):
            t0 = time.time()
            fn(QUESTIONS[0][0], 5)
            times.append(time.time() - t0)
        print(f"  {name} 平均延迟: {sum(times) / 3 * 1000:.0f}ms")

    # ---- 端到端对照：两版词法路各走完整 hybrid（向量+词法 → RRF → rerank）----
    # 词法路的真实职责是给融合/精排供候选，单路 hit 的差异未必传导到最终结果
    from app.rag.retriever import (
        HYBRID_CANDIDATES,
        rerank_chunks,
        rrf_fuse,
        search,
    )

    print(f"\n{'问题':<18}{'E-v1端到端':>12}{'E-v2端到端':>12}")
    e_totals = {"v1": [0, 0], "v2": [0, 0]}
    for zh, _en, keywords, _qtype in QUESTIONS:
        cells = []
        for name, lex_fn in (("v1", lexical_v1), ("v2", search_lexical)):
            fused = rrf_fuse([search(zh, HYBRID_CANDIDATES),
                              lex_fn(zh, HYBRID_CANDIDATES)], 5)
            final = rerank_chunks(zh, fused, 5) or fused[:5]
            n, top1 = hit_count(final, keywords)
            e_totals[name][0] += n
            e_totals[name][1] += int(top1)
            cells.append(f"{n}/{'1' if top1 else '0'}")
        print(f"{zh:<18}{cells[0]:>12}{cells[1]:>12}")

    n = len(QUESTIONS)
    print(f"\n端到端合计（{n} 题，满分 {5 * n}/{n}）")
    print(f"  ts_rank_cd 词法: hit@5={e_totals['v1'][0]}  hit@1={e_totals['v1'][1]}")
    print(f"  BM25       词法: hit@5={e_totals['v2'][0]}  hit@1={e_totals['v2'][1]}")


if __name__ == "__main__":
    main()
