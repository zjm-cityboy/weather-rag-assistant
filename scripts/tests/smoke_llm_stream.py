"""
冒烟测试：隔离定位 LLM 流式调用挂死问题（不经 FastAPI，直连硅基流动）

背景：/ask 接口带会话历史（长 prompt）时，llm.stream() 长时间无首字节。
假设：Qwen3.5 思考型模型 + 长 prompt 触发网关流式挂死。
实验：同一长 prompt（2 条历史 + 5 个知识块），分别用
    A. 默认参数（思考模式开）
    B. enable_thinking=False（非思考模式）
    C. 短 prompt + 默认参数（对照组）
各计时，输出首字节延迟与总耗时。
"""

import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))
from app.rag import chain, retriever  # 复用线上同款 Prompt/检索构造

load_dotenv(Path(__file__).parent.parent.parent / "backend" / ".env")

TIMEOUT = 60          # 与线上一致
MODEL = os.getenv("MODEL_NAME", "Qwen/Qwen3.5-35B")


def run_case(name: str, prompt_msgs, disable_thinking: bool) -> None:
    llm = ChatOpenAI(
        model=MODEL,
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("API_BASE_URL"),
        temperature=0.2,
        streaming=True,
        timeout=TIMEOUT,
        max_retries=0,                        # 冒烟测试不重试，暴露真实超时行为
        max_tokens=512,
        **({"extra_body": {"enable_thinking": False}} if disable_thinking else {}),
    )
    t0 = time.time()
    first = None
    n_chunks = 0
    try:
        for chunk in llm.stream(prompt_msgs):
            if chunk.content:
                if first is None:
                    first = time.time() - t0          # 首字节延迟
                    print(f"  [{name}] 首字节: {first:.1f}s")
                n_chunks += 1
        print(f"  [{name}] 完成: {n_chunks} 块, 总耗时 {time.time() - t0:.1f}s ✓")
    except Exception as e:  # noqa: BLE001 —— 冒烟测试需捕获全部异常类型并计时上报
        print(f"  [{name}] 异常({time.time() - t0:.1f}s): {type(e).__name__}: {str(e)[:120]}")
    print()


def main() -> None:
    # 构造与线上一致的长 prompt：检索 5 块 + 2 条历史
    chunks = retriever.search("台风预警信号分为哪几种颜色？")
    prompt = chain.build_prompt().invoke({
        "history": [HumanMessage(content="台风预警信号分为哪几种颜色？"),
                    AIMessage(content="台风预警信号分为蓝色、黄色、橙色和红色四种。")],
        "context": chain.build_context(chunks),
        "question": "其中最严重的是什么级别？",
    })
    print(f"模型: {MODEL} | prompt 长度: {len(str(prompt))} 字符 | 检索块: {len(chunks)}\n")

    print("A. 长 prompt + 默认（思考开）:")
    run_case("A", prompt, disable_thinking=False)

    print("B. 长 prompt + enable_thinking=False:")
    run_case("B", prompt, disable_thinking=True)

    print("C. 短 prompt + 默认（对照）:")
    run_case("C", [HumanMessage(content="用一句话说明什么是台风预警信号。")], disable_thinking=False)


if __name__ == "__main__":
    main()
