"""
このテストが守るもの: EntityExpander.expand_insert_entities()（ビジュアル差分の
ジオメトリ収集経路）が、ブロック内にさらにINSERTを含む「ネストINSERT」を
再帰的に展開すること。展開されなかった場合、そのINSERTは compare_dxf.py の
OutputGenerator.create_entity_from_absolute() の未サポートエンティティ
フォールバックにより `[INSERT]` というプレースホルダーテキストとして
出力され、本来のシンボル形状が失われる。

不具合の識別子: 2026-09-16 ユーザー報告
    「EE3273-039-06B_vs_EE4153-039-06B.dxf の DELETED に、28F・287・A0
    のようないくつかの要素が [INSERT] 表示になってシンボルが表示されない」

以前どう壊れていたか:
    expand_insert_entities() はINSERTを1段階しか展開しなかった。ブロック内の
    子エンティティが自身もINSERTだった場合、それをそのまま絶対座標エンティティ
    （dxftype='INSERT'）として扱っており、create_entity_from_absolute() の
    「サポートされていないエンティティ」フォールバックに落ちて `[INSERT]`
    というテキストに置き換わっていた。
    実データ EE3273-039-06B.dxf では、JZB_0008・JZB_0009・JZB_0019〜JZB_0027
    の計11ブロックがそれぞれ内部に2個のINSERT（匿名ブロック）を持つ「二重INSERT」
    構造になっており、22個のシンボルが欠落していた（本修正で0件に解消したことを
    tests/regression/spec 側ではなくこのテストの合成DXFで固定する。実データは
    プロジェクトルートに置かれたローカルファイルでgit管理外のため、CIでは
    再現できない）。

修正後に保証したいこと:
    - ブロック内にネストしたINSERTがあっても、その中身（LINE等の実体）まで
      再帰的に展開され、`[INSERT]` プレースホルダーに落ちない。
    - 2段階だけでなく3段階以上のネストも展開される。
    - 親INSERTの変換（平行移動・回転・スケール）とネストしたINSERT自身の変換が
      正しく合成される（絶対座標が両方の変換を反映する）。
    - ブロックの循環参照（自分自身を間接的に参照する）があっても無限再帰で
      クラッシュせず、max_depth で打ち切られる。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/bugfix/test_nested_insert_recursive_expansion.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf

from utils.compare_dxf import CoordinateTransformer, ToleranceConfig, EntityExpander


def _expand(path):
    doc = ezdxf.readfile(path)
    transformer = CoordinateTransformer(ToleranceConfig())
    expander = EntityExpander(transformer)
    return expander.expand_insert_entities(doc, 'test')


def test_single_level_nested_insert_is_expanded_not_placeholder():
    """ブロック内に1段のネストINSERTがあるケースで、中身のLINEが展開され、
    '[INSERT]' というdxftype='INSERT'の未展開エンティティが残らないこと"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'nested.dxf')
        doc = ezdxf.new()

        # 内側ブロック: LINE (0,0)-(5,0)
        block_inner = doc.blocks.new('BLOCK_INNER')
        block_inner.add_line(start=(0, 0), end=(5, 0), dxfattribs={'layer': '0'})

        # 外側ブロック: BLOCK_INNER を (10,0) にINSERT
        block_outer = doc.blocks.new('BLOCK_OUTER')
        block_outer.add_blockref('BLOCK_INNER', insert=(10, 0), dxfattribs={'layer': '0'})

        # トップレベル: BLOCK_OUTER を (100,100) にINSERT
        doc.modelspace().add_blockref('BLOCK_OUTER', insert=(100, 100), dxfattribs={'layer': '0'})

        doc.saveas(path)

        entities = _expand(path)
        dxftypes = [e['dxftype'] for e in entities]

        assert 'INSERT' not in dxftypes, \
            "ネストしたINSERTが展開されず、未展開のままdxftype='INSERT'として残っている"

        lines = [e for e in entities if e['dxftype'] == 'LINE']
        assert len(lines) == 1, "ネスト先のLINEが展開されていない"

        start = lines[0]['attributes']['start']
        end = lines[0]['attributes']['end']
        # 親INSERT(100,100) + ネストINSERT(10,0) + LINE自身(0,0)-(5,0) の合成
        assert (round(start[0], 3), round(start[1], 3)) == (110.0, 100.0)
        assert (round(end[0], 3), round(end[1], 3)) == (115.0, 100.0)


def test_multi_level_nested_insert_is_expanded():
    """3段階のネストINSERT（A→B→C→LINE）でも最内層まで展開されること"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'nested3.dxf')
        doc = ezdxf.new()

        block_c = doc.blocks.new('BLOCK_C')
        block_c.add_line(start=(1, 1), end=(2, 2), dxfattribs={'layer': '0'})

        block_b = doc.blocks.new('BLOCK_B')
        block_b.add_blockref('BLOCK_C', insert=(0, 0), dxfattribs={'layer': '0'})

        block_a = doc.blocks.new('BLOCK_A')
        block_a.add_blockref('BLOCK_B', insert=(0, 0), dxfattribs={'layer': '0'})

        doc.modelspace().add_blockref('BLOCK_A', insert=(0, 0), dxfattribs={'layer': '0'})

        doc.saveas(path)

        entities = _expand(path)
        dxftypes = [e['dxftype'] for e in entities]

        assert 'INSERT' not in dxftypes
        assert sum(1 for t in dxftypes if t == 'LINE') == 1


def test_circular_block_reference_does_not_hang():
    """ブロックが（間接的に）自分自身を参照する循環構造でも、無限再帰せず
    max_depthで打ち切られて正常終了すること"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'circular.dxf')
        doc = ezdxf.new()

        block_x = doc.blocks.new('BLOCK_X')
        block_x.add_line(start=(0, 0), end=(1, 1), dxfattribs={'layer': '0'})
        # BLOCK_X 自身の中に BLOCK_X への INSERT を追加（循環参照）
        block_x.add_blockref('BLOCK_X', insert=(1, 0), dxfattribs={'layer': '0'})

        doc.modelspace().add_blockref('BLOCK_X', insert=(0, 0), dxfattribs={'layer': '0'})
        doc.saveas(path)

        # タイムアウトせず（無限ループにならず）完了することを確認するだけでよい
        entities = _expand(path)
        assert isinstance(entities, list)
        # LINE自体は複数回（再帰の深さ分）展開されるはずだが、有限で終わること
        assert len([e for e in entities if e['dxftype'] == 'LINE']) > 0


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
