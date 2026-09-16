import os
import tempfile
import traceback
import re

def is_invisible(e):
    """DXFの`invisible`属性（グループコード60、1=非表示）が立っている
    エンティティ、または**エンティティが所属するレイヤーがオフ/フリーズ
    されている**エンティティかを返す。CADソフト上で「非表示」に設定された
    図形（紙面には一切表示されない）は、たとえDXFファイル中に座標・テキスト
    として存在していても収集対象にしてはならない（DXF-extract-labels
    2026-09-11の横展開に伴い、utils/extract_labels.pyのbyte一致維持のため
    ここへ追加。詳細はDXF-extract-labels/tests/regression/test_ref_designator.py
    のinvisible関連テストを参照）。

    呼び出し側は次の3箇所すべてでチェックする必要がある（`virtual_entities()`
    は親INSERTのinvisible属性を継承しないため、INSERT自身のチェックを
    省くと、INSERT自身がinvisibleでも展開後の中身は素通りしてしまう）:
      1. 直接配置エンティティ
      2. INSERT自身（invisibleなINSERTは中身ごと丸ごと除外する）
      3. `virtual_entities()`で展開した仮想エンティティ（親が可視でも
         個々の子エンティティにinvisibleが立っている場合があるため）

    レイヤー単位の非表示状態（DXF-extract-labels 2026-09-16の横展開に伴い追加）:
    エンティティ自身の`invisible`属性が立っていなくても、そのエンティティが
    置かれたレイヤー自体が「オフ」または「フリーズ」されていれば、画面上
    ・印刷時ともに一切表示されない。ULVAC標準の改版運用では、旧版の
    タイトルブロックをエンティティ単位のinvisible属性ではなく、専用レイヤー
    ごとオフ/フリーズして非表示にする例があり、この場合は上記の`invisible`
    属性チェックだけでは検出できない。`virtual_entities()`で展開した仮想
    エンティティも`.doc`経由で元のレイヤーテーブルを参照できるため、同じ
    チェックで対応できる（`.layer`属性は展開後も元のレイヤー名を保持し、
    親INSERTのレイヤー状態を継承しない`invisible`属性とは異なる）。
    レイヤーテーブルに存在しない・`.doc`が取得できない等の異常系は
    「非表示ではない」側にフォールバックする（誤って全除外にならないよう
    保守的に扱う）。詳細はDXF-extract-labels/tests/regression/
    test_ref_designator.pyのレイヤーoff/frozen関連テストを参照。
    """
    if bool(e.dxf.get('invisible', 0)):
        return True

    layer_name = e.dxf.get('layer', None)
    doc = getattr(e, 'doc', None)
    if layer_name and doc is not None:
        try:
            if layer_name in doc.layers:
                layer = doc.layers.get(layer_name)
                if layer.is_off() or layer.is_frozen():
                    return True
        except Exception:
            pass

    return False


def save_uploadedfile(uploadedfile):
    """アップロードされたファイルを一時ディレクトリに保存する"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploadedfile.name)[1]) as f:
        f.write(uploadedfile.getbuffer())
        return f.name

def handle_error(e, show_traceback=True):
    """エラーを適切に処理して表示する"""
    import streamlit as st
    st.error(f"エラーが発生しました: {str(e)}")
    if show_traceback:
        st.error(traceback.format_exc())


def filter_non_circuit_symbols(labels, debug=False):
    """機器符号フォーマットに一致しないラベルをフィルタリングする"""
    patterns = [
        r'^[A-Za-z]{2,}$',               # 英文字のみ（2文字以上）
        r'^[A-Za-z]+\d+$',               # 英文字+数字
        r'^[A-Za-z]+\d+[A-Za-z]+$',      # 英文字+数字+英文字
        r'^[A-Za-z]{2,}\([^)]*\)$',      # 英文字のみ+括弧
        r'^[A-Za-z]+\d+\([^)]*\)$',      # 英文字+数字+括弧
        r'^[A-Za-z]+\d+[A-Za-z]+\([^)]*\)$',  # 英文字+数字+英文字+括弧
    ]

    filtered_labels = []
    excluded_count = 0

    for label in labels:
        is_match = any(re.match(p, label) for p in patterns)
        if is_match:
            filtered_labels.append(label)
        else:
            excluded_count += 1

    return filtered_labels, excluded_count


def validate_circuit_symbols(labels):
    """機器符号の妥当性をチェックし、適合しないものを返す"""
    standard_patterns = [
        r'^CB\d+$', r'^ELB\(CB\)\d+$', r'^MCCB\d+$', r'^NFB\d+$',
        r'^R\d*$', r'^C\d*$', r'^L\d*$', r'^Q\d*$',
        r'^U\d*[A-Z]*$',
        r'^PSW?\d*$', r'^DC\d*$', r'^AC\d*$',
        r'^M\d*[A-Z]*$', r'^MOT\d*$',
        r'^K\d*[A-Z]*$', r'^MC\d*$',
        r'^S\d*[A-Z]*$', r'^SW\d*$', r'^PB\d*$',
        r'^H\d*[A-Z]*$', r'^HL\d*$', r'^PL\d*$',
        r'^X\d*[A-Z]*$', r'^CN\d*$', r'^TB\d*$',
        r'^F\d*$', r'^T\d*$', r'^A\d*$',
    ]

    invalid_symbols = []
    for label in labels:
        if not any(re.match(p, label) for p in standard_patterns):
            invalid_symbols.append(label)

    return invalid_symbols


def process_circuit_symbol_labels(labels, filter_non_parts=False, validate_ref_designators=False, debug=False):
    """ラベルに対して機器符号処理を統合的に実行する"""
    result = {
        'labels': labels.copy(),
        'filtered_count': 0,
        'invalid_ref_designators': []
    }

    if filter_non_parts:
        filtered_labels, filtered_count = filter_non_circuit_symbols(labels, debug)
        result['labels'] = filtered_labels
        result['filtered_count'] = filtered_count

    if validate_ref_designators and filter_non_parts:
        result['invalid_ref_designators'] = validate_circuit_symbols(result['labels'])

    return result
