"""
DXF Diff Manager で使用するラベル比較ユーティリティ。

独立した DXF-label-diff プロジェクトと同じロジックを組み込み、
diff_labels.xlsx / unchanged_labels.xlsx をアプリ内で生成する。
"""

import io
from collections import Counter
from typing import List, Dict, Tuple

import pandas as pd

from .extract_labels import extract_labels


def round_coordinate(value: float, tolerance: float) -> float:
    """ラベル比較で使用する座標を許容誤差単位で丸める。"""
    if not tolerance:
        return value
    return round(value / tolerance) * tolerance


def round_labels_with_coordinates(labels: List[Tuple[str, float, float]], tolerance: float):
    """(ラベル, X, Y) のタプルすべてに座標丸めを適用する。"""
    rounded = []
    for label, x, y in labels:
        rounded.append((label, round_coordinate(x, tolerance), round_coordinate(y, tolerance)))
    return rounded


def group_labels_by_coordinate(rounded_labels: List[Tuple[str, float, float]]):
    """座標ごとにラベルを Counter で集計し、辞書として返す。"""
    groups = {}
    for label, x, y in rounded_labels:
        coord = (x, y)
        if coord not in groups:
            groups[coord] = Counter()
        groups[coord][label] += 1
    return groups


def compute_label_differences(new_file: str, old_file: str, tolerance: float = 0.01):
    """
    ラベルを抽出（ブロック展開を含む）し、変更候補・未変更候補を計算する。

    Returns
    -------
    tuple(list, list)
        change_rows: 変更候補（座標と旧/新ラベルを含む辞書のリスト）
        unchanged_entries: 同一座標で一致したラベル情報のリスト
    """
    labels_new, _ = extract_labels(
        new_file,
        filter_non_parts=False,
        sort_order="none",
        include_coordinates=True
    )
    labels_old, _ = extract_labels(
        old_file,
        filter_non_parts=False,
        sort_order="none",
        include_coordinates=True
    )

    rounded_new = round_labels_with_coordinates(labels_new, tolerance)
    rounded_old = round_labels_with_coordinates(labels_old, tolerance)

    grouped_new = group_labels_by_coordinate(rounded_new)
    grouped_old = group_labels_by_coordinate(rounded_old)

    change_rows, unchanged_entries = find_label_change_pairs(grouped_new, grouped_old)
    change_rows.sort(key=lambda r: ((r['Old Label'] or ''), (r['New Label'] or '')))
    return change_rows, unchanged_entries


def find_label_change_pairs(group_new, group_old):
    """各座標ごとに旧/新のラベルを突き合わせ、追加・削除・名称変更を求める。"""
    change_rows = []
    unchanged_entries = []

    all_coords = sorted(set(group_new.keys()) | set(group_old.keys()))

    for coord in all_coords:
        counter_new = group_new.get(coord, Counter()).copy()
        counter_old = group_old.get(coord, Counter()).copy()

        shared_labels = set(counter_new.keys()) & set(counter_old.keys())
        for label in sorted(shared_labels):
            min_count = min(counter_new[label], counter_old[label])
            if min_count > 0:
                unchanged_entries.append({
                    'label': label,
                    'count': min_count,
                    'coordinate': coord
                })
                counter_new[label] -= min_count
                counter_old[label] -= min_count
                if counter_new[label] == 0:
                    del counter_new[label]
                if counter_old[label] == 0:
                    del counter_old[label]

        old_only = sorted(counter_old.elements())
        new_only = sorted(counter_new.elements())
        pairable = min(len(old_only), len(new_only))

        for i in range(pairable):
            change_rows.append({
                'Coordinate X': coord[0],
                'Coordinate Y': coord[1],
                'Old Label': old_only[i],
                'New Label': new_only[i]
            })

        for leftover in old_only[pairable:]:
            change_rows.append({
                'Coordinate X': coord[0],
                'Coordinate Y': coord[1],
                'Old Label': leftover,
                'New Label': None
            })

        for leftover in new_only[pairable:]:
            change_rows.append({
                'Coordinate X': coord[0],
                'Coordinate Y': coord[1],
                'Old Label': None,
                'New Label': leftover
            })

    return change_rows, unchanged_entries


def filter_unchanged_by_prefix(unchanged_entries, prefixes: List[str]):
    """指定された接頭辞で未変更ラベルを絞り込み、座標ごとに件数を集計する。"""
    if not prefixes:
        return []

    aggregated = {}
    for entry in unchanged_entries:
        label = entry['label']
        if any(label.startswith(prefix) for prefix in prefixes):
            coord = entry['coordinate']
            key = (label, coord[0], coord[1])
            aggregated[key] = aggregated.get(key, 0) + entry['count']

    rows = [
        {
            'Label': label,
            'Count': count,
            'Coordinate X': x,
            'Coordinate Y': y
        }
        for (label, x, y), count in sorted(aggregated.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))
    ]
    return rows


def build_diff_labels_workbook(sheets: List[Dict]) -> bytes:
    """diff_labels.xlsx のバイナリデータを生成する。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if not sheets:
            empty_df = pd.DataFrame(columns=['Coordinate X', 'Coordinate Y', 'Old Label', 'New Label'])
            empty_df.to_excel(writer, sheet_name='NoData', index=False)
            format_sheet(writer, 'NoData', empty_df)
        else:
            used_names = set()
            for sheet in sheets:
                sheet_name = ensure_unique_sheet_name(sheet.get('sheet_name') or "Sheet", used_names)
                rows = sheet.get('rows') or []
                df = pd.DataFrame(rows, columns=['Coordinate X', 'Coordinate Y', 'Old Label', 'New Label'])
                old_col = sheet.get('old_label_name', 'Old Label')
                new_col = sheet.get('new_label_name', 'New Label')
                df.rename(columns={'Old Label': old_col, 'New Label': new_col}, inplace=True)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                format_sheet(writer, sheet_name, df)
    output.seek(0)
    return output.getvalue()


def build_unchanged_labels_workbook(sheets: List[Dict]) -> bytes:
    """unchanged_labels.xlsx のバイナリデータを生成する。"""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if not sheets:
            empty_df = pd.DataFrame(columns=['Label', 'Count', 'Coordinate X', 'Coordinate Y'])
            empty_df.to_excel(writer, sheet_name='NoData', index=False)
            format_sheet(writer, 'NoData', empty_df)
        else:
            used_names = set()
            for sheet in sheets:
                sheet_name = ensure_unique_sheet_name(sheet.get('sheet_name') or "Sheet", used_names)
                rows = sheet.get('rows') or []
                df = pd.DataFrame(rows, columns=['Label', 'Count', 'Coordinate X', 'Coordinate Y'])
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                format_sheet(writer, sheet_name, df)
    output.seek(0)
    return output.getvalue()


def ensure_unique_sheet_name(name: str, used_names: set) -> str:
    """Excel のシート名制限を考慮しつつ一意な名前を返す。"""
    base_name = name[:31] if name else "Sheet"
    candidate = base_name
    index = 1
    while candidate in used_names or not candidate:
        suffix = f"_{index}"
        candidate = (base_name[:31 - len(suffix)] + suffix) if len(base_name) + len(suffix) > 31 else base_name + suffix
        index += 1
    used_names.add(candidate)
    return candidate


def format_sheet(writer, sheet_name: str, df: pd.DataFrame):
    """列幅やヘッダー固定などの書式設定を適用する。"""
    worksheet = writer.sheets[sheet_name]
    if not df.empty:
        for col_idx, column in enumerate(df.columns):
            if column in ('Coordinate X', 'Coordinate Y'):
                width = 14
            elif column in ('Old Label', 'New Label', 'Label'):
                width = 30
            else:
                width = 12
            worksheet.set_column(col_idx, col_idx, width)
    else:
        worksheet.set_column(0, max(len(df.columns) - 1, 1), 15)
    worksheet.freeze_panes(1, 0)
