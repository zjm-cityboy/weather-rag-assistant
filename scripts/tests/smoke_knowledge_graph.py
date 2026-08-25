"""
冒烟测试：验证知识图谱构建与查询链路（Neo4j 连通 → 实体匹配 → 多跳子图）

覆盖三层：①Bolt 连接与图规模 ②实体词典匹配 ③子图查询的关系多样性
（灾害链 + 防御措施都有输出，防"单类关系占满名额"的回归）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.core.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from app.graph.knowledge_graph import _match_entities, query_graph
from neo4j import GraphDatabase

# ==== 步骤 1：Bolt 连接与图规模 ====
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
with driver.session() as s:
    n_nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    n_edges = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
driver.close()
print(f"[1] Neo4j 连接成功：{n_nodes} 节点，{n_edges} 关系")
assert n_nodes > 0, "图谱为空，先跑 scripts/data/build_knowledge_graph.py"

# ==== 步骤 2：实体词典匹配 ====
entities = ["台风", "暴雨", "不存在的实体xyz"]
picked = _match_entities("台风和暴雨有什么关系", entities)
print(f"[2] 实体匹配：{picked}")
assert picked == ["台风", "暴雨"], "实体匹配异常"

# ==== 步骤 3：子图查询（关系多样性）====
result = query_graph("台风会引发哪些次生灾害该怎么防御")
rels = {t["relation"] for t in result["triples"]}
print(f"[3] 子图 {len(result['triples'])} 条三元组，关系类型：{sorted(rels)}")
assert "引发" in rels, "灾害链关系缺失"
assert "防御措施" in rels, "防御措施关系缺失"
print("结论：图谱链路正常")
