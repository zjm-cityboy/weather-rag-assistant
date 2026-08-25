"""
============================================================
气象 RAG 智能问答助手 · PDF 知识库构建管线（解析+清洗）
============================================================

【职责】
    把 datas/books_pdf/ 里的 PDF 解析成干净的页级文本，输出结构化 JSONL，
    供 ingest_to_pgvector.py（分块 + 嵌入 + pgvector 入库）消费。

【管线设计】
    逐页文本层检测 → OCR 路由 → 统一清洗管线

      每页文字量检测（get_text() < 50 字符 = 疑似扫描/图表页）
        ├─ 文字页 ──→ PyMuPDF 直接抽取（无损，毫秒级）
        └─ 扫描页 ──→ PyMuPDF 渲染成图 → DeepSeek-OCR（免费 API）→ 文本
                              ↓
                统一数据清洗管线（两路共用 cleaner 模块）
                去页眉页脚页码 / 合并断行 / 规范空白
                              ↓
                datas/processed/pages.jsonl
                每条 = {source, page, content_type, text}

【OCR 路由依据】
    文字型 PDF 直接抽取是无损的；OCR 是"图片→识别"，存在错误率，
    对文字页过 OCR = 无损变有损，且慢（毫秒→秒级/页）、消耗免费额度。

【健壮性设计】
    1. OCR 结果落盘缓存（ocr_cache/）——重跑脚本不重复调 API（免费也经不起反复调）
    2. 单页 OCR 失败不中断整书：记 warning，该页标记 ocr_failed 继续跑
    3. 全程验证输出：每本书的总页数/路由统计/清洗前后字符对比
"""

import base64
import json
import os
import sys
from pathlib import Path

import pymupdf  # PyMuPDF（新导入名，fitz 别名将移除——pitfalls 第 5 条）
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))              # data/ 进模块搜索路径
from cleaner import build_boilerplate, clean_text, is_low_quality

# ============================================================
# 配置常量（全部提取成带注释的常量，改参数不用翻代码）
# ============================================================
PROJECT_DIR = Path(__file__).parent.parent.parent    # 项目根（scripts/data/ 上两层）
PDF_DIR = PROJECT_DIR / "datas" / "books_pdf"       # 输入：原始 PDF
OUT_DIR = PROJECT_DIR / "datas" / "processed"       # 输出：清洗后的页级 JSONL
OCR_CACHE_DIR = OUT_DIR / "ocr_cache"               # OCR 结果缓存（避免重复调 API）

OCR_TEXT_THRESHOLD = 50     # 每页文字少于 50 字符 → 判定为扫描/图表页，走 OCR
RENDER_DPI = 150            # 扫描页渲染分辨率：150 够 OCR 识别，文件不过大
OCR_TIMEOUT = 120           # 单次 OCR 请求超时（秒）——外部调用必设超时（规范 2.5）
OCR_MODEL = "deepseek-ai/DeepSeek-OCR"   # 硅基流动限时免费的 OCR 模型（冒烟测试已验证）

load_dotenv(PROJECT_DIR / "backend" / ".env")   # 密钥外置（规范 2.6：禁止硬编码）


# ============================================================
# 第 1 步：文本层检测 + OCR 路由抽取
# ============================================================
def ocr_page(image_bytes: bytes) -> str:
    """把一页的 PNG 图片交给 DeepSeek-OCR 转成文本。

    走硅基流动的 OpenAI 兼容接口（chat/completions + image_url base64）。
    提示词要求：全部文字转写 + 表格转 Markdown（DeepSeek-OCR 原生爱输出
    HTML 表格，明确要求可部分矫正）。
    """
    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "model": OCR_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": "请完整转写图片中的所有文字内容（包括标题、段落、表格），"
                                         "表格用 Markdown 表格格式输出，不要遗漏任何段落。"},
            ],
        }],
        "max_tokens": 2000,
    }
    r = requests.post(
        f"{os.getenv('API_BASE_URL')}/chat/completions",
        headers={"Authorization": f"Bearer {os.getenv('API_KEY')}"},
        json=payload, timeout=OCR_TIMEOUT,
    )
    r.raise_for_status()                                # 4xx/5xx 直接抛错，由调用方降级
    msg = r.json()["choices"][0]["message"]
    return (msg.get("content") or "").strip()


def extract_page_text(page: pymupdf.Page, source_name: str, page_no: int) -> tuple[str, str]:
    """抽取单页文本：文本层检测路由——文字页直接抽取，扫描页走 OCR（带缓存）。

    返回 (文本, 类型标记)：类型 ∈ {"text" 文字页, "ocr" OCR 页, "ocr_failed" OCR 失败页}
    """
    direct = page.get_text().strip()

    # ---- 分支一：文字页（≥ 阈值）→ 无损直接抽取 ----
    if len(direct) >= OCR_TEXT_THRESHOLD:
        return direct, "text"

    # ---- 分支二：扫描/图表页 → 渲染成图 → OCR ----
    # 先查缓存：命中就不调 API（重跑脚本零成本）
    cache_file = OCR_CACHE_DIR / f"{source_name}_p{page_no}.txt"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8").strip(), "ocr"

    # 渲染 PNG（matrix 控制 DPI，默认 72 太糊，150 是清晰度/体积的平衡点）
    pix = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_DPI / 72, RENDER_DPI / 72))
    img_bytes = pix.tobytes("png")

    try:
        text = ocr_page(img_bytes)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")   # 落盘缓存
        return text, "ocr"
    except Exception as e:  # noqa: BLE001 —— OCR 链路失败面广，降级保整书流程
        # 单页失败不中断整书（规范 2.5：外部调用要有失败处理路径）
        print(f"    [warn] 第 {page_no} 页 OCR 失败，跳过：{e}")
        return direct, "ocr_failed"


# ============================================================
# 主流程：遍历 PDF → 逐页路由 → 清洗 → 汇总输出 JSONL
# ============================================================
def detect_lang(sample: str) -> str:
    """按中文字符占比判断语言（决定清洗规则用哪套）。"""
    cn = sum(1 for ch in sample if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cn > len(sample) * 0.1 else "en"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUT_DIR / "pages.jsonl"

    # ---- 缓存孤儿清理：源 PDF 已删除时，其 OCR 缓存一并删除 ----
    current_sources = {p.stem for p in PDF_DIR.glob("*.pdf")}
    for cache in OCR_CACHE_DIR.glob("*.txt"):
        # 缓存文件名格式：{pdf文件名去后缀}_p{页码}.txt
        source_stem = cache.name.rsplit("_p", 1)[0]
        if source_stem not in current_sources:
            cache.unlink()
            print(f"  [缓存清理] 删除孤儿缓存：{cache.name}")

    with output_path.open("w", encoding="utf-8") as fout:
        # ---- 第一遍：全量抽取（不做清洗，先攒齐）----
        all_pages = []                                  # (来源, 页码, 原文, 类型)
        per_pdf_pages: dict[str, int] = {}
        for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
            doc = pymupdf.open(pdf_path)
            for i, page in enumerate(doc):
                raw, kind = extract_page_text(page, pdf_path.stem, i + 1)
                all_pages.append((pdf_path.name, i + 1, raw, kind))
            per_pdf_pages[pdf_path.name] = len(doc)
            doc.close()
            print(f"[抽取] {pdf_path.name}（{per_pdf_pages[pdf_path.name]} 页）")

        # ---- Boilerplate 模板检测：跨页高频短行 = 页眉/页脚/版权 ----
        texts_by_doc = {f"{src}_p{pg}": raw for src, pg, raw, kind in all_pages
                        if raw and kind == "text"}
        boilerplate = build_boilerplate(texts_by_doc)
        print(f"[模板检测] 识别出 {len(boilerplate)} 个跨页重复的模板行（页眉/页脚/版权）")

        # ---- 第二遍：逐页清洗 + 质量过滤 + 写出 ----
        report: dict[str, int] = {"boilerplate_lines": 0, "page_number_lines": 0,
                                  "raw_chars": 0, "clean_chars": 0, "low_quality": 0}
        written = 0
        for src, pg, raw, kind in all_pages:
            if not raw:
                continue
            lang = detect_lang(raw[:500])
            cleaned = clean_text(raw, lang, boilerplate, report)
            if len(cleaned) < 30 or is_low_quality(cleaned):    # 质量过滤（封面/空白/乱码页）
                report["low_quality"] += 1
                continue
            fout.write(json.dumps({"source": src, "page": pg,
                                   "content_type": kind, "text": cleaned},
                                  ensure_ascii=False) + "\n")
            written += 1

        # ---- 清洗报告（可审计）----
        print("\n[清洗报告]")
        print(f"  输入 {len(all_pages)} 页 → 输出 {written} 条（过滤低质量 {report['low_quality']} 页）")
        print(f"  模板行剔除：{report['boilerplate_lines']} 行 ｜ 页码行剔除：{report['page_number_lines']} 行")
        shrink = 100 - report["clean_chars"] * 100 // max(report["raw_chars"], 1)
        print(f"  字符量：{report['raw_chars']} → {report['clean_chars']}（压缩 {shrink}%）")
    print(f"\n输出：{output_path}")


if __name__ == "__main__":
    main()
