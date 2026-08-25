"""
构建灾害预警知识图谱：预警知识卡 → LLM 三元组抽取 → Neo4j 入库

数据流：PG knowledge_chunks（source 为预警知识卡的块）→ LLM 抽取
(head, relation, tail) 三元组 → Cypher MERGE 写入 Neo4j（幂等可重跑）。

图模型：
    节点 label：Disaster 灾害 / Phenomenon 现象 / Measure 防御措施 /
               Condition 成因条件 / Signal 预警信号（name 属性存中文实体名）
    关系类型：CAUSES 引发 / DEFENDS 防御 / CAUSED_BY 成因 / BELONGS_TO 属于 / ACCOMPANIES 伴随
MERGE 语义：按 name / (src, relation, dst) 判存在——重复抽取自动去重，不产生重复数据。
"""

import json
import re
import sys
from pathlib import Path

import psycopg2
from langchain_openai import ChatOpenAI
from neo4j import GraphDatabase

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.core.config import (
    API_BASE_URL,
    API_KEY,
    CHAT_MODEL,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USER,
    PG_DSN,
)

# 三元组抽取 Prompt：类型与关系全部枚举约束，输出纯 JSON 数组（防自由发挥）
EXTRACT_PROMPT = """你是气象灾害知识图谱的信息抽取器。从下面的文本中抽取知识三元组。

规则：
1. head/tail 的类型只能从这五种选：Disaster(灾害), Phenomenon(气象现象), Measure(防御措施), Condition(成因条件), Signal(预警信号)
2. relation 只能从这五种选：CAUSES(引发), DEFENDS(防御/需要采取), CAUSED_BY(成因是), BELONGS_TO(属于), ACCOMPANIES(伴随)
3. DEFENDS 的方向固定为：灾害 DEFENDS 措施（表示"防御该灾害的措施是它"）
4. 实体名用简短中文短语（2~8 字），不要整句
5. 只输出 JSON 数组，不要任何其他文字：[{{"head":"台风","head_type":"Disaster","relation":"CAUSES","tail":"风暴潮","tail_type":"Phenomenon"}}]
6. 文本中没有可抽取的关系就输出 []

文本：{text}"""

# 卡片来源过滤条件（预警知识卡 + 应急预案是结构化程度最高的语料，关系密度大）
CARD_SOURCE_LIKE = "web:预警信号知识卡%"
PLAN_SOURCE_LIKE = "%应急预案%"


def main() -> None:
    llm = ChatOpenAI(model=CHAT_MODEL, api_key=API_KEY, base_url=API_BASE_URL,
                     temperature=0.0, timeout=60, max_retries=1,
                     extra_body={"enable_thinking": False})   # 非思考模式，输出即答案（pitfalls 第 9 条）

    # ========================================================
    # 步骤 0：name 唯一约束（自带索引；MERGE 按名字判重与查询都吃这个索引，幂等）
    # ========================================================
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    with driver.session() as s:
        for label in ("Disaster", "Phenomenon", "Measure", "Condition", "Signal"):
            s.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.name IS UNIQUE")
    print("[0] name 唯一约束就绪（5 类节点）")

    # ========================================================
    # 步骤 1：从 PG 读语料分块（预警知识卡 + 应急预案，关系密度最高的两类）
    # ========================================================
    conn = psycopg2.connect(PG_DSN)
    with conn.cursor() as cur:
        cur.execute("SELECT source, content FROM knowledge_chunks "
                    "WHERE source LIKE ANY(%s) ORDER BY id;",
                    ([CARD_SOURCE_LIKE, PLAN_SOURCE_LIKE],))
        cards = cur.fetchall()
    conn.close()
    print(f"[1] 语料分块 {len(cards)} 条（预警知识卡 + 应急预案）")

    # ========================================================
    # 步骤 2：逐块 LLM 抽取三元组（JSON 解析失败则跳过该块）
    #   每块一次 LLM 调用约 1~2s，全程数分钟——每 10 块打印进度（168 块不可静默跑）
    # ========================================================
    triples: list[dict] = []
    for i, (source, content) in enumerate(cards, 1):
        resp = llm.invoke(EXTRACT_PROMPT.format(text=content[:1500])).content.strip()
        m = re.search(r"\[.*\]", resp, re.DOTALL)   # 容错：剥出 JSON 数组部分（防前后缀杂字）
        if m:
            try:
                items = json.loads(m.group())
                triples.extend({"source": source, **t} for t in items
                               if isinstance(t, dict) and
                               {"head", "head_type", "relation", "tail", "tail_type"} <= t.keys())
            except json.JSONDecodeError:
                pass
        if i % 10 == 0 or i == len(cards):
            print(f"[2] 进度 {i}/{len(cards)} 块，累计三元组 {len(triples)} 条", flush=True)

    # ========================================================
    # 步骤 3：Cypher MERGE 入库（幂等：节点按 name、关系按三元组判重）
    # ========================================================
    with driver.session() as s:
        # 节点/关系写入：Cypher 的 label 与关系类型不能参数化（语言限制），
        # 故用白名单校验 + f-string 拼接（类型已由 Prompt 枚举约束 + 此处过滤双保险）
        VALID_LABELS = {"Disaster", "Phenomenon", "Measure", "Condition", "Signal"}
        VALID_RELS = {"CAUSES", "DEFENDS", "CAUSED_BY", "BELONGS_TO", "ACCOMPANIES"}
        for t in triples:
            if (t["head_type"] not in VALID_LABELS or t["tail_type"] not in VALID_LABELS
                    or t["relation"] not in VALID_RELS):
                continue
            s.run(f"""
                MERGE (h:{t['head_type']} {{name: $head}})
                MERGE (t:{t['tail_type']} {{name: $tail}})
                MERGE (h)-[r:{t['relation']}]->(t)
                SET r.source = $source
            """, head=t["head"], tail=t["tail"], source=t["source"])

        # ========================================================
        # 步骤 4：统计汇报
        # ========================================================
        n_nodes = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        n_edges = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        by_label = s.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(n) AS c ORDER BY c DESC").data()
        by_rel = s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c ORDER BY c DESC").data()
    driver.close()

    print(f"[3] 入库完成：节点 {n_nodes}，关系 {n_edges}")
    print("    节点分布：", {d["l"]: d["c"] for d in by_label})
    print("    关系分布：", {d["t"]: d["c"] for d in by_rel})


if __name__ == "__main__":
    main()
