"""
このテストが守るもの: DXF-visual-diff の utils/label_diff.py が
DXF-diff-manager の model/label_diff.py（正本）と一致していること。

背景（2026-09）:
    DXF-diff-manager 側の Step4「オプション設定」config.py 移行に合わせて、
    DXF-visual-diff 側も同じ形（config.py によるオプション設定・
    DIFF_LABEL_PREFIX_PATTERNS・unchanged_labels.xlsx 廃止）に揃えた。
    その一環で utils/label_diff.py を DXF-diff-manager の model/label_diff.py と
    byte-identical にした（従来は「移動しただけのラベル除外」機能・
    Title/Subtitle抽出修正が未伝播で114行の乖離があった）。

    Tools/CLAUDE.md に記載の通り、model/utils の共有ファイルは過去に何度も
    サイレントに乖離してきた実績があるため、`sync_utils.py` を使わず
    手動コピー＋この一貫性テストで一致を守る方針にした（sync_utils.py の
    UTILS_FILES には compare_dxf.py/common_utils.py も含まれ、それらは
    今回のスコープ外で大きく乖離したままのため、実行すると意図しない
    上書きが起きる。したがって sync_utils.py 自体は使わない）。

    DXF-diff-manager が隣（`../DXF-diff-manager`）にチェックアウトされていない
    環境では比較できないため、その場合は skip する（データ欠落ではなく前提条件の
    欠如のため、pytest -rs の skip 件数がこのテストに限り出るのは正常）。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/spec/test_label_diff_identical_to_diff_manager.py
"""
import hashlib
import os

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_VISUAL_DIFF_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
_DIFF_MANAGER_LABEL_DIFF = os.path.join(
    _VISUAL_DIFF_ROOT, "..", "DXF-diff-manager", "model", "label_diff.py")
_VISUAL_DIFF_LABEL_DIFF = os.path.join(_VISUAL_DIFF_ROOT, "utils", "label_diff.py")


def _md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def test_label_diff_is_byte_identical_to_diff_manager():
    if not os.path.exists(_DIFF_MANAGER_LABEL_DIFF):
        pytest.skip(
            "DXF-diff-manager が隣にチェックアウトされていないため比較できません "
            f"（想定パス: {_DIFF_MANAGER_LABEL_DIFF}）。"
        )

    diff_manager_hash = _md5(_DIFF_MANAGER_LABEL_DIFF)
    visual_diff_hash = _md5(_VISUAL_DIFF_LABEL_DIFF)

    assert diff_manager_hash == visual_diff_hash, (
        "utils/label_diff.py が DXF-diff-manager/model/label_diff.py と乖離しています。"
        "DXF-diff-manager 側の label_diff.py を修正した場合は、"
        "cat ../DXF-diff-manager/model/label_diff.py > utils/label_diff.py "
        "で手動コピーして同期してください（sync_utils.py は使わない）。"
    )
