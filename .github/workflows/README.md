# GitHub Actions 自動化說明

## 設定步驟

### 1. 設定 GitHub Secret

1. 進入您的 GitHub repo 頁面
2. 點擊 **Settings** → **Secrets and variables** → **Actions**
3. 點擊 **New repository secret**
4. 填入：
   - **Name**: `GOOGLE_API_KEY`
   - **Value**: 您的 Gemini API Key (從 https://aistudio.google.com/apikey 取得)
5. 點擊 **Add secret**

### 2. 啟用 GitHub Actions

1. 進入 repo 的 **Actions** 頁面
2. 如果第一次使用，點擊 **I understand my workflows, go ahead and enable them**

### 3. 測試執行

**手動觸發測試：**
1. 進入 **Actions** → **Daily arXiv Paper Selector**
2. 點擊 **Run workflow** → **Run workflow**
3. 等待約 1-2 分鐘
4. 檢查 `output/` 資料夾是否有生成報告

### 4. 自動執行時間

- 預設：每天 **UTC 01:00**（台灣時間 09:00）
- 修改時間：編輯 `daily-arxiv.yml` 中的 `cron` 設定

```yaml
schedule:
  - cron: '0 1 * * *'  # 格式: 分 時 日 月 週
```

常用時間範例：
- `0 0 * * *` - 每天 UTC 00:00 (台灣 08:00)
- `0 2 * * *` - 每天 UTC 02:00 (台灣 10:00)
- `0 1 * * 1-5` - 週一到週五 UTC 01:00

## 查看結果

### 方法 1：直接在 GitHub 瀏覽

```
https://github.com/YOUR_USERNAME/YOUR_REPO/blob/main/arxiv-selector/output/arxiv-2025-01-11.md
```

### 方法 2：使用 GitHub Actions Artifacts

如果 commit 失敗，可以從 Actions 頁面下載 artifacts：

1. 進入 **Actions** → 點擊執行的 workflow
2. 下方 **Artifacts** 區塊點擊 **arxiv-reports** 下載

### 方法 3：Clone 到本地

```bash
git pull
cat arxiv-selector/output/arxiv-$(date +%Y-%m-%d).md
```

## 整合 NotebookLM

### 手動匯入（每天 10 秒）

1. 開啟 [NotebookLM](https://notebooklm.google.com/)
2. 建立新的 Notebook 或開啟現有的
3. 點擊 **Add source** → **Web URL**
4. 貼上今天的報告 URL（從 GitHub 複製）
5. 點擊 **Generate** 生成語音摘要

### 快速存取書籤

建立瀏覽器書籤，快速開啟最新報告：

```
https://github.com/YOUR_USERNAME/YOUR_REPO/tree/main/arxiv-selector/output
```

## 進階設定

### 調整篩選數量

在 workflow 中修改 `TOP_N` 環境變數：

```yaml
env:
  TOP_N: 5  # 改為只選 Top 5
```

### 調整輸出目錄

```yaml
env:
  OUTPUT_DIR: ./reports  # 改為其他目錄
```

### 設定執行通知

如果想在執行失敗時收到通知，可以加入：

```yaml
- name: Notify on failure
  if: failure()
  run: |
    curl -X POST https://YOUR_WEBHOOK_URL \
      -H 'Content-Type: application/json' \
      -d '{"text":"arXiv selector failed on $(date)"}'
```

## 安全性最佳實踐

- ✅ API Key 存在 GitHub Secrets（加密存放）
- ✅ Secrets 不會出現在 logs 中
- ✅ 定期輪換 API Key（建議每 3-6 個月）
- ✅ 設定 API 配額限制（在 Google AI Studio）
- ✅ 使用私有 repository（如果不想公開論文清單）

## 成本估算

- **GitHub Actions**: 完全免費
  - 公開 repo: 無限制
  - 私有 repo: 2000 分鐘/月（此任務約 2 分鐘/天）

- **Gemini API**: ~$0.004/天
  - 每月約 $0.12

**總計：基本免費**

## 疑難排解

### Q1: Workflow 沒有自動執行

**A**: GitHub Actions 的 cron 有時會延遲 5-15 分鐘，這是正常的。如果超過 30 分鐘未執行，檢查：
- repo 是否有近期活動（inactive repo 可能停用 cron）
- 是否有其他 workflow 錯誤阻塞

### Q2: Commit 失敗

**A**: 檢查 workflow 是否有 `contents: write` 權限：

```yaml
permissions:
  contents: write
```

### Q3: API Key 無效

**A**: 重新檢查 Secret 設定：
- 確認名稱完全一致 `GOOGLE_API_KEY`
- 確認 API Key 沒有空格或換行
- 確認 API 已啟用 Gemini

## 未來擴展建議

### 1. 自動生成 GitHub Pages

可以設定 GitHub Pages 將報告變成靜態網站：

```yaml
- name: Deploy to GitHub Pages
  uses: peaceiris/actions-gh-pages@v3
  with:
    github_token: ${{ secrets.GITHUB_TOKEN }}
    publish_dir: ./arxiv-selector/output
```

### 2. 發送郵件通知

整合 email 服務，自動發送每日摘要。

### 3. 整合 Slack/Discord

將結果自動發送到團隊頻道。

---

有任何問題請查看：
- [GitHub Actions 文件](https://docs.github.com/en/actions)
- [專案 Issue](https://github.com/YOUR_USERNAME/YOUR_REPO/issues)
