# DXF Visual Diff - 技術ドキュメント

> 対象ファイル: `utils/compare_dxf.py`, `utils/extract_labels.py`, `utils/label_diff.py`
> 最終更新: 2026-03-15

---

## 目次

1. [アーキテクチャ概要](#アーキテクチャ概要)
2. [compare_dxf.py — 視覚的差分エンジン](#compare_dxfpy)
3. [extract_labels.py — テキスト抽出エンジン](#extract_labelspy)
4. [label_diff.py — ラベル比較エンジン](#label_diffpy)
5. [処理フロー全体図](#処理フロー全体図)
6. [設定パラメータ一覧](#設定パラメータ一覧)
7. [既知の制約と注意事項](#既知の制約と注意事項)

---

## アーキテクチャ概要

```
app.py
├── compare_dxf_files_and_generate_dxf()  ← compare_dxf.py
│   └── 出力: 差分DXFファイル (ADDED/DELETED/UNCHANGED レイヤー)
│
├── compute_label_differences()            ← label_diff.py
│   └── extract_labels()                  ← extract_labels.py
│   └── 出力: diff_labels.xlsx / unchanged_labels.xlsx
│
└── filter_unchanged_by_prefix()          ← label_diff.py
```

本プロジェクトの処理は2つの独立した軸で構成される:

- **視覚的差分** (`compare_dxf.py`): DXFエンティティ（線・円・テキスト等）を幾何学的に比較し、差分を色分けしたDXFファイルとして出力する
- **ラベル差分** (`label_diff.py` + `extract_labels.py`): テキストラベルのみを抽出・比較し、追加/削除/未変更をExcelで出力する

---

## compare_dxf.py

### 概要

DXFファイル2つを比較し、差分を色分けした新しいDXFファイルを生成するエンジン。
エンティティをハッシュベースで同一性判定するため、INSERT（ブロック参照）を絶対座標に展開してから比較する。

### クラス構成

```
ToleranceConfig
    └─ CoordinateTransformer
        ├─ EntityExpander
        │   └─ SignatureGenerator
        │       └─ DiffAnalyzer
        └─ OutputGenerator
                └─ LayerConfig
```

---

### `ToleranceConfig`

**役割**: エンティティタイプ・属性ごとの許容誤差を管理する設定ホルダー。

```python
ToleranceConfig(base_tolerance: float = 0.01)
```

| 属性 | 算出式 | 用途 |
|------|--------|------|
| `coordinate_tolerance` | `base_tolerance` | 一般座標比較 |
| `connection_tolerance` | `base_tolerance × 0.1` | 接続点（POINT）の比較 |
| `text_position_tolerance` | `base_tolerance × 2` | テキスト位置比較（緩め）|
| `angle_tolerance` | `0.1` (固定) | 角度比較（度単位）|
| `length_tolerance` | `base_tolerance` | 長さ比較（半径等）|

**`get_tolerance_for_entity(entity_type, attribute)`**
エンティティタイプと属性名を渡すと適切な許容誤差を返す。
`TEXT/MTEXT/ATTRIB` → `text_position_tolerance`、
`POINT` or `connection` を含む属性 → `connection_tolerance`、
`angle/rotation` → `angle_tolerance`、その他 → `coordinate_tolerance`。

---

### `CoordinateTransformer`

**役割**: 座標の正規化（量子化）と4×4アフィン変換行列の操作を担う。

#### `normalize_coordinate_precise(value, tolerance)`

`Decimal` クラスを使った高精度量子化。`value` を `tolerance` の格子点に丸める。

```
量子化後の値 = round(value / tolerance) * tolerance
```

浮動小数点誤差を避けるため `Decimal(str(value))` で文字列経由変換する。
`Decimal` 変換失敗時は通常の `round()` にフォールバック。

#### `normalize_coordinate_with_context(coord, entity_type, attribute)`

ベクトル/タプル/スカラーに対して `normalize_coordinate_precise` を適用する。
`ToleranceConfig.get_tolerance_for_entity` で適切な許容誤差を取得してから適用する。

#### `create_transformation_matrix(insert_entity)`

INSERTエンティティから4×4同次変換行列（`numpy.ndarray`）を作成。

変換の順序（右から左に適用）:
```
M = T(translation) @ R(rotation) @ S(scale)
```

- スケール行列: `xscale`, `yscale`, `zscale` を対角要素に設定
- 回転行列: Z軸まわりの2D回転（`rotation` 属性、度単位 → ラジアン変換）
- 平行移動行列: `insert` 点の (tx, ty, tz) を最右列に設定

属性取得失敗時は単位行列を返す。

#### `transform_point(point, transform_matrix)`

同次座標 `[x, y, z, 1]` に変換行列を掛けて変換後の点を返す。

#### `extract_scale_factors(transform_matrix)`

変換行列の各列ベクトルのノルムからスケールファクター `(sx, sy, sz)` を抽出。

---

### `EntityExpander`

**役割**: DXFのINSERTエンティティ（ブロック参照）を展開し、全エンティティを絶対座標の辞書形式に変換する。

#### コンストラクタ引数

| 引数 | 型 | 説明 |
|------|----|------|
| `transformer` | `CoordinateTransformer` | 座標変換に使用 |
| `global_offset` | `Optional[Tuple[float, float]]` | ファイルB用のオフセット補正 `(dx, dy)` |

`excluded_attributes`: ハンドル・オーナー等、比較に不要なezdxf内部属性を除外するセット。

#### `expand_insert_entities(doc, doc_label)`

モデル空間を走査し、以下を処理:

1. **INSERT エンティティ**: `create_transformation_matrix` で変換行列を生成し、
   ブロック内の各エンティティを `transform_entity_to_absolute` で絶対座標に変換。
   ATTDEF（属性定義）はスキップ。ATTRIB（属性値）は単位行列で処理（既に絶対座標）。

2. **直接エンティティ**: 単位行列を使って `transform_entity_to_absolute` を呼ぶ。

返却値は絶対座標エンティティの辞書リスト。各辞書の構造:

```python
{
    'dxftype': str,           # エンティティ種別
    'attributes': dict,       # 変換済みDXF属性
    'text_content': str,      # テキスト内容（TEXT/MTEXT/ATTRIBのみ）
    'is_transformed': bool,   # 変換適用済みフラグ
    'original_entity_id': int,
    'scale_factors': tuple or None,
    'insert_info': dict,      # ブロック由来の場合のみ
    'is_direct_modelspace': bool  # 直接エンティティの場合のみ
}
```

#### `transform_entity_to_absolute(entity, transform_matrix)`

1. `safe_get_dxf_attributes` で内部属性を除いたDXF属性辞書を取得
2. `_transform_coordinate_attributes` で座標属性を変換
3. スケールがある場合は `_transform_size_attributes` でサイズ属性も変換

#### `_transform_coordinate_attributes(clean_attrs, transformed_attrs, transform_matrix)`

変換対象座標属性: `insert`, `center`, `start`, `end`, `location`, `base_point`。
各点を同次変換後、`global_offset` を加算。

特殊処理:
- **ELLIPSE の `major_axis`**: 方向ベクトルのため、同次座標の w=0 で変換（平行移動を除外）
- **LWPOLYLINE の頂点**: 全頂点に変換＋オフセット適用後、2D点 `(x, y)` に切り捨て

#### `_transform_size_attributes(entity_type, ...)`

スケールがある場合のみ呼ばれる:
- `CIRCLE/ARC`: `radius` に `(scale_x + scale_y) / 2` を乗算
- `ELLIPSE`: `major_axis` に各軸スケールを乗算
- `TEXT/MTEXT/ATTRIB`: `height` に `scale_y` を乗算

#### `_extract_lwpolyline_vertices(entity)`

頂点取得を3通りの方法で試行（`get_points()`, `entity.vertices` でオブジェクト/タプル）、
最初に成功した方法の結果を返す。

---

### `SignatureGenerator`

**役割**: 絶対座標エンティティから、許容誤差を考慮した人間可読なシグネチャ文字列を生成する。
このシグネチャをSHA256ハッシュ化して同一性判定に使う。

#### `create_absolute_entity_signature(absolute_entity)`

シグネチャ生成の順序:

1. エンティティタイプ
2. 主要位置 (`insert/center/start/location` から最初に見つかったもの) の正規化座標
3. テキスト内容（空白・改行を除去）
4. ATTRIB の場合はタグ名
5. 重要属性 (`color`, `height`, `radius`, `start_angle`, `end_angle`, `rotation`)
6. エンティティ種別固有のジオメトリ詳細

各部分を `"_"` で結合した文字列を返す。

#### `_add_important_attributes(...)`

`height/radius` はスケール変換後のエンティティに対して `tolerance * 2` の緩めの正規化を適用。
`rotation` は `% (2π)` で正規化後、`math.radians(angle_tolerance)` 単位で量子化。

#### `_add_geometry_details(...)`

エンティティ種別ごとの詳細をシグネチャに追加:
- `LINE`: start, end 座標
- `CIRCLE`: center, radius
- `ARC`: center, radius, start_angle, end_angle
- `ELLIPSE`: center, major_axis, ratio, start_param, end_param
- `LWPOLYLINE`: 最初の5頂点（処理速度とのバランス）

---

### `DiffAnalyzer`

**役割**: エンティティの抽出とハッシュ生成、ドキュメント全体の比較を担う。

#### `generate_enhanced_hash(entity_data)`

`entity_data` に `absolute_signature` がある場合はそれをSHA256ハッシュ化。
ない場合はJSON文字列全体をハッシュ化（フォールバック）。

#### `extract_entities_from_doc(doc, doc_label, expander)`

返却値のタプル:
```python
(
    entities_by_hash: Dict[str, List[(location, virtual_entity)]],
    hash_to_entity_data: Dict[str, entity_data],
    hash_to_locations: Dict[str, Set[str]]
)
```

location は `"modelspace"` またはブロック名を含む `"expanded_from_{block_name}"`。

---

### `LayerConfig`

**役割**: 出力DXFの3レイヤー設定を保持する。

| レイヤー | デフォルト色 | 色番号 | 意味 |
|----------|------------|--------|------|
| `DELETED` | マゼンタ | 6 | Aにのみ存在 |
| `ADDED` | シアン | 4 | Bにのみ存在 |
| `UNCHANGED` | 白/黒 | 7 | 両方に存在 |

AutoCADの色番号（ACI: AutoCAD Color Index）を使用。

---

### `OutputGenerator`

**役割**: 差分計算結果を受け取り、実際のDXFファイルを書き出す。

#### `create_diff_dxf(...)`

処理手順:
1. `ezdxf.new('R2018')` で新規DXFドキュメントを作成（UTF-8 Unicode対応版）
2. 3つのレイヤーを作成
3. `deleted_hashes` の各エンティティ → DELETEDレイヤーに追加（最初のインスタンスのみ）
4. `added_hashes` の各エンティティ → ADDEDレイヤーに追加
5. `common_hashes` の各エンティティ → UNCHANGEDレイヤーに追加
6. `saveas()` で保存後、`_ensure_japanese_text_compatibility()` でUTF-8検証

#### `create_entity_from_absolute(absolute_entity, target_space, layer_name, layer_color)`

サポートされているエンティティ種別と生成メソッド:

| DXFtype | 生成メソッド | 備考 |
|---------|------------|------|
| `LINE` | `add_line` | start/end 使用 |
| `CIRCLE` | `add_circle` | center/radius 使用 |
| `ARC` | `add_arc` | center/radius/start_angle/end_angle |
| `ELLIPSE` | `add_ellipse` | 失敗時はCIRCLEにフォールバック |
| `TEXT` | `add_text` | text_content 使用 |
| `MTEXT` | `add_mtext` | text_content 使用 |
| `ATTRIB` | `add_text` として | タグ名か値をテキストで代替 |
| `POINT` | `add_point` | location 使用 |
| `LWPOLYLINE` | `add_lwpolyline` | 3点以上で`close()` |
| その他 | `add_text` | `[ENTITY_TYPE]` を代替テキストとして配置 |

ELLIPSE の特殊処理: `ratio <= 0` や ゼロ `major_axis` を検出してデフォルト値に修正。
ELLIPSEの作成に失敗した場合は `major_axis` の長さを半径とする円にフォールバック。

---

### 公開API: `compare_dxf_files_and_generate_dxf()`

```python
def compare_dxf_files_and_generate_dxf(
    file_a: str,
    file_b: str,
    output_file: str,
    tolerance: float = 0.01,
    deleted_color: int = 6,
    added_color: int = 4,
    unchanged_color: int = 7,
    offset_b: Optional[Tuple[float, float]] = None
) -> Tuple[bool, Optional[Dict[str, int]]]
```

処理の流れ:
```
1. 設定オブジェクト群を初期化
2. doc_a, doc_b を ezdxf.readfile() で読み込み
3. expander_a (offset=None), expander_b (offset=offset_b) でエンティティ展開
4. diff_analyzer でハッシュ辞書を生成
5. 集合演算: deleted = A-B, added = B-A, common = A∩B
6. output_generator.create_diff_dxf() で差分DXF出力
7. 大きなオブジェクトを del + gc.collect() で明示的に解放
```

返却値の `entity_counts` 辞書:

| キー | 意味 |
|------|------|
| `deleted_entities` | Aのみに存在したエンティティ数 |
| `added_entities` | Bのみに存在したエンティティ数 |
| `unchanged_entities` | 両方に存在したエンティティ数 |
| `diff_entities` | deleted + added |
| `total_entities` | deleted + added + unchanged |

---

## extract_labels.py

### 概要

DXFファイルからテキストエンティティを抽出するエンジン。
オプションで図番・タイトル・サブタイトルの自動抽出も行う。

### 設定の読み込み（環境適応型）

```python
try:
    from config import extraction_config  # DXF-diff-manager 環境
except ImportError:
    # 内部定義のデフォルト値を使用（DXF-visual-diff 環境）
```

`config.py` が存在しない場合は `ExtractionConfig` クラスをモジュール内で定義する。
これにより、本プロジェクトは `config.py` なしでも動作し、DXF-diff-manager では外部設定を優先する。

---

### `get_layers_from_dxf(dxf_file)`

ezdxf の `doc.layers` テーブルからレイヤー名一覧をアルファベット順で返す。

---

### `clean_mtext_format_codes(text, debug=False)`

MTEXTの制御コードを除去し、テキスト内容のみを残す。

**処理順序:**

1. `¥`（U+00A5, 円マーク）を `\`（U+005C）に正規化（日本語環境対応）
2. 以下の制御コードを除去（`\X...;` 形式）:
   - `\f`: フォント指定
   - `\H`: 高さ
   - `\W`: 幅
   - `\C`: カラー
   - `\A`: 配置
   - `\T`: 文字間隔（トラッキング）
   - その他 `\(?!P)[^\\;]*;`（`\P` は除く）
3. `\~` → スペースに変換（非改行スペース）
4. `\\` → `\`、`\{` → `{`、`\}` → `}` のエスケープ処理
5. 連続空白 → 単一スペース
6. `\P` → スペース（段落区切り。比較では改行より空白区切りが有用）
7. 最終的に `re.sub(r'\s+', ' ', ...).strip()` で正規化

**注意**: `\P` は段落区切りとして重要な構造を持つが、このプロジェクトでは空白に変換している。
これは「テキスト内容の同一性判定」のためであり、改行位置の差異を無視する設計。

---

### `extract_text_from_entity(entity, debug=False)`

返却値: `(raw_text, clean_text, (x, y))`

**座標取得の優先順:**
- MTEXT: `dxf.insert` → `dxf.x/dxf.y` → `getattr` でフォールバック
- TEXT: `dxf.insert` → `dxf.location`

**テキスト取得の優先順 (MTEXTのみ):**
1. `entity.dxf.text` （生テキスト）
2. `entity.text` （ezdxfプロパティ）
3. `entity.plain_text()` （ezdxfメソッド）

エラー時は `("", "", (0.0, 0.0))` を返す（例外は握りつぶす）。

---

### `extract_drawing_numbers(text, debug=False)`

`extraction_config.DRAWING_NUMBER_PATTERN` で図番パターンを取得し `re.findall` で抽出。

デフォルトパターン: `r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'`
例: `DE5313-008-02B`（長形式）、`DE5313-008B`（短形式、`-\d{2}` 省略）

重複除外は `upper()` で大文字統一してから判定する。

---

### `determine_drawing_number_types(...)`

図面番号候補のリストから「主図番」と「流用元図番」を判別する。

**判別ルールの優先順位:**

1. **ファイル名一致**: `filename.stem` と図番が一致/包含関係 → 主図番
2. **ラベル近接（流用元）**: `'流用元図番'` または `'流用元'` を含むラベルに最も近い図番
   （`extraction_config.SOURCE_LABEL_PROXIMITY = 80` 以内）→ 流用元図番
3. **ラベル近接（DWG No.）**: `'DWG'+'NO'` を含むラベルに最も近い図番
   （`extraction_config.DWG_NO_LABEL_PROXIMITY = 80` 以内）→ 主図番
4. **座標フォールバック**: X座標が最大の範囲 (`RIGHTMOST_DRAWING_TOLERANCE = 100.0` 以内) に絞り、
   `X + Y` が最大（右下）を主図番、次点を流用元図番とする

最終検証: 流用元図番と主図番が同じ場合は流用元を `None` にする。

---

### `extract_title_and_subtitle(all_labels, drawing_numbers, debug=False)`

テキストラベルの空間的位置関係からタイトルとサブタイトルを抽出する。

**処理手順:**

1. `TITLE` ラベルを全て検出し、X座標が最大のものを採用
2. `REVISION` ラベルを検出し、X座標が最大のものを採用
3. タイトル候補を収集:
   - TITLEの右側 `(10 < x_diff < TITLE_PROXIMITY_X=80)` に位置する
   - REVISIONがある場合はその下方（Y座標が小さい）
4. 座標許容誤差 `1.0` で重複除去
5. Y座標でグルーピング（許容誤差 `5.0`）
6. 最も高い（Y座標が最大）グループをタイトル行とする
   ※ 同Y範囲 (`10.0` 以内) の複数グループがある場合は最もX最小値が小さいものを選択
7. タイトル行より下にある重複X範囲のグループをサブタイトル候補とし、
   最も高い（Y座標最大）ものを採用
8. サブタイトルの末尾が英大文字1文字（半角/全角）なら除外

返却値: `{'title': str or None, 'subtitle': str or None}`

---

### `extract_labels(dxf_file, ...)` — メイン抽出関数

```python
def extract_labels(
    dxf_file,
    filter_non_parts=False,      # 現在は未使用（パラメータのみ残存）
    sort_order="asc",            # "asc" / "desc" / "none"
    debug=False,
    selected_layers=None,        # None = 全レイヤー
    validate_ref_designators=False,  # 現在は未使用
    extract_drawing_numbers_option=False,
    extract_title_option=False,
    include_coordinates=False,   # True: (label, x, y) タプルを返す
    original_filename=None       # 一時ファイル時の元ファイル名
)
```

**エンティティ収集の順序:**

1. モデル空間から `TEXT/MTEXT`
2. ペーパースペース（`Model` 以外のレイアウト）から `TEXT/MTEXT`
3. モデル空間の `INSERT` を展開してブロック内の `TEXT/MTEXT`
4. ペーパースペースの `INSERT` を展開してブロック内の `TEXT/MTEXT`

**重複除去**: `(dxftype, layer, insert座標)` をキーとする `set` で重複を除外。
※ 同一テキストが異なる座標にある場合は重複とみなさない（意図的な設計）。

**返却値の形式:**
- `include_coordinates=False`: `[str, ...]`
- `include_coordinates=True`: `[(label, x, y), ...]`

`info` 辞書のキー:

| キー | 型 | 説明 |
|------|----|------|
| `total_extracted` | int | 抽出したラベル総数 |
| `filtered_count` | int | フィルタ除外数（現在常に0） |
| `final_count` | int | 最終ラベル数 |
| `processed_layers` | int | 処理対象レイヤー数 |
| `total_layers` | int | 総レイヤー数 |
| `filename` | str | ファイル名 |
| `invalid_ref_designators` | list | 無効な回路記号（現在常に空） |
| `main_drawing_number` | str/None | 主図番 |
| `source_drawing_number` | str/None | 流用元図番 |
| `all_drawing_numbers` | list | 全図番候補 |
| `title` | str/None | タイトル |
| `subtitle` | str/None | サブタイトル |

---

## label_diff.py

### 概要

`extract_labels()` で取得した座標付きラベルを比較し、差分・未変更をExcelで出力する。
「座標が一致するラベル同士を突き合わせる」という位置ベースの差分アルゴリズムを採用する。

---

### 差分アルゴリズムの設計思想

```
旧ファイル (A) の座標Pにラベル集合 {R1, R2, C1}
新ファイル (B) の座標Pにラベル集合 {R1, R3, C1, C1}

共通ラベルを最小個数分消去:
  R1: min(1,1)=1 → unchanged
  C1: min(1,2)=1 → unchanged (C1が1個余る)

残り:
  old_only = [R2]      (削除)
  new_only = [C1, R3]  (追加)

pairable = min(1, 2) = 1 → (R2, C1) として変更ペアに
leftover new = [R3] → New のみで追加扱い
```

このアルゴリズムにより、「同じ座標での名称変更」を1つの変更として表現できる。

---

### 関数一覧

#### `round_coordinate(value, tolerance)`

```python
round(value / tolerance) * tolerance
```

`tolerance=0` の場合は値をそのまま返す（ゼロ除算防止）。

#### `round_labels_with_coordinates(labels, tolerance)`

`(label, x, y)` のリストに `round_coordinate` を適用して返す。

#### `group_labels_by_coordinate(rounded_labels)`

```python
{(x, y): Counter({label: count, ...}), ...}
```

量子化された座標をキー、`Counter` オブジェクトを値とする辞書。

#### `compute_label_differences(new_file, old_file, tolerance=0.01)`

```python
change_rows, unchanged_entries = compute_label_differences(new_file, old_file)
```

1. 両ファイルから `extract_labels(..., include_coordinates=True)` で座標付きラベルを取得
2. 座標を `tolerance` で量子化
3. 座標ごとに `Counter` を作成
4. `find_label_change_pairs()` で差分計算
5. `change_rows` を `(Old Label, New Label)` のアルファベット順でソートして返す

**引数の順序に注意**: 第1引数が「新ファイル (B)」、第2引数が「旧ファイル (A)」。
`app.py` の呼び出し側: `compute_label_differences(temp_file_b, temp_file_a, ...)` の順。

#### `find_label_change_pairs(group_new, group_old)`

返却値:
```python
change_rows = [
    {
        'Coordinate X': float,
        'Coordinate Y': float,
        'Old Label': str or None,   # None = 追加（旧にない）
        'New Label': str or None    # None = 削除（新にない）
    },
    ...
]

unchanged_entries = [
    {
        'label': str,
        'count': int,
        'coordinate': (x, y)
    },
    ...
]
```

#### `filter_unchanged_by_prefix(unchanged_entries, prefixes)`

指定されたプレフィックスで始まるラベルのみを抽出。
同一 `(label, x, y)` キーで集計し、`count` を合算して返す。

デフォルト設定 (`prefix_config.txt`) では `W No.` が設定されており、
ワイヤー番号ラベルの未変更一覧を出力することを想定している。

---

### Excelワークブック生成

#### `build_diff_labels_workbook(sheets)`

```python
sheets = [
    {
        'sheet_name': str,     # Excelシート名（ファイル名から自動生成）
        'rows': list,          # change_rows のデータ
        'old_label_name': str, # "Old: ファイル名" カラムヘッダー
        'new_label_name': str  # "New: ファイル名" カラムヘッダー
    }
]
```

出力列: `Coordinate X`, `Coordinate Y`, `Old Label (カスタム名)`, `New Label (カスタム名)`

#### `build_unchanged_labels_workbook(sheets)`

出力列: `Label`, `Count`, `Coordinate X`, `Coordinate Y`

#### `ensure_unique_sheet_name(name, used_names)`

Excelのシート名制限（31文字）に対応しつつ、重複が発生した場合は `_1`, `_2` ... と連番サフィックスを付与する。
サフィックス付与後も31文字制限を超えないよう、ベース名を短縮する。

#### `format_sheet(writer, sheet_name, df)`

列幅の設定:
- `Coordinate X/Y`: 14文字
- `Old Label`, `New Label`, `Label`: 30文字
- その他（`Count`等）: 12文字

さらに `freeze_panes(1, 0)` でヘッダー行を固定する。

---

## 処理フロー全体図

```
ユーザー操作 (app.py)
│
├─ ファイルペア登録 (最大5ペア)
├─ 許容誤差・色設定
├─ オフセット補正設定 (オプション)
└─ 「DXF差分を比較」ボタン
    │
    ├─ [各ペアに対して]
    │   ├─ save_uploadedfile() → 一時ファイル
    │   │
    │   ├─ compare_dxf_files_and_generate_dxf()
    │   │   ├─ ezdxf.readfile(A), ezdxf.readfile(B)
    │   │   ├─ EntityExpander.expand_insert_entities(A) → 絶対座標エンティティリスト
    │   │   ├─ EntityExpander.expand_insert_entities(B) → 絶対座標エンティティリスト
    │   │   │                                              (offset_b 適用済み)
    │   │   ├─ SignatureGenerator.create_absolute_entity_signature()
    │   │   │   → 文字列シグネチャ
    │   │   ├─ DiffAnalyzer.generate_enhanced_hash()
    │   │   │   → SHA256ハッシュ
    │   │   ├─ 集合演算: deleted=A-B, added=B-A, unchanged=A∩B
    │   │   └─ OutputGenerator.create_diff_dxf() → 差分DXFファイル
    │   │
    │   └─ compute_label_differences()
    │       ├─ extract_labels(B, include_coordinates=True)
    │       │   ├─ MODEL/PAPER SPACE エンティティ収集
    │       │   ├─ INSERT 展開
    │       │   ├─ 重複除去
    │       │   └─ clean_mtext_format_codes() + extract_text_from_entity()
    │       ├─ extract_labels(A, include_coordinates=True)  [同上]
    │       ├─ round_labels_with_coordinates() × 2
    │       ├─ group_labels_by_coordinate() × 2
    │       └─ find_label_change_pairs()
    │           → change_rows, unchanged_entries
    │
    ├─ filter_unchanged_by_prefix(unchanged_entries, prefixes)
    ├─ build_diff_labels_workbook(diff_sheets) → diff_labels.xlsx
    ├─ build_unchanged_labels_workbook(unchanged_sheets) → unchanged_labels.xlsx
    └─ 一時ファイル削除
```

---

## 設定パラメータ一覧

### compare_dxf.py

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `tolerance` | `0.01` | 座標の量子化単位（DXF座標系のユニット）|
| `deleted_color` | `6` (マゼンタ) | DELETEDレイヤーのACI色番号 |
| `added_color` | `4` (シアン) | ADDEDレイヤーのACI色番号 |
| `unchanged_color` | `7` (白/黒) | UNCHANGEDレイヤーのACI色番号 |
| `offset_b` | `None` | ファイルBの座標オフセット `(dx, dy)` |

### extract_labels.py / ExtractionConfig

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `DRAWING_NUMBER_PATTERN` | `r'[A-Z]{2}\d{4}-\d{3}(?:-\d{2})?[A-Z]'` | 図番の正規表現 |
| `SOURCE_LABEL_PROXIMITY` | `80` | 流用元図番ラベルからの検出距離 |
| `DWG_NO_LABEL_PROXIMITY` | `80` | DWG No.ラベルからの検出距離 |
| `TITLE_PROXIMITY_X` | `80` | TITLEラベル右側の検出範囲 |
| `RIGHTMOST_DRAWING_TOLERANCE` | `100.0` | 右端図面判定の許容範囲 |

### label_diff.py

| パラメータ | デフォルト | 説明 |
|-----------|-----------|------|
| `tolerance` | `0.01` | 座標量子化単位 |
| `prefixes` | `prefix_config.txt` から読み込み | 未変更フィルタ用プレフィックス |

---

## 既知の制約と注意事項

### compare_dxf.py

1. **LWPOLYLINE のシグネチャは最初の5頂点のみ使用**
   長い多角形で先頭5頂点が同一の場合、異なる図形を同一と誤判定する可能性がある。

2. **ATTRIB はテキストとして出力**
   ezdxf の制約により、出力DXFで ATTRIB を INSERT 内の属性として復元していない。
   ATTRIB の内容は TEXT エンティティとして DELETEDレイヤー等に配置される。

3. **サポートされていないエンティティ**
   `SPLINE`, `HATCH`, `DIMENSION`, `LEADER` 等は `[ENTITY_TYPE]` というテキストに置換される。
   視覚的な形状は失われる。

4. **INSERT の多重ネスト**
   ブロック内にさらにINSERTが含まれるケースは展開されない（1段階のみ展開）。

5. **グローバル精度設定**
   `getcontext().prec = 50` をモジュール読み込み時に設定するため、
   同プロセスで他の `Decimal` 演算を行うコードに影響を与える可能性がある。

### extract_labels.py

1. **重複除去は `(dxftype, layer, insert)` キーで判定**
   同一位置に異なるレイヤーの同一テキストがある場合は重複とみなされず、両方が抽出される。

2. **`filter_non_parts` と `validate_ref_designators` は未実装**
   パラメータとして受け付けるが処理はしない（他プロジェクトとのAPI互換性維持のため残存）。

3. **図番パターンは `ExtractionConfig.DRAWING_NUMBER_PATTERN` に依存**
   図番フォーマットが変わった場合は `config.py` または内部定義のパターンを更新すること。

4. **タイトル抽出はTITLE/REVISIONラベルの存在に依存**
   これらのラベルが図面に含まれていない場合、タイトルは `None` になる。

### label_diff.py

1. **差分の対応付けは順序依存**
   同座標に複数のラベルが削除/追加された場合、`sorted()` によるアルファベット順でペアを作る。
   真の「変更前→変更後」対応とは異なる場合がある。

2. **座標許容誤差が compare_dxf.py と独立**
   視覚的差分とラベル差分で同じ `tolerance` 値を使用しているが、
   内部処理（`Decimal` vs `round()`）が異なるため、わずかな挙動差が生じる可能性がある。

3. **`ensure_unique_sheet_name` はシート名のみ管理**
   ファイルペアが5つある場合でも全て異なるシート名を保証するが、
   元のファイル名が非常に長い場合に切り捨てが発生し、意図しない重複が起きることがある。
