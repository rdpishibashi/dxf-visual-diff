"""
このテストが守るもの: compare_dxf_files_and_generate_dxf() の複数オフセット
自動検出（`offset_detection` パラメータ、`utils/offset_detector.py`）が、
2026-09-17 のユーザー要求どおりに動作すること:

    「analyze_offset.py でオフセット補正によって一致する図形となるケースを
    複数リストアップしている。この処理を採り入れて、自動的に複数のオフセット
    補正値を算出し、その補正をした図形を UNCHANGED_OFFSET として表示する機能を、
    現在の「オフセット補正設定」オプションをなくして、追加したい。」

対応する受入条件（ユーザー承認済み・引き継ぎ書 HANDOVER_auto_offset_detection.md
第3章「確定した設計判断」より）:
    1. 候補オフセットの算出元はジオメトリ由来のみ（形状キー＝アンカーを原点へ
       移した署名、アンカー差分をクラスタリング）
    2. UNCHANGED_OFFSET は1レイヤーに集約。検出値と件数は entity_counts の
       detected_offsets / rejected_offset_candidates で確認できる
    3. 採用しきい値: 一致件数としきい値・形状種類数としきい値の両方を満たすこと
       （形状多様性ガードは、等間隔に並ぶ同一形状が1ピッチずれて偶然一致する
       偽陽性を排除するため。実データで `(0.0,15.0)` 27件/形状1種類等が
       37個見つかったことに基づく）
    5. offset_b（明示指定）は残る。offset_detection と併用可能（和集合）

以前どう壊れていたか（この機能追加前）:
    ファイルの一部だけが平行移動していても、その移動量を手動で入力しない限り
    UNCHANGED_OFFSET として救済されなかった（DELETED+ADDED のまま）。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/spec/test_auto_offset_detection.py -v
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf

from utils.compare_dxf import compare_dxf_files_and_generate_dxf
from utils.offset_detector import OffsetDetectionConfig

TOLERANCE = 0.05


def _circle_centers(doc, layer_name):
    """指定レイヤーのCIRCLEエンティティの (center_x, center_y, radius) 集合を返す"""
    out = set()
    for e in doc.modelspace().query('CIRCLE'):
        if e.dxf.layer == layer_name:
            out.add((round(e.dxf.center.x, 3), round(e.dxf.center.y, 3), round(e.dxf.radius, 3)))
    return out


def _line_starts(doc, layer_name):
    """指定レイヤーのLINEエンティティの start 座標(x, y) 集合を返す"""
    out = set()
    for e in doc.modelspace().query('LINE'):
        if e.dxf.layer == layer_name:
            out.add((round(e.dxf.start.x, 3), round(e.dxf.start.y, 3)))
    return out


def _run_compare(path_a, path_b, tmpdir, offset_detection, suffix=""):
    output_path = os.path.join(tmpdir, f'diff{suffix}.dxf')
    success, entity_counts = compare_dxf_files_and_generate_dxf(
        path_a, path_b, output_path,
        tolerance=TOLERANCE,
        offset_detection=offset_detection,
    )
    assert success, "DXF比較処理が失敗した"
    doc = ezdxf.readfile(output_path)
    return doc, entity_counts


def _default_config(**overrides):
    cfg = dict(min_matches=10, min_distinct_shapes=5, max_offsets=20,
               max_candidates=50, max_instances_per_shape=8)
    cfg.update(overrides)
    return OffsetDetectionConfig(**cfg)


def _save_pair(build_a, build_b, tmpdir, name='pair'):
    doc_a = ezdxf.new()
    build_a(doc_a.modelspace())
    doc_b = ezdxf.new()
    build_b(doc_b.modelspace())
    path_a = os.path.join(tmpdir, f'{name}_a.dxf')
    path_b = os.path.join(tmpdir, f'{name}_b.dxf')
    doc_a.saveas(path_a)
    doc_b.saveas(path_b)
    return path_a, path_b


def _distinct_circles(msp, count, base_x, base_y, dx=0.0, dy=0.0, radius_start=1.0):
    """半径の異なる（＝形状が異なる）CIRCLEをcount個、等間隔に配置する。
    (dx, dy) は全体に加算するオフセット（B側を作る際に使う）。"""
    for i in range(count):
        cx = base_x + i * 20.0 + dx
        cy = base_y + dy
        radius = radius_start + i  # 半径を変えて形状キーを全て異ならせる
        msp.add_circle(center=(cx, cy), radius=radius, dxfattribs={'layer': '0'})


def test_detects_single_block_move():
    """12個の異なる形状（半径違いのCIRCLE）が同一デルタで移動している場合、
    1個のオフセットが検出され、全12件がUNCHANGED_OFFSETに入る"""
    with tempfile.TemporaryDirectory() as d:
        delta = (50.0, 30.0)

        def build_a(msp):
            _distinct_circles(msp, 12, base_x=0, base_y=0)

        def build_b(msp):
            # B + delta = A となるように B 側は A から delta を引いた位置に置く
            _distinct_circles(msp, 12, base_x=0, base_y=0, dx=-delta[0], dy=-delta[1])

        path_a, path_b = _save_pair(build_a, build_b, d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        assert counts['unchanged_offset_entities'] == 12
        assert counts['deleted_entities'] == 0
        assert counts['added_entities'] == 0
        assert len(counts['detected_offsets']) == 1
        detected = counts['detected_offsets'][0]
        assert detected['offset'] == delta
        assert detected['matches'] == 12
        assert detected['shapes'] == 12
        assert 'UNCHANGED_OFFSET' in doc.layers


def test_below_min_matches_is_not_adopted():
    """5個だけ移動（既定しきい値10未満）→ 検出されずDELETED+ADDEDのまま。
    rejected_offset_candidates が1以上になる"""
    with tempfile.TemporaryDirectory() as d:
        delta = (50.0, 30.0)

        def build_a(msp):
            _distinct_circles(msp, 5, base_x=0, base_y=0)

        def build_b(msp):
            _distinct_circles(msp, 5, base_x=0, base_y=0, dx=-delta[0], dy=-delta[1])

        path_a, path_b = _save_pair(build_a, build_b, d)
        cfg = _default_config()  # min_matches=10 のまま
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        assert counts['unchanged_offset_entities'] == 0
        assert counts['deleted_entities'] == 5
        assert counts['added_entities'] == 5
        assert counts['detected_offsets'] == []
        assert counts['rejected_offset_candidates'] >= 1


def test_regular_spacing_artifact_rejected():
    """同一形状のLINEを等間隔（ピッチ5.0）で10本並べ、B側を半ピッチ(2.5)ずらす
    → 一致件数はしきい値を超えるが、形状種類数が1なので不採用になる
    （このテストが形状多様性ガードの存在理由そのもの。消さないこと）"""
    with tempfile.TemporaryDirectory() as d:
        pitch = 5.0
        shift = 2.5  # 半ピッチ。A・Bどちらの座標もピッチの倍数からずらし、偶然の完全一致を避ける
        count = 10

        def build_a(msp):
            for i in range(count):
                x = i * pitch
                msp.add_line(start=(x, 200.0, 0), end=(x + 2.0, 200.0, 0), dxfattribs={'layer': '0'})

        def build_b(msp):
            for i in range(count):
                x = i * pitch - shift  # B + (shift, 0) = A の対応する位置になる
                msp.add_line(start=(x, 200.0, 0), end=(x + 2.0, 200.0, 0), dxfattribs={'layer': '0'})

        path_a, path_b = _save_pair(build_a, build_b, d)
        # min_matches を低くして「一致件数はクリアする」状況を作り、
        # min_distinct_shapes（既定5）だけで弾かれることを確認する
        cfg = _default_config(min_matches=5, max_instances_per_shape=15)
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        assert counts['unchanged_offset_entities'] == 0, \
            "形状が1種類しかない等間隔配置が誤ってUNCHANGED_OFFSETとして採用されている"
        assert counts['detected_offsets'] == []
        assert counts['deleted_entities'] == count
        assert counts['added_entities'] == count
        assert counts['rejected_offset_candidates'] >= 1


def test_multiple_offsets_detected():
    """2つの異なるデルタで動く2グループ（各12件・形状多様）→ 2個とも検出される"""
    with tempfile.TemporaryDirectory() as d:
        delta1 = (50.0, 0.0)
        delta2 = (0.0, 80.0)

        def build_a(msp):
            _distinct_circles(msp, 12, base_x=0, base_y=0, radius_start=1.0)
            _distinct_circles(msp, 12, base_x=0, base_y=500.0, radius_start=101.0)

        def build_b(msp):
            _distinct_circles(msp, 12, base_x=0, base_y=0, dx=-delta1[0], dy=-delta1[1], radius_start=1.0)
            _distinct_circles(msp, 12, base_x=0, base_y=500.0, dx=-delta2[0], dy=-delta2[1], radius_start=101.0)

        path_a, path_b = _save_pair(build_a, build_b, d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        assert len(counts['detected_offsets']) == 2
        assert counts['unchanged_offset_entities'] == 24
        assert counts['deleted_entities'] == 0
        assert counts['added_entities'] == 0
        found_offsets = {d['offset'] for d in counts['detected_offsets']}
        assert found_offsets == {delta1, delta2}


def test_entity_matched_by_two_offsets_counted_once():
    """同一形状が複数箇所に重複して存在し、1つのB側エンティティが複数の
    候補オフセットいずれでも一致しうる場合でも、実際に採用されるのは
    どちらか一方のみで、二重計上されない"""
    with tempfile.TemporaryDirectory() as d:
        # 10種類の形状（半径1〜10のCIRCLE）について、Aは同じ形状を2箇所
        # （P1, P2）に持ち、Bは1箇所（Q）だけに持つ。
        # delta1 = P1-Q は全形状で共通、delta2 = P2-Q も全形状で共通。
        # → delta1・delta2 どちらも「候補」になりうるが、一致件数が多い方
        #   （タイブレークでオフセット値が小さい方 = delta1）が先に採用され、
        #   採用済みのB側エンティティは他方の候補から除外される。
        delta1 = (-500.0, 0.0)
        delta2 = (500.0, 0.0)

        def build_a(msp):
            for i in range(10):
                radius = 1.0 + i
                base_x = i * 100.0
                msp.add_circle(center=(base_x, 0.0), radius=radius, dxfattribs={'layer': '0'})       # P1
                msp.add_circle(center=(base_x + 1000.0, 0.0), radius=radius, dxfattribs={'layer': '0'})  # P2

        def build_b(msp):
            for i in range(10):
                radius = 1.0 + i
                base_x = i * 100.0
                # Q = P1 - delta1 = P2 - delta2 となる位置に1個だけ置く
                q_x = base_x - delta1[0]
                msp.add_circle(center=(q_x, 0.0), radius=radius, dxfattribs={'layer': '0'})

        path_a, path_b = _save_pair(build_a, build_b, d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        # Bの10個すべてが、どちらか一方のオフセットで一度だけ一致する
        assert counts['unchanged_offset_entities'] == 10
        assert counts['added_entities'] == 0
        # Aは20個（P1×10 + P2×10）のうち、採用されなかった側の10個がDELETEDに残る
        assert counts['deleted_entities'] == 10
        # 採用されたのは1個のオフセットのみ（他方は一致先が全て奪われ0件になる）
        assert len(counts['detected_offsets']) == 1
        assert counts['detected_offsets'][0]['matches'] == 10
        assert counts['detected_offsets'][0]['offset'] in (delta1, delta2)


def test_max_offsets_cap():
    """max_offsets=1 を指定すると、2グループのうち1個だけ採用される"""
    with tempfile.TemporaryDirectory() as d:
        delta1 = (50.0, 0.0)
        delta2 = (0.0, 80.0)

        def build_a(msp):
            _distinct_circles(msp, 12, base_x=0, base_y=0, radius_start=1.0)
            _distinct_circles(msp, 12, base_x=0, base_y=500.0, radius_start=101.0)

        def build_b(msp):
            _distinct_circles(msp, 12, base_x=0, base_y=0, dx=-delta1[0], dy=-delta1[1], radius_start=1.0)
            _distinct_circles(msp, 12, base_x=0, base_y=500.0, dx=-delta2[0], dy=-delta2[1], radius_start=101.0)

        path_a, path_b = _save_pair(build_a, build_b, d)
        cfg = _default_config(max_offsets=1)
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        assert len(counts['detected_offsets']) == 1
        assert counts['unchanged_offset_entities'] == 12
        # 採用されなかった側の12件ずつがDELETED/ADDEDに残る
        assert counts['deleted_entities'] == 12
        assert counts['added_entities'] == 12


def test_exact_match_unaffected_by_detection():
    """完全一致した要素は、自動検出が有効でもUNCHANGEDのまま
    （検出対象は「一致しなかった要素」のみ）"""
    with tempfile.TemporaryDirectory() as d:
        def build_a(msp):
            msp.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})

        def build_b(msp):
            msp.add_line(start=(0, 0, 0), end=(10, 0, 0), dxfattribs={'layer': '0'})

        path_a, path_b = _save_pair(build_a, build_b, d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        assert counts['unchanged_entities'] == 1
        assert counts['unchanged_offset_entities'] == 0
        assert counts['detected_offsets'] == []
        assert (0.0, 0.0) in _line_starts(doc, 'UNCHANGED')


def test_added_deleted_use_raw_coordinates():
    """自動検出が有効でも、A/Bのみに存在する要素はそれぞれの生座標のまま
    DELETED/ADDEDに入る（オフセットの影響を受けない）"""
    with tempfile.TemporaryDirectory() as d:
        def build_a(msp):
            msp.add_line(start=(200.0, 200.0, 0), end=(210.0, 200.0, 0), dxfattribs={'layer': '0'})

        def build_b(msp):
            msp.add_line(start=(300.0, 300.0, 0), end=(310.0, 300.0, 0), dxfattribs={'layer': '0'})

        path_a, path_b = _save_pair(build_a, build_b, d)
        cfg = _default_config()
        doc, counts = _run_compare(path_a, path_b, d, cfg)

        assert (200.0, 200.0) in _line_starts(doc, 'DELETED')
        assert (300.0, 300.0) in _line_starts(doc, 'ADDED')


def test_detection_disabled_matches_baseline():
    """offset_detection=None のとき、結果は自動検出なしの従来どおりになる
    （移動グループはDELETED+ADDEDのまま、UNCHANGED_OFFSETは作られない）"""
    with tempfile.TemporaryDirectory() as d:
        delta = (50.0, 30.0)

        def build_a(msp):
            _distinct_circles(msp, 12, base_x=0, base_y=0)

        def build_b(msp):
            _distinct_circles(msp, 12, base_x=0, base_y=0, dx=-delta[0], dy=-delta[1])

        path_a, path_b = _save_pair(build_a, build_b, d)
        doc, counts = _run_compare(path_a, path_b, d, offset_detection=None)

        assert counts['unchanged_offset_entities'] == 0
        assert counts['deleted_entities'] == 12
        assert counts['added_entities'] == 12
        assert counts['detected_offsets'] == []
        assert counts['rejected_offset_candidates'] == 0
        assert 'UNCHANGED_OFFSET' not in doc.layers


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
