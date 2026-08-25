"""
实验 5：混合检索消融实验（ablation）——单路 vs 三路 RRF vs +精排

四种配置（渐进叠加，隔离每个组件的贡献）：
    A 向量·中文单路          —— 纯语义召回基线（多数 RAG 项目的默认形态）
    B 全文·中文单路          —— 纯词法召回基线
    D 三路 RRF（中向量+英向量+全文）—— 召回融合，不含精排
    E 三路 RRF + Reranker    —— 完整方案（精排的贡献 = E - D）

问题集覆盖三种检索难度：
    术语精确型（全文强）：预警分级、量级编号
    语义改写型（向量强）：比喻、口语化提问
评价：每题预设期望关键词，统计 top-5 命中块数与 top-1 命中（hit@5 / hit@1）。
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.rag.retriever import (
    rrf_fuse,
    search,
    search_hybrid,
    search_lexical,
)

QUESTIONS = [
    # (问题, 英译, 期望关键词列表, 难度类型)
    ("台风预警信号分几级", "typhoon warning signal levels",
     ["预警信号", "台风"], "术语精确"),
    ("3小时内降雨量达100毫米以上是什么预警信号", "rainstorm warning signal criteria",
     ["暴雨", "预警"], "术语精确"),
    ("为什么夏天午后容易下雷阵雨", "why thunderstorms form in summer afternoons",
     ["雷", "对流", "thunderstorm", "convection"], "语义改写"),
    ("台风的天敌是什么", "what weakens or kills a tropical cyclone",
     ["台风", "热带气旋", "冷空气", "切变"], "语义改写"),
    ("热带气旋的结构是怎样的", "structure of a tropical cyclone eye eyewall",
     ["台风", "热带气旋", "眼", "cyclone"], "跨语言"),
]


def hit_count(hits: list[dict], keywords: list[str]) -> tuple[int, bool]:
    """top-5 中命中任一关键词的块数 + top-1 是否命中（source+content 联合匹配）。"""
    n = sum(1 for h in hits if any(k in h["source"] or k in h["content"] for k in keywords))
    top1 = any(k in hits[0]["source"] or k in hits[0]["content"] for k in keywords) if hits else False
    return n, top1


def main() -> None:
    print(f"{'问题':<18}{'类型':<6}{'A向量':>7}{'B全文':>7}{'D三路':>7}{'E精排':>7}")
    totals = {k: [0, 0] for k in "ABDE"}     # 配置 → [hit@5 累计, hit@1 累计]

    for zh, en, keywords, qtype in QUESTIONS:
        configs = {
            "A": search(zh, 5),
            "B": search_lexical(zh, 5),
            "D": rrf_fuse([search(zh, 20), search(en, 20), search_lexical(zh, 20)], 5),
            "E": search_hybrid(zh, en, 5),
        }
        cells = []
        for name, hits in configs.items():
            n, top1 = hit_count(hits, keywords)
            totals[name][0] += n
            totals[name][1] += int(top1)
            cells.append(f"{n}/{'1' if top1 else '0'}")
        print(f"{zh:<18}{qtype:<6}" + "".join(f"{c:>7}" for c in cells))

    print(f"\n{'合计 hit@5/hit@1':<26}" + "".join(f"{totals[k][0]}/{totals[k][1]:>2}" .rjust(8) for k in "ABDE"))
    t0 = time.time()
    search_hybrid(*QUESTIONS[0][:2], 5)
    print(f"\n单次混合检索总耗时（含嵌入×3 + 全文 + rerank）：{time.time()-t0:.2f}s")


if __name__ == "__main__":
    main()
