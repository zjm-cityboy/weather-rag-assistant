"""
冒烟测试：验证 Python hashlib.md5 与 PG md5() 对同一内容产出一致

用途：入库管线两侧（应用层 Python 计算 / 数据库层 md5()）的 content hash
必须同算法互认，exact dedup 才成立。改哈希算法或换数据库版本后重跑。
"""

import hashlib

import psycopg2

PG_DSN = "host=localhost port=5432 dbname=weather user=postgres password=weather_dev_2026"
SAMPLE_ROWS = 5     # 抽样比对行数


def main() -> None:
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor()
    cur.execute("SELECT id, content, content_hash FROM knowledge_chunks ORDER BY id LIMIT %s;",
                (SAMPLE_ROWS,))
    rows = cur.fetchall()
    conn.close()

    ok = 0
    for rid, content, db_hash in rows:
        py_hash = hashlib.md5(content.encode("utf-8")).hexdigest()   # 与 PG md5(content) 同算法
        match = "一致" if py_hash == db_hash else "★不一致"
        ok += (py_hash == db_hash)
        print(f"id={rid:<4} py={py_hash[:12]}… db={db_hash[:12]}… {match}")
    print(f"\n结论：{ok}/{len(rows)} 一致"
          + ("，exact dedup 前提成立" if ok == len(rows) else "，算法有差异需排查！"))


if __name__ == "__main__":
    main()
