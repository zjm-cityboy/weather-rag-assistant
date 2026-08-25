"""
检索模块（第 4 期混合检索；实验 8 后简化为两路，检索与精排同模块）

两路召回，单层 RRF 融合，cross-encoder 精排：
    ① 向量·中文  embedding <=> 余弦距离（HNSW 索引）——语义召回；
       BGE-M3 为多语言模型，中文 query 可直接命中英文块（实验 8：Hit@10 0→6）
    ② 全文·中文  PG tsvector @@ 匹配 + ts_rank_cd 排序（GIN 索引）——关键词精确召回
                                        ↓
        RRF 融合（只看排名，不看分数量纲） → Reranker 精排取 top-k

为什么需要两路：向量检索对"术语精确匹配"弱（嵌入把词压成一个点，专有名词容易糊），
全文检索对"同义改写"弱（字面不同就查不到）；两者的失败模式恰好互补。
（英文查询路已下线：对照实验命中持平 13/20 vs 14/20 而延迟省 43%，见 experiments.md）
"""

import re

import jieba
import requests
from langchain_openai import OpenAIEmbeddings

from app.core.config import API_BASE_URL, API_KEY, EMBED_MODEL, TOP_K
from app.core.db import pg_conn

# 向量检索：查询向量绑定两次（SELECT 距离列 + ORDER BY），HNSW 索引生效前提
VECTOR_SQL = """
    SELECT id, content, source, page, url,
           embedding <=> %(qvec)s::vector AS distance
    FROM knowledge_chunks
    ORDER BY embedding <=> %(qvec)s::vector
    LIMIT %(top_k)s
"""

# 全文检索（AND 语义）：plainto_tsquery 把分词串按 & 连接（对特殊字符免疫，天然防语法注入），
# ts_rank_cd 按词频+词距打分（tf-idf 家族，与 BM25 的关系见 docs/hybrid-search.md）
LEXICAL_SQL = """
    SELECT id, content, source, page, url,
           ts_rank_cd(tsv, plainto_tsquery('simple', %(tokens)s)) AS score
    FROM knowledge_chunks
    WHERE tsv @@ plainto_tsquery('simple', %(tokens)s)
    ORDER BY score DESC
    LIMIT %(top_k)s
"""

# 全文检索降级（OR 语义）：AND 全灭时放宽为任一词命中，ts_rank_cd 仍偏向命中词多的文档
LEXICAL_OR_SQL = """
    SELECT id, content, source, page, url,
           ts_rank_cd(tsv, to_tsquery('simple', %(tokens)s)) AS score
    FROM knowledge_chunks
    WHERE tsv @@ to_tsquery('simple', %(tokens)s)
    ORDER BY score DESC
    LIMIT %(top_k)s
"""

# 全文检索分词清洗：合法 lexeme（汉字/字母/数字/下划线）+ 常见疑问词虚词过滤
_TOKEN_RE = re.compile(r"^[\w\u4e00-\u9fff]+$")
STOPWORDS = {
    "什么", "怎么", "怎样", "如何", "为什么", "哪些", "哪个", "几",
    "多少", "请问", "一下", "还是", "有没有", "分别", "进行", "可以",
    "的", "了", "吗", "呢", "啊", "吧", "是", "有", "和", "与", "及",
    "对", "对于", "关于", "在", "从", "到", "被", "把", "让", "给",
    "我", "你", "他", "她", "它", "们", "这", "那", "都", "也", "就", "才",
}


def get_embeddings() -> OpenAIEmbeddings:
    """嵌入模型单例（模块级构造，零竞态；httpx client 复用——代码审查 P1-1）。

    timeout 必设：openai SDK 默认 600s，第三方接口偶发挂死会拖垮整个请求
    （实测症状：SSE 一个字节都不返回）。
    """
    global _EMBEDDINGS
    if _EMBEDDINGS is None:
        _EMBEDDINGS = OpenAIEmbeddings(
            model=EMBED_MODEL,
            api_key=API_KEY,
            base_url=API_BASE_URL,
            check_embedding_ctx_length=False,   # 第三方接口必设，禁止移除（pitfalls 第 1 条）
            timeout=30,                         # 单次嵌入调用超时（秒）
            max_retries=2,                      # 连接类错误自动重试（SDK 内建）
        )
    return _EMBEDDINGS


_EMBEDDINGS: OpenAIEmbeddings | None = None


def to_vec_literal(vec: list[float]) -> str:
    """浮点列表 → pgvector 字面量 '[0.1,0.2,...]'（与入库格式一致）。"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _fetch(sql: str, params: dict) -> list[tuple]:
    """执行查询并返回全部行（走连接池，连接借还微秒级——代码审查 P0-1）。"""
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)        # 参数绑定，禁止拼接
        return cur.fetchall()


def _as_chunk(row: tuple, score_field: str) -> dict:
    """查询行 → 统一 chunk 结构；score_field 指定分数字段名（distance / lex_score）。"""
    chunk = {"id": row[0], "content": row[1], "source": row[2],
             "page": row[3], "url": row[4], score_field: round(row[5], 4)}
    chunk.setdefault("distance", None)      # 两路分数量纲不同，统一补 distance 键
    return chunk


def _tokenize(zh_query: str) -> list[str]:
    """分词 + 清洗：过滤标点/虚词，去重保序（保序让 OR 表达式首个词是核心词）。"""
    words = [w for w in jieba.lcut(zh_query)
             if _TOKEN_RE.match(w) and w not in STOPWORDS]
    return list(dict.fromkeys(words))


# ============================================================
# 单路检索（实验对照的基线，线上走 search_hybrid）
# ============================================================
def search(question: str, top_k: int = TOP_K) -> list[dict]:
    """向量单路：问题嵌入 + pgvector 余弦距离 top-k。"""
    qvec = to_vec_literal(get_embeddings().embed_query(question))
    rows = _fetch(VECTOR_SQL, {"qvec": qvec, "top_k": top_k})
    return [_as_chunk(r, "distance") for r in rows]


def search_lexical(zh_query: str, top_k: int = TOP_K) -> list[dict]:
    """全文单路：jieba 分词 → 停用词过滤 → PG 全文匹配 top-k。

    两级匹配：先 AND（所有词都要命中，精准）；空结果降级 OR（任一词命中，
    ts_rank_cd 排序仍偏向命中多的）。不降级的话，"台风预警信号分几级"
    里的"几级"会让 AND 全灭——用户问题必然带疑问词，这是必经路径。
    分词必须与入库侧一致（都是 jieba 精确模式），否则词面不同无法命中。
    """
    tokens = _tokenize(zh_query)
    if not tokens:
        return []

    rows = _fetch(LEXICAL_SQL, {"tokens": " ".join(tokens), "top_k": top_k})
    if not rows:                     # AND 全灭 → OR 降级
        rows = _fetch(LEXICAL_OR_SQL, {"tokens": " | ".join(tokens), "top_k": top_k})
    return [_as_chunk(r, "lex_score") for r in rows]


# ============================================================
# RRF 融合（Reciprocal Rank Fusion）+ 精排（cross-encoder）
# ============================================================
RRF_K = 60          # 平滑常数（业界标准值，Elasticsearch 同款）；越小头部权重越集中

RERANK_MODEL = "Qwen/Qwen3-Reranker-0.6B"
RERANK_TIMEOUT = 15     # rerank 接口超时（秒），外部调用必设


def rrf_fuse(rank_lists: list[list[dict]], top_k: int) -> list[dict]:
    """多路排名融合：score = Σ 1/(RRF_K + rank)，取累计分 top_k。

    只用排名不用原始分数——向量距离、ts_rank_cd 分数、rerank 分数量纲互不相同，
    融合层对量纲免疫才能接任意检索源（这也是不用分数归一化的原因：归一化
    对离群值敏感，且每路的分数分布不同，强行拉到同一尺度反而失真）。
    """
    scores: dict[int, float] = {}      # 块 id → RRF 累计分
    pooled: dict[int, dict] = {}       # 块 id → 元信息（取首见，不跨路比较量纲）
    for hits in rank_lists:
        for rank, h in enumerate(hits, 1):
            scores[h["id"]] = scores.get(h["id"], 0.0) + 1.0 / (RRF_K + rank)
            pooled.setdefault(h["id"], h)

    merged = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
    result = [pooled[rid] for rid, _ in merged]
    for h, (_, s) in zip(result, merged):
        h["rrf_score"] = round(s, 6)   # 融合分随结果带出（调试/实验用）
    return result


def rerank_chunks(query: str, chunks: list[dict], top_n: int) -> list[dict] | None:
    """cross-encoder 精排：query 与每个候选拼接过同一个模型，按相关度取 top_n。

    与嵌入检索（bi-encoder）的区别：双塔分开编码可预计算、毫秒级扫全库，
    但 query 与文档互不知情；reranker 逐词注意力交互精度高，可每对都要
    现算——所以只精排 top 候选，不扫全库（两阶段漏斗，原理详见
    docs/hybrid-search.md）。冒烟测试实测中文 query 对英文文档 0.989，
    英文教材语料可被中文问题直接精排。

    失败返回 None（网络/接口异常），调用方降级用 RRF 排序——精排是增强项，
    不能让它的故障阻断检索主链路。
    """
    try:
        r = requests.post(
            f"{API_BASE_URL}/rerank",
            headers={"Authorization": f"Bearer {API_KEY}"},
            json={"model": RERANK_MODEL, "query": query,
                  "documents": [c["content"] for c in chunks], "top_n": top_n},
            timeout=RERANK_TIMEOUT,
        )
        r.raise_for_status()
        results = r.json()["results"]      # [{index, relevance_score}, ...] 按分数降序
    except Exception:  # noqa: BLE001 —— 精排失败降级用 RRF 顺序，不阻断主链路
        return None

    reranked = []
    for item in results:
        chunk = dict(chunks[item["index"]])            # 复制原 dict，不污染候选池
        chunk["relevance_score"] = round(item["relevance_score"], 4)
        reranked.append(chunk)
    return reranked


# ============================================================
# 混合检索主入口（两路召回 → RRF → cross-encoder 精排）
# ============================================================
HYBRID_CANDIDATES = 20    # 融合后送精排的候选池大小（召回求全，精排求准）


def search_hybrid(zh_query: str, top_k: int = TOP_K) -> list[dict]:
    """混合检索：两路召回（向量中文 + 全文中文）→ RRF 融合 → Reranker 精排。

    英文查询路已下线（实验 8/对照实验数据）：BGE-M3 换代后中文 query 单路即可
    命中英文教材（Hit@10 0→6），开/关英文路命中持平（13/20 vs 14/20）而关路
    检索延迟省 43%（0.78s vs 1.36s），线上另省一次 LLM 英译改写调用。
    rerank 用中文问题直接精排（冒烟测试实测跨语 relevance_score 0.989，
    英文 chunk 可被中文问题正确排序）；失败降级用 RRF 顺序（不阻断主链路）。
    """
    rank_lists = [
        search(zh_query, HYBRID_CANDIDATES),          # ① 向量·中文（BGE-M3 跨语直接命中英文块）
        search_lexical(zh_query, HYBRID_CANDIDATES),  # ② 全文·中文（字面匹配强项）
    ]
    candidates = rrf_fuse(rank_lists, HYBRID_CANDIDATES)

    reranked = rerank_chunks(zh_query, candidates, top_k)
    if reranked:
        return reranked
    return candidates[:top_k]                         # 精排失败的降级路径
