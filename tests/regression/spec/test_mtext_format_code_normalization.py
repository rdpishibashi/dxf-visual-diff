"""
このテストが守るもの: compare_dxf_files_and_generate_dxf() のジオメトリ差分が、
MTEXTの比較時にラベル差分側（extract_labels.py::extract_text_from_entity()）と
同じ基準で書式コード（\\W=文字幅係数、\\T=文字間隔等）を無視すること。

対応する受入条件（ユーザー承認済み・引き継ぎ書
HANDOVER_mtext_format_normalization.md 第3章「確定した設計判断」より）:
    1. compare_dxf.py の署名生成で、extract_labels.clean_mtext_format_codes() を
       import して使う（複製しない）。ラベル差分側と将来にわたって基準が揃う
    2. 正規化の対象は MTEXT のみ（TEXT/ATTRIB は対象外）
    3. 出力DXFに描画される文字は元の書式コードを保つ（正規化するのは署名だけ）

ユーザー原文（要約）:
    「line spacing や character spacing が違っていても文字コードが同じであれば
    平行移動（UNCHANGED_OFFSET）としていい。文字の意味はまったく同じなので」

以前どう壊れていたか:
    diff_labels.xlsx（ラベル差分）は書式コードを無視して「変更なし」と判定するのに、
    差分DXF（ジオメトリ差分）は書式コードをそのまま署名に含めていたため「別物」
    （DELETED+ADDED）と判定していた——同じツール内で判定基準が割れていた。

実データでの確認（自動テスト対象外・手元確認）:
    EE3294-039-03B vs EE4153-039-03A: 完全一致114→133 DELETED513→494 ADDED730→711
    EE3273-039-06B vs EE4153-039-06B: 完全一致201→221 ofs一致467→476
    どちらも A固有/B固有のハッシュ数（識別可能なエンティティ数）は不変
    （tolerance を上げる案と異なり、識別不能になる要素が発生しない）。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/spec/test_mtext_format_code_normalization.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf

from utils.compare_dxf import compare_dxf_files_and_generate_dxf
from utils.offset_detector import OffsetDetectionConfig

TOLERANCE = 0.05


def _save_pair(build_a, build_b, tmpdir, name='pair'):
    doc_old = ezdxf.new()
    build_a(doc_old.modelspace())
    doc_new = ezdxf.new()
    build_b(doc_new.modelspace())
    path_old = os.path.join(tmpdir, f'{name}_a.dxf')
    path_new = os.path.join(tmpdir, f'{name}_b.dxf')
    doc_old.saveas(path_old)
    doc_new.saveas(path_new)
    return path_old, path_new


def _run_compare(path_old, path_new, tmpdir, offset_detection=None, suffix=""):
    output_path = os.path.join(tmpdir, f'diff{suffix}.dxf')
    success, entity_counts = compare_dxf_files_and_generate_dxf(
        path_old, path_new, output_path,
        tolerance=TOLERANCE,
        offset_detection=offset_detection,
    )
    assert success, "DXF比較処理が失敗した"
    doc = ezdxf.readfile(output_path)
    return doc, entity_counts


def test_mtext_format_only_difference_is_unchanged():
    """同一位置・同一プレーンテキストで、書式コード（\\W/\\T）だけが異なるMTEXTは
    UNCHANGEDになる（従来はDELETED+ADDEDだった）"""
    with tempfile.TemporaryDirectory() as d:
        def build_a(msp):
            msp.add_mtext(r'\A1;\W0.814801;\T0.814801;SCALE',
                          dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        def build_b(msp):
            msp.add_mtext(r'\A1;\W0.912688;\T0.912688;SCALE',
                          dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        path_old, path_new = _save_pair(build_a, build_b, d)
        doc, counts = _run_compare(path_old, path_new, d)

        assert counts['unchanged_entities'] == 1
        assert counts['deleted_entities'] == 0
        assert counts['added_entities'] == 0


def test_mtext_different_text_still_differs():
    """プレーンテキスト自体が異なるMTEXTは、書式コードの正規化後も
    引き続きDELETED+ADDEDのまま（正規化が効きすぎていないことの確認）"""
    with tempfile.TemporaryDirectory() as d:
        def build_a(msp):
            msp.add_mtext(r'\A1;\W0.814801;\T0.814801;SCALE',
                          dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        def build_b(msp):
            msp.add_mtext(r'\A1;\W0.814801;\T0.814801;WEIGHT',
                          dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        path_old, path_new = _save_pair(build_a, build_b, d)
        doc, counts = _run_compare(path_old, path_new, d)

        assert counts['unchanged_entities'] == 0
        assert counts['deleted_entities'] == 1
        assert counts['added_entities'] == 1


def test_mtext_format_difference_with_offset_becomes_unchanged_offset():
    """書式コードのみが異なるMTEXTの一群が、同一デルタで平行移動している場合、
    オフセット自動検出によりUNCHANGED_OFFSETとして採用される
    （書式正規化とオフセット自動検出の組み合わせ）"""
    with tempfile.TemporaryDirectory() as d:
        delta = (50.0, 30.0)
        # 5種類の異なるプレーンテキスト（形状キーを分けるため）× 書式コード違い
        pairs = [
            (r'\W0.81;\T0.81;SCALE', r'\W0.91;\T0.91;SCALE'),
            (r'\W0.81;\T0.81;DATE', r'\W0.91;\T0.91;DATE'),
            (r'\W0.81;\T0.81;REVISION', r'\W0.91;\T0.91;REVISION'),
            (r'\W0.81;\T0.81;REMARKS', r'\W0.91;\T0.91;REMARKS'),
            (r'\W0.81;\T0.81;MARK', r'\W0.91;\T0.91;MARK'),
        ]

        def build_a(msp):
            for i, (fmt_a, _) in enumerate(pairs):
                msp.add_mtext(fmt_a, dxfattribs={'insert': (i * 2.0, 0, 0), 'layer': '0'})

        def build_b(msp):
            for i, (_, fmt_b) in enumerate(pairs):
                # B + delta = A となるように B 側は A から delta を引いた位置に置く
                msp.add_mtext(fmt_b, dxfattribs={
                    'insert': (i * 2.0 - delta[0], -delta[1], 0), 'layer': '0'})

        path_old, path_new = _save_pair(build_a, build_b, d)
        # コンパクト救済の既定値（一致4件以上・形状2種類以上・広がり15以下）で
        # 十分採用される規模（5個・広がり約8）にしている
        cfg = OffsetDetectionConfig(min_matches=10, min_distinct_shapes=5, max_offsets=20,
                                     max_candidates=50, max_instances_per_shape=8,
                                     compact_min_matches=4, compact_min_distinct_shapes=2,
                                     compact_max_span=15.0)
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        assert counts['unchanged_offset_entities'] == 5, \
            "書式コード正規化とオフセット自動検出を組み合わせても救済されなかった"
        assert counts['deleted_entities'] == 0
        assert counts['added_entities'] == 0
        assert len(counts['detected_offsets']) == 1
        assert counts['detected_offsets'][0]['offset'] == delta


def test_output_dxf_preserves_original_format_codes():
    """UNCHANGEDとして出力されたMTEXTは、正規化前の元の書式コードのまま
    描画される（署名生成時にのみ正規化し、描画データは書き換えない設計の回帰テスト）"""
    with tempfile.TemporaryDirectory() as d:
        original_a = r'\A1;\W0.814801;\T0.814801;SCALE'

        def build_a(msp):
            msp.add_mtext(original_a, dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        def build_b(msp):
            msp.add_mtext(r'\A1;\W0.912688;\T0.912688;SCALE',
                          dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        path_old, path_new = _save_pair(build_a, build_b, d)
        doc, counts = _run_compare(path_old, path_new, d)

        assert counts['unchanged_entities'] == 1
        unchanged_mtexts = [e for e in doc.modelspace().query('MTEXT')
                             if e.dxf.layer == 'UNCHANGED']
        assert len(unchanged_mtexts) == 1
        # UNCHANGEDはA側の実体から描画される（create_diff_dxf の仕様、
        # 2026-09-18の7レイヤー化でOLD_UNCHANGED/NEW_UNCHANGEDを統合した後も同じ）。
        # 出力された生テキストが、正規化前のAの元の書式コードと完全一致することを確認する
        assert unchanged_mtexts[0].dxf.text == original_a, \
            "出力DXFのMTEXTが正規化後のプレーンテキストになっている（描画データが" \
            "書き換わってしまっている）"


def test_plain_text_entity_not_affected():
    """TEXTエンティティ（MTEXTではない）は書式コード正規化の対象外。
    MTEXTの書式コードに似た文字列がTEXTの内容に含まれていても、そのまま
    文字列として比較される（entity_type=='MTEXT'のガードが外れていないことの
    回帰テスト）"""
    with tempfile.TemporaryDirectory() as d:
        def build_a(msp):
            msp.add_text(r'A\W1.0;B', dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        def build_b(msp):
            msp.add_text(r'A\W2.0;B', dxfattribs={'insert': (0, 0, 0), 'layer': '0'})

        path_old, path_new = _save_pair(build_a, build_b, d)
        doc, counts = _run_compare(path_old, path_new, d)

        # もしMTEXT用の正規化が誤ってTEXTにも適用されていれば、
        # \W2.0; のような疑似書式コードが除去されて両者が一致してしまう。
        # 適用されていなければ、文字列そのものが違うためDELETED+ADDEDのまま。
        assert counts['unchanged_entities'] == 0
        assert counts['deleted_entities'] == 1
        assert counts['added_entities'] == 1


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
