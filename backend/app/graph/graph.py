"""
LangGraph 状态图：意图路由（knowledge / chitchat / weather / graph 四通路）

结构（README 核心架构"三种知识三条通路"的代码版，第 5 期扩展图谱路）：
                ┌─ chitchat ──→ answer_chitchat ──────────┐
    classify ───┤                                         ├→ END
                ├─ weather ──→ fetch_weather → answer_weather
                ├─ graph ───→ query_graph ──→ answer_graph（空子图降级走 retrieve）
                └─ knowledge → retrieve → answer_knowledge

流式：生成类节点用 get_stream_writer() 逐 token 推 custom 事件，
外层 graph.stream(..., stream_mode="custom") 消费（LangGraph 官方流式模式）。
"""

from typing import TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.graph import knowledge_graph
from app.rag import chain, retriever
from app.weather import client as qweather

WEATHER_SYSTEM_PROMPT = """你是天气播报员。依据提供的实况天气数据，用简体中文做一段简洁友好的播报：
包含气温、体感、天气现象、风向风力、湿度；结尾给一句贴心的出行建议。
数据是 JSON，不要编造数据里没有的字段。新版接口 humidity 为 0-1 小数（如 0.87 即 87%），
visibility 单位是米。若数据带 error 字段，如实告知获取失败并致歉；
若数据带 "mock": true，播报开头注明【演示数据】（未配置和风天气 API）。"""

GRAPH_SYSTEM_PROMPT = """你是灾害防御顾问。依据提供的知识图谱三元组回答问题，用简体中文：
1. 沿关系链路组织回答（如 X 引发 Y、防御 X 的措施是 Z），条理清晰；
2. 只依据给出的三元组，图谱未覆盖的部分明确说明；
3. 涉及防御措施时逐条列出，突出可操作性。"""


class State(TypedDict):
    """图状态：节点间传递的全部数据。"""
    question: str          # 用户原问题
    history: list          # 会话历史（消息列表）
    intent: str            # knowledge / chitchat / weather / graph
    city: str              # weather 路的目标城市
    weather: dict          # fetch_weather 的结果
    subgraph: str          # query_graph 的结果（三元组文本化后的子图）
    subgraph_triples: list # query_graph 的三元组（前端图谱可视化用）
    chunks: list           # retrieve 的结果（知识块）
    answer: str            # 最终回答全文（各 answer 节点累积）


# ============================================================
# 节点定义
# ============================================================
def classify_node(state: State) -> dict:
    """意图分类（+weather 时提取城市）。"""
    intent, city = chain.classify_intent(state["question"])
    return {"intent": intent, "city": city}


def fetch_weather_node(state: State) -> dict:
    """调和风 API 取实况天气（无 KEY 时 mock 演示数据）。"""
    return {"weather": qweather.fetch_now(state["city"])}


def answer_weather_node(state: State) -> dict:
    """天气播报生成（流式推 token 事件）。"""
    writer = get_stream_writer()
    prompt = f"实况天气数据：{state['weather']}\n\n请播报：{state['city']}现在的天气"
    parts: list[str] = []
    for chunk in chain.get_llm().stream([
        ("system", WEATHER_SYSTEM_PROMPT), ("human", prompt),
    ]):
        if chunk.content:
            parts.append(chunk.content)
            writer({"type": "token", "text": chunk.content})
    return {"answer": "".join(parts)}


def retrieve_node(state: State) -> dict:
    """混合检索：查询预处理（指代消解）→ 两路召回（向量+全文）RRF 融合 → rerank 精排。"""
    zh_q = chain.rewrite_query(state["question"], state["history"])
    return {"chunks": retriever.search_hybrid(zh_q)}


def query_graph_node(state: State) -> dict:
    """图谱检索：实体匹配 → Cypher 多跳子图（文本给生成，三元组给前端可视化）。"""
    result = knowledge_graph.query_graph(state["question"])
    return {"subgraph": result["context"], "subgraph_triples": result["triples"]}


def answer_graph_node(state: State) -> dict:
    """图谱问答生成：沿关系链路回答（流式推 token 事件）。"""
    writer = get_stream_writer()
    prompt = f"知识图谱三元组：\n{state['subgraph']}\n\n请回答：{state['question']}"
    parts: list[str] = []
    for chunk in chain.get_llm().stream([
        ("system", GRAPH_SYSTEM_PROMPT), ("human", prompt),
    ]):
        if chunk.content:
            parts.append(chunk.content)
            writer({"type": "token", "text": chunk.content})
    return {"answer": "".join(parts)}


def answer_knowledge_node(state: State) -> dict:
    """RAG 生成（流式推 token 事件）。"""
    writer = get_stream_writer()
    prompt = chain.build_prompt().invoke({
        "history": state["history"],
        "context": chain.build_context(state["chunks"]),
        "question": state["question"],
    })
    parts: list[str] = []
    for chunk in chain.get_llm().stream(prompt):
        if chunk.content:
            parts.append(chunk.content)
            writer({"type": "token", "text": chunk.content})
    return {"answer": "".join(parts)}


def answer_chitchat_node(state: State) -> dict:
    """闲聊/身份类直接对话（不检索，流式推 token 事件）。"""
    writer = get_stream_writer()
    prompt = chain.ChatPromptTemplate.from_messages([
        ("system", chain.CHITCHAT_SYSTEM_PROMPT),
        ("placeholder", "{history}"),
        ("human", "{question}"),
    ]).invoke({"history": state["history"], "question": state["question"]})
    parts: list[str] = []
    for chunk in chain.get_llm().stream(prompt):
        if chunk.content:
            parts.append(chunk.content)
            writer({"type": "token", "text": chunk.content})
    return {"answer": "".join(parts)}


# ============================================================
# 组装图
# ============================================================
def route_by_intent(state: State) -> str:
    """条件边：按意图分流到对应通路的首节点。"""
    return {"knowledge": "retrieve",
            "chitchat": "answer_chitchat",
            "weather": "fetch_weather",
            "graph": "query_graph"}[state["intent"]]


def route_after_graph(state: State) -> str:
    """条件边：图谱查到子图走生成；空子图（实体未命中）降级走向量知识路。"""
    return "answer_graph" if state.get("subgraph") else "retrieve"


def build_graph():
    """构建并编译状态图（compile 后可调用 .invoke / .stream）。"""
    g = StateGraph(State)
    g.add_node("classify", classify_node)
    g.add_node("fetch_weather", fetch_weather_node)
    g.add_node("answer_weather", answer_weather_node)
    g.add_node("query_graph", query_graph_node)
    g.add_node("answer_graph", answer_graph_node)
    g.add_node("retrieve", retrieve_node)
    g.add_node("answer_knowledge", answer_knowledge_node)
    g.add_node("answer_chitchat", answer_chitchat_node)

    g.add_edge(START, "classify")
    g.add_conditional_edges("classify", route_by_intent)
    g.add_edge("fetch_weather", "answer_weather")
    g.add_edge("answer_weather", END)
    g.add_conditional_edges("query_graph", route_after_graph)   # 空子图降级走 retrieve
    g.add_edge("answer_graph", END)
    g.add_edge("retrieve", "answer_knowledge")
    g.add_edge("answer_knowledge", END)
    g.add_edge("answer_chitchat", END)
    return g.compile()
