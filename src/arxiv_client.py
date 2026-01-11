"""
arXiv API 查詢模組

負責從 arXiv 取得指定日期的 AI 相關論文
使用 feedparser + requests (參考成功的 arxiv_fetch.py)
"""
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import requests

from config import (
    ARXIV_CATEGORIES,
    ARXIV_MAX_RESULTS,
    ARXIV_REQUEST_DELAY,
)

# arXiv API 設定
BASE_URL = "https://export.arxiv.org/api/query"
USER_AGENT = "arxiv-selector/1.0 (github.com/arxiv-selector)"


@dataclass
class Paper:
    """論文資料結構"""
    arxiv_id: str
    title: str
    abstract: str
    authors: list[str]
    categories: list[str]
    primary_category: str
    published: str
    updated: str
    url: str
    pdf_url: str


def get_yesterday_date() -> str:
    """取得前一天的日期 (YYYY-MM-DD)，基於 UTC+8 時區"""
    # 使用 UTC+8 計算「昨天」
    utc8 = timezone(timedelta(hours=8))
    now_utc8 = datetime.now(utc8)
    yesterday = now_utc8 - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


def date_window(target_date: str) -> tuple[str, str]:
    """
    將日期轉換為 arXiv API 的時間區間格式

    時間邏輯 (針對台灣 UTC+8 時區):
    - 輸入日期的 UTC+8 08:00 → 隔天 UTC+8 08:00
    - 換算成 UTC: 輸入日期 00:00 → 隔天 00:00

    Args:
        target_date: YYYY-MM-DD 格式的日期 (UTC+8 日期)

    Returns:
        (start_iso, end_iso) tuple，格式為 YYYYMMDDHHMM (UTC 時間)
    """
    date_obj = datetime.strptime(target_date, "%Y-%m-%d").date()
    # UTC+8 08:00 = UTC 00:00，所以直接用輸入日期的 00:00 UTC
    start = datetime.combine(date_obj, datetime.min.time().replace(hour=0, minute=0), tzinfo=timezone.utc)
    # 到隔天 UTC 00:00 (即隔天 UTC+8 08:00)
    end_date = date_obj + timedelta(days=1)
    end = datetime.combine(end_date, datetime.min.time().replace(hour=0, minute=0), tzinfo=timezone.utc)
    return start.strftime("%Y%m%d%H%M"), end.strftime("%Y%m%d%H%M")


def fetch_category(
    category: str,
    start_iso: str,
    end_iso: str,
    *,
    page_size: int = 200,
    total_limit: int = 2000,
    delay: float = ARXIV_REQUEST_DELAY,
    verbose: bool = False,
    max_retries: int = 3,
) -> list[Paper]:
    """
    逐頁抓取單一分類在指定日期區間的論文

    Args:
        category: arXiv 類別 (如 cs.AI)
        start_iso: 開始時間 (YYYYMMDDHHMM)
        end_iso: 結束時間 (YYYYMMDDHHMM)
        page_size: 單次請求的數量
        total_limit: 單分類最多抓取數量
        delay: 每次請求的延遲秒數
        verbose: 是否顯示詳細資訊
        max_retries: 最大重試次數

    Returns:
        Paper 物件列表
    """
    results: list[Paper] = []
    start = 0
    headers = {"User-Agent": USER_AGENT}
    category_query = category.lower()

    while start < total_limit:
        max_results = min(page_size, total_limit - start)

        # 使用 all: 查詢語法 (參考成功的 arxiv_fetch.py)
        query = (
            f"search_query=all:{category_query}+AND+submittedDate:[{start_iso}+TO+{end_iso}]"
            f"&start={start}&max_results={max_results}"
            f"&sortBy=submittedDate&sortOrder=ascending"
        )
        url = f"{BASE_URL}?{query}"

        if verbose:
            print(f"[arXiv] GET {url}")

        # 重試機制
        resp = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(url, headers=headers, timeout=60)
                resp.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    wait_time = delay * (attempt + 2)
                    print(f"[arXiv] 請求失敗: {e}，等待 {wait_time:.1f} 秒後重試...")
                    time.sleep(wait_time)
                else:
                    print(f"[arXiv] 請求失敗，已達最大重試次數: {e}")
                    raise

        if resp is None:
            break

        feed = feedparser.parse(resp.text)
        entries = feed.entries

        if not entries:
            break

        for entry in entries:
            # 取得 PDF 連結
            pdf_link = next(
                (link.href for link in entry.get("links", []) if link.get("type") == "application/pdf"),
                None,
            )

            # 取得主要類別
            primary_category = ""
            if entry.get("tags"):
                primary_category = entry.tags[0].get("term", "")

            # 取得所有類別
            categories = [tag.get("term", "") for tag in entry.get("tags", []) if tag.get("term")]

            # 取得作者
            authors = [author.name for author in entry.get("authors", [])]

            # 取得 arXiv ID
            arxiv_id = entry.id.split("/abs/")[-1] if "/abs/" in entry.id else entry.id

            paper = Paper(
                arxiv_id=arxiv_id,
                title=entry.title.strip().replace("\n", " "),
                abstract=entry.summary.strip().replace("\n", " "),
                authors=authors,
                categories=categories,
                primary_category=primary_category,
                published=entry.get("published", ""),
                updated=entry.get("updated", ""),
                url=entry.id,
                pdf_url=pdf_link or f"https://arxiv.org/pdf/{arxiv_id}",
            )
            results.append(paper)

        start += len(entries)
        if len(entries) < max_results:
            break

        time.sleep(delay)

    return results


def dedupe_papers(papers: list[Paper]) -> list[Paper]:
    """
    去除重複的論文 (依 arxiv_id)

    Args:
        papers: Paper 物件列表

    Returns:
        去重後的 Paper 列表
    """
    seen: dict[str, Paper] = {}
    for paper in papers:
        seen.setdefault(paper.arxiv_id, paper)
    return list(seen.values())


def fetch_papers(
    date: Optional[str] = None,
    categories: Optional[list[str]] = None,
    verbose: bool = False,
) -> list[Paper]:
    """
    從 arXiv 取得指定日期的論文

    Args:
        date: 查詢日期 (YYYY-MM-DD)，預設為前一天
        categories: arXiv 類別列表，預設使用 config 中的設定
        verbose: 是否顯示詳細資訊

    Returns:
        Paper 物件列表 (已去重)
    """
    if date is None:
        date = get_yesterday_date()

    if categories is None:
        categories = ARXIV_CATEGORIES

    start_iso, end_iso = date_window(date)
    print(f"[arXiv] 查詢日期: {date}")
    print(f"[arXiv] 時間區間: {start_iso} ~ {end_iso}")
    print(f"[arXiv] 類別: {', '.join(categories)}")

    all_papers: list[Paper] = []

    for category in categories:
        print(f"[arXiv] 正在取得 {category} 類別...")
        papers = fetch_category(
            category,
            start_iso,
            end_iso,
            page_size=200,
            total_limit=ARXIV_MAX_RESULTS,
            verbose=verbose,
        )
        print(f"[arXiv] {category}: 取得 {len(papers)} 篇")
        all_papers.extend(papers)

        # 每個類別之間的延遲
        if category != categories[-1]:
            time.sleep(ARXIV_REQUEST_DELAY)

    # 去重
    deduped = dedupe_papers(all_papers)
    print(f"[arXiv] 總計: {len(deduped)} 篇 (去重前: {len(all_papers)})")

    return deduped


def fetch_papers_by_recent(
    days: int = 1,
    categories: Optional[list[str]] = None,
    verbose: bool = False,
) -> list[Paper]:
    """
    取得最近 N 天的論文

    Args:
        days: 查詢最近幾天
        categories: arXiv 類別列表
        verbose: 是否顯示詳細資訊

    Returns:
        Paper 物件列表
    """
    all_papers: list[Paper] = []

    for i in range(days):
        target_date = (datetime.now() - timedelta(days=i + 1)).strftime("%Y-%m-%d")
        print(f"\n[arXiv] === 查詢 {target_date} ===")
        papers = fetch_papers(date=target_date, categories=categories, verbose=verbose)
        all_papers.extend(papers)

    deduped = dedupe_papers(all_papers)
    print(f"\n[arXiv] 最近 {days} 天總計: {len(deduped)} 篇")

    return deduped


if __name__ == "__main__":
    # 測試用
    papers = fetch_papers(verbose=True)
    print(f"\n=== 前 5 篇論文 ===")
    for i, paper in enumerate(papers[:5]):
        print(f"\n[{i}] {paper.title}")
        print(f"    ID: {paper.arxiv_id}")
        print(f"    Categories: {paper.categories}")
        print(f"    Authors: {', '.join(paper.authors[:3])}...")
