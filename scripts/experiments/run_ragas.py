"""
RAGAS 评估：知识问答全链路（检索 → 生成）四指标量化

流程（两阶段，生成结果落盘可复用）：
    ① 逐题生成：rewrite_query → search_hybrid 检索 top-5 → 线上同款 Prompt 生成回答
       （与 /ask 知识路完全同链路，评的就是线上效果；结果存 evals/ragas_answers.json）
    ② ragas 四指标评估（LLM-as-judge，evaluator 与被评模型同款但独立调用）：
       Faithfulness          忠实度：回答的每条陈述是否被检索上下文支撑（幻觉率）
       AnswerRelevancy       回答相关性：回答是否切题（embedding 反向相似度）
       ContextPrecision      上下文精确率：检索的 top-k 里相关内容是否排在前面
       ContextRecall         上下文召回率：参考答案的要点是否都被检索内容覆盖

用法：python scripts/experiments/run_ragas.py（约 15~25 分钟，evaluator 需数百次 LLM 调用）
"""

import json
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_DIR / "backend"))

from app.core.config import API_BASE_URL, API_KEY, CHAT_MODEL, EMBED_MODEL
from app.rag import chain, retriever
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas import RunConfig
from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.evaluation import EvaluationDataset, evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    AnswerRelevancy,
    Faithfulness,
    LLMContextPrecisionWithReference,
    LLMContextRecall,
)

EVAL_SET = PROJECT_DIR / "evals" / "weather_qa_eval.jsonl"
ANSWERS_CACHE = PROJECT_DIR / "evals" / "ragas_answers.json"
RESULTS_OUT = PROJECT_DIR / "evals" / "ragas_results.json"


def generate_answers(items: list[dict]) -> list[dict]:
    """阶段①：逐题走线上同链路生成回答（缓存文件存在则复用，不重复花生成费用）。"""
    if ANSWERS_CACHE.exists():
        cached = json.loads(ANSWERS_CACHE.read_text(encoding="utf-8"))
        print(f"[1] 复用已生成的回答缓存 {len(cached)} 条（删除 {ANSWERS_CACHE.name} 可强制重生成）")
        return cached

    llm = chain.get_llm()                       # 与线上一致（含 enable_thinking=False）
    records = []
    for i, item in enumerate(items, 1):
        t0 = time.time()
        zh_q = chain.rewrite_query(item["question"], [])                # 预处理（无历史）
        chunks = retriever.search_hybrid(zh_q)                          # 混合检索 top-5
        prompt = chain.build_prompt().invoke({
            "history": [], "context": chain.build_context(chunks), "question": item["question"]})
        answer = llm.invoke(prompt).content                              # 非流式取全文
        records.append({"question": item["question"], "reference": item["reference"],
                        "answer": answer,
                        "contexts": [c["content"] for c in chunks]})
        print(f"[1] 进度 {i}/{len(items)}（{time.time()-t0:.1f}s）", flush=True)

    ANSWERS_CACHE.write_text(json.dumps(records, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"[1] 生成完成，缓存至 {ANSWERS_CACHE.name}")
    return records


def run_ragas(records: list[dict]) -> dict:
    """阶段②：ragas 四指标评估（evaluator 独立 LLM/embedding 实例）。

    reference 用中英双语要点（评测集 reference + reference_en 按问题对齐拼接）——
    校准"英文教材块 vs 中文参考要点"的语言错位低估（实验 8 结论的落地）。
    """
    # 评测集的英文要点按问题对齐（answers 缓存里只有中文 reference）
    en_by_q = {}
    for line in EVAL_SET.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            en_by_q[d["question"]] = d.get("reference_en", "")

    dataset = EvaluationDataset([
        SingleTurnSample(
            user_input=r["question"], retrieved_contexts=r["contexts"],
            response=r["answer"],
            reference=f"{r['reference']}\nEnglish key points: {en_by_q.get(r['question'], '')}")
        for r in records
    ])
    evaluator_llm = LangchainLLMWrapper(ChatOpenAI(
        model=CHAT_MODEL, api_key=API_KEY, base_url=API_BASE_URL,
        temperature=0.0, timeout=60, max_retries=2,
        extra_body={"enable_thinking": False}))                   # evaluator 同样关思考
    evaluator_emb = LangchainEmbeddingsWrapper(OpenAIEmbeddings(
        model=EMBED_MODEL, api_key=API_KEY, base_url=API_BASE_URL,
        check_embedding_ctx_length=False, timeout=30, max_retries=2))

    print("[2] ragas 评估开始（20 题 × 4 指标，evaluator 数百次调用，预计 10~20 分钟）…",
          flush=True)
    t0 = time.time()
    # 兼容路径：经典 metrics + evaluate(llm=) 注入（collections 新 API 只认 InstructorLLM）
    metrics = [Faithfulness(), AnswerRelevancy(),
               LLMContextPrecisionWithReference(), LLMContextRecall()]
    result = evaluate(
        dataset=dataset,
        metrics=metrics,
        llm=evaluator_llm, embeddings=evaluator_emb,
        run_config=RunConfig(max_workers=4, timeout=120),         # 限并发防 429
    )
    print(f"[2] 评估完成，耗时 {(time.time()-t0)/60:.1f} 分钟")
    # EvaluationResult → DataFrame 后按列取均值（ragas 0.4 返回类型不再支持 .items()）
    return {k: round(v, 4) for k, v in
            result.to_pandas().mean(numeric_only=True).items()}


def main() -> None:
    items = [json.loads(line) for line in
             EVAL_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"[0] 评测集 {len(items)} 条（{EVAL_SET.name}）")

    records = generate_answers(items)
    scores = run_ragas(records)

    print("\n===== RAGAS 四指标（20 题均值）=====")
    for name, value in scores.items():
        print(f"  {name:<28} {value}")
    RESULTS_OUT.write_text(json.dumps(
        {"dataset": EVAL_SET.name, "n": len(records), "scores": scores},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果已存 {RESULTS_OUT}")


if __name__ == "__main__":
    main()
