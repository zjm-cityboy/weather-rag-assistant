"""
实验 6：知识图谱 vs 向量检索（关系类问题专项对比）

假设：防御/引发链类问题的答案分散在多条关系里，向量检索只能召回独立文本块
（块间结构关系已在嵌入时丢失），图谱的多跳子图能成链召回。

两配置：
    G 图谱路：query_graph → 多跳子图三元组（实体识别 + Cypher）
    V 向量路：search_hybrid 三路混合检索 top-5（第 4 期最强基线）

评价：每题人工预设"期望实体"清单（来自图谱真实数据），统计答案素材中
命中的期望实体数（hit_entities）——衡量"给了 LLM 多少可用的关系素材"。
计算依据：素材文本 = 图谱三元组串 / 检索块串，期望实体做子串匹配计数。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.graph.knowledge_graph import query_graph
from app.rag.retriever import search_hybrid

QUESTIONS = [
    # (问题, 英译, 期望实体清单——按图谱实际实体表述校准，两配置同一标准)
    ("台风会引发哪些次生灾害", "secondary disasters caused by typhoon",
     ["风暴潮", "洪水", "暴雨", "电力"]),
    ("台风来了该怎么防御", "typhoon defense measures",
     ["关紧门窗", "停业停课", "加固", "防风", "海上"]),
    ("暴雨的防御措施有哪些", "rainstorm defense measures",
     ["积水", "地下空间", "低洼", "涉水"]),
    ("雷电天气应该注意什么", "thunderstorm safety precautions",
     ["门窗", "室内", "电源", "露天"]),
    ("高温天气怎么防护", "heat wave protection measures",
     ["中暑", "户外", "火灾", "工作时间"]),
]


def hits(text: str, entities: list[str]) -> int:
    """素材文本中命中的期望实体数。"""
    return sum(1 for e in entities if e in text)


def main() -> None:
    print(f"{'问题':<16}{'G图谱':>7}{'V向量':>7}   G耗时   V耗时")
    tot_g = tot_v = 0

    for zh, en, entities in QUESTIONS:
        t0 = time.time()
        g = query_graph(zh)
        t_g = time.time() - t0
        g_text = " ".join(f"{t['head']}{t['relation']}{t['tail']}" for t in g["triples"])
        n_g = hits(g_text, entities)

        t0 = time.time()
        v = search_hybrid(zh, en, 5)
        t_v = time.time() - t0
        v_text = " ".join(c["content"] for c in v)
        n_v = hits(v_text, entities)

        tot_g += n_g
        tot_v += n_v
        print(f"{zh:<16}{n_g:>5}/{len(entities)}{n_v:>5}/{len(entities)}   {t_g:5.2f}s  {t_v:5.2f}s")

    print(f"\n合计：图谱 {tot_g}/{sum(len(e) for *_, e in QUESTIONS)}"
          f" vs 向量 {tot_v}/{sum(len(e) for *_, e in QUESTIONS)}")
    rel = (tot_g - tot_v) / tot_v * 100 if tot_v else float("inf")
    print(f"图谱相对向量：期望实体召回 {'+' if rel >= 0 else ''}{rel:.1f}%")


if __name__ == "__main__":
    main()
