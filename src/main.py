#!/usr/bin/env python3
"""
arXiv AI 論文自動篩選系統

自動從 arXiv 下載前一天的 AI 相關論文，
使用 LLM 評估並篩選出最有價值的 Top N 篇論文。
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 確保可以 import 同目錄的模組
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arxiv_client import Paper, fetch_papers, fetch_papers_by_recent
from config import (
    MARKDOWN_TEMPLATE,
    OUTPUT_DIR,
    PAPER_DETAIL_TEMPLATE,
    TOP_N,
)
from llm_evaluator import (
    BatchTokenUsage,
    EvaluationResult,
    TokenUsage,
    evaluate_papers,
    evaluate_papers_batch,
    evaluate_papers_batch_with_api,
)


def generate_markdown(
    papers: list[Paper],
    result: EvaluationResult,
    date: str,
    output_dir: str,
) -> str:
    """
    生成 Markdown 報告

    Args:
        papers: 所有論文列表
        result: 評估結果
        date: 查詢日期
        output_dir: 輸出目錄

    Returns:
        輸出檔案路徑
    """
    # 確保輸出目錄存在
    output_path = Path(os.path.expanduser(output_dir))
    output_path.mkdir(parents=True, exist_ok=True)

    # 生成排名表格
    ranking_rows = []
    paper_details = []

    for rank, (idx, reason) in enumerate(zip(result.ranked_indices, result.reasons), 1):
        paper = papers[idx]

        # 表格行
        title_short = paper.title[:60] + "..." if len(paper.title) > 60 else paper.title
        ranking_rows.append(
            f"| {rank} | [{title_short}]({paper.url}) | {paper.primary_category} | {reason} |"
        )

        # 詳情區塊
        authors_str = ", ".join(paper.authors[:5])
        if len(paper.authors) > 5:
            authors_str += f" 等 {len(paper.authors)} 人"

        detail = PAPER_DETAIL_TEMPLATE.format(
            rank=rank,
            title=paper.title,
            arxiv_id=paper.arxiv_id,
            url=paper.url,
            categories=", ".join(paper.categories),
            authors=authors_str,
            reason=reason,
            abstract=paper.abstract,
        )
        paper_details.append(detail)

    # 組合 Markdown
    markdown_content = MARKDOWN_TEMPLATE.format(
        date=date,
        total_count=len(papers),
        top_n=len(result.ranked_indices),
        ranking_table="\n".join(ranking_rows),
        paper_details="\n".join(paper_details),
        model=result.model,
    )

    # 寫入檔案
    filename = f"arxiv-{date}.md"
    filepath = output_path / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    return str(filepath)


def save_token_stats(
    result: EvaluationResult,
    date: str,
    paper_count: int,
    output_dir: str,
) -> str:
    """
    儲存 Token 使用量統計

    Args:
        result: 評估結果
        date: 查詢日期
        paper_count: 論文總數
        output_dir: 輸出目錄

    Returns:
        輸出檔案路徑
    """
    output_path = Path(os.path.expanduser(output_dir))
    output_path.mkdir(parents=True, exist_ok=True)

    stats = {
        "date": date,
        "timestamp": datetime.now().isoformat(),
        "model": result.model,
        "paper_count": paper_count,
        "selected_count": len(result.ranked_indices),
        "token_usage": {
            "input_tokens": result.token_usage.input_tokens,
            "output_tokens": result.token_usage.output_tokens,
            "thinking_tokens": result.token_usage.thinking_tokens,
            "total_tokens": result.token_usage.total_tokens,
        },
    }

    # 如果有分開的 batch/sync 統計，加入
    if result.batch_token_usage:
        batch_usage = result.batch_token_usage.batch_usage
        sync_usage = result.batch_token_usage.sync_usage
        stats["token_usage_breakdown"] = {
            "batch_api": {
                "input_tokens": batch_usage.input_tokens,
                "output_tokens": batch_usage.output_tokens,
                "thinking_tokens": batch_usage.thinking_tokens,
                "total_tokens": batch_usage.total_tokens,
            },
            "sync_api": {
                "input_tokens": sync_usage.input_tokens,
                "output_tokens": sync_usage.output_tokens,
                "thinking_tokens": sync_usage.thinking_tokens,
                "total_tokens": sync_usage.total_tokens,
            },
        }

    # 寫入 JSON 檔案
    filename = f"arxiv-{date}-stats.json"
    filepath = output_path / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    return str(filepath)


def main():
    """主程式入口"""
    parser = argparse.ArgumentParser(
        description="arXiv AI 論文自動篩選系統",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例:
  python main.py                     # 查詢前一天的論文
  python main.py --date 2025-01-09   # 查詢指定日期
  python main.py --top 5             # 只選出 Top 5
  python main.py --output ~/papers   # 指定輸出目錄
  python main.py --recent 3          # 查詢最近 3 天 (備用模式)
        """,
    )

    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="查詢日期 (YYYY-MM-DD)，預設為前一天",
    )

    parser.add_argument(
        "--top",
        type=int,
        default=TOP_N,
        help=f"篩選前 N 篇論文 (預設: {TOP_N})",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=OUTPUT_DIR,
        help=f"輸出目錄 (預設: {OUTPUT_DIR})",
    )

    parser.add_argument(
        "--recent",
        type=int,
        default=None,
        help="使用備用模式，查詢最近 N 天的論文",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只查詢不評估，用於測試 arXiv API",
    )

    args = parser.parse_args()

    # 決定查詢日期
    if args.date:
        query_date = args.date
    else:
        yesterday = datetime.now() - timedelta(days=1)
        query_date = yesterday.strftime("%Y-%m-%d")

    print("=" * 60)
    print("arXiv AI 論文自動篩選系統")
    print("=" * 60)
    print(f"查詢日期: {query_date}")
    print(f"篩選數量: Top {args.top}")
    print(f"輸出目錄: {args.output}")
    print("=" * 60)

    # 取得論文
    try:
        if args.recent:
            papers = fetch_papers_by_recent(days=args.recent)
        else:
            papers = fetch_papers(date=query_date)
    except Exception as e:
        print(f"[錯誤] 無法取得論文: {e}")
        sys.exit(1)

    if not papers:
        print("[錯誤] 沒有找到任何論文")
        sys.exit(1)

    print(f"\n共取得 {len(papers)} 篇論文")

    # Dry run 模式
    if args.dry_run:
        print("\n[Dry Run] 前 5 篇論文:")
        for i, paper in enumerate(papers[:5]):
            print(f"  [{i}] {paper.title[:70]}...")
            print(f"       {paper.primary_category} | {paper.arxiv_id}")
        print("\n[Dry Run] 完成，未進行 LLM 評估")
        return

    # LLM 評估
    print("\n" + "-" * 60)
    try:
        if len(papers) > 50:
            # 使用 Batch API 分批評估（第一輪用 Batch API 省 50%，最後一輪用同步 API）
            result = evaluate_papers_batch_with_api(papers, top_n=args.top)
        else:
            result = evaluate_papers(papers, top_n=args.top)
    except Exception as e:
        print(f"[錯誤] LLM 評估失敗: {e}")
        sys.exit(1)

    # 生成 Markdown
    print("\n" + "-" * 60)
    print("[輸出] 生成 Markdown 報告...")

    try:
        filepath = generate_markdown(papers, result, query_date, args.output)
        print(f"[輸出] 報告已儲存: {filepath}")
    except Exception as e:
        print(f"[錯誤] 無法生成報告: {e}")
        sys.exit(1)

    # 儲存 Token 統計
    try:
        stats_filepath = save_token_stats(result, query_date, len(papers), args.output)
        print(f"[輸出] Token 統計已儲存: {stats_filepath}")
    except Exception as e:
        print(f"[警告] 無法儲存統計: {e}")

    # 顯示結果摘要
    print("\n" + "=" * 60)
    print(f"篩選完成! Top {len(result.ranked_indices)} 論文:")
    print("=" * 60)

    for rank, (idx, reason) in enumerate(zip(result.ranked_indices, result.reasons), 1):
        paper = papers[idx]
        print(f"\n{rank}. {paper.title[:65]}...")
        print(f"   {paper.url}")
        print(f"   原因: {reason}")

    # Token 使用量摘要
    print("\n" + "-" * 60)
    print("[Token 統計]")
    print(f"  模型: {result.model}")

    # 如果有分開的統計，顯示詳細資訊
    if result.batch_token_usage and result.batch_token_usage.batch_usage.total_tokens > 0:
        batch_usage = result.batch_token_usage.batch_usage
        sync_usage = result.batch_token_usage.sync_usage
        print(f"  [Batch API - 第一輪篩選]")
        print(f"    Input:    {batch_usage.input_tokens:,} tokens")
        print(f"    Output:   {batch_usage.output_tokens:,} tokens")
        if batch_usage.thinking_tokens > 0:
            print(f"    Thinking: {batch_usage.thinking_tokens:,} tokens")
        print(f"    Total:    {batch_usage.total_tokens:,} tokens")
        print(f"  [Sync API - 最終篩選]")
        print(f"    Input:    {sync_usage.input_tokens:,} tokens")
        print(f"    Output:   {sync_usage.output_tokens:,} tokens")
        if sync_usage.thinking_tokens > 0:
            print(f"    Thinking: {sync_usage.thinking_tokens:,} tokens")
        print(f"    Total:    {sync_usage.total_tokens:,} tokens")
        print(f"  [總計]")

    print(f"  Input:    {result.token_usage.input_tokens:,} tokens")
    print(f"  Output:   {result.token_usage.output_tokens:,} tokens")
    if result.token_usage.thinking_tokens > 0:
        print(f"  Thinking: {result.token_usage.thinking_tokens:,} tokens")
    print(f"  Total:    {result.token_usage.total_tokens:,} tokens")

    print("\n" + "=" * 60)
    print(f"報告位置: {filepath}")
    print("=" * 60)


if __name__ == "__main__":
    main()
    # papers = fetch_papers(date='2026-01-09')
