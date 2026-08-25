"""
知识图谱查询模块：实体匹配 → Cypher 多跳子图 → 三元组文本化（GraphRAG 式检索）

与向量检索的分工（第 5 期架构定位）：
    向量检索回答"是什么"（单点知识，语义相似召回）；
    图谱检索回答"有什么关系"（多跳推理，如 台风-引发->风暴潮-防御->停止海上作业）。
子图作为额外上下文注入生成，LLM 沿着关系链路推理——这是向量检索做不到的
（向量只能召回彼此独立的文本块，块与块之间的结构关系在嵌入时已丢失）。

实体匹配用"词典法"：节点名作为子串出现在问题中即命中（中文无空格分词歧义，
"台风" in "台风来了该怎么防御" 直接判断）。曾试过把全部实体名塞给 LLM 做
受限选择，183 实体时可用、扩到 890 后清单过长导致选择质量下降且延迟涨到 30s
（docs/experiments.md 实验 6），改词典法后省一次 LLM 调用且毫秒级。
"""

from neo4j import GraphDatabase

from app.core.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

# 关系类型 → 中文（生成时展示用；与 build_knowledge_graph.py 的枚举一一对应）
REL_ZH = {"CAUSES": "引发", "DEFENDS": "防御措施", "CAUSED_BY": "成因",
          "BELONGS_TO": "属于", "ACCOMPANIES": "伴随"}

_DRIVER = None


def _driver():
    """Neo4j 连接（模块级单例复用，避免每请求握手）。"""
    global _DRIVER
    if _DRIVER is None:
        _DRIVER = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _DRIVER


def _match_entities(question: str, entities: list[str]) -> list[str]:
    """词典匹配：节点名（≥2 字）作为子串出现在问题中即命中，最多取 8 个防子图爆炸。"""
    return [e for e in entities if len(e) >= 2 and e in question][:8]


# 子图查询：三段 UNION 各自限额，保证结构多样性（实测教训：单池限额时
# 台风的几十条 DEFENDS 边会占满名额，把 CAUSES 灾害链挤出去）。
#   ① 灾害链边（引发/成因/属于/伴随）——回答"会引发什么"的骨架
#   ② 防御措施边（DEFENDS）——回答"怎么防"
#   ③ 二跳链边（只沿 CAUSES/CAUSED_BY 扩展，避免措施×措施组合爆炸）
# -(无方向)- 匹配后用 CASE 按 startNode 还原真实方向（入库时 MERGE 全部有向）；
# Python 端去重后总截断 40 条（子图过大 prompt 会溢出）
SUBGRAPH_CYPHER = """
    MATCH (a)-[r]-(b)
    WHERE a.name IN $names
      AND type(r) IN ['CAUSES', 'CAUSED_BY', 'BELONGS_TO', 'ACCOMPANIES']
    RETURN CASE WHEN startNode(r) = a THEN a.name ELSE b.name END AS head,
           CASE WHEN startNode(r) = a THEN labels(a)[0] ELSE labels(b)[0] END AS head_type,
           type(r) AS relation,
           CASE WHEN startNode(r) = a THEN b.name ELSE a.name END AS tail,
           CASE WHEN startNode(r) = a THEN labels(b)[0] ELSE labels(a)[0] END AS tail_type
    LIMIT 20
    UNION
    MATCH (a)-[r:DEFENDS]->(b)
    WHERE a.name IN $names AND type(r) = 'DEFENDS'
    RETURN CASE WHEN startNode(r) = a THEN a.name ELSE b.name END AS head,
           CASE WHEN startNode(r) = a THEN labels(a)[0] ELSE labels(b)[0] END AS head_type,
           type(r) AS relation,
           CASE WHEN startNode(r) = a THEN b.name ELSE a.name END AS tail,
           CASE WHEN startNode(r) = a THEN labels(b)[0] ELSE labels(a)[0] END AS tail_type
    ORDER BY CASE WHEN r.source STARTS WITH 'web:预警信号知识卡' THEN 0 ELSE 1 END
    LIMIT 15
    UNION
    MATCH (a)-[r1]-(m)-[r2]-(b)
    WHERE a.name IN $names AND m <> a AND b <> a AND b <> m
      AND type(r1) IN ['CAUSES', 'CAUSED_BY'] AND type(r2) IN ['CAUSES', 'CAUSED_BY']
    RETURN CASE WHEN startNode(r2) = m THEN m.name ELSE b.name END AS head,
           CASE WHEN startNode(r2) = m THEN labels(m)[0] ELSE labels(b)[0] END AS head_type,
           type(r2) AS relation,
           CASE WHEN startNode(r2) = m THEN b.name ELSE m.name END AS tail,
           CASE WHEN startNode(r2) = m THEN labels(b)[0] ELSE labels(m)[0] END AS tail_type
    LIMIT 15
"""


def query_graph(question: str) -> dict:
    """图谱检索主入口。返回 {triples, context}；无命中返回 {triples: [], context: ""}。

    调用方（LangGraph 路由）在 context 为空时降级走向量检索知识路——
    图谱是增强通路，不能让它的问题挡住回答。
    """
    with _driver().session() as s:
        entities = [r["name"] for r in s.run("MATCH (n) RETURN n.name AS name")]
        if not entities:
            return {"triples": [], "context": ""}

        picked = _match_entities(question, entities)
        if not picked:
            return {"triples": [], "context": ""}

        rows = s.run(SUBGRAPH_CYPHER, names=picked).data()

    triples, seen = [], set()          # UNION 三段可能有重复边，去重
    for r in rows:
        key = (r["head"], r["relation"], r["tail"])
        if key in seen:
            continue
        seen.add(key)
        triples.append({"head": r["head"], "head_type": r["head_type"],
                        "relation": REL_ZH.get(r["relation"], r["relation"]),
                        "tail": r["tail"], "tail_type": r["tail_type"]})
    triples = triples[:40]             # 总量截断（三段上限 50，合并去重后留 40）

    context = "\n".join(f"- {t['head']} -[{t['relation']}]-> {t['tail']}"
                        for t in triples)     # 三元组文本化：LLM 可直接沿链路推理
    return {"triples": triples, "context": context}
