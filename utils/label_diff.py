"""
DXF Diff Manager で使用するラベル比較ユーティリティ。

独立した DXF-label-diff プロジェクトと同じロジックを組み込み、
diff_labels.xlsx をアプリ内で生成する。
"""

import io
import re
from collections import Counter, defaultdict
from typing import List, Dict, Tuple, Optional

import pandas as pd

from .extract_labels import extract_labels


def _load_labels_with_cache(
    file_path: str,
    label_cache: Optional[dict],
    filter_non_parts: bool = False,
    validate_ref_designators: bool = False,
    original_filename: Optional[str] = None,
):
    """キャッシュを利用してラベルを読み込む。(labels, info) を返す。

    extract_drawing_numbers_option=True を渡すことで、同一図番を持つ複数の
    タイトルブロックが1ファイル内に存在する図面（例: 複数シートを1つの
    DXFにまとめた図面）でも、タイトル/サブタイトル抽出が対象タイトルブロック
    のグループ内に限定される（extract_labels.py の main_drawing_group 機構）。
    この呼び出しで extract_drawing_numbers_option を省略すると、グループ制限が
    働かず全ブロックのラベルが候補に混在し、対象ブロックにたまたま欠落した
    ラベルがあると別ブロックの内容を誤って拾う（2026-07-29 実データで発覚）。
    """
    cache_key = (file_path, True, filter_non_parts, validate_ref_designators, original_filename)
    if label_cache is not None and cache_key in label_cache:
        return label_cache[cache_key]

    labels, info = extract_labels(
        file_path,
        filter_non_parts=filter_non_parts,
        sort_order="none",
        include_coordinates=True,
        validate_ref_designators=validate_ref_designators,
        extract_title_option=True,
        extract_drawing_numbers_option=True,
        original_filename=original_filename,
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
    tolerance: float = 0.05,
    label_cache: Optional[dict] = None,
    filter_non_parts: bool = False,
    validate_ref_designators: bool = False,
    ignore_moved_labels: bool = False,
    new_file_original_name: Optional[str] = None,
):
    """
    ラベルを抽出（ブロック展開を含む）し、変更候補・未変更候補を計算する。

    Args:
        ignore_moved_labels: True の場合、同一ラベルの削除件数・追加件数が一致する
            分を「移動しただけ」とみなし、座標が異なっていても変更候補から除外する
            （reclassify_moved_labels 参照）。
        new_file_original_name: new_file のアップロード時の元ファイル名（temp_path
            ではなく図番を含む本来のファイル名）。図番の所属タイトルブロック判定
            （優先順位1: ファイル名照合）に使う。省略時はファイル名一致に頼れず、
            座標ベースのフォールバック判定になる（extract_labels.determine_drawing_number_types
            参照）。

    Returns
    -------
    tuple(list, list, dict)
        change_rows: 変更候補（座標と旧/新ラベルを含む辞書のリスト）
        unchanged_entries: 同一座標で一致した（または移動とみなされた）ラベル情報のリスト
        extra_info: {'labels_new': [...], 'invalid_ref_designators': [...]}
    """
    labels_new, info_new = _load_labels_with_cache(
        new_file, label_cache, filter_non_parts, validate_ref_designators,
        original_filename=new_file_original_name,
    )
    labels_old, _ = _load_labels_with_cache(old_file, label_cache, filter_non_parts, False)

    rounded_new = round_labels_with_coordinates(labels_new, tolerance)
    rounded_old = round_labels_with_coordinates(labels_old, tolerance)

    grouped_new = group_labels_by_coordinate(rounded_new)
    grouped_old = group_labels_by_coordinate(rounded_old)

    change_rows, unchanged_entries = find_label_change_pairs(grouped_new, grouped_old)
    if ignore_moved_labels:
        change_rows, unchanged_entries = reclassify_moved_labels(change_rows, unchanged_entries)
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
                'X': coord[0],
                'Y': coord[1],
                'Old Label': old_only[i],
                'New Label': new_only[i]
            })

        for leftover in old_only[pairable:]:
            change_rows.append({
                'X': coord[0],
                'Y': coord[1],
                'Old Label': leftover,
                'New Label': None
            })

        for leftover in new_only[pairable:]:
            change_rows.append({
                'X': coord[0],
                'Y': coord[1],
                'Old Label': None,
                'New Label': leftover
            })

    return change_rows, unchanged_entries


# 「移動しただけ」の再分類から除外するラベル文字列（注記・特記事項用の記号）。
_MOVED_LABEL_EXCLUDE_CHARS = ('☆',)


def reclassify_moved_labels(change_rows, unchanged_entries):
    """回路ブロックの移動により座標だけが変わったラベルを「変更なし」に振り替える。

    find_label_change_pairs() は座標単位でしか突き合わせないため、ラベルの集合が
    そのまま別の座標へ丸ごと移動すると、移動元では「削除」、移動先では「追加」として
    検出される。同一ラベル文字列の削除件数・追加件数がちょうど一致する分は「移動した
    だけ」とみなし、change_rows から取り除いて unchanged_entries に振り替える。

    対象になるのは Old Label のみ（削除）・New Label のみ（追加）の行だけで、
    同一座標での名称変更（Old/New 両方が存在する行）には影響しない。

    「☆」を含むラベル（注記・特記事項として使われることが多い）は対象外とし、
    件数が一致していても常に変更候補のまま残す。

    注意（呼び出し元に伝えるべきリスク）: 座標を見ずに件数だけで判定するため、
    たまたま同じラベル名の部品が「別の場所で削除」され「別の無関係な場所に同名の
    部品が新規追加」された場合も区別できず「移動」とみなされる。また、同一ラベルが
    複数箇所で移動した場合の新旧座標の対応付けは一意ではなく、座標順のソートで
    機械的に対応付ける（実害はないが、対応関係自体に意味はない）。

    Args:
        change_rows: find_label_change_pairs() の戻り値（1つ目）
        unchanged_entries: find_label_change_pairs() の戻り値（2つ目）

    Returns:
        tuple(list, list): (再分類後の change_rows, 移動分を追加した unchanged_entries)
    """
    deleted_by_label = defaultdict(list)  # label -> [change_row, ...]（New Label が None）
    added_by_label = defaultdict(list)    # label -> [change_row, ...]（Old Label が None）
    other_rows = []

    for row in change_rows:
        old_label, new_label = row['Old Label'], row['New Label']
        if old_label is not None and new_label is None:
            deleted_by_label[old_label].append(row)
        elif old_label is None and new_label is not None:
            added_by_label[new_label].append(row)
        else:
            other_rows.append(row)

    remaining_rows = list(other_rows)
    moved_entries = []

    for label in sorted(set(deleted_by_label) | set(added_by_label)):
        if any(ch in label for ch in _MOVED_LABEL_EXCLUDE_CHARS):
            remaining_rows.extend(deleted_by_label.get(label, []))
            remaining_rows.extend(added_by_label.get(label, []))
            continue

        deleted_rows = sorted(deleted_by_label.get(label, []),
                               key=lambda r: (r['X'], r['Y']))
        added_rows = sorted(added_by_label.get(label, []),
                             key=lambda r: (r['X'], r['Y']))
        matched = min(len(deleted_rows), len(added_rows))

        for i in range(matched):
            new_row = added_rows[i]
            moved_entries.append({
                'label': label,
                'count': 1,
                'coordinate': (new_row['X'], new_row['Y']),
            })

        remaining_rows.extend(deleted_rows[matched:])
        remaining_rows.extend(added_rows[matched:])

    return remaining_rows, unchanged_entries + moved_entries


def filter_change_rows_by_patterns(change_rows: List[Dict], patterns: List[str]) -> List[Dict]:
    """差分抽出するラベルの先頭文字列（正規表現）で change_rows を絞り込む。

    Old Label・New Label のいずれかがパターンのいずれかに先頭一致すれば、
    その行を残す（名称変更で片方だけ一致する場合も残る。フィルタ非該当←→該当へ
    改名されたラベルが「追加のみ」「削除のみ」として誤検出されるのを避けるため、
    ラベル比較そのもの〈compute_label_differences〉は全ラベルで行い、算出後の
    change_rows だけをここで絞り込む設計）。

    patterns が空リストの場合はフィルタをかけず、change_rows をそのまま返す
    （config.LabelFilterConfig.DIFF_LABEL_PREFIX_PATTERNS の既定値）。

    Args:
        change_rows: find_label_change_pairs()/reclassify_moved_labels() の戻り値
        patterns: 正規表現文字列のリスト（re.match で先頭一致判定）

    Returns:
        list: 絞り込み後の change_rows

    Raises:
        re.error: patterns に不正な正規表現が含まれる場合
    """
    if not patterns:
        return change_rows

    compiled = [re.compile(p) for p in patterns]

    def _matches(label):
        if label is None:
            return False
        return any(c.match(label) for c in compiled)

    return [row for row in change_rows if _matches(row['Old Label']) or _matches(row['New Label'])]


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
            empty_df = pd.DataFrame(columns=['X', 'Y', 'Old Label', 'New Label'])
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
                df = pd.DataFrame(rows, columns=['X', 'Y', 'Old Label', 'New Label'])
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
            if column in ('X', 'Y'):
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
