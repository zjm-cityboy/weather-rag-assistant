"""
工具：把零散的页面图片（jpg/png）按序拼成一个 PDF

【用途】
    官方公报常以"页面图片"形式发布（如《2025年中国气候公报》13 张 jpg）。
    拼成 PDF 后可直接进入 build_knowledge_base.py 管线
    （每页无文字层 → 自动走 OCR 分支，无需任何代码改动）。

【用法】
    python images_to_pdf.py <图片目录> <输出.pdf>
    图片按文件名升序排列（page_00, page_01, ... 的零填充命名保证顺序正确）。
"""
import sys
from pathlib import Path

import pymupdf


def images_to_pdf(img_dir: Path, out_pdf: Path) -> int:
    """把 img_dir 下所有 jpg/png 按文件名序拼成单个 PDF，返回页数。"""
    images = sorted([p for p in img_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png")])
    if not images:
        raise FileNotFoundError(f"{img_dir} 下没有图片")

    doc = pymupdf.open()
    for img in images:
        # 以图片尺寸建页（1 像素 = 1 point 的 72dpi 基准），保持原始比例不变形
        pix = pymupdf.Pixmap(img)
        rect = pymupdf.Rect(0, 0, pix.width, pix.height)
        page = doc.new_page(width=pix.width, height=pix.height)
        page.insert_image(rect, filename=str(img))
    doc.save(str(out_pdf))
    doc.close()
    return len(images)


if __name__ == "__main__":
    img_dir = Path(sys.argv[1])       # 命令行参数 1：图片目录
    out_pdf = Path(sys.argv[2])       # 命令行参数 2：输出 PDF 路径
    n = images_to_pdf(img_dir, out_pdf)
    print(f"[OK] {n} 张图片 → {out_pdf.name}（{out_pdf.stat().st_size // 1024} KB）")
