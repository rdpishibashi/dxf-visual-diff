"""
DXF Diff Manager で使用するラベル比較ユーティリティ。

独立した DXF-label-diff プロジェクトと同じロジックを組み込み、
diff_labels.xlsx / unchanged_labels.xlsx をアプリ内で生成する。
"""

import io
from collections import Counter
from typing import List, Dict, Tuple, Optional

import pandas as pd

from .extract_labels import extract_labels


def _load_labels_with_cache(
    file_path: str,
    label_cache: Optional[dict],
    filter_non_parts: bool = False,
    validate_ref_designators: bool = False,
):
    """キャッシュを利用してラベルを読み込む。(labels, info) を返す。"""
    cache_key = (file_path, True, filter_non_parts, validate_ref_designators)
    if label_cache is not None and cache_key in label_cache:
        return label_cache[cache_key]

    labels, info = extract_labels(
        file_path,
        filter_non_parts=filter_non_parts,
        sort_order="none",
        include_coordinates=True,
        validate_ref_designators=validate_ref_designators,
        extract_title_option=True,
    )
    result = (labels, info)
    if label_cache is not None:
        label_cache[cache_key] = result
    return result


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


def compute_label_differences(
    new_file: str,
    old_file: str,
    tolerance: float = 0.01,
    label_cache: Optional[dict] = None,
    filter_non_parts: bool = False,
    validate_ref_designators: bool = False,
):
    """
    ラベルを抽出（ブロック展開を含む）し、変更候補・未変更候補を計算する。

    Returns
    -------
    tuple(list, list, dict)
        change_rows: 変更候補（座標と旧/新ラベルを含む辞書のリスト）
        unchanged_entries: 同一座標で一致したラベル情報のリスト
        extra_info: {'labels_new': [...], 'invalid_ref_designators': [...]}
    """
    labels_new, info_new = _load_labels_with_cache(new_file, label_cache, filter_non_parts, validate_ref_designators)
    labels_old, _ = _load_labels_with_cache(old_file, label_cache, filter_non_parts, False)

    rounded_new = round_labels_with_coordinates(labels_new, tolerance)
    rounded_old = round_labels_with_coordinates(labels_old, tolerance)

    grouped_new = group_labels_by_coordinate(rounded_new)
    grouped_old = group_labels_by_coordinate(rounded_old)

    change_rows, unchanged_entries = find_label_change_pairs(grouped_new, grouped_old)
    change_rows.sort(key=lambda r: ((r['Old Label'] or ''), (r['New Label'] or '')))

    extra_info = {
        'labels_new': labels_new,
        'invalid_ref_designators': info_new.get('invalid_ref_designators', []),
        'title': info_new.get('title'),
        'subtitle': info_new.get('subtitle'),
    }
    return change_rows, unchanged_entries, extra_info


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


def build_diff_labels_workbook(
    sheets: List[Dict],
    summary_data: Optional[List[Dict]] = None,
    total_data: Optional[List[Dict]] = None,
    invalid_data: Optional[List[Dict]] = None,
) -> bytes:
    """diff_labels.xlsx のバイナリデータを生成する。

    シート順: Summary → Total（任意）→ ペアシート × N → Invalid（任意）
    """
    # ペアシート名を事前決定（Summary の図番ハイパーリンクに必要）
    tmp_used: set = set()
    if summary_data is not None:
        tmp_used.add('Summary')
    if total_data is not None:
        tmp_used.add('Total')
    pair_sheet_names = [
        ensure_unique_sheet_name(s.get('sheet_name') or 'Sheet', tmp_used)
        for s in sheets
    ]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book

        if not sheets and summary_data is None and total_data is None and invalid_data is None:
            empty_df = pd.DataFrame(columns=['Coordinate X', 'Coordinate Y', 'Old Label', 'New Label'])
            empty_df.to_excel(writer, sheet_name='NoData', index=False)
            format_sheet(writer, 'NoData', empty_df)
        else:
            # ── Summary シート ──
            if summary_data is not None:
                header_fmt = workbook.add_format({
                    'bold': True, 'bg_color': '#4472C4', 'font_color': 'white', 'border': 1
                })
                link_fmt = workbook.add_format({'font_color': 'blue', 'underline': True})
                num_fmt = workbook.add_format({'num_format': '#,##0'})

                ws = workbook.add_worksheet('Summary')
                ws.freeze_panes(1, 0)
                headers = ['図番', '流用元図番', '追加ラベル数', '削除ラベル数', '変更ラベル数', 'タイトル', 'サブタイトル']
                col_widths = [22, 22, 14, 14, 14, 30, 30]
                for col_idx, (h, w) in enumerate(zip(headers, col_widths)):
                    ws.write(0, col_idx, h, header_fmt)
                    ws.set_column(col_idx, col_idx, w)

                for row_idx, (row, sheet_name) in enumerate(zip(summary_data, pair_sheet_names), start=1):
                    main_drawing = row.get('図番', '')
                    url = f"internal:'{sheet_name}'!A1"
                    ws.write_url(row_idx, 0, url, link_fmt, main_drawing)
                    ws.write(row_idx, 1, row.get('流用元図番') or '')
                    ws.write(row_idx, 2, row.get('追加ラベル数', 0), num_fmt)
                    ws.write(row_idx, 3, row.get('削除ラベル数', 0), num_fmt)
                    ws.write(row_idx, 4, row.get('変更ラベル数', 0), num_fmt)
                    ws.write(row_idx, 5, row.get('タイトル') or '')
                    ws.write(row_idx, 6, row.get('サブタイトル') or '')

            # ── Total シート ──
            if total_data is not None:
                total_df = pd.DataFrame(total_data, columns=['ラベル', '個数'])
                total_df.to_excel(writer, sheet_name='Total', index=False)
                format_sheet(writer, 'Total', total_df)

            # ── ペアシート ──
            for sheet, sheet_name in zip(sheets, pair_sheet_names):
                rows = sheet.get('rows') or []
                df = pd.DataFrame(rows, columns=['Coordinate X', 'Coordinate Y', 'Old Label', 'New Label'])
                old_col = sheet.get('old_label_name', 'Old Label')
                new_col = sheet.get('new_label_name', 'New Label')
                df.rename(columns={'Old Label': old_col, 'New Label': new_col}, inplace=True)
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                format_sheet(writer, sheet_name, df)

            # ── Invalid シート ──
            if invalid_data is not None:
                invalid_df = pd.DataFrame(invalid_data, columns=['機器符号', '個数', 'ファイル名'])
                invalid_df.to_excel(writer, sheet_name='Invalid', index=False)
                format_sheet(writer, 'Invalid', invalid_df)

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
                width = 100
            elif column in ('ラベル', '機器符号'):
                width = 20
            elif column == 'ファイル名':
                width = 40
            else:
                width = 12
            worksheet.set_column(col_idx, col_idx, width)
    else:
        worksheet.set_column(0, max(len(df.columns) - 1, 1), 15)
    worksheet.freeze_panes(1, 0)
