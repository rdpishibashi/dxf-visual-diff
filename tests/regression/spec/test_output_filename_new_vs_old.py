"""
このテストが守るもの: 差分DXFの出力ファイル名が `{流用先(NEW)}_vs_{流用元(OLD)}.dxf`
の順序であること。

2026-09-18、DXF-diff-manager との命名統一（A/B → OLD/NEW）に伴い、
DXF-visual-diff の出力ファイル名も DXF-diff-manager 側の慣習
（`{main_drawing}_vs_{source_drawing}.dxf` = `{流用先}_vs_{流用元}.dxf`）に揃えた。
以前は `{基準ファイルA}_vs_{比較対象ファイルB}.dxf`（A=左のアップロードボックス
が先頭）だったため、2ツール間でファイル名の並び順が逆だった
——DXF-visual-diff での A は流用元、B は流用先であり、DXF-diff-manager の
出力は「流用先_vs_流用元」なので、単純にA/Bをそのままリネームすると順序を
取り違えるリスクがあった（ユーザーが依頼時に明示的に注意喚起した点）。

対応する受入条件（ユーザー承認済み）:
    1. `generate_output_filename(file_new_name, file_old_name)` は
       `{NEW}_vs_{OLD}.dxf` を返す（引数順は NEW が先）。
    2. UI 上は流用先(NEW)が左のボックス、流用元(OLD)が右のボックスに配置される
       （出力ファイル名の並びと視覚的に揃える）。
    3. `compare_dxf_files_and_generate_dxf()` への実引数は
       `(file_old, file_new, ...)` の順のまま——OLDが第1引数（DXF-diff-manager と
       同じ契約）。UI上の左右表示位置と関数の引数順は独立している。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/spec/test_output_filename_new_vs_old.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from app import generate_output_filename


def test_output_filename_is_new_vs_old():
    """出力ファイル名は {NEW}_vs_{OLD}.dxf の順序（DXF-diff-managerと同じ並び）"""
    result = generate_output_filename("EE4153-039-06B.dxf", "EE3273-039-06B.dxf")
    assert result == "EE4153-039-06B_vs_EE3273-039-06B.dxf"


def test_output_filename_strips_extension_from_both_sides():
    """拡張子は両ファイル名から除去され、末尾に .dxf が1回だけ付く"""
    result = generate_output_filename("NewDrawing.dxf", "OldDrawing.dxf")
    assert result == "NewDrawing_vs_OldDrawing.dxf"
    assert result.count(".dxf") == 1
