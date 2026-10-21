# 公司行動的離線測試樣本

證交所公開端點的原始回應（2026-10-15 取得），只裁切掉測試用不到的列，欄位、注意事項與數值都保持原文。

| 檔案 | 端點 |
|---|---|
| twt49u.json | `/rwd/zh/exRight/TWT49U?startDate=20260101&endDate=20261015`（除權除息計算結果表） |
| twt49u-detail-*.json | `/rwd/zh/exRight/TWT49UDetail?STK_NO=&T1=` |
| twtauu.json | `/rwd/zh/reducation/TWTAUU?startDate=20240101&endDate=20261015`（股票減資恢復買賣參考價格） |
| twtavu-detail-*.json | `/rwd/zh/reducation/TWTAVUDetail?STK_NO=&FILE_DATE=` |
| twtb8u.json | `/rwd/zh/change/TWTB8U?startDate=20240101&endDate=20261015`（變更股票面額恢復買賣參考價格） |
