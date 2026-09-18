"""
このテストが守るもの: 差分DXFの出力レイヤー構成が7レイヤー
（OLD_DELETED/NEW_ADDED/UNCHANGED/OLD_UNCHANGED_OFFSET/NEW_UNCHANGED_OFFSET/
OLD_ALL/NEW_ALL）であり、「OLD_ALL を1枚ONにするとファイルOLDの全図形が色の
区別つきで再現され、NEW_ALL を1枚ONにするとファイルNEWの全図形が再現される」
という不変条件を満たすこと。旧・6レイヤー構成（test_six_layer_ab_split.py
から改名）からの変更点に対応する、2026-09-18 のユーザー要求:

    「外部CADソフトで見るとき、A/B_* をセットでON/OFFすれば図面A・Bの
    全体を表示できるが、レイヤーが3つあるので手間が3回かかる。
    A/B_UNCHANGEDは同じ内容なのでUNCHANGEDだけにする。図面A/Bの全体を
    表示するレイヤーを別途作成する（ADDED/DELETED+UNCHANGED+
    UNCHANGED_OFFSET_A/Bをそれぞれの色で表示する）。これで図面A/Bを
    見たいときは1回のレイヤー選択ですみ、詳細を見たいときは個別の
    レイヤーを選択できる」

対応する受入条件（ユーザー承認済み）:
    1. レイヤー構成は7枚固定（OLD_DELETED/NEW_ADDED/UNCHANGED/
       OLD_UNCHANGED_OFFSET/NEW_UNCHANGED_OFFSET/OLD_ALL/NEW_ALL）。
       旧6レイヤー（OLD_UNCHANGED/NEW_UNCHANGEDの2枚持ち）は廃止。
    2. OLD_UNCHANGED/NEW_UNCHANGED は内容が同一（同一座標）のため
       UNCHANGED 1枚に統合する。
    3. OLD_ALL は OLD_DELETED + UNCHANGED + OLD_UNCHANGED_OFFSET の物理複製、
       NEW_ALL は NEW_ADDED + UNCHANGED + NEW_UNCHANGED_OFFSET の物理複製。
       各図形は複製先でも元のカテゴリの色を保持する（BYLAYERにしない）。
    4. 色はエンティティ属性として各図形に直接書き込まれる（レイヤー属性
       ではない）。OLD_ALL/NEW_ALLレイヤー自体の色設定は使われない。
    5. ⚠️ OLD側オフセットは matched_old_hashes_by_offset から common_hashes を
       除外してから描画する（そうしないと UNCHANGED と二重描画になる。
       実データで4件・23件の重複を確認済み——この制約は6レイヤー時代から
       変わらず有効）。
    6. オフセット一致が0件のときは OLD_UNCHANGED_OFFSET/NEW_UNCHANGED_OFFSET を
       両方とも作らない（空レイヤーを増やさない）。OLD_ALL/NEW_ALLは
       オフセット有無に関わらず常に作られる（DELETED/ADDED/UNCHANGEDと
       同じ扱い）。
    7. OLD側/NEW側の件数は揃わないことがある（複数のNEW図形が1つのOLD図形に対応する
       ことがあるため。バグではない）。
    8. OLD_ALL/NEW_ALL以外の5レイヤー（OLD_DELETED/NEW_ADDED/UNCHANGED/
       OLD_UNCHANGED_OFFSET/NEW_UNCHANGED_OFFSET）は既定で非表示（レイヤーOFF）
       にする（2026-09-18追加のユーザー要求「OLD_ALLとNEW_ALL以外は非表示にし、
       必要なときにユーザー自身がONにする」）。OLD_ALL/NEW_ALLは表示のまま。
       エンティティ自身の色（正の色番号）は変えない——OFFはレイヤー側の色を
       負にするDXFの標準的な表現であり、ユーザーがレイヤーをONに戻せば
       元の色分け表示にそのまま戻る。

以前どう壊れていたか（7レイヤー化前）:
    外部CADソフトでファイルA・Bそれぞれの全体を見るには、A_*/B_*で始まる
    複数レイヤー（DELETED・UNCHANGED・UNCHANGED_OFFSET系）を毎回手動で
    まとめてON/OFFする必要があった（レイヤーパネルでの複数選択の手間）。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/spec/test_seven_layer_view_layers.py -v
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
BYLAYER = 256


def _circles(doc, layer_name):
    """指定レイヤーのCIRCLEエンティティの (center_x, center_y, radius) 集合を返す"""
    out = set()
    for e in doc.modelspace().query('CIRCLE'):
        if e.dxf.layer == layer_name:
            out.add((round(e.dxf.center.x, 3), round(e.dxf.center.y, 3), round(e.dxf.radius, 3)))
    return out


def _circles_with_color(doc, layer_name):
    """指定レイヤーのCIRCLEエンティティの (center_x, center_y, radius, color) 集合を返す"""
    out = set()
    for e in doc.modelspace().query('CIRCLE'):
        if e.dxf.layer == layer_name:
            out.add((round(e.dxf.center.x, 3), round(e.dxf.center.y, 3), round(e.dxf.radius, 3), e.dxf.color))
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


def _run_compare(path_old, path_new, tmpdir, offset_detection=None, suffix=""):
    output_path = os.path.join(tmpdir, f'diff{suffix}.dxf')
    success, entity_counts = compare_dxf_files_and_generate_dxf(
        path_old, path_new, output_path,
        tolerance=TOLERANCE,
        deleted_color=DELETED_COLOR,
        added_color=ADDED_COLOR,
        unchanged_color=UNCHANGED_COLOR,
        unchanged_offset_old_color=UNCHANGED_OFFSET_A_COLOR,
        unchanged_offset_new_color=UNCHANGED_OFFSET_B_COLOR,
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
        common な C のハッシュと衝突する。これにより matched_old_hashes に
        common_hashes の要素（Cのハッシュ）が混入する状況を作れる
        （実データで観測された4件・23件の重複と同じ構造）。

    delta = (50.0, 30.0)
    """
    delta = (50.0, 30.0)
    doc_old = ezdxf.new()
    msp_old = doc_old.modelspace()
    msp_old.add_circle(center=(0.0, 0.0), radius=3.0, dxfattribs={'layer': '0'})  # C
    block_radii = [1.0, 2.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
    for i, r in enumerate(block_radii):
        msp_old.add_circle(center=(i * 20.0, 1000.0), radius=r, dxfattribs={'layer': '0'})

    doc_new = ezdxf.new()
    msp_new = doc_new.modelspace()
    msp_new.add_circle(center=(0.0, 0.0), radius=3.0, dxfattribs={'layer': '0'})  # C（完全一致）
    for i, r in enumerate(block_radii):
        msp_new.add_circle(center=(i * 20.0 - delta[0], 1000.0 - delta[1]), radius=r,
                          dxfattribs={'layer': '0'})  # block（B側 = A - delta）
    msp_new.add_circle(center=(0.0 - delta[0], 0.0 - delta[1]), radius=3.0,
                      dxfattribs={'layer': '0'})  # D（B側のみ。delta shiftでCと衝突する）

    path_old = os.path.join(tmpdir, 'overlap_a.dxf')
    path_new = os.path.join(tmpdir, 'overlap_b.dxf')
    doc_old.saveas(path_old)
    doc_new.saveas(path_new)
    return path_old, path_new, delta


def _build_full_category_pair(tmpdir):
    """5カテゴリ（DELETED/ADDED/UNCHANGED/UNCHANGED_OFFSET_A/_B）が
    すべて非空になる合成DXFペア（色混在の検証専用。他のテストが使う
    _build_overlap_pair は意図的にDELETED/ADDEDを0件にしているため流用しない）。

    delta = (50.0, 30.0)
    """
    delta = (50.0, 30.0)
    doc_old = ezdxf.new()
    msp_old = doc_old.modelspace()
    msp_old.add_circle(center=(0.0, 0.0), radius=3.0, dxfattribs={'layer': '0'})  # common
    block_radii = [1.0, 2.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
    for i, r in enumerate(block_radii):
        msp_old.add_circle(center=(i * 20.0, 1000.0), radius=r, dxfattribs={'layer': '0'})
    msp_old.add_line(start=(500, 500, 0), end=(510, 500, 0), dxfattribs={'layer': '0'})  # A_ONLY

    doc_new = ezdxf.new()
    msp_new = doc_new.modelspace()
    msp_new.add_circle(center=(0.0, 0.0), radius=3.0, dxfattribs={'layer': '0'})  # common
    for i, r in enumerate(block_radii):
        msp_new.add_circle(center=(i * 20.0 - delta[0], 1000.0 - delta[1]), radius=r,
                          dxfattribs={'layer': '0'})  # block（B側 = A - delta）
    msp_new.add_line(start=(600, 600, 0), end=(610, 600, 0), dxfattribs={'layer': '0'})  # B_ONLY

    path_old = os.path.join(tmpdir, 'full_a.dxf')
    path_new = os.path.join(tmpdir, 'full_b.dxf')
    doc_old.saveas(path_old)
    doc_new.saveas(path_new)
    return path_old, path_new, delta


def _build_no_offset_pair(tmpdir):
    """オフセット一致が1件も発生しない、ごく単純な合成DXFペア
    （common 1件・A_ONLY 1件・B_ONLY 1件）"""
    doc_old = ezdxf.new()
    msp_old = doc_old.modelspace()
    msp_old.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})  # common
    msp_old.add_line(start=(200, 200, 0), end=(210, 200, 0), dxfattribs={'layer': '0'})  # A_ONLY

    doc_new = ezdxf.new()
    msp_new = doc_new.modelspace()
    msp_new.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})  # common
    msp_new.add_line(start=(300, 300, 0), end=(310, 300, 0), dxfattribs={'layer': '0'})  # B_ONLY

    path_old = os.path.join(tmpdir, 'simple_a.dxf')
    path_new = os.path.join(tmpdir, 'simple_b.dxf')
    doc_old.saveas(path_old)
    doc_new.saveas(path_new)
    return path_old, path_new


# ── 1. オフセット採用時に7レイヤーすべてが作られる ──────────────────────

def test_all_seven_layers_created_when_offset_adopted():
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        expected_layers = {'OLD_DELETED', 'NEW_ADDED', 'UNCHANGED',
                            'OLD_UNCHANGED_OFFSET', 'NEW_UNCHANGED_OFFSET',
                            'OLD_ALL', 'NEW_ALL'}
        actual_layers = {layer.dxf.name for layer in doc.layers}
        assert expected_layers <= actual_layers, \
            f"7レイヤーが揃っていない: {actual_layers}"


# ── 2. オフセット未採用時も常設5レイヤー（OLD_ALL/NEW_ALLを含む）は作られ、
#      OLD_UNCHANGED_OFFSET/NEW_UNCHANGED_OFFSET だけが作られない ────────────

def test_no_offset_layers_when_none_adopted():
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new = _build_no_offset_pair(d)
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=None)

        assert 'OLD_UNCHANGED_OFFSET' not in doc.layers
        assert 'NEW_UNCHANGED_OFFSET' not in doc.layers
        # 常設5レイヤーは作られる
        for layer_name in ('OLD_DELETED', 'NEW_ADDED', 'UNCHANGED', 'OLD_ALL', 'NEW_ALL'):
            assert layer_name in doc.layers, f"{layer_name} が作成されていない"


# ── 3. UNCHANGED は1枚に統合され、OLD_ALL・NEW_ALL の両方に複製される ────────

def test_unchanged_is_single_layer_duplicated_into_both_view_layers():
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        # 旧OLD_UNCHANGED/NEW_UNCHANGEDは存在しない
        assert 'A_UNCHANGED' not in doc.layers
        assert 'B_UNCHANGED' not in doc.layers

        unchanged = _circles(doc, 'UNCHANGED')
        assert (0.0, 0.0, 3.0) in unchanged
        assert counts['unchanged_entities'] == 1
        assert len(unchanged) == 1, "UNCHANGEDが1枚に統合されず二重に含まれている"

        # OLD_ALL・NEW_ALLの両方にUNCHANGED分の図形が複製されていること
        a_all = _circles(doc, 'OLD_ALL')
        b_all = _circles(doc, 'NEW_ALL')
        assert (0.0, 0.0, 3.0) in a_all, "UNCHANGED要素がOLD_ALLに複製されていない"
        assert (0.0, 0.0, 3.0) in b_all, "UNCHANGED要素がNEW_ALLに複製されていない"


# ── 4. OLD_UNCHANGED_OFFSET は A座標、NEW_UNCHANGED_OFFSET は B座標に描かれる ──

def test_offset_layers_use_respective_coordinates():
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        a_offset = _circles(doc, 'OLD_UNCHANGED_OFFSET')
        b_offset = _circles(doc, 'NEW_UNCHANGED_OFFSET')

        # block の1つ（radius=1.0）が、A座標(0,1000)・B座標(-50,970)にそれぞれ描かれること
        assert (0.0, 1000.0, 1.0) in a_offset, \
            "OLD_UNCHANGED_OFFSETがA（旧）座標で描画されていない"
        assert (0.0 - delta[0], 1000.0 - delta[1], 1.0) in b_offset, \
            "NEW_UNCHANGED_OFFSETがB（新）座標で描画されていない"
        # A座標がNEW_UNCHANGED_OFFSETに、B座標がOLD_UNCHANGED_OFFSETに紛れ込んでいないこと
        assert (0.0, 1000.0, 1.0) not in b_offset
        assert (0.0 - delta[0], 1000.0 - delta[1], 1.0) not in a_offset


# ── 5,6. OLD_ALL・NEW_ALL がそれぞれファイルA/Bの全図形を再現する
#         （物理複製された合成レイヤー自体で検証する） ───────────────────

def test_view_layers_reconstruct_source_files():
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        # |hashes_old| = C(1) + block(12) = 13, |hashes_new| = C(1) + block(12) + D(1) = 14
        total_a = (counts['deleted_entities'] + counts['unchanged_entities']
                   + counts['unchanged_offset_old_entities'])
        total_b = (counts['added_entities'] + counts['unchanged_entities']
                   + counts['unchanged_offset_entities'])
        assert total_a == 13, f"A側合計が期待値と異なる: {total_a}"
        assert total_b == 14, f"B側合計が期待値と異なる: {total_b}"
        assert counts['total_old_entities'] == total_a
        assert counts['total_entities'] == total_b

        # OLD_ALL・NEW_ALLレイヤー自体のエンティティ数がこれに一致する
        # （旧: OLD_DELETED+A_UNCHANGED+OLD_UNCHANGED_OFFSETの合算で代用していたが、
        # 7レイヤー化後はOLD_ALL/NEW_ALLという物理レイヤーが実在するため直接数えられる）
        a_all_count = len(_entities_on_layer(doc, 'OLD_ALL'))
        b_all_count = len(_entities_on_layer(doc, 'NEW_ALL'))
        assert a_all_count == 13, f"OLD_ALLのエンティティ数が期待値と異なる: {a_all_count}"
        assert b_all_count == 14, f"NEW_ALLのエンティティ数が期待値と異なる: {b_all_count}"

        # 詳細カテゴリ層の合計とも一致すること（二重計上・欠落がないことの確認）
        a_detail_count = sum(len(_entities_on_layer(doc, l))
                              for l in ('OLD_DELETED', 'UNCHANGED', 'OLD_UNCHANGED_OFFSET'))
        b_detail_count = sum(len(_entities_on_layer(doc, l))
                              for l in ('NEW_ADDED', 'UNCHANGED', 'NEW_UNCHANGED_OFFSET'))
        assert a_all_count == a_detail_count
        assert b_all_count == b_detail_count


# ── 7. ★ UNCHANGED_OFFSET系にcommonと重複する図形が入らない（最重要） ──

def test_a_unchanged_offset_excludes_common_overlap():
    """matched_old_hashes が common_hashes と重なる状況（D→C衝突）を意図的に
    作り、OLD_UNCHANGED_OFFSET に C（common）が紛れ込まないことを確認する。
    これが確認できないと、UNCHANGED と OLD_UNCHANGED_OFFSET に同じ図形が
    二重に描かれてしまう（この制約は6レイヤー時代から変わらず有効）。"""
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new, delta = _build_overlap_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        a_offset = _circles(doc, 'OLD_UNCHANGED_OFFSET')
        unchanged = _circles(doc, 'UNCHANGED')

        # Cの座標・形状 (0,0,radius=3.0) がOLD_UNCHANGED_OFFSETに入っていないこと
        assert (0.0, 0.0, 3.0) not in a_offset, \
            "common要素（C）がOLD_UNCHANGED_OFFSETに二重描画されている"
        # CはUNCHANGEDにのみ存在する
        assert (0.0, 0.0, 3.0) in unchanged

        # block由来の12件のみがOLD_UNCHANGED_OFFSETに入っている（Cを含めた13件ではない）
        assert counts['unchanged_offset_old_entities'] == 12, \
            f"OLD_UNCHANGED_OFFSET件数が期待値(12)と異なる: {counts['unchanged_offset_old_entities']}"
        assert len(a_offset) == 12

        # B側は構造上この問題が起きない（D自身がNEW_UNCHANGED_OFFSETに正しく入る）
        assert counts['unchanged_offset_entities'] == 13  # block12 + D


# ── 8. 各エンティティが自分の色を持つ（BYLAYERではなく実色）。
#      OLD_ALL/NEW_ALLは複数カテゴリの色が混在すること ───────────────────────

def test_entities_carry_explicit_color_not_bylayer():
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new, delta = _build_full_category_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        # 前提: このフィクスチャは5カテゴリすべてが非空であること
        assert counts['deleted_entities'] == 1
        assert counts['added_entities'] == 1
        assert counts['unchanged_entities'] == 1
        assert counts['unchanged_offset_old_entities'] == 12
        assert counts['unchanged_offset_entities'] == 12

        checks = [
            ('UNCHANGED', UNCHANGED_COLOR),
            ('OLD_UNCHANGED_OFFSET', UNCHANGED_OFFSET_A_COLOR),
            ('NEW_UNCHANGED_OFFSET', UNCHANGED_OFFSET_B_COLOR),
        ]
        for layer_name, expected_color in checks:
            entities = _entities_on_layer(doc, layer_name)
            assert entities, f"{layer_name} にエンティティが無い"
            for e in entities:
                assert e.dxf.color == expected_color, \
                    f"{layer_name}のエンティティがBYLAYER({BYLAYER})または誤った色" \
                    f"({e.dxf.color})になっている。期待値={expected_color}"
                assert e.dxf.color != BYLAYER

        # OLD_ALLはDELETED色・UNCHANGED色・UNCHANGED_OFFSET_A色が混在する
        a_all_colors = {e.dxf.color for e in _entities_on_layer(doc, 'OLD_ALL')}
        assert a_all_colors == {DELETED_COLOR, UNCHANGED_COLOR, UNCHANGED_OFFSET_A_COLOR}, \
            f"OLD_ALLの色構成が期待と異なる: {a_all_colors}"
        assert BYLAYER not in a_all_colors

        # NEW_ALLはADDED色・UNCHANGED色・UNCHANGED_OFFSET_B色が混在する
        b_all_colors = {e.dxf.color for e in _entities_on_layer(doc, 'NEW_ALL')}
        assert b_all_colors == {ADDED_COLOR, UNCHANGED_COLOR, UNCHANGED_OFFSET_B_COLOR}, \
            f"NEW_ALLの色構成が期待と異なる: {b_all_colors}"
        assert BYLAYER not in b_all_colors

        # OLD_ALL内のUNCHANGED由来の図形は、UNCHANGEDレイヤー単体と同じ色で
        # 複製されていること（座標+色のペアで一致を確認）
        unchanged_with_color = _circles_with_color(doc, 'UNCHANGED')
        a_all_with_color = _circles_with_color(doc, 'OLD_ALL')
        b_all_with_color = _circles_with_color(doc, 'NEW_ALL')
        assert unchanged_with_color <= a_all_with_color
        assert unchanged_with_color <= b_all_with_color


# ── 9. OLD_ALL/NEW_ALL以外は既定で非表示（レイヤーOFF）、OLD_ALL/NEW_ALLは表示のまま ──

def test_only_view_layers_visible_by_default():
    with tempfile.TemporaryDirectory() as d:
        path_old, path_new, delta = _build_full_category_pair(d)
        cfg = _default_config()
        doc, counts = _run_compare(path_old, path_new, d, offset_detection=cfg)

        hidden_layers = ('OLD_DELETED', 'NEW_ADDED', 'UNCHANGED',
                          'OLD_UNCHANGED_OFFSET', 'NEW_UNCHANGED_OFFSET')
        for layer_name in hidden_layers:
            layer = doc.layers.get(layer_name)
            assert layer.is_off(), f"{layer_name} が既定で非表示になっていない"

        for layer_name in ('OLD_ALL', 'NEW_ALL'):
            layer = doc.layers.get(layer_name)
            assert not layer.is_off(), f"{layer_name} が既定で非表示になってしまっている"

        # レイヤーOFFはレイヤー自体の色の符号だけを変える。エンティティ自身の
        # 色（正の値）はそのままであること（ONに戻せば元の色分けが復元される）
        for e in _entities_on_layer(doc, 'OLD_DELETED'):
            assert e.dxf.color > 0, "非表示レイヤーのエンティティ色が書き換わっている"


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
