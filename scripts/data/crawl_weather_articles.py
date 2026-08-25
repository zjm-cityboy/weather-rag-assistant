"""
气象 RAG · 语料获取：爬取中国天气网科普文章

抓取台风科普专题与科普频道的文章，产出三件套（可溯源）：
    web_pages.jsonl  正文记录（source/url/page/content_type/text）
    sources.txt      抓取清单（时间 | URL | 标题）
    raw_html/        每篇原始 HTML 存档（复查/重解析用，不再重复请求）

流程：收集入口页链接 → 逐篇抓取正文 → 落盘输出。
"""

import json
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ============================================================
# 配置
# ============================================================
PROJECT_DIR = Path(__file__).parent.parent.parent      # 项目根目录（scripts/data/ 上两层）
OUT_DIR = PROJECT_DIR / "datas" / "web_pages"
RAW_DIR = OUT_DIR / "raw_html"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
TIMEOUT = 30          # 单次请求超时（秒），外部调用必设
SLEEP = 1.5           # 请求间隔（秒），降低目标站压力
MIN_BODY_LEN = 200    # 正文低于此长度视为空页/视频页，跳过

ENTRY_PAGES = [       # 入口页：从这些页面发现文章链接
    "https://typhoon.weather.com.cn/tfkp/",          # 台风科普专题
    "https://www.weather.com.cn/science/",           # 科普频道首页
]
# 中国天气网文章页 URL 形如 https://xxx.weather.com.cn/column/2024/05/1234.shtml
ARTICLE_PAT = re.compile(r"https?://(?:[\w-]+\.)*weather\.com\.cn/\w+/\d{4}/\d{2}/\d+\.shtml")


# ============================================================
# 基础抓取单元
# ============================================================
def fetch(url: str) -> requests.Response | None:
    """带超时与 1 次重试的 GET；失败返回 None，单篇失败不中断整批。"""
    for attempt in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.encoding = r.apparent_encoding or "utf-8"   # 按页面内容探测编码，防乱码
            if r.status_code == 200:
                return r
        except requests.RequestException as e:            # requests 网络异常基类（超时/连接/协议）
            print(f"  [warn] {url} 第{attempt + 1}次失败: {e}")
            time.sleep(2)                                 # 重试前稍等
    return None


def extract_article(html: str) -> tuple[str, str]:
    """从文章页提取（标题, 正文）。正文容器按 class 关键词探测，逐级兜底。"""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()                                   # 先剔掉脚本/样式/导航，防混入正文
    title = soup.title.get_text(strip=True) if soup.title else ""
    # 正文容器探测：article 标签 → class 含 text/content 等关键词的 div → body 兜底
    body = (soup.find("article")
            or soup.find("div", class_=lambda c: c and any(k in str(c) for k in ["text", "content", "article", "detail"]))
            or soup.body)
    text = body.get_text("\n", strip=True) if body else ""
    text = re.sub(r"\n{3,}", "\n\n", text)                # 压缩连续空行
    return title, text


# ============================================================
# 步骤 1：收集文章链接（多入口汇总去重）
# ============================================================
def collect_urls() -> dict[str, str]:
    """遍历入口页解析文章链接。返回 {url: 入口链接文本}。"""
    article_urls: dict[str, str] = {}
    for entry in ENTRY_PAGES:
        r = fetch(entry)
        if not r:
            print(f"[失败] 入口页 {entry}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        found = 0
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.startswith("/"):                      # 站内相对链接 → 补全域名
                href = "https://www.weather.com.cn" + href
            if ARTICLE_PAT.match(href):
                title = a.get_text(strip=True)
                if title and href not in article_urls:    # 字典去重
                    article_urls[href] = title
                    found += 1
        print(f"[入口] {entry} → 新增 {found} 篇")
    return article_urls


# ============================================================
# 步骤 2：逐篇抓取正文（礼貌间隔 + 原始 HTML 存档）
# ============================================================
def crawl_articles(article_urls: dict[str, str]) -> list[dict]:
    """逐篇抓取，产出 JSONL 记录并写 raw_html 存档。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)
    records: list[dict] = []
    total = len(article_urls)
    for i, (url, title) in enumerate(article_urls.items(), 1):
        r = fetch(url)
        if not r:
            continue                                      # fetch 内已打印告警
        art_title, text = extract_article(r.text)
        if len(text) < MIN_BODY_LEN:
            print(f"  [跳过] {url}（正文仅 {len(text)} 字）")
            continue
        real_title = art_title or title                   # 页内标题优先，入口链接文本兜底
        slug = url.rstrip("/").split("/")[-1].replace(".shtml", "")   # URL 尾部做唯一文件名
        (RAW_DIR / f"{slug}.html").write_text(r.text, encoding="utf-8")
        records.append({
            "source": f"web:{real_title}",                # 与 pages.jsonl 的 source 命名风格一致
            "url": url,
            "page": 0,                                    # 网页无页码，统一 0
            "content_type": "web",
            "text": text,
        })
        print(f"  [{i}/{total}] {real_title[:40]} | {len(text)} 字 | {url}")
        time.sleep(SLEEP)
    return records


# ============================================================
# 步骤 3：落盘输出（web_pages.jsonl + sources.txt）
# ============================================================
def save_results(records: list[dict]) -> None:
    """写正文 JSONL 与来源清单。"""
    out_jsonl = OUT_DIR / "web_pages.jsonl"
    with out_jsonl.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    sources = OUT_DIR / "sources.txt"
    with sources.open("w", encoding="utf-8") as f:
        f.write("# 语料来源清单（爬取时间 | URL | 标题）\n")
        for rec in records:
            f.write(f"2026-08-24 | {rec['url']} | {rec['source'][4:]}\n")   # [4:] 去掉 "web:" 前缀

    total_chars = sum(len(r["text"]) for r in records)
    print(f"\n[完成] {len(records)} 篇文章，共 {total_chars} 字")
    print(f"  正文 JSONL：{out_jsonl}")
    print(f"  来源清单：{sources}")


def main() -> None:
    article_urls = collect_urls()           # 步骤 1：收集链接
    print(f"\n共收集 {len(article_urls)} 篇待抓文章")
    records = crawl_articles(article_urls)  # 步骤 2：逐篇抓取
    save_results(records)                   # 步骤 3：落盘输出


if __name__ == "__main__":
    main()
