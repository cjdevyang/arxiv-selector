"""
LLM 論文評估模組

使用 Gemini 評估論文價值並排名
"""
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from google import genai
from google.genai import types
from dotenv import load_dotenv

from arxiv_client import Paper
from config import EVALUATION_PROMPT1, EVALUATION_PROMPT2, LLM_MODEL, LLM_TEMPERATURE, LLM_THINKING_BUDGET

# Batch API 設定
BATCH_API_ENABLED = os.getenv("BATCH_API_ENABLED", "false").lower() == "true"
BATCH_POLL_INTERVAL = int(os.getenv("BATCH_POLL_INTERVAL", "10"))  # 輪詢間隔秒數
BATCH_MAX_WAIT_TIME = int(os.getenv("BATCH_MAX_WAIT_TIME", "600"))  # 最大等待秒數

# 載入環境變數
load_dotenv()


@dataclass
class TokenUsage:
    """Token 使用量資料結構"""
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0  # Gemini 2.5+ thinking tokens
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass
class BatchTokenUsage:
    """分開追蹤 Batch API 和同步 API 的 Token 使用量"""
    batch_usage: TokenUsage = None  # Batch API 用量（第一輪篩選）
    sync_usage: TokenUsage = None   # 同步 API 用量（最終篩選）

    def __post_init__(self):
        if self.batch_usage is None:
            self.batch_usage = TokenUsage()
        if self.sync_usage is None:
            self.sync_usage = TokenUsage()

    @property
    def total_usage(self) -> TokenUsage:
        """取得總用量"""
        return self.batch_usage + self.sync_usage


@dataclass
class EvaluationResult:
    """評估結果資料結構"""
    ranked_indices: list[int]  # 排名後的論文索引
    reasons: list[str]  # 每篇論文的入選原因
    model: str  # 使用的模型
    token_usage: TokenUsage = None  # Token 使用量統計（總量，向下相容）
    batch_token_usage: BatchTokenUsage = None  # 分開的 Token 統計（batch vs sync）

    def __post_init__(self):
        if self.token_usage is None:
            self.token_usage = TokenUsage()
        if self.batch_token_usage is None:
            self.batch_token_usage = BatchTokenUsage()


def init_gemini() -> genai.Client:
    """
    初始化 Gemini Client

    Returns:
        genai.Client 實例
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("請設定 GOOGLE_API_KEY 環境變數")

    client = genai.Client(api_key=api_key)
    return client


def format_papers_for_prompt(papers: list[Paper]) -> str:
    """
    將論文列表格式化為 Prompt 輸入

    Args:
        papers: Paper 物件列表

    Returns:
        格式化的字串
    """
    lines = []
    for i, paper in enumerate(papers):
        # 截斷過長的摘要 (約 500 字)
        abstract = paper.abstract[:1500] + "..." if len(paper.abstract) > 1500 else paper.abstract
        lines.append(f"[{i}] Title: {paper.title}")
        lines.append(f"    Categories: {', '.join(paper.categories[:3])}")
        lines.append(f"    Abstract: {abstract}")
        lines.append("")

    return "\n".join(lines)


def parse_llm_response(response_text: str, top_n: int, require_reasons: bool = True) -> tuple[list[int], list[str]]:
    """
    解析 LLM 回傳的 JSON

    Args:
        response_text: LLM 回傳的文字
        top_n: 預期的排名數量
        require_reasons: 是否需要 reasons 欄位

    Returns:
        (ranked_indices, reasons) tuple
    """
    # 嘗試直接解析 JSON
    try:
        data = json.loads(response_text)
        ranked_indices = data.get("rank", [])
        reasons = data.get("reasons", [])

        # 驗證格式
        if require_reasons:
            if len(ranked_indices) >= top_n and len(reasons) >= top_n:
                return ranked_indices[:top_n], reasons[:top_n]
        else:
            # 不需要 reasons，只要有 rank 即可
            if len(ranked_indices) >= top_n:
                return ranked_indices[:top_n], []

    except json.JSONDecodeError:
        pass

    # 如果直接解析失敗，嘗試從文字中提取 JSON
    json_match = re.search(r'\{.*?\}', response_text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            ranked_indices = data.get("rank", [])
            reasons = data.get("reasons", [])

            if require_reasons:
                if ranked_indices and reasons:
                    return ranked_indices[:top_n], reasons[:top_n]
            else:
                if ranked_indices:
                    return ranked_indices[:top_n], []
        except json.JSONDecodeError:
            pass

    raise ValueError(f"無法解析 LLM 回應: {response_text[:500]}")


def evaluate_papers(
    papers: list[Paper],
    top_n: int = 10,
    client: Optional[genai.Client] = None,
    require_reasons: bool = True,
) -> EvaluationResult:
    """
    使用 LLM 評估論文並排名

    Args:
        papers: Paper 物件列表
        top_n: 選出前 N 篇
        client: Gemini Client 實例 (可選，預設會自動初始化)
        require_reasons: 是否需要 reasons (第一輪篩選不需要，最終輪需要)

    Returns:
        EvaluationResult 物件
    """
    if not papers:
        raise ValueError("論文列表為空")

    if client is None:
        client = init_gemini()

    # 調整 top_n 不超過論文數量
    actual_top_n = min(top_n, len(papers))

    # 格式化論文列表
    papers_list = format_papers_for_prompt(papers)

    # 根據是否需要 reasons 選擇不同的 prompt
    prompt_template = EVALUATION_PROMPT2 if require_reasons else EVALUATION_PROMPT1

    # 構建完整 Prompt
    prompt = prompt_template.format(
        top_n=actual_top_n,
        papers_list=papers_list,
    )

    print(f"[LLM] 正在評估 {len(papers)} 篇論文，選出 Top {actual_top_n}...")
    print(f"[LLM] 使用模型: {LLM_MODEL}")
    if LLM_THINKING_BUDGET > 0:
        print(f"[LLM] Thinking Budget: {LLM_THINKING_BUDGET} tokens")

    # 估算 token 數 (粗略估計)
    estimated_input_tokens = len(prompt) // 4
    print(f"[LLM] 預估 Input tokens: ~{estimated_input_tokens:,}")

    try:
        # 構建 config
        config_params = {
            "temperature": LLM_TEMPERATURE,
            "response_mime_type": "application/json",
        }

        # 如果有設定 thinking budget，加入 thinking config
        if LLM_THINKING_BUDGET > 0:
            config_params["thinking_config"] = types.ThinkingConfig(
                thinking_budget=LLM_THINKING_BUDGET
            )

        response = client.models.generate_content(
            model=LLM_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(**config_params),
        )
        response_text = response.text

        # 記錄 token 使用量
        token_usage = TokenUsage()
        usage = response.usage_metadata
        if usage:
            # 取得 thinking tokens（Gemini 2.5+ 特有）
            thinking_tokens = getattr(usage, 'thoughts_token_count', 0) or 0
            token_usage = TokenUsage(
                input_tokens=usage.prompt_token_count or 0,
                output_tokens=usage.candidates_token_count or 0,
                thinking_tokens=thinking_tokens,
                total_tokens=usage.total_token_count or 0,
            )
            print(f"[LLM] Token 使用量:")
            print(f"      - Input:    {token_usage.input_tokens:,} tokens")
            print(f"      - Output:   {token_usage.output_tokens:,} tokens")
            if thinking_tokens > 0:
                print(f"      - Thinking: {token_usage.thinking_tokens:,} tokens")
            print(f"      - Total:    {token_usage.total_tokens:,} tokens")

        print(f"[LLM] 收到回應，正在解析...")

        # 解析回應
        ranked_indices, reasons = parse_llm_response(response_text, actual_top_n, require_reasons)

        # 驗證索引有效性
        valid_indices = []
        valid_reasons = []
        for i, idx in enumerate(ranked_indices):
            if 0 <= idx < len(papers):
                valid_indices.append(idx)
                # 如果有 reasons，一併加入
                if reasons and i < len(reasons):
                    valid_reasons.append(reasons[i])
            else:
                print(f"[LLM] 警告: 無效的索引 {idx}，已跳過")

        if not valid_indices:
            raise ValueError("沒有有效的排名結果")

        print(f"[LLM] 成功選出 {len(valid_indices)} 篇論文")

        return EvaluationResult(
            ranked_indices=valid_indices,
            reasons=valid_reasons,
            model=LLM_MODEL,
            token_usage=token_usage,
        )

    except Exception as e:
        print(f"[LLM] 評估失敗: {e}")
        raise


def evaluate_papers_batch(
    papers: list[Paper],
    top_n: int = 10,
    batch_size: int = 50,
) -> EvaluationResult:
    """
    分批評估大量論文

    當論文數量超過 batch_size 時，先進行初步篩選，
    再從候選中選出最終結果。

    Args:
        papers: Paper 物件列表
        top_n: 最終選出前 N 篇
        batch_size: 每批處理的論文數量

    Returns:
        EvaluationResult 物件
    """
    if len(papers) <= batch_size:
        return evaluate_papers(papers, top_n)

    print(f"[LLM] 論文數量 ({len(papers)}) 超過單批上限，啟用分批評估...")

    client = init_gemini()
    candidates = []
    total_usage = TokenUsage()  # 累計 token 使用量

    # 計算總批次數
    total_batches = (len(papers) + batch_size - 1) // batch_size

    # 第一輪: 每批選出 top_n 候選 (不需要 reasons，省 output tokens)
    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"[LLM] 處理第 {batch_num}/{total_batches} 批 ({len(batch)} 篇)...")

        # 第一輪篩選不需要 reasons
        result = evaluate_papers(batch, top_n, client, require_reasons=False)
        total_usage = total_usage + result.token_usage  # 累加 token

        for idx in result.ranked_indices:
            # 轉換回原始索引
            original_idx = i + idx
            candidates.append(papers[original_idx])

    # 第二輪: 從候選中選出最終結果 (需要 reasons)
    if len(candidates) > top_n:
        print(f"[LLM] 從 {len(candidates)} 個候選中選出最終 Top {top_n}...")
        # 最終輪需要 reasons
        final_result = evaluate_papers(candidates, top_n, client, require_reasons=True)
        total_usage = total_usage + final_result.token_usage  # 累加 token

        # 映射回原始論文
        final_indices = []
        final_reasons = []

        for i, idx in enumerate(final_result.ranked_indices):
            candidate_paper = candidates[idx]
            # 找到在原始列表中的索引
            for orig_idx, orig_paper in enumerate(papers):
                if orig_paper.arxiv_id == candidate_paper.arxiv_id:
                    final_indices.append(orig_idx)
                    final_reasons.append(final_result.reasons[i])
                    break

        return EvaluationResult(
            ranked_indices=final_indices,
            reasons=final_reasons,
            model=LLM_MODEL,
            token_usage=total_usage,
        )

    else:
        # 候選數量不足，需要對候選再評估一次以取得 reasons
        print(f"[LLM] 候選數量 ({len(candidates)}) 不超過 Top {top_n}，直接評估取得 reasons...")
        final_result = evaluate_papers(candidates, top_n, client, require_reasons=True)
        total_usage = total_usage + final_result.token_usage

        final_indices = []
        final_reasons = []

        for i, idx in enumerate(final_result.ranked_indices):
            candidate_paper = candidates[idx]
            for orig_idx, orig_paper in enumerate(papers):
                if orig_paper.arxiv_id == candidate_paper.arxiv_id:
                    final_indices.append(orig_idx)
                    final_reasons.append(final_result.reasons[i])
                    break

        return EvaluationResult(
            ranked_indices=final_indices,
            reasons=final_reasons,
            model=LLM_MODEL,
            token_usage=total_usage,
        )


def _build_batch_request(papers: list[Paper], top_n: int) -> dict:
    """
    建構單一 batch request

    Args:
        papers: 論文列表
        top_n: 選出前 N 篇

    Returns:
        符合 Gemini Batch API 格式的 request dict
    """
    papers_list = format_papers_for_prompt(papers)
    prompt = EVALUATION_PROMPT1.format(
        top_n=top_n,
        papers_list=papers_list,
    )

    request = {
        'contents': [{
            'parts': [{'text': prompt}],
            'role': 'user'
        }],
        'config': {
            'temperature': LLM_TEMPERATURE,
            'response_mime_type': 'application/json',
        }
    }

    # 如果有設定 thinking budget，加入 thinking config
    if LLM_THINKING_BUDGET > 0:
        request['config']['thinking_config'] = {
            'thinking_budget': LLM_THINKING_BUDGET
        }

    return request


def evaluate_papers_batch_api(
    paper_batches: list[list[Paper]],
    top_n: int,
    client: genai.Client,
) -> tuple[list[list[int]], TokenUsage]:
    """
    使用 Batch API 批量評估多個論文批次（第一輪篩選）

    Args:
        paper_batches: 論文批次列表，每個元素是一批論文
        top_n: 每批選出前 N 篇
        client: Gemini Client

    Returns:
        (每批的排名索引列表, 總 token 使用量)
    """
    if not paper_batches:
        return [], TokenUsage()

    print(f"[Batch API] 準備 {len(paper_batches)} 個批次請求...")

    # 建構所有 batch requests
    inline_requests = []
    for batch in paper_batches:
        actual_top_n = min(top_n, len(batch))
        request = _build_batch_request(batch, actual_top_n)
        inline_requests.append(request)

    # 建立 batch job
    print(f"[Batch API] 建立批次任務...")
    batch_job = client.batches.create(
        model=f"models/{LLM_MODEL}",
        src=inline_requests,
        config={
            'display_name': f"arxiv-selector-{time.strftime('%Y%m%d-%H%M%S')}",
        },
    )

    job_name = batch_job.name
    print(f"[Batch API] 任務已建立: {job_name}")

    # 輪詢等待完成
    completed_states = {
        'JOB_STATE_SUCCEEDED',
        'JOB_STATE_FAILED',
        'JOB_STATE_CANCELLED',
        'JOB_STATE_EXPIRED',
    }

    start_time = time.time()
    batch_job = client.batches.get(name=job_name)

    while batch_job.state.name not in completed_states:
        elapsed = int(time.time() - start_time)
        print(f"[Batch API] 狀態: {batch_job.state.name} (已等待 {elapsed}s)")

        if elapsed > BATCH_MAX_WAIT_TIME:
            raise TimeoutError(f"Batch API 超時，已等待 {elapsed} 秒")

        time.sleep(BATCH_POLL_INTERVAL)
        batch_job = client.batches.get(name=job_name)

    elapsed = int(time.time() - start_time)
    print(f"[Batch API] 任務完成: {batch_job.state.name} (耗時 {elapsed}s)")

    # 檢查是否成功
    if batch_job.state.name != 'JOB_STATE_SUCCEEDED':
        raise RuntimeError(f"Batch API 任務失敗: {batch_job.state.name}")

    # 解析回應
    all_ranked_indices = []
    total_usage = TokenUsage()

    for i, inline_response in enumerate(batch_job.dest.inlined_responses):
        batch = paper_batches[i]
        actual_top_n = min(top_n, len(batch))

        if inline_response.error:
            print(f"[Batch API] 批次 {i+1} 錯誤: {inline_response.error}")
            all_ranked_indices.append([])
            continue

        response = inline_response.response
        if not response:
            print(f"[Batch API] 批次 {i+1} 無回應")
            all_ranked_indices.append([])
            continue

        # 累加 token 使用量
        if response.usage_metadata:
            usage = response.usage_metadata
            thinking_tokens = getattr(usage, 'thoughts_token_count', 0) or 0
            total_usage = total_usage + TokenUsage(
                input_tokens=usage.prompt_token_count or 0,
                output_tokens=usage.candidates_token_count or 0,
                thinking_tokens=thinking_tokens,
                total_tokens=usage.total_token_count or 0,
            )

        # 解析結果
        try:
            response_text = response.text
            ranked_indices, _ = parse_llm_response(response_text, actual_top_n, require_reasons=False)

            # 驗證索引有效性
            valid_indices = [idx for idx in ranked_indices if 0 <= idx < len(batch)]
            all_ranked_indices.append(valid_indices)
            print(f"[Batch API] 批次 {i+1}/{len(paper_batches)} 完成，選出 {len(valid_indices)} 篇")

        except Exception as e:
            print(f"[Batch API] 批次 {i+1} 解析失敗: {e}")
            all_ranked_indices.append([])

    print(f"[Batch API] Token 使用量:")
    print(f"      - Input:    {total_usage.input_tokens:,} tokens")
    print(f"      - Output:   {total_usage.output_tokens:,} tokens")
    if total_usage.thinking_tokens > 0:
        print(f"      - Thinking: {total_usage.thinking_tokens:,} tokens")
    print(f"      - Total:    {total_usage.total_tokens:,} tokens")

    return all_ranked_indices, total_usage


def evaluate_papers_batch_with_api(
    papers: list[Paper],
    top_n: int = 10,
    batch_size: int = 50,
) -> EvaluationResult:
    """
    使用 Batch API 分批評估大量論文

    第一輪使用 Batch API（省 50% 費用），最後一輪使用同步 API。
    分開追蹤兩者的 token 用量。

    Args:
        papers: Paper 物件列表
        top_n: 最終選出前 N 篇
        batch_size: 每批處理的論文數量

    Returns:
        EvaluationResult 物件（包含分開的 token 統計）
    """
    if len(papers) <= batch_size:
        # 論文數量少，直接用同步 API
        result = evaluate_papers(papers, top_n)
        result.batch_token_usage = BatchTokenUsage(
            batch_usage=TokenUsage(),
            sync_usage=result.token_usage,
        )
        return result

    print(f"[LLM] 論文數量 ({len(papers)}) 超過單批上限，啟用 Batch API 分批評估...")

    client = init_gemini()

    # 分批
    paper_batches = []
    batch_start_indices = []  # 記錄每批在原始列表中的起始索引

    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        paper_batches.append(batch)
        batch_start_indices.append(i)

    print(f"[LLM] 共分成 {len(paper_batches)} 批")

    # 第一輪：使用 Batch API
    all_ranked_indices, batch_usage = evaluate_papers_batch_api(
        paper_batches, top_n, client
    )

    # 收集候選論文（轉換為原始索引）
    candidates = []
    for batch_idx, ranked_indices in enumerate(all_ranked_indices):
        start_idx = batch_start_indices[batch_idx]
        for idx in ranked_indices:
            original_idx = start_idx + idx
            candidates.append(papers[original_idx])

    print(f"[LLM] 第一輪篩選完成，共 {len(candidates)} 個候選")

    # 第二輪：使用同步 API 選出最終結果
    if len(candidates) > top_n:
        print(f"[LLM] 從 {len(candidates)} 個候選中選出最終 Top {top_n}...")
        final_result = evaluate_papers(candidates, top_n, client, require_reasons=True)
        sync_usage = final_result.token_usage

        # 映射回原始論文
        final_indices = []
        final_reasons = []

        for i, idx in enumerate(final_result.ranked_indices):
            candidate_paper = candidates[idx]
            for orig_idx, orig_paper in enumerate(papers):
                if orig_paper.arxiv_id == candidate_paper.arxiv_id:
                    final_indices.append(orig_idx)
                    final_reasons.append(final_result.reasons[i])
                    break

    else:
        # 候選數量不足，需要對候選再評估一次以取得 reasons
        print(f"[LLM] 候選數量 ({len(candidates)}) 不超過 Top {top_n}，直接評估取得 reasons...")
        final_result = evaluate_papers(candidates, top_n, client, require_reasons=True)
        sync_usage = final_result.token_usage

        final_indices = []
        final_reasons = []

        for i, idx in enumerate(final_result.ranked_indices):
            candidate_paper = candidates[idx]
            for orig_idx, orig_paper in enumerate(papers):
                if orig_paper.arxiv_id == candidate_paper.arxiv_id:
                    final_indices.append(orig_idx)
                    final_reasons.append(final_result.reasons[i])
                    break

    # 組合結果
    total_usage = batch_usage + sync_usage
    batch_token_usage = BatchTokenUsage(
        batch_usage=batch_usage,
        sync_usage=sync_usage,
    )

    return EvaluationResult(
        ranked_indices=final_indices,
        reasons=final_reasons,
        model=LLM_MODEL,
        token_usage=total_usage,
        batch_token_usage=batch_token_usage,
    )


if __name__ == "__main__":
    # 測試用
    from arxiv_client import fetch_papers

    papers = fetch_papers()
    if papers:
        result = evaluate_papers(papers[:20], top_n=5)
        print("\n=== 評估結果 ===")
        for rank, (idx, reason) in enumerate(zip(result.ranked_indices, result.reasons), 1):
            print(f"{rank}. [{idx}] {papers[idx].title[:50]}... - {reason}")
