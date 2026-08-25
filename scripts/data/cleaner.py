"""
============================================================
气象 RAG · 数据清洗模块（参照业内 ingest 管线标准实现）
============================================================
【业内对标的六个环节】
    ① Unicode 规范化（NFKC：全角半角统一、零宽字符/BOM 清除）
    ② Boilerplate 模板检测（跨文档高频短行 = 页眉/页脚/导航，自动剔除
       ——替代"硬编码特定书的版权行"，任意书通用）
    ③ 断行合并（中文直接拼 / 英文按句读规则拼 + 连字符断词还原）
    ④ 空白规范化（多空格/制表符/连续空行压缩）
    ⑤ 质量过滤（特殊字符占比过高 / 有效字符过少 → 标记低质量）
    ⑥ 清洗报告（每类删除量可量化、可审计）

【用法】（two-pass：第一遍全量统计 boilerplate，第二遍逐文档清洗）
    stats = build_boilerplate(texts, doc_ids)
    cleaned = clean_text(raw, lang, boilerplate=stats, report=report)
"""
import re
import unicodedata
from collections import Counter

# ============================================================
# 配置常量
# ============================================================
BOILERPLATE_PAGE_RATIO = 0.30   # 一行出现在 ≥30% 的文档里 → 判为模板行（页眉/页脚）
BOILERPLATE_MAX_LEN = 80        # 只对短行做模板检测（长行是正文概率高）
MIN_EFFECTIVE_CHARS = 30        # 清洗后有效字符低于此值 = 低质量（过滤出候选）

CN_END_PUNCT = "。！？；：）”」』】"
EN_END_PUNCT = ".!?;:)\"']"

# 页码类噪声行（保留通用规则；特定书的版权行已由 boilerplate 检测兜住）
PAGE_NO_PAT = re.compile(r"^[-—–\s]*\d{1,4}[-—–\s]*$")


# ============================================================
# ① Unicode 规范化
# ============================================================
def normalize(text: str) -> str:
    """NFKC 规范化 + 清除零宽字符/BOM/控制字符（保留换行）。"""
    text = unicodedata.normalize("NFKC", text)          # 全角字母数字→半角、兼容形式统一
    text = text.replace("\ufeff", "")                   # BOM
    text = re.sub(r"[\u200b\u200c\u200d\u2060]", "", text)   # 零宽字符
    text = re.sub(r"[ \t]+", " ", text)                 # 行内多空白压一
    return text


# ============================================================
# ② Boilerplate 模板检测（业内做法：跨文档重复行统计）
# ============================================================
def build_boilerplate(texts_by_doc: dict[str, str]) -> set[str]:
    """统计每行出现在多少个文档里；出现比例 ≥ 阈值的短行 → 模板行集合。

    原理：页眉页脚/版权声明在整本书几十页里逐字重复，而正文句子几乎不会
    在多页重复——用"跨文档文档频率"自动识别模板，无需任何领域硬编码。
    """
    line_docs: Counter[str] = Counter()      # 行 → 出现在几个文档
    for text in texts_by_doc.values():
        seen = set()                          # 同一文档内重复只记一次
        for line in text.split("\n"):
            line = line.strip()
            if line and len(line) <= BOILERPLATE_MAX_LEN:
                seen.add(line)
        line_docs.update(seen)

    n_docs = max(len(texts_by_doc), 1)
    threshold = max(2, int(n_docs * BOILERPLATE_PAGE_RATIO))   # 至少 2 个文档才可能成模板
    boilerplate = {line for line, cnt in line_docs.items() if cnt >= threshold}
    return boilerplate


# ============================================================
# ③④ 断行合并 + 空白规范化
# ============================================================
def _merge_lines(lines: list[str], lang: str) -> list[str]:
    """合并被硬换行切断的句子。

    中文：上一行尾不是句末标点 → 直接拼。
    英文：同规则 + 补空格；行尾是 "xxx-"（连字符断词）→ 去连字符直接拼词。
    """
    merged: list[str] = []
    for ln in lines:
        if not merged:
            merged.append(ln)
            continue
        prev = merged[-1]
        if not prev:                                  # 上一行是空行 → 不拼
            merged.append(ln)
            continue
        if lang == "zh":
            if prev[-1] in CN_END_PUNCT:
                merged.append(ln)
            else:
                merged[-1] = prev + ln                # 中文直接拼
        else:  # en
            # 英文连字符断词还原：行尾连字符且前行是字母、本行以小写开头
            if prev.endswith("-") and prev[:-1][-1:].isalpha() and ln[:1].islower():
                merged[-1] = prev[:-1] + ln           # email- + letter → emailletter
                continue
            if prev[-1] in EN_END_PUNCT or (ln[:1].isupper()):
                merged.append(ln)                     # 上句已完 / 新句大写开头 → 不拼
            else:
                merged[-1] = prev + " " + ln
    return merged


# ============================================================
# 主清洗入口（①~④ + 报告）
# ============================================================
def clean_text(raw: str, lang: str, boilerplate: set[str],
               report: dict | None = None) -> str:
    """单文档清洗全流程。report 传入时累计各类删除量（⑥ 可审计）。"""
    text = normalize(raw)                                     # ①
    lines = [ln.strip() for ln in text.split("\n")]

    kept: list[str] = []
    for ln in lines:
        if not ln:
            kept.append(ln)
            continue
        if PAGE_NO_PAT.match(ln):                             # 通用页码行
            if report is not None:
                report["page_number_lines"] += 1
            continue
        if ln in boilerplate:                                 # ② 模板行（页眉/页脚/版权）
            if report is not None:
                report["boilerplate_lines"] += 1
            continue
        kept.append(ln)

    merged = _merge_lines(kept, lang)                         # ③
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(merged))        # ④
    out = out.strip()
    if report is not None:
        report["raw_chars"] += len(raw)
        report["clean_chars"] += len(out)
    return out


# ============================================================
# ⑤ 质量过滤（识别乱码/无意义页）
# ============================================================
def is_low_quality(text: str) -> bool:
    """有效字符过少 或 可打印符号占比异常 → 判低质量。"""
    if len(text) < MIN_EFFECTIVE_CHARS:
        return True
    # 中文或字母数字占比 < 50% → 大概率乱码/表格线/符号堆
    effective = sum(1 for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
    return effective / len(text) < 0.5
