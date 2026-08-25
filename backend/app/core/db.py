"""
PostgreSQL 连接池（模块级单例 + 线程锁，代码审查 P0-1）

为什么需要池：psycopg2.connect 每次都要 TCP 三次握手 + 认证（毫秒级开销），
混合检索一次请求要查 3 次、高并发下连接数线性膨胀打满 PG max_connections。
SimpleConnectionPool 复用连接，开销降为借还（微秒级）。
"""

import threading
from contextlib import contextmanager

import psycopg2.pool

from app.core.config import PG_DSN

_POOL: psycopg2.pool.SimpleConnectionPool | None = None
_LOCK = threading.Lock()             # 保护惰性初始化（避免并发首请求建多个池）


def _get_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _POOL
    if _POOL is None:
        with _LOCK:
            if _POOL is None:        # 双重检查：锁内再判一次
                _POOL = psycopg2.pool.SimpleConnectionPool(
                    1, 10, PG_DSN)   # 最小 1 / 最大 10 连接（单机演示够用，生产再上调）
    return _POOL


@contextmanager
def pg_conn():
    """借出连接的上下文管理器：正常提交、异常回滚、最终归还。

    用法：with pg_conn() as conn: conn.cursor().execute(...)
    """
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)
