"""
このテストが守るもの: compare_dxf_files_and_generate_dxf() のオフセット補正
（offset_b）が「和集合型」（補正なしで一致した要素は UNCHANGED のまま残り、
補正して初めて一致した要素だけが別レイヤーに追加される）であること。
2026-09-16 のユーザー要求「A, B が一致した図形要素も（座標は一致しないが）
UNCHANGED に加えて表示したい」に対応する。

2026-09-18、出力を7レイヤー構成（`A_DELETED`/`B_ADDED`/`UNCHANGED`/
`A_UNCHANGED_OFFSET`/`B_UNCHANGED_OFFSET`/`A_ALL`/`B_ALL`）に変更したことに
伴い、レイヤー名の期待値を更新した（旧A_UNCHANGED/B_UNCHANGEDはUNCHANGED
1枚に統合。詳細は tests/regression/spec/test_seven_layer_view_layers.py 参照）。
オフセット一致は A_UNCHANGED_OFFSET（A座標）・B_UNCHANGED_OFFSET（B座標）
の**両方**に描かれる（旧テストは「A側旧位置には描かれない」ことを検証していたが、
それは旧・単一レイヤー仕様の話であり、6レイヤー化後は仕様が変わっている）。

対応する受入条件（ユーザー承認済み・引き継ぎ書 HANDOVER_offset_union_unchanged.md
第3章「確定した設計判断」、および HANDOVER_six_layer_ab_split.md より）:
    1. オフセット一致分は UNCHANGED に合流させず、別レイヤー
       A_UNCHANGED_OFFSET/B_UNCHANGED_OFFSET（既定色8=濃灰/9=明灰）に分ける。
    2. A_UNCHANGED_OFFSET は A（旧）の座標に、B_UNCHANGED_OFFSET は B（新）の
       座標に描く。
    3. オフセット有効時の B_ADDED は B の生の座標のまま（従来の B+offset から変更）。
    4. 和集合は offset_b が有効なときの唯一の挙動（旧・置き換え型のモードは残さない）。

以前どう壊れていたか（旧・置き換え型の問題）:
    旧実装は EntityExpander の global_offset でファイルB全体を平行移動してから
    比較していたため、補正前から一致していた要素がオフセット適用後は座標が
    ずれて不一致になり、DELETED+ADDED に転落していた（本来 UNCHANGED であるべき
    ものが失われる）。

境界・例外:
    - offset_b=None と offset_b=(0,0) は同一の結果になるべき（第2パスを
      走らせる必要がない）。
    - オフセット無効時は UNCHANGED_OFFSET レイヤー自体を作らない
      （空レイヤーでファイルを汚さない）。
    - 完全一致した要素は、たとえ偶然オフセット後の座標も別の一致を示しうる
      としても、UNCHANGED 側でのみ数える（二重計上しない）。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/spec/test_offset_union_unchanged.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf

from utils.compare_dxf import (
    compare_dxf_files_and_generate_dxf,
    translate_absolute_entity,
    CoordinateTransformer,
    ToleranceConfig,
    EntityExpander,
    SignatureGenerator,
    DiffAnalyzer,
)

TOLERANCE = 0.05
OFFSET = (10.0, 20.0)  # tolerance(0.05) の倍数。格子ずれの心配なし


def _build_pair(dx, dy, tmpdir):
    """
    4種類の要素を持つ合成DXFペアを作る:

    - EXACT: A・B同一座標のLINE（オフセットの有無に関わらずUNCHANGEDであるべき）
    - OFFSET_ONLY: Aのラインに対し、Bは(dx,dy)だけ手前にずれた位置にLINEを持つ
      （compare_dxf_files_and_generate_dxf の offset_b 規約は「B + offset_b = A」
      ＝ B側にoffset_bを足すとAの位置に一致する、なのでB側は A - offset_b に置く。
      オフセット有効時のみUNCHANGED_OFFSETで救われるべき）
    - A_ONLY: Aだけに存在するLINE（常にDELETED）
    - B_ONLY: Bだけに存在するLINE（常にADDED。オフセット有効でもB生座標のまま）
    """
    doc_a = ezdxf.new()
    msp_a = doc_a.modelspace()
    msp_a.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})       # EXACT
    msp_a.add_line(start=(100, 100, 0), end=(110, 100, 0), dxfattribs={'layer': '0'})  # OFFSET_ONLY (A側)
    msp_a.add_line(start=(200, 200, 0), end=(210, 200, 0), dxfattribs={'layer': '0'})  # A_ONLY

    doc_b = ezdxf.new()
    msp_b = doc_b.modelspace()
    msp_b.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})       # EXACT
    msp_b.add_line(
        start=(100 - dx, 100 - dy, 0), end=(110 - dx, 100 - dy, 0),
        dxfattribs={'layer': '0'})                                                    # OFFSET_ONLY (B側 = A - offset)
    msp_b.add_line(start=(300, 300, 0), end=(310, 300, 0), dxfattribs={'layer': '0'})  # B_ONLY

    path_a = os.path.join(tmpdir, 'a.dxf')
    path_b = os.path.join(tmpdir, 'b.dxf')
    doc_a.saveas(path_a)
    doc_b.saveas(path_b)
    return path_a, path_b


def _line_starts(doc, layer_name):
    """指定レイヤーのLINEエンティティの start 座標(x, y) 集合を返す"""
    starts = set()
    for e in doc.modelspace().query('LINE'):
        if e.dxf.layer == layer_name:
            starts.add((round(e.dxf.start.x, 3), round(e.dxf.start.y, 3)))
    return starts


def _run_compare(path_a, path_b, tmpdir, offset_b, suffix=""):
    output_path = os.path.join(tmpdir, f'diff{suffix}.dxf')
    success, entity_counts = compare_dxf_files_and_generate_dxf(
        path_a, path_b, output_path,
        tolerance=TOLERANCE,
        offset_b=offset_b,
    )
    assert success, "DXF比較処理が失敗した"
    doc = ezdxf.readfile(output_path)
    return doc, entity_counts


def test_exact_match_stays_unchanged_with_offset():
    """完全一致の要素は、オフセット有効時でも UNCHANGED に残る
    （旧・置き換え型ではDELETED+ADDEDに転落していたケース）。
    UNCHANGED は entities_a の実体から1回だけ描画される（7レイヤー化、
    2026-09-18。旧A_UNCHANGED/B_UNCHANGEDの2枚持ちから統合）"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)
        doc, entity_counts = _run_compare(path_a, path_b, d, OFFSET)

        unchanged_starts = _line_starts(doc, 'UNCHANGED')
        assert (0.0, 0.0) in unchanged_starts, \
            "完全一致のLINEがUNCHANGEDレイヤーに存在しない"
        assert entity_counts['unchanged_entities'] >= 1


def test_offset_matched_goes_to_unchanged_offset_layer():
    """オフセット補正で初めて一致した要素は、A_UNCHANGED_OFFSET に A（旧）の
    座標で、B_UNCHANGED_OFFSET に B（新）の座標で、それぞれ描画される
    （6レイヤー化、2026-09-18。旧仕様はB座標のみの単一レイヤーだった）"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)
        doc, entity_counts = _run_compare(path_a, path_b, d, OFFSET)

        assert 'A_UNCHANGED_OFFSET' in doc.layers, \
            "A_UNCHANGED_OFFSETレイヤーが作成されていない"
        assert 'B_UNCHANGED_OFFSET' in doc.layers, \
            "B_UNCHANGED_OFFSETレイヤーが作成されていない"

        a_offset_starts = _line_starts(doc, 'A_UNCHANGED_OFFSET')
        b_offset_starts = _line_starts(doc, 'B_UNCHANGED_OFFSET')
        dx, dy = OFFSET
        assert (100.0, 100.0) in a_offset_starts, \
            "オフセット一致したLINEがA（旧）の座標でA_UNCHANGED_OFFSETに描画されていない"
        assert (100.0 - dx, 100.0 - dy) in b_offset_starts, \
            "オフセット一致したLINEがB（新）の座標でB_UNCHANGED_OFFSETに描画されていない"

        assert entity_counts['unchanged_offset_entities'] == 1
        assert entity_counts['unchanged_offset_a_entities'] == 1


def test_added_uses_raw_b_coordinates():
    """Bのみに存在する要素は、オフセット有効時でもBの生座標のままB_ADDEDに入る
    （従来の B+offset から変更）"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)
        doc, entity_counts = _run_compare(path_a, path_b, d, OFFSET)

        added_starts = _line_starts(doc, 'B_ADDED')
        assert (300.0, 300.0) in added_starts, \
            "B_ADDEDのLINEがB生座標で描画されていない（誤ってオフセット適用されている可能性）"
        dx, dy = OFFSET
        assert (300.0 + dx, 300.0 + dy) not in added_starts


def test_deleted_uses_a_coordinates():
    """Aのみに存在する要素は、オフセット有効時でもAの座標のままA_DELETEDに入る"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)
        doc, entity_counts = _run_compare(path_a, path_b, d, OFFSET)

        deleted_starts = _line_starts(doc, 'A_DELETED')
        assert (200.0, 200.0) in deleted_starts


def test_zero_offset_equals_no_offset():
    """offset_b=(0,0) と offset_b=None は同一の件数・レイヤー構成になる
    （第2パスを走らせる必要がない）"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)

        doc_none, counts_none = _run_compare(path_a, path_b, d, None, suffix="_none")
        doc_zero, counts_zero = _run_compare(path_a, path_b, d, (0.0, 0.0), suffix="_zero")

        assert counts_none == counts_zero
        assert 'A_UNCHANGED_OFFSET' not in doc_none.layers
        assert 'B_UNCHANGED_OFFSET' not in doc_none.layers
        assert 'A_UNCHANGED_OFFSET' not in doc_zero.layers
        assert 'B_UNCHANGED_OFFSET' not in doc_zero.layers


def test_no_offset_layer_when_disabled():
    """オフセット無効時（offset_b=None）にA_UNCHANGED_OFFSET/B_UNCHANGED_OFFSET
    レイヤーが（両方とも）作られない（空レイヤーでファイルを汚さない）"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)
        doc, entity_counts = _run_compare(path_a, path_b, d, None)

        assert 'A_UNCHANGED_OFFSET' not in doc.layers
        assert 'B_UNCHANGED_OFFSET' not in doc.layers
        assert entity_counts['unchanged_offset_entities'] == 0
        assert entity_counts['unchanged_offset_a_entities'] == 0
        # オフセット無効時、OFFSET_ONLYペアはDELETED+ADDEDとして残る
        assert entity_counts['deleted_entities'] >= 2  # A_ONLY + OFFSET_ONLY(A側)
        assert entity_counts['added_entities'] >= 2     # B_ONLY + OFFSET_ONLY(B側)


def test_translate_absolute_entity_matches_expander_global_offset():
    """translate_absolute_entity() による事後の平行移動＋再ハッシュが、
    EntityExpander(global_offset=...) による展開時オフセット適用と
    数値的に等価であること（この等価性が崩れると和集合の第2パスが
    静かに壊れる）"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)

        doc_b_1 = ezdxf.readfile(path_b)
        doc_b_2 = ezdxf.readfile(path_b)

        tolerance_config = ToleranceConfig(TOLERANCE)
        transformer = CoordinateTransformer(tolerance_config, debug=False)
        signature_generator = SignatureGenerator(transformer, debug=False)
        diff_analyzer = DiffAnalyzer(signature_generator, debug=False)

        # 経路1: EntityExpander に global_offset を渡して展開時に適用
        expander_with_offset = EntityExpander(transformer, debug=False, global_offset=OFFSET)
        entities_with_offset = expander_with_offset.expand_insert_entities(doc_b_1, "B")
        hashes_with_offset = set()
        for absolute_entity in entities_with_offset:
            data = diff_analyzer.create_entity_data_from_absolute(absolute_entity)
            h = diff_analyzer.generate_enhanced_hash(data)
            if h:
                hashes_with_offset.add(h)

        # 経路2: global_offset=None で展開後、translate_absolute_entity() で事後平行移動
        expander_raw = EntityExpander(transformer, debug=False, global_offset=None)
        entities_raw = expander_raw.expand_insert_entities(doc_b_2, "B")
        hashes_translated = set()
        for absolute_entity in entities_raw:
            shifted = translate_absolute_entity(absolute_entity, OFFSET)
            data = diff_analyzer.create_entity_data_from_absolute(shifted)
            h = diff_analyzer.generate_enhanced_hash(data)
            if h:
                hashes_translated.add(h)

        assert hashes_with_offset == hashes_translated, \
            "translate_absolute_entity()とEntityExpander(global_offset=...)のハッシュ集合が一致しない"
        assert len(hashes_with_offset) > 0


def test_entity_counts_sum():
    """total_entities = deleted + added + unchanged + unchanged_offset（B側合計）。
    total_a_entities = deleted + unchanged + unchanged_offset_a（A側合計、
    2026-09-18新設）も同時に検証する"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_pair(*OFFSET, tmpdir=d)

        _, counts_with_offset = _run_compare(path_a, path_b, d, OFFSET, suffix="_with")
        expected = (counts_with_offset['deleted_entities']
                    + counts_with_offset['added_entities']
                    + counts_with_offset['unchanged_entities']
                    + counts_with_offset['unchanged_offset_entities'])
        assert counts_with_offset['total_entities'] == expected
        expected_a = (counts_with_offset['deleted_entities']
                      + counts_with_offset['unchanged_entities']
                      + counts_with_offset['unchanged_offset_a_entities'])
        assert counts_with_offset['total_a_entities'] == expected_a

        _, counts_no_offset = _run_compare(path_a, path_b, d, None, suffix="_no")
        expected_no = (counts_no_offset['deleted_entities']
                       + counts_no_offset['added_entities']
                       + counts_no_offset['unchanged_entities']
                       + counts_no_offset['unchanged_offset_entities'])
        assert counts_no_offset['total_entities'] == expected_no
        expected_a_no = (counts_no_offset['deleted_entities']
                          + counts_no_offset['unchanged_entities']
                          + counts_no_offset['unchanged_offset_a_entities'])
        assert counts_no_offset['total_a_entities'] == expected_a_no


def test_exact_match_not_double_counted_in_offset_pass():
    """完全一致した要素は、オフセット有効時でも UNCHANGED 側でのみ数えられ、
    UNCHANGED_OFFSET に二重計上されない"""
    with tempfile.TemporaryDirectory() as d:
        doc_a = ezdxf.new()
        doc_a.modelspace().add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})
        doc_b = ezdxf.new()
        doc_b.modelspace().add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})

        path_a = os.path.join(d, 'a_single.dxf')
        path_b = os.path.join(d, 'b_single.dxf')
        doc_a.saveas(path_a)
        doc_b.saveas(path_b)

        doc, entity_counts = _run_compare(path_a, path_b, d, OFFSET, suffix="_single")

        assert entity_counts['unchanged_entities'] == 1
        assert entity_counts['unchanged_offset_entities'] == 0
        assert entity_counts['unchanged_offset_a_entities'] == 0
        assert entity_counts['deleted_entities'] == 0
        assert entity_counts['added_entities'] == 0

        # A_UNCHANGED_OFFSET/B_UNCHANGED_OFFSETレイヤーは（一致対象が無いため）作られない
        assert 'A_UNCHANGED_OFFSET' not in doc.layers
        assert 'B_UNCHANGED_OFFSET' not in doc.layers


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
