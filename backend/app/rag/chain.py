"""
生成链：Prompt 组装 + LLM 流式生成（LangChain ChatOpenAI）

Prompt 结构（三条消息）：
    system  角色设定 + 回答规则（依据资料、标注引用编号）
    history 多轮对话历史（由 memory 模块裁剪后传入）
    human   检索到的知识块（带 [编号]）+ 本次问题
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.core.config import API_BASE_URL, API_KEY, CHAT_MODEL

SYSTEM_PROMPT = """你是一名气象知识助手。请严格遵守：
1. 只依据下面提供的【参考资料】回答，资料中没有的内容要明确说"参考资料中未涉及"。
2. 回答中不要出现 [1]、[2] 之类的引用编号标注（来源清单由系统另行展示），直接用自然语言作答。
3. 回答使用简体中文，条理清晰；实时天气问题（如今日气温）不在资料范围内，请如实说明。
4. 与天气预警相关的问题，优先给出预警含义与防御措施。"""

CHITCHAT_SYSTEM_PROMPT = """你是"气象 RAG 智能问答助手"，一个基于检索增强生成（RAG）的气象领域问答系统，
底层由 FastAPI 服务 + pgvector 向量知识库（2675 条气象知识块）+ 大语言模型组成。
面对问候、闲聊、身份询问等非知识类输入时：友好、简洁地回应，自然介绍自己的能力
（气象知识问答、预警信号解读、来源引用），并引导用户提出气象问题。不要编造资料类答案。"""

# 意图分类：knowledge 走 RAG / chitchat 直接对话 / weather 调和风 API（LangGraph 路由）
INTENT_PROMPT = """判断用户输入的意图类别，按下面的格式输出一行，不要其他内容：
- 输入是问候、闲聊、身份询问（如"你是谁""你好"）→ 只输出：chitchat
- 输入要查询某地当前/实时天气（如"北京今天多少度""上海天气怎么样"）→ 输出：weather 城市名
  （城市名从输入中提取，如"weather 北京"；未提及城市则输出：weather 默认）
- 输入涉及灾害之间的关系或防御链条——出现"引发、导致、次生、连锁、带来什么、有什么关系、
  该怎么防、防御措施"等表述（如"台风会引发什么次生灾害""暴雨引发了洪水怎么办"）→ 只输出：graph
- 其余的气象知识/原理/含义类单点问题（"是什么""为什么""什么含义"）→ 只输出：knowledge

注意：只要问到"某灾害的防御措施"或"某灾害引发/导致的其他灾害"，一律 graph，不是 knowledge。

用户输入：{question}"""

CONTEXT_TEMPLATE = """【参考资料】
{context}

【用户问题】
{question}"""

# 指代消解：把"其中最严重的？"这类依赖上下文的问题改写成独立问题（检索质量的关键）
CONDENSE_PROMPT = """你是气象知识库的查询预处理器。根据对话历史处理用户的最新问题，输出两行：
第 1 行以 "ZH:" 开头，是消解了代词指代、独立完整的中文问题；
第 2 行以 "EN:" 开头，是该中文问题的英文翻译（气象术语准确，如台风=tropical cyclone/typhoon）。
只输出这两行，不要任何其他内容。

对话历史：
{history}

最新问题：{question}"""


def classify_intent(question: str) -> tuple[str, str]:
    """意图分类，返回 (intent, city)。

    intent ∈ {knowledge, chitchat, weather, graph}；city 仅 weather 时有值。
    解析失败降级走 knowledge——错走 RAG 只是回答保守，错走闲聊才会答非所问。
    """
    llm = ChatOpenAI(
        model=CHAT_MODEL,
        api_key=API_KEY,
        base_url=API_BASE_URL,
        temperature=0.0,
        timeout=15,
        max_retries=1,
        max_tokens=16,                                # "weather 城市名" 足够
        extra_body={"enable_thinking": False},
    )
    try:
        out = llm.invoke(INTENT_PROMPT.format(question=question)).content.strip().lower()
        if out.startswith("weather"):
            city = out[len("weather"):].strip() or "北京"
            return "weather", "北京" if city == "默认" else city
        if "chitchat" in out:
            return "chitchat", ""
        if "graph" in out:
            return "graph", ""
        return "knowledge", ""
    except Exception:  # noqa: BLE001 —— 分类失败降级走知识路
        return "knowledge", ""


def rewrite_query(question: str, history: list) -> tuple[str, str]:
    """查询预处理：指代消解（中文）+ 英文翻译，返回 (中文查询, 英文查询)。

    两个预处理都为检索服务：
    - 指代消解：指代句（"其中最严重的？"）嵌入语义泛化，top-k 会跑偏；
    - 英文翻译：知识库中英文混合，Qwen3-Embedding 跨语言对齐弱（实测中文
      "台风怎么形成"召不回英文台风章，见 experiments.md 实验 4），
      需双语两路检索互补。无历史时中文路直接用原话，仅翻译。
    """
    history_text = "\n".join(
        f"{'用户' if m.type == 'human' else '助手'}: {m.content}" for m in history
    ) if history else "（无）"
    llm = ChatOpenAI(
        model=CHAT_MODEL,
        api_key=API_KEY,
        base_url=API_BASE_URL,
        temperature=0.0,                               # 预处理任务要确定性
        timeout=30,
        max_retries=1,
        max_tokens=150,                                # 两行短问题足够
        extra_body={"enable_thinking": False},         # 同 get_llm，关闭思考保低延迟
    )
    try:
        out = llm.invoke(CONDENSE_PROMPT.format(history=history_text, question=question)).content
        zh = en = ""
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("ZH:"):
                zh = line[3:].strip()
            elif line.startswith("EN:"):
                en = line[3:].strip()
        return (zh or question), (en or question)      # 解析失败降级用原话
    except Exception:  # noqa: BLE001 —— 预处理失败不阻断问答，降级用原话
        return question, question


def get_llm() -> ChatOpenAI:
    """聊天模型实例：流式输出、低 temperature（事实型问答求稳）。"""
    return ChatOpenAI(
        model=CHAT_MODEL,
        api_key=API_KEY,
        base_url=API_BASE_URL,
        temperature=0.2,
        streaming=True,
        timeout=60,           # 外部调用必设超时（规范 2.5）
        max_retries=1,        # 连接类错误重试 1 次；流式开始后断流不重试（防重复输出）
        max_tokens=1024,
        extra_body={"enable_thinking": False},   # 思考型模型流式首字节延迟 30s+（tests/smoke_llm_stream.py 实测，pitfalls 第 9 条）
    )


def build_context(chunks: list[dict]) -> str:
    """知识块列表 → 带编号的参考资料文本（编号与最终 sources 事件一一对应）。"""
    parts = []
    for i, c in enumerate(chunks, 1):
        src = c["source"]
        page = f" 第{c['page']}页" if c["page"] else ""      # 网页块 page=0 不显示页码
        parts.append(f"[{i}] （来源：{src}{page}）\n{c['content']}")
    return "\n\n".join(parts)


def build_prompt() -> ChatPromptTemplate:
    """三段式对话 Prompt 模板（history 由路由层按会话填充）。"""
    return ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        ("placeholder", "{history}"),          # MessagesPlaceholder 的简写形式
        ("human", CONTEXT_TEMPLATE),
    ])


# 追问推荐：根据本轮问答预测用户下一步最可能问什么（ChatGPT/Perplexity 同款功能）
FOLLOWUP_PROMPT = """根据用户的当前问题和助手的回答，预测用户接下来最可能追问的 3 个问题。

要求：
1. 口语化，像用户真的会打出来的话（带"呢/吗/怎么"等语气）
2. 与当前话题连续（深入细节 / 横向相关 / 实际应用，方向错开）
3. 只输出 JSON 数组，如 ["问题1", "问题2", "问题3"]，不要任何其他文字

用户问题：{question}

助手回答：{answer}"""


def suggest_followups(question: str, answer: str) -> list[str]:
    """生成本轮回答后的追问建议（最多 3 个）。失败返回空列表（增强项不阻断）。"""
    import json
    import re

    llm = ChatOpenAI(
        model=CHAT_MODEL,
        api_key=API_KEY,
        base_url=API_BASE_URL,
        temperature=0.7,                      # 追问求多样性，温度高于问答的 0.2
        timeout=15,
        max_retries=1,
        max_tokens=200,
        extra_body={"enable_thinking": False},
    )
    try:
        resp = llm.invoke(FOLLOWUP_PROMPT.format(
            question=question, answer=answer[:800])).content.strip()
        m = re.search(r"\[.*\]", resp, re.DOTALL)      # 容错：剥出 JSON 数组（防前后缀杂字）
        if not m:
            return []
        items = [q for q in json.loads(m.group()) if isinstance(q, str) and 4 <= len(q) <= 60]
        return items[:3]
    except Exception:  # noqa: BLE001 —— 追问生成失败静默跳过，不影响回答
        return []
