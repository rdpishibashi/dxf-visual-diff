# INDEX.md — DXF-visual-diff 技術文書の歩き方

作業内容に応じて必要なファイルだけを読めばよい。

| ファイル | 内容 | こんなときに読む |
|---------|------|----------------|
| [OVERVIEW.md](OVERVIEW.md) | アーキテクチャ概要・`compare_dxf.py`/`extract_labels.py`/`label_diff.py` の技術仕様・トラブルシューティング・バージョン履歴 | 全体像を把握したい／比較ロジック本体を触る |
| [LABEL_COMPARISON_INTEGRATION.md](LABEL_COMPARISON_INTEGRATION.md) | DXF-diff-manager由来のラベル比較機能の統合内容 | ラベル差分Excel出力まわりを触る |
| [OFFSET_COMPENSATION_GUIDE.md](OFFSET_COMPENSATION_GUIDE.md) | オフセット補正機能の使用ガイド | オフセット補正の設定・挙動を確認したい |
| [OFFSET_COMPENSATION_QUICK_START.md](OFFSET_COMPENSATION_QUICK_START.md) | オフセット分析ツールのクイックスタート | `analyze_offset.py` をすぐ使いたい |
| [OFFSET_FEATURE_SUMMARY.md](OFFSET_FEATURE_SUMMARY.md) | オフセット補正機能の実装サマリー | 実装済み内容の全体像を知りたい |
| [OFFSET_ANALYSIS_TOOL.md](OFFSET_ANALYSIS_TOOL.md) | `analyze_offset.py` の詳細な使用方法 | オフセット分析ツールの個別オプションを確認したい |

ルート直下の `TECHNICAL.md` は本ファイルへの短いポインタとして維持している。

---
最終更新: 2026-08-03（DXF-extract-labels のドキュメント構成に合わせて新設。
`TECHNICAL_DOCS.md` → `OVERVIEW.md`、`README_offset_analysis.md` → `OFFSET_ANALYSIS_TOOL.md`
にリネーム）
