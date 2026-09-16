"""
このテストが守るもの: EntityExpander.expand_insert_entities()（ビジュアル差分の
ジオメトリ収集経路）が、off/frozenレイヤー上のエンティティを収集対象から
除外すること。特に、ブロック内エンティティのレイヤーが'0'（INSERTのレイヤーを
継承する特殊値）の場合に、参照元INSERT自身のレイヤー可視性で正しく判定される
ことを固定する。

不具合の識別子: 2026-09-16 ユーザー報告（DXF-diff-manager で先に発覚・修正済み）
    「レイヤー単位のオフ/フリーズによる非表示が、entity単位のinvisible属性とは
    別の穴として、ラベル抽出だけでなくジオメトリ比較(compare_dxf.py)側にも
    存在する可能性がある」という指摘に基づき、DXF-diff-manager（対応済み）と
    同一パターン（_build_layer_visibility/_is_layer_visible）をDXF-visual-diff
    へ横展開した。

以前どう壊れていたか:
    expand_insert_entities()はis_invisible()（エンティティ自身のinvisible属性
    ＋2026-09-16以降はレイヤー可視性チェックを含む）のみに依存していたが、
    レイヤー名が'0'（INSERTのレイヤーを継承する特殊値）のブロック内エンティティ
    は、それ自身の'0'という文字列に対してレイヤーテーブルを引いても
    off/frozenと判定されない。参照元INSERTがoff/frozenレイヤー上にあっても、
    そのINSERT自身がinvisibleと判定されない限り、'0'レイヤーの子エンティティが
    素通りする可能性があった。DXF-diff-managerと同じ_is_layer_visible()の
    2段階チェック（INSERT自身の可視性→ブロック内エンティティ自身の可視性、
    '0'は常に表示扱いとしてINSERT側の判定に委ねる）を移植して対応した。

修正後に保証したいこと:
    - off/frozenレイヤー上のINSERTは、中身（レイヤー'0'の子エンティティを
      含む）ごと丸ごと除外される。
    - INSERT自身は可視レイヤーでも、ブロック内エンティティが明示的な
      off/frozenレイヤーを持つ場合はそのエンティティだけ除外される。
    - 通常レイヤーの直接配置エンティティ・可視INSERT経由のエンティティは
      引き続き正しく収集される。

実行:
    cd DXF-visual-diff
    python -m pytest tests/regression/bugfix/test_off_frozen_layer_geometry_excluded.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

import ezdxf

from utils.compare_dxf import CoordinateTransformer, ToleranceConfig, EntityExpander


def _build_dxf(path):
    """4つのラベル（テキスト）で経路を区別する合成DXF:

    - 'VISIBLE_DIRECT': 通常レイヤーに直接配置
    - 'VISIBLE_VIA_INSERT': 可視レイヤーのINSERT経由、ブロック内レイヤーは'0'
      （INSERTのレイヤーを継承する特殊値）
    - 'HIDDEN_VIA_HIDDEN_INSERT': off+frozenレイヤーのINSERT経由、ブロック内
      レイヤーも'0'（INSERT自身が非表示なら'0'の子も丸ごと除外されるはず）
    - 'HIDDEN_BLOCK_ENTITY_OWN_LAYER': 可視レイヤーのINSERT経由だが、ブロック内
      エンティティ自身が明示的なoff+frozenレイヤーを持つ（そのエンティティ
      だけが除外されるはず）
    """
    doc = ezdxf.new()
    msp = doc.modelspace()

    # 直接配置（通常レイヤー）
    msp.add_text('VISIBLE_DIRECT', dxfattribs={'insert': (0, 0), 'layer': '0'})

    # off+frozenレイヤーを2種類用意
    hidden_layer_name = 'HIDDEN_INSERT_LAYER'
    doc.layers.add(hidden_layer_name)
    hl = doc.layers.get(hidden_layer_name)
    hl.off()
    hl.freeze()

    hidden_block_layer_name = 'HIDDEN_BLOCK_ENTITY_LAYER'
    doc.layers.add(hidden_block_layer_name)
    hbl = doc.layers.get(hidden_block_layer_name)
    hbl.off()
    hbl.freeze()

    # ブロックA: 子エンティティのレイヤーは'0'（INSERTのレイヤーを継承）
    block_a = doc.blocks.new('BLOCK_A')
    block_a.add_text('VISIBLE_VIA_INSERT', dxfattribs={'insert': (0, 0), 'layer': '0'})

    # 可視レイヤーのINSERT（'VISIBLE_VIA_INSERT'を含む）
    msp.add_blockref('BLOCK_A', insert=(50, 50), dxfattribs={'layer': '0'})

    # off+frozenレイヤーのINSERT（同じブロックだが'HIDDEN_VIA_HIDDEN_INSERT'側の
    # 検証用。実際には同じブロックを2回挿すと両方のテキストが複製されるため、
    # 判定用に別ブロックを用意する）
    block_b = doc.blocks.new('BLOCK_B')
    block_b.add_text('HIDDEN_VIA_HIDDEN_INSERT', dxfattribs={'insert': (0, 0), 'layer': '0'})
    msp.add_blockref('BLOCK_B', insert=(100, 100), dxfattribs={'layer': hidden_layer_name})

    # 可視レイヤーのINSERTだが、ブロック内エンティティ自身が明示的なoff+frozen
    # レイヤーを持つケース
    block_c = doc.blocks.new('BLOCK_C')
    block_c.add_text('HIDDEN_BLOCK_ENTITY_OWN_LAYER',
                      dxfattribs={'insert': (0, 0), 'layer': hidden_block_layer_name})
    msp.add_blockref('BLOCK_C', insert=(150, 150), dxfattribs={'layer': '0'})

    doc.saveas(path)


def _expand(path):
    doc = ezdxf.readfile(path)
    transformer = CoordinateTransformer(ToleranceConfig())
    expander = EntityExpander(transformer)
    return expander.expand_insert_entities(doc, 'test')


def test_off_frozen_layer_entities_excluded_from_geometry_expansion():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'synthetic.dxf')
        _build_dxf(path)

        entities = _expand(path)
        texts = {e['text_content'] for e in entities if e.get('text_content')}

        assert 'VISIBLE_DIRECT' in texts
        assert 'VISIBLE_VIA_INSERT' in texts
        assert 'HIDDEN_VIA_HIDDEN_INSERT' not in texts, \
            "off+frozenレイヤーのINSERT経由（ブロック内レイヤー'0'）のエンティティが混入している"
        assert 'HIDDEN_BLOCK_ENTITY_OWN_LAYER' not in texts, \
            "ブロック内エンティティ自身のoff+frozenレイヤーが無視されている"


if __name__ == '__main__':
    import pytest
    sys.exit(pytest.main([__file__, '-v']))
