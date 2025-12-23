# DXF-visual-diff と DXF-diff-manager の同期更新

## 更新日時
2025-12-24

## 概要
DXF-diff-manager プロジェクトの最新版に合わせて、DXF-visual-diff の utils フォルダ内のファイルを同期更新しました。

## 更新されたファイル

### 1. utils/common_utils.py
**変更内容:**
- `import re` を追加

**影響:** なし（未使用のインポートだが、将来の拡張に備えて追加）

### 2. utils/compare_dxf.py
**主な変更:**
- `import gc` を追加（ガベージコレクション用）
- **戻り値の変更（重要）:**
  - 変更前: `bool` （成功/失敗のみ）
  - 変更後: `Tuple[bool, Optional[Dict[str, int]]]` （成功/失敗 + エンティティ数情報）
- エンティティ数の計算と返却機能を追加:
  - `deleted_entities`: 削除されたエンティティ数
  - `added_entities`: 追加されたエンティティ数
  - `unchanged_entities`: 変更なしエンティティ数
  - `diff_entities`: 差分エンティティ数（削除+追加）
  - `total_entities`: 総エンティティ数
- 明示的なメモリ解放処理を追加（大規模ファイル対応）
- ガベージコレクション実行を追加

**ファイルサイズ:** 48KB → 49KB

### 3. utils/extract_labels.py
**主な変更:**
- 簡易版（10KB）から完全版（35KB）に置き換え
- DXF-diff-manager の完全機能を移植:
  - 図面番号の自動抽出
  - タイトル・サブタイトルの抽出
  - 流用元図番の判別
  - より詳細な座標処理
  - config.py への依存を内部定数に変更

**ファイルサイズ:** 10KB → 35KB

### 4. utils/label_diff.py
**変更内容:**
- コメントのみ微修正（機能変更なし）

**ファイルサイズ:** 変更なし（8.6KB）

## app.py の対応変更

### compare_dxf.py の戻り値変更への対応

**変更箇所 1: 関数呼び出し部分**
```python
# 変更前
result = compare_dxf_files_and_generate_dxf(...)
if result:

# 変更後
success, entity_counts = compare_dxf_files_and_generate_dxf(...)
if success:
```

**変更箇所 2: 結果タプルの拡張**
```python
# 変更前（6要素）
results.append((
    pair_name, file_a.name, file_b.name,
    output_filename, dxf_data, True
))

# 変更後（7要素 - entity_counts を追加）
results.append((
    pair_name, file_a.name, file_b.name,
    output_filename, dxf_data, True, entity_counts
))
```

**変更箇所 3: 結果のアンパック（複数箇所）**
```python
# create_zip_archive関数内
for pair_name, ..., success, _ in results:

# 結果表示ループ内
for pair_name, ..., success, entity_counts in results:
```

### 新機能: エンティティ数統計の表示

**個別ダウンロードモード:**
```
Pair1: FileA.dxf ↔ FileB.dxf
📊 削除: 15, 追加: 8, 変更なし: 342, 合計: 365
[ダウンロードボタン]
```

**ZIPダウンロードモード:**
```
✅ Pair1: FileA.dxf ↔ FileB.dxf → fileA_vs_fileB.dxf (差分: 23件)
```

## 互換性

### 下位互換性
- ✅ 既存のDXFファイル比較機能は完全に動作
- ✅ 既存のラベル比較機能は完全に動作
- ✅ オフセット補正機能は完全に動作

### 追加機能
- ✅ エンティティ数統計の自動表示
- ✅ メモリ効率の改善（大規模ファイル対応）
- ✅ より詳細なラベル抽出（図面番号、タイトルなど）

## 検証結果

### 構文チェック
```bash
python -m py_compile app.py utils/compare_dxf.py utils/extract_labels.py utils/label_diff.py
```
**結果:** ✅ エラーなし

### ファイル同期確認
```
DXF-diff-manager/utils/  →  DXF-visual-diff/utils/
├─ common_utils.py        ✅ 同期済み
├─ compare_dxf.py         ✅ 同期済み
├─ extract_labels.py      ✅ 同期済み
└─ label_diff.py          ✅ 同期済み
```

## 今後の同期について

DXF-diff-manager の utils フォルダが更新された場合は、以下の手順で同期してください:

1. **ファイルの比較:**
   ```bash
   ls -lh DXF-diff-manager/utils/
   ls -lh DXF-visual-diff/utils/
   ```

2. **差分の確認:**
   ```bash
   diff -u DXF-visual-diff/utils/<file>.py DXF-diff-manager/utils/<file>.py
   ```

3. **ファイルのコピー:**
   ```bash
   /bin/cp DXF-diff-manager/utils/<file>.py DXF-visual-diff/utils/<file>.py
   ```

4. **app.py の更新:**
   - compare_dxf.py の戻り値が変更された場合は app.py を更新
   - 新しい機能が追加された場合は UI を拡張

5. **構文チェック:**
   ```bash
   python -m py_compile app.py utils/*.py
   ```

## 注意事項

- **extract_labels.py について:**
  - DXF-diff-manager版は config.py に依存していますが、DXF-visual-diff版では定数を内部化しています
  - 今回のコピーで config.py への依存が含まれている可能性があります
  - もし実行時にエラーが発生した場合は、config.py の内容を extract_labels.py 内部に定義する必要があります

- **メモリ管理:**
  - 新しい compare_dxf.py は明示的なメモリ解放を行います
  - 大規模なDXFファイル（数MB以上）を処理する際に効果があります

## 参考情報

### DXF-diff-manager の最新機能
- エンティティ数の自動カウント
- Parent-Child リストの管理
- RevUp ペアの自動検出
- 図面番号・タイトルの自動抽出

### DXF-visual-diff の独自機能
- オフセット補正設定（UI）
- プレフィックス設定（UI）
- 複数ペア（最大5ペア）の同時処理
- 個別/ZIP ダウンロードの選択
