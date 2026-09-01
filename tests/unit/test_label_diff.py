"""
utils.label_diff（UI 非依存）のユニットテスト。

DXF-diff-manager の tests/unit/test_label_diff.py を流用したもの（2026-09、
utils/label_diff.py を DXF-diff-manager の model/label_diff.py と byte-identical に
した際に追加）。streamlit に依存しないため app.py をインポートせず、コアを直接検証する。

実行:
    cd DXF-visual-diff
    python -m tests.unit.test_label_diff
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.label_diff import (
    group_labels_by_coordinate,
    round_labels_with_coordinates,
    find_label_change_pairs,
    reclassify_moved_labels,
)


def _change_rows_for(new_labels, old_labels, tolerance=0.01):
    """(label, x, y) のリスト2つから change_rows/unchanged_entries を計算するヘルパー。"""
    rounded_new = round_labels_with_coordinates(new_labels, tolerance)
    rounded_old = round_labels_with_coordinates(old_labels, tolerance)
    grouped_new = group_labels_by_coordinate(rounded_new)
    grouped_old = group_labels_by_coordinate(rounded_old)
    return find_label_change_pairs(grouped_new, grouped_old)


# --- reclassify_moved_labels: 基本ケース ---

def test_moved_block_reclassified_as_unchanged():
    """回路ブロックがまるごと別座標に移動した場合、削除+追加ではなく変更なしになる。"""
    old_labels = [('R10', 0, 0), ('C1', 0, 0)]
    new_labels = [('R10', 100, 100), ('C1', 100, 100)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    # reclassify前は削除2件+追加2件のはず
    assert len(change_rows) == 4

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    assert remaining == []
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert len(moved) == 2
    assert {e['label'] for e in moved} == {'R10', 'C1'}
    for e in moved:
        assert e['coordinate'] == (100, 100)  # 新座標を採用


def test_unmatched_count_partially_remains_as_change():
    """削除件数と追加件数が一致しない分は変更候補として残る。"""
    old_labels = [('R10', 0, 0), ('R10', 1, 1), ('R10', 2, 2)]  # 3件削除
    new_labels = [('R10', 100, 100)]  # 1件追加
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    # 1件だけ移動とみなされ、残り2件は削除のまま
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert len(moved) == 1
    assert len(remaining) == 2
    assert all(r['New Label'] is None for r in remaining)  # 残りは全て削除


def test_star_labels_never_reclassified():
    """「☆」を含むラベルは件数が一致しても常に変更候補として残る。"""
    old_labels = [('☆注記1', 0, 0)]
    new_labels = [('☆注記1', 100, 100)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    assert len(remaining) == 2  # 削除1件・追加1件のまま
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert moved == []


def test_rename_at_same_coordinate_not_affected():
    """同一座標での名称変更（Old/New両方が存在する行）は再分類の対象外。"""
    old_labels = [('R10', 0, 0)]
    new_labels = [('R11', 0, 0)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    assert len(change_rows) == 1
    assert change_rows[0]['Old Label'] == 'R10'
    assert change_rows[0]['New Label'] == 'R11'

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    # Old/New どちらも None でないため対象外、そのまま残る
    assert remaining == change_rows
    assert unchanged == unchanged_entries


def test_unrelated_deletion_and_addition_different_labels_not_matched():
    """異なるラベル文字列同士は誤って対応付けられない。"""
    old_labels = [('R10', 0, 0)]
    new_labels = [('C1', 100, 100)]
    change_rows, unchanged_entries = _change_rows_for(new_labels, old_labels)

    remaining, unchanged = reclassify_moved_labels(change_rows, unchanged_entries)
    assert len(remaining) == 2  # R10削除・C1追加はそれぞれ独立して残る
    moved = [e for e in unchanged if e not in unchanged_entries]
    assert moved == []


def test_ignore_moved_labels_disabled_by_default_via_compute_label_differences(tmp_path):
    """compute_label_differences() は ignore_moved_labels=False がデフォルト
    （既存呼び出し元の挙動を変えない）ことを、シグネチャのデフォルト値で確認する。"""
    import inspect
    from utils.label_diff import compute_label_differences
    sig = inspect.signature(compute_label_differences)
    assert sig.parameters['ignore_moved_labels'].default is False


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failures = []
    import tempfile
    for t in tests:
        try:
            if 'tmp_path' in t.__code__.co_varnames[:t.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    from pathlib import Path
                    t(Path(d))
            else:
                t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failures.append(t.__name__); print(f"FAIL: {t.__name__}\n      {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(_run_all())
