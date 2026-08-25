"""
/ask 路由：SSE 流式问答接口（LangGraph 意图路由驱动）

请求：POST /ask  {"session_id": "...", "question": "..."}
响应：text/event-stream，事件序列：
    event: token        data: {"text": "..."}       ← 逐块生成内容（打字机）
    event: sources      data: [{...}, ...]          ← knowledge 路的引用来源清单
    event: weather_data data: {"city","now",...}    ← weather 路的原始实况（前端渲染卡片）
    event: graph_data   data: [{head,relation,tail}]  ← graph 路的子图三元组（前端渲染关系图）
    event: suggestions  data: ["问题1","问题2","问题3"] ← 追问推荐（回答完成后生成）
    event: done         data: {"session_id","meta"} ← 本轮流结束（meta 含意图/耗时/检索统计）
    event: error        data: {"message": "..."}    ← 任一环节失败

路由结构（app/graph/graph.py）：
    classify → { knowledge: 检索+RAG ｜ chitchat: 直接对话 ｜ weather: 和风API+播报 ｜ graph: 图谱 }
"""

import json
import logging
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.auth import verify_token
from app.graph.graph import build_graph
from app.memory import store
from app.rag.chain import suggest_followups

logger = logging.getLogger(__name__)

router = APIRouter()

# 图编译单例：StateGraph 构建+compile 只做一次（compile 产物线程安全可复用，
# 每请求重建是纯开销——代码审查 P0-2）
_GRAPH = build_graph()


class AskRequest(BaseModel):
    """请求体模型（Pydantic 校验：缺字段/类型错直接 422）。"""
    session_id: str = Field(min_length=1, description="会话标识，多轮对话的键")
    question: str = Field(min_length=1, max_length=500, description="用户问题")


def sse_event(event: str, data) -> str:
    """组装一条 SSE 事件（data 统一 JSON 序列化）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask")
def ask(req: AskRequest, username: str = Depends(verify_token)) -> StreamingResponse:
    """问答接口（登录后可用；会话隔离由前端按用户生成 session_id 实现）。"""
    def generate():
        t0 = time.time()
        # SSE 注释行立即下发：防止中间代理缓冲、客户端尽早建立连接（代码审查 P1-2）
        yield ": connected\n\n"
        final_state: dict = {}
        try:
            # ① 运行编译好的图：custom 流收 token 事件，values 流收节点状态
            last_intent = ""
            for mode, payload in _GRAPH.stream(
                {"question": req.question, "history": store.get_history(req.session_id),
                 "intent": "", "city": "", "weather": {}, "chunks": [],
                 "subgraph": "", "subgraph_triples": [], "answer": ""},
                stream_mode=["custom", "values"],
            ):
                if mode == "custom" and payload.get("type") == "token":
                    yield sse_event("token", {"text": payload["text"]})
                elif mode == "values":
                    final_state = payload
                    if payload.get("intent") and payload["intent"] != last_intent:
                        last_intent = payload["intent"]
                        logger.info("[%s] 意图=%s (%.1fs)", req.session_id,
                                    last_intent, time.time() - t0)

            # ② knowledge 路推送引用来源（编号与参考资料一一对应）
            chunks = final_state.get("chunks") or []
            if chunks:
                sources = [{"no": i, "source": c["source"], "page": c["page"],
                            "url": c["url"], "distance": c["distance"]}
                           for i, c in enumerate(chunks, 1)]
                yield sse_event("sources", sources)

            # ③ weather 路推送原始实况（前端渲染结构化天气卡片）
            weather = final_state.get("weather") or {}
            if weather.get("now"):
                yield sse_event("weather_data", weather)

            # ④ graph 路推送子图三元组（前端渲染力导向关系图）
            triples = final_state.get("subgraph_triples") or []
            if triples:
                yield sse_event("graph_data", triples)

            # ⑤ 写入会话记忆 → 通知前端（meta 带本轮量化信息）
            top = chunks[0].get("relevance_score") or chunks[0].get("rrf_score") if chunks else None
            yield sse_event("done", {
                "session_id": req.session_id,
                "meta": {"intent": final_state.get("intent", ""),
                         "elapsed": round(time.time() - t0, 1),
                         "n_chunks": len(chunks),
                         "top_score": top},
            })
            logger.info("[%s] 完成 intent=%s (%.1fs)", req.session_id,
                        final_state.get("intent"), time.time() - t0)

            # ⑥ 追问推荐后置于 done：回答先结束（loading 立即停），建议晚 ~1s 到不阻塞感知
            followups = suggest_followups(req.question, final_state.get("answer", ""))
            if followups:
                yield sse_event("suggestions", followups)

        except Exception as e:  # noqa: BLE001 —— SSE 流内异常无法走 HTTP 状态码，转 error 事件
            logger.warning("[%s] 异常 (%.1fs): %s", req.session_id, time.time() - t0, e)
            yield sse_event("error", {"message": f"生成失败：{e}"})
        finally:
            # 客户端中途断开（GeneratorExit）也要落记忆——本轮问答已消耗计算（代码审查 P1-2）
            if final_state.get("answer"):
                store.append_round(req.session_id, req.question, final_state["answer"])

    return StreamingResponse(generate(), media_type="text/event-stream")
