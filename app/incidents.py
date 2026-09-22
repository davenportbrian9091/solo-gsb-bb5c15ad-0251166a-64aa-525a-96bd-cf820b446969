"""检测异常段聚合为事件（incident）。

思路：
1. 输入为若干已保存检测的异常段（每段带 detection_id / series_id / 段内编号 /
   起止时间与段内最大 |z|），以及聚合间隔 ``gap_seconds``（秒，≥0）。
2. 全部段按 (开始时间, detection_id, 段内编号) 稳定排序；
3. 单次扫描：下一段的开始时间不超过当前事件末尾 + ``gap_seconds``
   （即时间重叠，或间隔不超过 ``gap_seconds``）即并入当前事件，事件末尾取
   成员结束时间的最大值；否则关闭当前事件、开启新事件。同一检测的相邻段
   与跨检测的段一视同仁，都按此规则合并；
4. 每个事件输出起止时间、覆盖的 series_id / detection_id、段数、成员中的
   最大 |z|，以及按 (开始时间, detection_id, 段内编号) 稳定排序的成员定位。

本模块为纯函数实现，只读取已保存的检测结果，不做任何写操作。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .models import parse_timestamp


def _describe_incident(members: list[dict[str, Any]]) -> dict[str, Any]:
    """构造单个事件：起止时间、覆盖序列 / 检测、段数、最大 |z| 与成员定位。

    成员已按 (开始时间, detection_id, 段内编号) 排序：事件起点即首个成员的
    开始时间；终点取成员结束时间的最大值（被包含的短段不会缩短事件）。
    """
    end_member = max(members, key=lambda m: parse_timestamp(m["end_time"]))
    return {
        "start_time": members[0]["start_time"],
        "end_time": end_member["end_time"],
        # 覆盖序列去重并按字典序返回；series_id 为 null 的检测不出现在此处，
        # 但其 detection_id 仍会列入 detection_ids
        "series_ids": sorted({m["series_id"] for m in members if m["series_id"] is not None}),
        "detection_ids": sorted({m["detection_id"] for m in members}),
        "segment_count": len(members),
        "max_abs_zscore": max(m["max_abs_zscore"] for m in members),
        "members": [
            {
                "detection_id": m["detection_id"],
                "series_id": m["series_id"],
                "segment_index": m["segment_index"],
                "start_time": m["start_time"],
                "end_time": m["end_time"],
                "max_abs_zscore": m["max_abs_zscore"],
            }
            for m in members
        ],
    }


def aggregate_incidents(
    segments: list[dict[str, Any]],
    gap_seconds: float,
) -> dict[str, Any]:
    """把异常段按时间重叠 / 间隔不超过 gap_seconds 聚合为事件。

    :param segments: 异常段列表，每段需含 ``detection_id`` / ``series_id`` /
        ``segment_index`` / ``start_time`` / ``end_time`` / ``max_abs_zscore``
    :param gap_seconds: 聚合间隔（秒，≥0）：下一段开始时间不超过当前事件
        末尾 + gap_seconds 即并入当前事件
    """
    ordered = sorted(
        segments,
        key=lambda m: (
            parse_timestamp(m["start_time"]),
            m["detection_id"],
            m["segment_index"],
        ),
    )

    incidents: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_end: datetime | None = None
    max_gap = timedelta(seconds=gap_seconds)

    for seg in ordered:
        start = parse_timestamp(seg["start_time"])
        end = parse_timestamp(seg["end_time"])
        if current_end is not None and start > current_end + max_gap:
            # 间隔超过 gap_seconds：关闭当前事件，开启新事件
            incidents.append(_describe_incident(current))
            current = []
            current_end = None
        current.append(seg)
        current_end = end if current_end is None else max(current_end, end)

    if current:
        incidents.append(_describe_incident(current))

    return {
        "segment_count": len(segments),
        "incident_count": len(incidents),
        "incidents": incidents,
    }
