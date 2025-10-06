#!/usr/bin/env python3
"""
DXF Offset Analysis Tool - Standalone Version
スタンドアロン版：すべての機能を1ファイルに統合

使用方法:
    python analyze_offset.py fileA.dxf fileB.dxf

オプション:
    -t, --tolerance FLOAT   クラスタリングの許容誤差 (デフォルト: 0.1)
    -n, --top-n INT         表示するトップクラスタ数 (デフォルト: 10)
    -a, --all               すべてのクラスタを表示
    -h, --help              ヘルプを表示
"""
import sys
import argparse
import ezdxf
from collections import defaultdict
import numpy as np
import re


def extract_labels_with_positions(dxf_path):
    """
    Extract all text labels with their positions from a DXF file.
    Returns: dict mapping label text to list of positions [(x, y), ...]
    """
    labels_positions = defaultdict(list)

    try:
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        # Extract TEXT entities
        for entity in msp.query('TEXT'):
            text = entity.dxf.text.strip()
            if text:
                pos = (entity.dxf.insert.x, entity.dxf.insert.y)
                labels_positions[text].append(pos)

        # Extract MTEXT entities
        for entity in msp.query('MTEXT'):
            # Clean MTEXT format codes
            text = entity.text.strip()
            # Remove common MTEXT format codes
            text = re.sub(r'\\[fFhHwWcCpPqQaAlLtT][^;]*;', '', text)
            text = re.sub(r'\\[pP]x[^;]*;', '', text)
            text = text.replace('\\P', ' ')
            text = text.replace('¥', '\\')  # Japanese yen symbol to backslash
            text = text.strip()

            if text:
                pos = (entity.dxf.insert.x, entity.dxf.insert.y)
                labels_positions[text].append(pos)

        # Extract INSERT (block references)
        for entity in msp.query('INSERT'):
            if hasattr(entity.dxf, 'name'):
                block_name = entity.dxf.name
                pos = (entity.dxf.insert.x, entity.dxf.insert.y)
                labels_positions[block_name].append(pos)

    except Exception as e:
        raise Exception(f"Error reading {dxf_path}: {e}")

    return labels_positions


def calculate_offsets(labels_a, labels_b):
    """
    Calculate position offsets for all matching labels between two files.
    Returns: list of (dx, dy, label) tuples

    Note: Offsets are calculated as (position_a - position_b) so they can be
    directly applied to file B in DXF-visual-diff to align it with file A.
    """
    offsets = []

    # Find common labels
    common_labels = set(labels_a.keys()) & set(labels_b.keys())

    # For each common label, calculate offsets
    for label in common_labels:
        positions_a = labels_a[label]
        positions_b = labels_b[label]

        # If label appears same number of times in both files
        if len(positions_a) == len(positions_b):
            # Sort positions to match them up
            positions_a_sorted = sorted(positions_a)
            positions_b_sorted = sorted(positions_b)

            for pos_a, pos_b in zip(positions_a_sorted, positions_b_sorted):
                # Calculate offset as (A - B) so it can be directly applied to B
                dx = pos_a[0] - pos_b[0]
                dy = pos_a[1] - pos_b[1]
                offsets.append((dx, dy, label))
        else:
            # If different counts, match closest positions
            for pos_a in positions_a:
                min_dist = float('inf')
                closest_pos_b = None
                for pos_b in positions_b:
                    dist = ((pos_b[0] - pos_a[0])**2 + (pos_b[1] - pos_a[1])**2)**0.5
                    if dist < min_dist:
                        min_dist = dist
                        closest_pos_b = pos_b

                if closest_pos_b:
                    # Calculate offset as (A - B) so it can be directly applied to B
                    dx = pos_a[0] - closest_pos_b[0]
                    dy = pos_a[1] - closest_pos_b[1]
                    offsets.append((dx, dy, label))

    return offsets


def cluster_analysis(offsets, tolerance=0.1):
    """
    Group offsets into clusters and analyze each cluster.
    """
    # Round offsets to tolerance precision
    clustered = defaultdict(list)
    for dx, dy, label in offsets:
        rounded_dx = round(dx / tolerance) * tolerance
        rounded_dy = round(dy / tolerance) * tolerance
        clustered[(rounded_dx, rounded_dy)].append(label)

    # Sort by cluster size
    sorted_clusters = sorted(clustered.items(), key=lambda x: len(x[1]), reverse=True)

    return sorted_clusters


def main():
    parser = argparse.ArgumentParser(
        description='DXFファイル間のラベル位置オフセットを分析します',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用例:
  python analyze_offset.py drawing_A.dxf drawing_B.dxf
  python analyze_offset.py drawing_A.dxf drawing_B.dxf --tolerance 0.5
  python analyze_offset.py drawing_A.dxf drawing_B.dxf --top-n 20
        '''
    )

    parser.add_argument('file_a', help='基準DXFファイル (File A)')
    parser.add_argument('file_b', help='比較対象DXFファイル (File B)')
    parser.add_argument('-t', '--tolerance', type=float, default=0.1,
                        help='クラスタリングの許容誤差 (デフォルト: 0.1)')
    parser.add_argument('-n', '--top-n', type=int, default=10,
                        help='表示するトップクラスタ数 (デフォルト: 10)')
    parser.add_argument('-a', '--all', action='store_true',
                        help='すべてのクラスタを表示（デフォルトではtop-nのみ）')

    args = parser.parse_args()

    print("=" * 80)
    print("DXF Label Position Offset Analysis")
    print("=" * 80)
    print(f"File A: {args.file_a}")
    print(f"File B: {args.file_b}")
    print(f"Tolerance: {args.tolerance}")
    print()

    # Extract labels with positions
    print("ラベルを抽出中...")
    try:
        labels_a = extract_labels_with_positions(args.file_a)
        labels_b = extract_labels_with_positions(args.file_b)
    except FileNotFoundError as e:
        print(f"エラー: ファイルが見つかりません - {e}")
        sys.exit(1)
    except Exception as e:
        print(f"エラー: ファイル読み込み中にエラーが発生しました - {e}")
        sys.exit(1)

    print(f"Total labels in file A: {len(labels_a)}")
    print(f"Total labels in file B: {len(labels_b)}")

    # Calculate offsets
    print("オフセットを計算中...")
    offsets = calculate_offsets(labels_a, labels_b)

    # Count common labels
    common_labels = set(labels_a.keys()) & set(labels_b.keys())
    print(f"Common labels: {len(common_labels)}")

    if not offsets:
        print("警告: 共通するラベルが見つかりませんでした。")
        sys.exit(0)

    # Cluster analysis
    print("クラスタ分析中...")
    clusters = cluster_analysis(offsets, tolerance=args.tolerance)

    print(f"\n総ユニークオフセットクラスタ数: {len(clusters)}")
    print(f"総オフセット測定数: {len(offsets)}")

    # Analyze zero-offset cluster
    zero_offset_labels = []
    for (dx, dy), labels in clusters:
        if abs(dx) < args.tolerance/2 and abs(dy) < args.tolerance/2:
            zero_offset_labels = labels
            break

    # Analyze dominant non-zero offset
    non_zero_clusters = [(offset, labels) for offset, labels in clusters
                         if abs(offset[0]) >= args.tolerance/2 or abs(offset[1]) >= args.tolerance/2]

    print("\n" + "=" * 80)
    print("分析サマリー")
    print("=" * 80)
    print(f"\n1. 変更なしラベル (offset ≈ 0.0, 0.0): {len(zero_offset_labels)} ({len(zero_offset_labels)/len(offsets)*100:.2f}%)")
    print(f"   これらのラベルは両ファイルで完全に同じ位置にあります。")

    if non_zero_clusters:
        dominant_offset, dominant_labels = non_zero_clusters[0]
        print(f"\n2. 支配的シフトパターン (offset = {dominant_offset}):")
        print(f"   Count: {len(dominant_labels)} ({len(dominant_labels)/len(offsets)*100:.2f}%)")
        print(f"   このオフセットを持つラベルのグループが一緒に移動したと考えられます。")

        print(f"\n3. その他のオフセット: {len(offsets) - len(zero_offset_labels) - len(dominant_labels)}")
        print(f"   ({(len(offsets) - len(zero_offset_labels) - len(dominant_labels))/len(offsets)*100:.2f}%)")
        print(f"   これらは{len(clusters) - 2}個の異なるオフセット値に分散しています。")

        # Validation
        different_pct = (len(offsets) - len(zero_offset_labels)) / len(offsets) * 100
        correctable_pct = len(dominant_labels) / len(offsets) * 100
        remaining_diff_pct = different_pct - correctable_pct

        print("\n" + "=" * 80)
        print("仮説の検証:")
        print("=" * 80)
        print(f"\n補正なしの場合:")
        print(f"  - {different_pct:.1f}% のラベルが異なる位置にあります")
        print(f"  - {100-different_pct:.1f}% のみが真に変更なしです")

        print(f"\n支配的オフセット {dominant_offset} で補正した場合:")
        print(f"  - {correctable_pct:.1f}% の差分を補正できます")
        print(f"  - 残りの差分: {remaining_diff_pct:.1f}%")

        print(f"\n**結論:**")
        if correctable_pct > 15:
            print(f"  ✓ 仮説は部分的に有効です")
            print(f"  ✓ 支配的なオフセットパターンが存在します ({dominant_offset})")
            print(f"  ✓ このオフセット補正により誤検知を{correctable_pct:.1f}%減らせます")
            print(f"  ! ただし、{remaining_diff_pct:.1f}%の差分は残ります。これは以下を示唆します:")
            print(f"    - 複数の異なる変換が適用された")
            print(f"    - 実際のコンテンツ差分が存在する")
            print(f"    - 要素が個別に移動された")
        else:
            print(f"  ✗ 仮説は強く支持されません")
            print(f"  ✗ 単一の支配的オフセットでは大部分の差分を説明できません")
            print(f"  ✗ 差分は実際のコンテンツ/位置変更によるものと思われます")

    # Top clusters display
    display_count = len(clusters) if args.all else min(args.top_n, len(clusters))

    print("\n" + "=" * 80)
    if args.all:
        print(f"すべてのオフセットクラスタ（全{len(clusters)}個、割合順）")
    else:
        print(f"トップ {display_count} オフセットクラスタ（割合順）")
    print("=" * 80)
    print(f"{'Rank':<6} {'Offset (dx, dy)':<30} {'Count':<10} {'%':<10} {'Bar'}")
    print("-" * 80)

    for rank, ((dx, dy), labels) in enumerate(clusters[:display_count], 1):
        pct = len(labels) / len(offsets) * 100
        # Scale bar appropriately
        if pct >= 2.0:
            bar = '█' * int(pct / 2)
        elif pct >= 1.0:
            bar = '▓' * int(pct)
        elif pct >= 0.5:
            bar = '▒' * int(pct * 2)
        else:
            bar = '░' * min(int(pct * 4), 1)

        print(f"{rank:<6} ({dx:>10.2f}, {dy:>10.2f})    {len(labels):<10} {pct:>8.2f}%  {bar}")

    if not args.all and len(clusters) > display_count:
        print(f"\n... 他 {len(clusters) - display_count} クラスタ（--all オプションで全て表示）")

    print("\n" + "=" * 80)
    print("分析完了")
    print("=" * 80)


if __name__ == "__main__":
    main()
