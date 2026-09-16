"""
DXF Visual Diff - 設定ファイル

このファイルにはアプリケーション全体で使用される設定値が定義されています。
2026-09、Step「オプション設定」のUIをconfigファイルへ移行した際に新設した
（DXF-diff-manager の config.py と構造・コメント文言を揃えている）。
"""


class DiffConfig:
    """差分比較関連の設定"""

    # ── 差分抽出オプション（旧: 「オプション設定」。2026-09 にUIから移行）──

    # 移動しただけのラベルを差分から除外する
    #   回路ブロックをまるごと別の位置に移動すると、座標単位の比較では
    #   「削除＋追加」として検出されます。同一ラベルの削除件数と追加件数が
    #   一致する分は、座標が異なっていても diff_labels.xlsx の変更候補から
    #   除外し、変更なしとして扱います（差分DXFのエンティティ比較には
    #   影響しません）。「☆」を含むラベルは対象外（常に変更候補として残ります）。
    #
    #   注意: 座標を見ず件数だけで判定するため、たまたま同じラベル名の部品が
    #   別の場所で削除・別の無関係な場所に追加された場合も「移動」とみなされ、
    #   見た目上区別できなくなります。
    IGNORE_MOVED_LABELS = False

    # 色だけが異なる図形は変更なし扱いにする
    #   ※ この項目は utils/compare_dxf.py の ignore_color 対応が必要なため、
    #      DXF-diff-manager からの compare_dxf.py 移植が完了するまで未対応。
    #      設定しても効果がないため、あえて定義しない。
    # IGNORE_COLOR_ONLY_CHANGES = False

    # 差分検出の際の座標マージン
    #   同じ図形と判定する座標の許容誤差です。大きくするほど位置ずれを無視します。
    #   2026-09、DXF-diff-manager と揃えるため 0.01 → 0.05 に変更。
    DEFAULT_TOLERANCE = 0.05

    # レイヤー色設定（AutoCADカラーインデックス）
    #   1=赤 / 2=黄 / 3=緑 / 4=シアン / 5=青 / 6=マゼンタ / 7=白・黒 / 8=灰 / 9=明灰
    DEFAULT_DELETED_COLOR = 6      # 削除図形（基準ファイルAのみに存在）
    DEFAULT_ADDED_COLOR = 4        # 追加図形（比較対象ファイルBのみに存在）
    DEFAULT_UNCHANGED_COLOR = 7    # 変更なし図形
    DEFAULT_UNCHANGED_OFFSET_COLOR = 8  # オフセット補正で一致した図形（B側の座標で描画）


class LabelFilterConfig:
    """差分抽出するラベルの絞り込み設定"""

    # 差分抽出するラベルの先頭文字列（正規表現・複数指定可）
    #   ここに書いた正規表現のいずれかに「先頭から」一致するラベルだけを
    #   diff_labels.xlsx の差分（変更候補）として出力します。
    #   旧ラベル・新ラベルのどちらかが一致すればその行は残ります。
    #
    #   空リスト（既定）の場合はフィルタをかけず、すべてのラベルが
    #   差分抽出の対象になります。
    #
    #   差分DXF（図形の ADDED/DELETED/UNCHANGED 判定）には影響しません。
    #
    #   記述例:
    #     DIFF_LABEL_PREFIX_PATTERNS = [
    #         r"W No\.",          # 「W No.」で始まるラベル
    #         r"[A-Z]{1,3}\d+",   # 機器符号らしいラベル（R10, CB001 など）
    #     ]
    DIFF_LABEL_PREFIX_PATTERNS = []


# 設定クラスのインスタンスを作成（簡単にアクセスできるように）
diff_config = DiffConfig()
label_filter_config = LabelFilterConfig()
