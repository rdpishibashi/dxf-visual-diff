"""
このテストが守るもの: 差分DXFの出力レイヤー構成が6レイヤー
（A_DELETED/B_ADDED/A_UNCHANGED/B_UNCHANGED/A_UNCHANGED_OFFSET/
B_UNCHANGED_OFFSET）であり、「A_* を全部ONにするとファイルAの全図形が
色の区別つきで再現され、B_* を全部ONにするとファイルBの全図形が再現される」
という不変条件を満たすこと。2026-09-17 のユーザー要求に対応する:

    「UNCHANGED_OFFSET は ADDED に合わせていますが、DELETED 表示の時に
    やはり見づらいのが難点です。（中略）A図面 = (old)DELETED + UNCHANGED +
    UNCHANGED_OFFSET_A / B図面 = (old)ADDED + UNCHANGED + UNCHANGED_OFFSET_B」
    「6レイヤーで進めてください」

対応する受入条件（ユーザー承認済み・引き継ぎ書 HANDOVER_six_layer_ab_split.md
第3章「確定した設計判断」より）:
    1. レイヤー構成は6枚固定（A_DELETED/B_ADDED/A_UNCHANGED/B_UNCHANGED/
       A_UNCHANGED_OFFSET/B_UNCHANGED_OFFSET）。旧4レイヤーは廃止。
    2. 色はエンティティ属性として各図形に直接書き込まれる（レイヤー属性ではない）。
    3. A_UNCHANGED は entities_a、B_UNCHANGED は entities_b の実体から描画する。
    4. ⚠️ A側オフセットは matched_a_hashes_by_offset から common_hashes を
       除外してから描画する（そうしないと A_UNCHANGED と二重描画になる。
       matched_a_hashes は common_hashes と重なりうる——B側は unmatched_b
       由来のため構造上重ならないのに対し、A側は「未一致B要素を平行移動した先」
       が common な A要素の位置と偶然一致することがあるため。実データで
       4件・23件の重複を確認済み）。
    5. オフセット一致が0件のときは A_UNCHANGED_OFFSET/B_UNCHANGED_OFFSET を
       両方とも作らない（空レイヤーを増やさない）。
    6. A側/B側の件数は揃わないことがある（複数のB図形が1つのA図形に対応する
       ことがあるため。バグではない）。

以前どう壊れていたか（6レイヤー化前）:
    オフセット一致図形がB（新）座標にしか描かれず、A側（DELETED表示）だけを
    見てもファイルAの図面として再現できなかった（見づらさの原因）。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/spec/test_six_layer_ab_split.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf

from utils.compare_dxf import compare_dxf_files_and_generate_dxf
from utils.offset_detector import OffsetDetectionConfig

TOLERANCE = 0.05
DELETED_COLOR = 6
ADDED_COLOR = 4
UNCHANGED_COLOR = 7
UNCHANGED_OFFSET_A_COLOR = 8
UNCHANGED_OFFSET_B_COLOR = 9


def _circles(doc, layer_name):
    """指定レイヤーのCIRCLEエンティティの (center_x, center_y, radius) 集合を返す"""
    out = set()
    for e in doc.modelspace().query('CIRCLE'):
        if e.dxf.layer == layer_name:
            out.add((round(e.dxf.center.x, 3), round(e.dxf.center.y, 3), round(e.dxf.radius, 3)))
    return out


def _entities_on_layer(doc, layer_name):
    """指定レイヤーの全エンティティ（LINE/CIRCLE問わず）のリストを返す"""
    return [e for e in doc.modelspace() if e.dxf.layer == layer_name]


def _default_config(**overrides):
    cfg = dict(min_matches=10, min_distinct_shapes=5, max_offsets=20,
               max_candidates=50, max_instances_per_shape=8,
               compact_min_matches=4, compact_min_distinct_shapes=2, compact_max_span=15.0)
    cfg.update(overrides)
    return OffsetDetectionConfig(**cfg)


def _run_compare(path_a, path_b, tmpdir, offset_detection=None, suffix=""):
    output_path = os.path.join(tmpdir, f'diff{suffix}.dxf')
    success, entity_counts = compare_dxf_files_and_generate_dxf(
        path_a, path_b, output_path,
        tolerance=TOLERANCE,
        deleted_color=DELETED_COLOR,
        added_color=ADDED_COLOR,
        unchanged_color=UNCHANGED_COLOR,
        unchanged_offset_a_color=UNCHANGED_OFFSET_A_COLOR,
        unchanged_offset_b_color=UNCHANGED_OFFSET_B_COLOR,
        offset_detection=offset_detection,
    )
    assert success, "DXF比較処理が失敗した"
    doc = ezdxf.readfile(output_path)
    return doc, entity_counts


def _build_overlap_pair(tmpdir):
    """A側オフセット一致が common_hashes と重なる状況を意図的に作る合成DXFペア。

    構成:
      - C: CIRCLE radius=3.0 at (0,0)。A・B両方の同一座標に存在 → common。
      - block: 形状が異なる12個のCIRCLE（radius 1,2,4..13。3は意図的に欠番）を
        y=1000 に等間隔配置。B側は delta だけ引いた位置に配置 → 正規のオフセット
        一致グループとして検出される（一致12件・形状12種類 → 採用条件①を満たす）。
      - D: B側だけに存在する CIRCLE radius=3.0 at ((0,0) - delta)。
        block と同じ delta で「動いた」ことにすると、D を delta シフトした結果は
        C とちょうど同じ座標・同じ形状になる——つまり D の一致先ハッシュが
        common な C のハッシュと衝突する。これにより matched_a_hashes に
        common_hashes の要素（Cのハッシュ）が混入する状況を作れる
        （実データで観測された4件・23件の重複と同じ構造）。

    delta = (50.0, 30.0)
    """
    delta = (50.0, 30.0)
    doc_a = ezdxf.new()
    msp_a = doc_a.modelspace()
    msp_a.add_circle(center=(0.0, 0.0), radius=3.0, dxfattribs={'layer': '0'})  # C
    block_radii = [1.0, 2.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
    for i, r in enumerate(block_radii):
        msp_a.add_circle(center=(i * 20.0, 1000.0), radius=r, dxfattribs={'layer': '0'})

    doc_b = ezdxf.new()
    msp_b = doc_b.modelspace()
    msp_b.add_circle(center=(0.0, 0.0), radius=3.0, dxfattribs={'layer': '0'})  # C（完全一致）
    for i, r in enumerate(block_radii):
        msp_b.add_circle(center=(i * 20.0 - delta[0], 1000.0 - delta[1]), radius=r,
                          dxfattribs={'layer': '0'})  # block（B側 = A - delta）
    msp_b.add_circle(center=(0.0 - delta[0], 0.0 - delta[1]), radius=3.0,
                      dxfattribs={'layer': '0'})  # D（B側のみ。delta shiftでCと衝突する）

    path_a = os.path.join(tmpdir, 'overlap_a.dxf')
    path_b = os.path.join(tmpdir, 'overlap_b.dxf')
    doc_a.saveas(path_a)
    doc_b.saveas(path_b)
    return path_a, path_b, delta


def _build_no_offset_pair(tmpdir):
    """オフセット一致が1件も発生しない、ごく単純な合成DXFペア
    （common 1件・A_ONLY 1件・B_ONLY 1件）"""
    doc_a = ezdxf.new()
    msp_a = doc_a.modelspace()
    msp_a.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})  # common
    msp_a.add_line(start=(200, 200, 0), end=(210, 200, 0), dxfattribs={'layer': '0'})  # A_ONLY

    doc_b = ezdxf.new()
    msp_b = doc_b.modelspace()
    msp_b.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})  # common
    msp_b.add_line(start=(300, 300, 0), end=(310, 300, 0), dxfattribs={'layer': '0'})  # B_ONLY

    path_a = os.path.join(tmpdir, 'simple_a.dxf')
    path_b = os.path.join(tmpdir, 'simple_b.dxf')
    doc_a.saveas(path_a)
    doc_b.saveas(path_b)
    return path_a, path_b


# ── 1. オフセット採用時に6レイヤーすべてが作られる ──────────────────────

def test_all_six_layers_created_when_offset_adopted():
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=cfg)

        expected_layers = {'A_DELETED', 'B_ADDED', 'A_UNCHANGED', 'B_UNCHANGED',
                            'A_UNCHANGED_OFFSET', 'B_UNCHANGED_OFFSET'}
        actual_layers = {layer.dxf.name for layer in doc.layers}
        assert expected_layers <= actual_layers, \
            f"6レイヤーが揃っていない: {actual_layers}"


# ── 2. オフセット未採用時は A_UNCHANGED_OFFSET/B_UNCHANGED_OFFSET が
#      両方とも作られない ──────────────────────────────────────────────

def test_no_offset_layers_when_none_adopted():
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b = _build_no_offset_pair(d)
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=None)

        assert 'A_UNCHANGED_OFFSET' not in doc.layers
        assert 'B_UNCHANGED_OFFSET' not in doc.layers
        # 常設4レイヤーは作られる
        for layer_name in ('A_DELETED', 'B_ADDED', 'A_UNCHANGED', 'B_UNCHANGED'):
            assert layer_name in doc.layers, f"{layer_name} が作成されていない"


# ── 3. A_UNCHANGED と B_UNCHANGED が同一座標に同じ図形を持つ ─────────────

def test_a_unchanged_and_b_unchanged_share_coordinates():
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=cfg)

        a_unchanged = _circles(doc, 'A_UNCHANGED')
        b_unchanged = _circles(doc, 'B_UNCHANGED')
        assert (0.0, 0.0, 3.0) in a_unchanged
        assert (0.0, 0.0, 3.0) in b_unchanged
        assert a_unchanged == b_unchanged, \
            "A_UNCHANGEDとB_UNCHANGEDの座標集合が一致しない（同一座標のはず）"
        assert counts['unchanged_entities'] == 1


# ── 4. A_UNCHANGED_OFFSET は A座標、B_UNCHANGED_OFFSET は B座標に描かれる ──

def test_offset_layers_use_respective_coordinates():
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=cfg)

        a_offset = _circles(doc, 'A_UNCHANGED_OFFSET')
        b_offset = _circles(doc, 'B_UNCHANGED_OFFSET')

        # block の1つ（radius=1.0）が、A座標(0,1000)・B座標(-50,970)にそれぞれ描かれること
        assert (0.0, 1000.0, 1.0) in a_offset, \
            "A_UNCHANGED_OFFSETがA（旧）座標で描画されていない"
        assert (0.0 - delta[0], 1000.0 - delta[1], 1.0) in b_offset, \
            "B_UNCHANGED_OFFSETがB（新）座標で描画されていない"
        # A座標がB_UNCHANGED_OFFSETに、B座標がA_UNCHANGED_OFFSETに紛れ込んでいないこと
        assert (0.0, 1000.0, 1.0) not in b_offset
        assert (0.0 - delta[0], 1000.0 - delta[1], 1.0) not in a_offset


# ── 5,6. A側/B側レイヤー群がそれぞれファイルA/Bの全図形を再現する ─────────

def test_layer_groups_reconstruct_source_files():
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=cfg)

        # |hashes_a| = C(1) + block(12) = 13, |hashes_b| = C(1) + block(12) + D(1) = 14
        total_a = (counts['deleted_entities'] + counts['unchanged_entities']
                   + counts['unchanged_offset_a_entities'])
        total_b = (counts['added_entities'] + counts['unchanged_entities']
                   + counts['unchanged_offset_entities'])
        assert total_a == 13, f"A側合計が期待値と異なる: {total_a}"
        assert total_b == 14, f"B側合計が期待値と異なる: {total_b}"
        assert counts['total_a_entities'] == total_a
        assert counts['total_entities'] == total_b

        # 実際に描画されたエンティティ数もこれに一致する
        a_layers_count = sum(len(_entities_on_layer(doc, l))
                              for l in ('A_DELETED', 'A_UNCHANGED', 'A_UNCHANGED_OFFSET'))
        b_layers_count = sum(len(_entities_on_layer(doc, l))
                              for l in ('B_ADDED', 'B_UNCHANGED', 'B_UNCHANGED_OFFSET'))
        assert a_layers_count == 13
        assert b_layers_count == 14


# ── 7. ★ A_UNCHANGED_OFFSET に common と重複する図形が入らない（最重要） ──

def test_a_unchanged_offset_excludes_common_overlap():
    """matched_a_hashes が common_hashes と重なる状況（D→C衝突）を意図的に
    作り、A_UNCHANGED_OFFSET に C（common）が紛れ込まないことを確認する。
    これが確認できないと、A_UNCHANGED と A_UNCHANGED_OFFSET に同じ図形が
    二重に描かれてしまう（HANDOVER_six_layer_ab_split.md 設計判断4）。"""
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=cfg)

        a_offset = _circles(doc, 'A_UNCHANGED_OFFSET')
        a_unchanged = _circles(doc, 'A_UNCHANGED')

        # Cの座標・形状 (0,0,radius=3.0) がA_UNCHANGED_OFFSETに入っていないこと
        assert (0.0, 0.0, 3.0) not in a_offset, \
            "common要素（C）がA_UNCHANGED_OFFSETに二重描画されている"
        # CはA_UNCHANGEDにのみ存在する
        assert (0.0, 0.0, 3.0) in a_unchanged

        # block由来の12件のみがA_UNCHANGED_OFFSETに入っている（Cを含めた13件ではない）
        assert counts['unchanged_offset_a_entities'] == 12, \
            f"A_UNCHANGED_OFFSET件数が期待値(12)と異なる: {counts['unchanged_offset_a_entities']}"
        assert len(a_offset) == 12

        # B側は構造上この問題が起きない（D自身がB_UNCHANGED_OFFSETに正しく入る）
        assert counts['unchanged_offset_entities'] == 13  # block12 + D


# ── 8. 各エンティティが自分の色を持つ（BYLAYERではなく実色） ──────────────

def test_entities_carry_explicit_color_not_bylayer():
    BYLAYER = 256
    with tempfile.TemporaryDirectory() as d:
        path_a, path_b, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=cfg)

        checks = [
            ('A_UNCHANGED', UNCHANGED_COLOR),
            ('B_UNCHANGED', UNCHANGED_COLOR),
            ('A_UNCHANGED_OFFSET', UNCHANGED_OFFSET_A_COLOR),
            ('B_UNCHANGED_OFFSET', UNCHANGED_OFFSET_B_COLOR),
        ]
        for layer_name, expected_color in checks:
            entities = _entities_on_layer(doc, layer_name)
            assert entities, f"{layer_name} にエンティティが無い"
            for e in entities:
                assert e.dxf.color == expected_color, \
                    f"{layer_name}のエンティティがBYLAYER({BYLAYER})または誤った色" \
                    f"({e.dxf.color})になっている。期待値={expected_color}"
                assert e.dxf.color != BYLAYER


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
