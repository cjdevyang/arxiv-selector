# arXiv AI 論文自動篩選系統

自動從 arXiv 下載前一天的 AI 相關論文，使用 LLM 評估並篩選出最有價值的 Top N 篇論文。

## 系統架構

```
┌─────────────────────────────────────────────────────────────────┐
│                        每日自動執行流程                           │
├─────────────────────────────────────────────────────────────────┤
│  1. 查詢 arXiv API    →  2. 解析論文資料  →  3. LLM 評估排名     │
│         ↓                      ↓                    ↓           │
│  取得前一天 AI 論文      提取 title/abstract    選出 Top N 論文   │
│                                                     ↓           │
│                                              4. 輸出 Markdown   │
└─────────────────────────────────────────────────────────────────┘
```

## 功能特色

- **自動查詢**: 透過 arXiv API 取得指定日期的 AI 相關論文
- **智能篩選**: 使用 Gemini 2.5 Flash 進行論文價值評估
- **Batch API 優化**: 大量論文時自動使用 Batch API，節省 50% 費用
- **Markdown 輸出**: 生成易讀的排名報告
- **Token 統計**: 詳細追蹤 input/output/thinking tokens 用量

## 查詢的 arXiv 類別

- `cs.AI` - Artificial Intelligence
- `cs.LG` - Machine Learning
- `cs.CL` - Computation and Language (NLP)
- `cs.CV` - Computer Vision
- `cs.NE` - Neural and Evolutionary Computing
- `stat.ML` - Machine Learning (Statistics)

## 評估標準

### 優先關注 (Priorities)

1. **系統架構與設計**: RAG、Agent、Context Engineering、Prompt Optimization
2. **流程與評估優化**: LLMOps、Auto-Evaluation、Data Flywheel、Error Analysis
3. **推論效率與成本**: Latency、Quantization、FlashAttention、部署優化
4. **大廠評估報告**: 模型能力邊界、Safety、企業級應用
5. **落地可行性**: 真實場景驗證、魯棒性、合理硬體需求

### 排除條件 (Negative Constraints)

- 純數學理論推導
- 全新的基礎模型訓練架構設計（除非涉及推論加速）
- 僅適用於學術刷榜但無實際應用價值的微小精度提升

## 需求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/) (推薦) 或 pip

## 安裝

使用 uv（推薦）：

```bash
cd arxiv-selector
uv sync
```

或使用 pip：

```bash
cd arxiv-selector
pip install .
```

## 設定

1. 複製環境變數範例檔：
```bash
cp .env.example .env
```

2. 編輯 `.env` 填入設定：
```bash
# 必填
GOOGLE_API_KEY=your_gemini_api_key_here

# 可選
OUTPUT_DIR=~/arxiv-papers    # 輸出目錄
TOP_N=10                     # 篩選前 N 篇
LLM_MODEL=gemini-2.5-flash   # 使用的模型
LLM_TEMPERATURE=0.1          # 溫度參數
LLM_THINKING_BUDGET=0        # Thinking token 預算 (0=關閉)
```

## 使用方式

### 手動執行

```bash
# 預設: 查詢前一天的論文
uv run python src/main.py

# 指定日期
uv run python src/main.py --date 2025-01-09

# 指定輸出目錄
uv run python src/main.py --output ~/my-papers

# 指定篩選數量
uv run python src/main.py --top 5

# Dry run（只查詢不評估）
uv run python src/main.py --dry-run
```

### GitHub Actions 自動執行

參考 [SETUP_GITHUB_ACTIONS.md](SETUP_GITHUB_ACTIONS.md) 設定每日自動執行。

### Cron Job 自動執行

```bash
# 編輯 crontab
crontab -e

# 每天早上 9:00 執行
0 9 * * * cd /path/to/arxiv-selector && uv run python src/main.py >> /var/log/arxiv-selector.log 2>&1
```

## 輸出範例

檔案: `~/arxiv-papers/arxiv-2025-01-09.md`

```markdown
# arXiv AI 論文精選 - 2025-01-09

> 自動篩選自 arXiv，共掃描 127 篇，精選 Top 10

## 排名

| # | 標題 | 類別 | 原因 |
|---|------|------|------|
| 1 | [Paper Title](https://arxiv.org/abs/2501.xxxxx) | cs.AI | RAG檢索優化 |
| 2 | [Paper Title](https://arxiv.org/abs/2501.xxxxx) | cs.LG | 多Agent協作框架 |
...
```

## 成本估算

假設每天 100 篇論文（Batch API 50% off）：

| 模型 | 每日成本 | 每月成本 |
|------|---------|---------|
| Gemini 2.5 Flash (Batch API) | ~$0.002 | ~$0.06 |
| Gemini 2.5 Flash (Sync API) | ~$0.004 | ~$0.12 |

> 當論文數量 > 50 篇時，第一輪篩選會自動使用 Batch API 節省費用。

## License

MIT
