# クイックスタートガイド - DXFオフセット分析ツール

## 最も簡単な使い方

### 1回目のみ: セットアップ

```bash
cd /Users/ryozo/Dropbox/Client/ULVAC/ElectricDesignManagement/Tools/DXF-visual-diff
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 毎回: 分析実行

```bash
# 仮想環境を有効化
source venv/bin/activate

# 分析実行（自分のDXFファイルを指定）
python analyze_offset.py ファイルA.dxf ファイルB.dxf
```

## 使用例

### 例1: 基本的な分析

```bash
source venv/bin/activate
python analyze_offset.py EE3273-601-09B.dxf EE4475-300-01G.dxf
```

**何が表示されるか:**
- 変更なしラベルの割合
- 支配的なオフセットパターン
- トップ10のオフセットクラスタ

### 例2: トップ20クラスタを表示

```bash
python analyze_offset.py drawing1.dxf drawing2.dxf --top-n 20
```

### 例3: すべてのクラスタを表示

```bash
python analyze_offset.py drawing1.dxf drawing2.dxf --all
```

### 例4: 許容誤差を変更（より細かいクラスタリング）

```bash
python analyze_offset.py drawing1.dxf drawing2.dxf --tolerance 0.01
```

## 出力の読み方

### サマリーセクション

```
1. 変更なしラベル: 408 (29.35%)
   → 両ファイルで位置が完全に一致

2. 支配的シフトパターン: (-2.6, -7.1)
   Count: 319 (22.95%)
   → このオフセットで移動したラベルの集団

3. その他のオフセット: 663 (47.70%)
   → バラバラな位置変更
```

### 仮説の検証セクション

```
補正なしの場合:
  - 70.6% のラベルが異なる位置

支配的オフセット補正後:
  - 22.9% の差分を補正可能
  - 残り 47.7%
```

**判断基準:**
- **補正可能 > 20%** → オフセット補正を実装する価値あり
- **補正可能 10-20%** → 場合によっては有効
- **補正可能 < 10%** → 実際の差分が主要因、補正効果は限定的

### クラスタリストセクション

```
Rank   Offset (dx, dy)         Count      %        Bar
1      (0.00, 0.00)           408       29.35%   ██████████████
2      (-2.60, -7.10)         319       22.95%   ███████████
3      (-2.60, 42.90)         41         2.95%   █
```

- **Rank 1-2**: 主要なパターン（変更なし + 支配的オフセット）
- **Rank 3-5**: 副次的なオフセットパターン（複数セクション移動の可能性）
- **Rank 6以降**: 個別の編集や微調整

## よくある質問

### Q: ファイル名にスペースが含まれる場合は？

```bash
python analyze_offset_cli.py "File A.dxf" "File B.dxf"
```

### Q: 相対パスでも指定できる？

はい。現在のディレクトリからの相対パスでOKです：

```bash
python analyze_offset_cli.py ../drawings/fileA.dxf ../drawings/fileB.dxf
```

### Q: 複数のファイルペアを一度に分析したい

現在は1ペアずつです。複数分析する場合はスクリプトを繰り返し実行してください：

```bash
python analyze_offset.py pair1_A.dxf pair1_B.dxf > result1.txt
python analyze_offset.py pair2_A.dxf pair2_B.dxf > result2.txt
```

### Q: 結果をファイルに保存したい

リダイレクトを使用：

```bash
python analyze_offset.py fileA.dxf fileB.dxf > analysis_result.txt
```

### Q: エラーが出た

**エラー: `ModuleNotFoundError`**
→ 仮想環境を有効化してください: `source venv/bin/activate`

**エラー: `FileNotFoundError`**
→ ファイルパスを確認してください

**エラー: `共通するラベルが見つかりませんでした`**
→ 完全に異なるファイルか、ラベルが存在しません

## 終了方法

```bash
deactivate
```

## 詳細オプション

すべてのオプションを確認：

```bash
python analyze_offset.py --help
```

## 次のステップ

詳細なドキュメント: `README_offset_analysis.md`を参照してください。
