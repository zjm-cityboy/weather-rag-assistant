"""
气象 RAG · 数据库初始化：建库 → 扩展 → 表 → 约束 → 索引

全部语句幂等（IF NOT EXISTS / 异常吞并 / SET NOT NULL 天然可重复），
已初始化的环境重复执行无副作用，新环境一键建齐。

前置条件：weather-pg 容器运行中（见 README 数据库章节）。
"""

import os

import psycopg2

# 连接参数支持 env 覆盖（Docker Compose 里 host=db）；库名固定常量——
# CREATE DATABASE 语法不支持参数绑定，消除动态源即消除注入面（compose 的建库由 POSTGRES_DB 完成）
PG_HOST = os.getenv("PGHOST", "localhost")
PG_PORT = int(os.getenv("PGPORT", "5432"))
PG_USER = os.getenv("PGUSER", "postgres")
PG_PASSWORD = os.getenv("PGPASSWORD", "weather_dev_2026")
DB_NAME = "weather"

# ============================================================
# 1. 建库（CREATE DATABASE 不支持 IF NOT EXISTS，先查系统表）
# ============================================================
def ensure_database() -> None:
    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname="postgres",
                            user=PG_USER, password=PG_PASSWORD)
    conn.autocommit = True                      # CREATE DATABASE 不能在事务内执行
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (DB_NAME,))
    if cur.fetchone():
        print(f"[1] 数据库 {DB_NAME} 已存在，跳过建库")
    else:
        cur.execute("CREATE DATABASE weather;")   # 库名为模块常量，见文件头说明
        print(f"[1] 已创建数据库 {DB_NAME}")
    conn.close()


# ============================================================
# 2. 表结构（含 pgvector 扩展；图谱数据在第 5 期起存 Neo4j，不建 PG 表）
# ============================================================
DDL_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS vector;",
    """
    CREATE TABLE IF NOT EXISTS knowledge_chunks (
        id            BIGSERIAL PRIMARY KEY,
        content       TEXT NOT NULL,
        source        TEXT,
        page          INTEGER,
        content_type  TEXT,
        embedding     VECTOR(1024),
        created_at    TIMESTAMP DEFAULT now(),
        url           TEXT,
        content_hash  TEXT
    );
    """,
    # 第 6 期：注册登录的用户表（密码只存 bcrypt 哈希，绝不存明文）
    """
    CREATE TABLE IF NOT EXISTS users (
        id            BIGSERIAL PRIMARY KEY,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        created_at    TIMESTAMP DEFAULT now()
    );
    """,
    # 会话表：多轮对话历史持久化（重启不丢、多副本共享——代码审查 P0-3）
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id         BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        role       TEXT NOT NULL CHECK (role IN ('human', 'ai')),
        content    TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT now()
    );
    """,
]

# ============================================================
# 3. 约束（ADD CONSTRAINT 无 IF NOT EXISTS，用异常吞并实现幂等）
# ============================================================
CONSTRAINT_STATEMENTS = [
    "ALTER TABLE knowledge_chunks ALTER COLUMN content_hash SET NOT NULL;",   # 天然幂等
    "ALTER TABLE knowledge_chunks ALTER COLUMN embedding SET NOT NULL;",
    """
    DO $$
    BEGIN
        ALTER TABLE knowledge_chunks ADD CONSTRAINT uq_content UNIQUE (content_hash);
    EXCEPTION
        WHEN duplicate_object THEN NULL;
        WHEN duplicate_table THEN NULL;   -- 约束底层是索引，已存在时报 DuplicateTable（pitfalls 第 8 条）
    END $$;
    """,
    """
    DO $$
    BEGIN
        ALTER TABLE knowledge_chunks ADD CONSTRAINT chk_page CHECK (page >= 0);
    EXCEPTION
        WHEN duplicate_object THEN NULL;
        WHEN duplicate_table THEN NULL;
    END $$;
    """,
]

# ============================================================
# 4. 索引
# ============================================================
INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_chunks_source ON knowledge_chunks (source);",
    """CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON knowledge_chunks
       USING hnsw (embedding vector_cosine_ops)
       WITH (m = 16, ef_construction = 64);""",
    # 第 4 期：全文检索（词法分支）。content_tokens 存 jieba 分词结果（空格分隔），
    # tsv 为 STORED 生成列自动维护；'simple' 配置按空格切词不做词干还原，切词由应用层 jieba 负责
    "CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON knowledge_chunks USING GIN (tsv);",
    # 会话按 session_id 顺序读（最近 N 条）
    "CREATE INDEX IF NOT EXISTS idx_sessions_sid ON sessions (session_id, id);",
]

# ============================================================
# 5. 全文检索列（第 4 期新增，幂等）
# ============================================================
FTS_STATEMENTS = [
    "ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS content_tokens TEXT;",
    """ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS tsv tsvector
       GENERATED ALWAYS AS (to_tsvector('simple', coalesce(content_tokens, ''))) STORED;""",
]


def main() -> None:
    ensure_database()                                # 步骤 1：建库
    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=DB_NAME,
                            user=PG_USER, password=PG_PASSWORD)
    cur = conn.cursor()
    for sql in DDL_STATEMENTS:                       # 步骤 2：扩展与表
        cur.execute(sql)
    print("[2] 扩展与表结构就绪（已存在则跳过）")
    for sql in CONSTRAINT_STATEMENTS:                # 步骤 3：约束
        cur.execute(sql)
    print("[3] 约束就绪：content_hash/embedding NOT NULL，UNIQUE(content_hash)，CHECK(page>=0)")
    for sql in FTS_STATEMENTS:                       # 步骤 4：全文检索列（先加列，GIN 索引依赖它）
        cur.execute(sql)
    print("[4] 全文检索列就绪：content_tokens + tsv 生成列（入库由 ingest 写入分词）")
    for sql in INDEX_STATEMENTS:                     # 步骤 5：索引（B-tree/HNSW/GIN）
        cur.execute(sql)
    print("[5] 索引就绪：B-tree(source)，HNSW(embedding)，GIN(tsv)")
    conn.commit()

    cur.execute("SELECT count(*) FROM knowledge_chunks;")   # 步骤 6：现状汇报
    print(f"[6] 初始化完成，knowledge_chunks 现有 {cur.fetchone()[0]} 条")
    conn.close()


if __name__ == "__main__":
    main()
