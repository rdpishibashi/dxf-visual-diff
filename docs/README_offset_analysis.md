# DXF Offset Analysis Tool - 使用方法

## 概要

このツールは、2つのDXFファイル間でラベル（テキスト）の位置オフセットを分析し、基準点の違いによる誤検知を特定します。

## ファイル構成

- `analyze_label_offsets.py` - 基本的なオフセット分析スクリプト
- `detailed_offset_analysis.py` - 詳細な分析とクラスタリング（推奨）

## 必要な環境

- Python 3.x
- 必要なパッケージ（requirements.txtに記載）

## セットアップ

### 1. 仮想環境の作成（初回のみ）

```bash
cd /Users/ryozo/Dropbox/Client/ULVAC/ElectricDesignManagement/Tools/DXF-visual-diff
python3 -m venv venv
```

### 2. 仮想環境の有効化

```bash
source venv/bin/activate
```

### 3. 必要なパッケージのインストール（初回のみ）

```bash
pip install -r requirements.txt
```

## 使い方

### 基本的な分析

```bash
# 仮想環境を有効化
source venv/bin/activate

# 基本分析を実行
python analyze_label_offsets.py
```

**出力内容:**
- 共通ラベルの数
- オフセットの統計情報（平均、標準偏差、最小値、最大値）
- 支配的なオフセットの検出
- サンプルラベルとそのオフセット値

### 詳細分析（推奨）

```bash
# 仮想環境を有効化
source venv/bin/activate

# 詳細分析を実行
python detailed_offset_analysis.py
```

**出力内容:**
1. **Top 5クラスタの詳細**
   - 各クラスタのオフセット値
   - 該当するラベル数と割合
   - サンプルラベル（最初の10個）

2. **分析サマリー**
   - 変更なしラベルの数
   - 支配的なシフトパターン
   - その他のオフセット

3. **仮説の検証**
   - 補正なしの場合の差分率
   - 支配的オフセット補正後の改善率
   - 結論と推奨事項

4. **完全なオフセット分布リスト**
   - 全クラスタを割合順にランキング表示
   - 視覚的なバーチャート付き

## 自分のDXFファイルで分析する方法

### 方法1: スクリプトを直接編集

`detailed_offset_analysis.py`の26-27行目を編集：

```python
def main():
    file_a = "あなたのファイルA.dxf"  # ← ここを変更
    file_b = "あなたのファイルB.dxf"  # ← ここを変更
```

### 方法2: コマンドライン引数対応版を使う（新規作成）

コマンドラインから直接ファイルパスを指定できるバージョンを作成します。

## 出力の見方

### オフセット値の意味

- `(0.00, 0.00)` - 位置が完全に一致（変更なし）
- `(-2.60, -7.10)` - X方向に-2.6、Y方向に-7.1だけずれている

### 割合の意味

- **29.35%** - 全体の約3割のラベルがこのオフセット値を持つ
- **1%未満** - 少数のラベルのみ（個別の移動や編集の可能性）

### バーチャートの記号

- `█` - 2%以上の大きなクラスタ
- `▓` - 1-2%の中規模クラスタ
- `▒` - 0.5-1%の小規模クラスタ
- `░` - 0.5%未満の微小クラスタ

## 分析結果の活用

### ケース1: 支配的オフセットが20%以上

→ 基準点の違いが主要因。オフセット補正の実装を推奨。

### ケース2: 支配的オフセットが10-20%

→ 部分的に基準点が異なる。複数クラスタ補正を検討。

### ケース3: 支配的オフセットが10%未満

→ 実際のコンテンツ変更が主要因。補正の効果は限定的。

## トラブルシューティング

### エラー: `ModuleNotFoundError: No module named 'ezdxf'`

仮想環境が有効化されていません：

```bash
source venv/bin/activate
```

### エラー: `FileNotFoundError`

DXFファイルのパスが正しくありません。スクリプト内のファイルパスを確認してください。

### 分析時間が長い

大きなDXFファイルの場合、分析に時間がかかることがあります。正常です。

## 応用例

### 許容誤差の調整

`detailed_offset_analysis.py`の41行目でクラスタリングの許容誤差を調整できます：

```python
clusters = cluster_analysis(offsets, tolerance=0.1)  # デフォルト: 0.1
```

- `tolerance=0.01` - より細かいクラスタリング
- `tolerance=1.0` - より大まかなクラスタリング

## 終了方法

```bash
# 仮想環境を無効化
deactivate
```

## 参考情報

- ezdxfドキュメント: https://ezdxf.readthedocs.io/
- DXF仕様: https://help.autodesk.com/view/OARX/2024/ENU/
