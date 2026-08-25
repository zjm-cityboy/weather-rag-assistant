"""
会话记忆：session_id → 对话历史（PostgreSQL sessions 表持久化）

第 1 期为内存 dict 实现；按代码审查 P0-3 升级为 PG 存储：
重启不丢、多副本部署共享、内存无泄漏风险。接口签名保持不变
（get_history / append_round），业务代码零改动——当初按接口抽象的回报。
"""

from langchain_core.messages import AIMessage, HumanMessage

from app.core.config import MAX_HISTORY_ROUNDS
from app.core.db import pg_conn


def get_history(session_id: str) -> list:
    """取该会话的历史消息（最近 MAX_HISTORY_ROUNDS 轮 = N*2 条，按时间正序返回）。

    子查询取最近 N*2 条（ORDER BY id DESC LIMIT）再反转为正序——
    直接 ORDER BY id ASC LIMIT 无法只取"最后"几条。
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
                SELECT role, content FROM (
                    SELECT id, role, content FROM sessions
                    WHERE session_id = %s
                    ORDER BY id DESC LIMIT %s
                ) recent ORDER BY recent.id ASC;
                """,
            (session_id, MAX_HISTORY_ROUNDS * 2))
        rows = cur.fetchall()

    cls = {"human": HumanMessage, "ai": AIMessage}
    return [cls[r[0]](content=r[1]) for r in rows]


def append_round(session_id: str, question: str, answer: str) -> None:
    """一轮对话结束后追加（问答成对写入，单事务）。"""
    with pg_conn() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO sessions (session_id, role, content) VALUES (%s, %s, %s);",
            [(session_id, "human", question), (session_id, "ai", answer)])
