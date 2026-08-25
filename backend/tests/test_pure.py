"""
纯函数单元测试（不依赖数据库/网络——可离线跑，CI 就绪）

覆盖对象：RRF 融合、全文分词清洗、图谱实体匹配、JWT 签发与校验。
运行：cd backend && python -m pytest tests/ -v
"""

# ruff: noqa: I001 —— import 按测试分节就近放置（分节注释是阅读结构的一部分）

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import HTTPException

# ---------- RRF 融合 ----------
from app.rag.retriever import _tokenize, rrf_fuse


def _hit(cid, dist=0.4):
    """构造最小 chunk 结构（rrf_fuse 只用 id 与元信息透传）。"""
    return {"id": cid, "content": f"c{cid}", "source": "s", "page": 0,
            "url": "", "distance": dist}


def test_rrf_multi_list_beats_single():
    """两路都命中的块应排到只有单路命中的块前面（累计分叠加）。"""
    both = _hit(1)                       # 两路都排第 1：2/(60+1) ≈ 0.0328
    only_a = _hit(2)                     # 仅 A 路第 1：1/(60+1) ≈ 0.0164
    merged = rrf_fuse([[both, only_a], [both, _hit(3)]], top_k=3)
    assert merged[0]["id"] == 1          # 双路命中登顶
    assert {m["id"] for m in merged} == {1, 2, 3}


def test_rrf_rank_not_score():
    """排名靠前的分高：同路第 1 名 > 第 2 名。"""
    merged = rrf_fuse([[_hit(1), _hit(2)]], top_k=2)
    assert merged[0]["id"] == 1
    assert merged[0]["rrf_score"] > merged[1]["rrf_score"]


# ---------- 全文分词清洗 ----------
def test_tokenize_filters_stopwords_and_dedup():
    """停用词被过滤、结果去重。"""
    toks = _tokenize("什么的台风 台风")
    assert toks == ["台风"]              # "的/什么"全滤，"台风"去重


def test_tokenize_empty_query():
    assert _tokenize("什么怎么") == []    # 全停用词 → 空（调用方直接短路）


# ---------- 图谱实体匹配 ----------
from app.graph.knowledge_graph import _match_entities


def test_match_entities_substring():
    entities = ["台风", "暴雨", "风暴潮"]
    assert _match_entities("台风会引发什么", entities) == ["台风"]
    assert _match_entities("台风和暴雨的关系", entities) == ["台风", "暴雨"]


def test_match_entities_single_char_excluded():
    """单字实体跳过（防'云'这类单字误命中任意包含场景）。"""
    assert _match_entities("看云图识天气", ["云", "云图"]) == ["云图"]
    assert _match_entities("今天风很大", ["风"]) == []       # 单字不参与匹配


# ---------- JWT 签发与校验 ----------
import pytest

import app.api.auth as auth_mod


def test_token_roundtrip(monkeypatch):
    monkeypatch.setattr(auth_mod, "JWT_SECRET", "test-secret")
    token = auth_mod.make_token("alice")
    assert auth_mod.verify_token(f"Bearer {token}") == "alice"


def test_token_rejects_garbage(monkeypatch):
    monkeypatch.setattr(auth_mod, "JWT_SECRET", "test-secret")
    with pytest.raises(HTTPException):
        auth_mod.verify_token("Bearer not-a-jwt")
    with pytest.raises(HTTPException):
        auth_mod.verify_token("")             # 无 Authorization 头


def test_token_expired(monkeypatch):
    monkeypatch.setattr(auth_mod, "JWT_SECRET", "test-secret")
    import jwt as pyjwt
    expired = pyjwt.encode({"sub": "bob", "exp": time.time() - 10},
                           "test-secret", algorithm="HS256")
    with pytest.raises(HTTPException):
        auth_mod.verify_token(f"Bearer {expired}")
