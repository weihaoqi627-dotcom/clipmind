"""
人脸检测工具 — UniFace 驱动
============================
基于 UniFace (ONNX Runtime) 的高精度人脸检测,替代旧版 OpenCV Haar Cascade.

内置检测器:
  - SCRFD 500M: 极速(3.6ms/帧),2.4MB 模型(默认)
  - RetinaFace MNET_V2: 高精度(WIDER Hard 86.6%),3.5MB 模型

工具函数:
  - detect_faces: 单帧人脸检测,返回位置,置信度,关键点,镜头类型
  - analyze_faces: 全视频人脸扫描,返回统计信息和 talking head 判断
  - face_track: 视频人脸追踪,跨帧保持同一人物 ID
  - crop_to_face: 以人脸为中心裁剪视频到目标比例
"""
import json, os, subprocess, re, base64, hashlib, tempfile
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent

# 全局检测器(延迟加载单例)
_DETECTOR = None
_DETECTOR_TYPE = None


def _get_detector(detector_type: str = "scrfd"):
    """获取 UniFace 检测器(延迟加载单例).

    Args:
        detector_type: "scrfd" | "retinaface"
    """
    global _DETECTOR, _DETECTOR_TYPE
    if _DETECTOR is None or _DETECTOR_TYPE != detector_type:
        if detector_type == "retinaface":
            from uniface.detection import RetinaFace, RetinaFaceWeights
            _DETECTOR = RetinaFace(
                model_name=RetinaFaceWeights.MNET_V2,
                confidence_threshold=0.5,
                nms_threshold=0.4,
                input_size=(640, 640),
            )
        else:
            from uniface.detection import SCRFD, SCRFDWeights
            _DETECTOR = SCRFD(
                model_name=SCRFDWeights.SCRFD_500M_KPS,
                confidence_threshold=0.5,
                nms_threshold=0.4,
                input_size=(640, 640),
            )
        _DETECTOR_TYPE = detector_type
    return _DETECTOR


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════

def _parse_json(data: str):
    """安全解析 JSON 字符串,失败返回 None"""
    if not data:
        return None
    try:
        return json.loads(data)
    except (json.JSONDecodeError, TypeError):
        return None


def _get_video_dimensions(video_path: str) -> tuple:
    """用 ffmpeg -i 获取视频分辨率 (width, height)"""
    r = subprocess.run(
        ["ffmpeg", "-i", video_path],
        capture_output=True, timeout=30
    )
    output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    m = re.search(r",\s*(\d{3,})x(\d{3,})", output)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


def _detect_video_duration(video_path: str) -> float:
    """用 ffmpeg -i 解析视频时长(秒)"""
    r = subprocess.run(
        ["ffmpeg", "-i", video_path],
        capture_output=True, timeout=30
    )
    output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", output)
    if m:
        h = float(m.group(1))
        m_ = float(m.group(2))
        s = float(m.group(3))
        return h * 3600 + m_ * 60 + s
    return 0.0


def _extract_frame(video_path: str, time_pos: float, out_path: str) -> bool:
    """
    用 ffmpeg 提取视频某一帧保存到 out_path.
    返回 True 表示成功.
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_pos),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    return r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _cleanup_tmp(*paths):
    """清理临时文件"""
    for p in paths:
        if not p:
            continue
        if isinstance(p, (list, tuple)):
            for sub in p:
                _cleanup_tmp(sub)
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
        except Exception:
            pass


def _classify_shot_type(face_areas: list, frame_area: float) -> str:
    """
    根据最大人脸面积占比分类镜头类型.

    Args:
        face_areas: 各个人脸的面积(像素)
        frame_area: 总画面面积(像素)

    Returns:
        "close_up" | "medium_shot" | "wide_shot" | "no_faces"
    """
    if not face_areas or frame_area <= 0:
        return "no_faces"
    max_ratio = max(face_areas) / frame_area
    if max_ratio > 0.15:
        return "close_up"
    elif max_ratio >= 0.05:
        return "medium_shot"
    else:
        return "wide_shot"


def _parse_target_ratio(ratio_str: str) -> tuple:
    """解析目标比例字符串,返回 (w_ratio, h_ratio)"""
    ratio_map = {
        "9:16": (9, 16),
        "16:9": (16, 9),
        "1:1": (1, 1),
        "4:3": (4, 3),
        "3:4": (3, 4),
    }
    return ratio_map.get(ratio_str, (9, 16))


def _encode_frame_preview(frame) -> str:
    """将 OpenCV 帧编码为 base64 PNG 字符串"""
    import cv2
    success, buf = cv2.imencode(".png", frame)
    if success:
        return base64.b64encode(buf.tobytes()).decode("utf-8")
    return ""


def _draw_face_boxes(frame, faces: list, frame_w: int, frame_h: int):
    """
    在帧上绘制人脸矩形框和关键点.

    Args:
        frame: OpenCV BGR 图像
        faces: UniFace Face 对象列表
        frame_w, frame_h: 帧尺寸
    """
    import cv2
    import numpy as np
    for face in faces:
        bbox = face.bbox  # [x1, y1, x2, y2] 像素坐标
        x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

        # 画关键点
        if face.landmarks is not None and len(face.landmarks) > 0:
            for pt in face.landmarks:
                px, py = int(pt[0]), int(pt[1])
                cv2.circle(frame, (px, py), 2, (0, 0, 255), -1)

        # 画置信度和 track_id
        label = f"{face.confidence:.2f}"
        if getattr(face, 'track_id', None) is not None:
            label += f" ID:{face.track_id}"
        cv2.putText(frame, label, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
    return frame


def _face_to_dict(face) -> dict:
    """
    将 UniFace Face 对象转为标准 dict.
    坐标归一化到 [0, 1].

    Args:
        face: UniFace Face 对象
        frame_w, frame_h: 帧尺寸

    Returns:
        标准化 dict: x, y, w, h, center_x, center_y, confidence, landmarks, track_id
    """
    import numpy as np
    bbox = face.bbox  # [x1, y1, x2, y2] 像素坐标

    result = {
        "x": float(bbox[0]),
        "y": float(bbox[1]),
        "w": float(bbox[2] - bbox[0]),
        "h": float(bbox[3] - bbox[1]),
        "center_x": float((bbox[0] + bbox[2]) / 2),
        "center_y": float((bbox[1] + bbox[3]) / 2),
        "confidence": round(float(face.confidence), 4),
    }

    if face.landmarks is not None and len(face.landmarks) > 0:
        result["landmarks"] = face.landmarks.tolist()

    track_id = getattr(face, 'track_id', None)
    if track_id is not None:
        result["track_id"] = int(track_id)

    return result


def _faces_to_result(faces: list, frame_w: int, frame_h: int) -> dict:
    """
    将 UniFace 检测结果转为标准 JSON 结构.

    Args:
        faces: UniFace Face 对象列表
        frame_w, frame_h: 帧尺寸

    Returns:
        包含 face_count, faces, shot_type 等的 dict
    """
    frame_area = frame_w * frame_h
    total = len(faces)
    face_list = []
    face_areas = []

    for f in faces:
        d = _face_to_dict(f)
        face_list.append(d)
        face_areas.append(d["w"] * d["h"])

    shot_type = _classify_shot_type(face_areas, frame_area)

    return {
        "face_count": total,
        "faces": face_list,
        "shot_type": shot_type,
        "frame_size": f"{frame_w}x{frame_h}",
        "has_faces": total > 0,
    }


# ═══════════════════════════════════════════════════════════
#  核心工具函数
# ═══════════════════════════════════════════════════════════

@tool(
    name="detect_faces",
    description="检测视频某一帧中的人脸(基于 UniFace SCRFD/RetinaFace),返回像素坐标,置信度,5点关键点和镜头类型",
    phase="analyze",
    category="face",
    tags=["face", "detect", "uniface", "scrfd"],
    group="画面与场景",
)
def detect_faces(
    video_path: str,
    time_pos: float = 1.0,
    return_preview: bool = False,
    detector_type: str = "scrfd",
) -> str:
    """
    检测视频某一帧中的人脸.

    使用 UniFace SCRFD(默认)或 RetinaFace 在指定时间点提取帧并检测人脸,
    返回像素坐标的人脸位置,置信度,5点关键点和镜头类型分类.

    Args:
        video_path: 视频文件路径
        time_pos: 检测时间点(秒),默认 1.0
        return_preview: 是否返回带人脸框和关键点的预览图(base64 PNG)
        detector_type: 检测器类型,"scrfd"(极速,默认)或 "retinaface"(高精度)

    Returns:
        JSON 格式的检测结果
    """
    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"}, ensure_ascii=False)

    import cv2
    detector = _get_detector(detector_type)

    # 提取帧
    tmp_dir = tempfile.gettempdir()
    hash_s = hashlib.md5(f"{video_path}_{time_pos}_{detector_type}".encode()).hexdigest()[:8]
    frame_path = os.path.join(tmp_dir, f"face_detect_{hash_s}.jpg")

    if not _extract_frame(video_path, time_pos, frame_path):
        return json.dumps({"error": "帧提取失败,请检查视频路径和时间点"}, ensure_ascii=False)

    try:
        frame = cv2.imread(frame_path)
        if frame is None:
            return json.dumps({"error": "无法读取提取的帧"}, ensure_ascii=False)

        frame_h, frame_w = frame.shape[:2]

        # UniFace 检测(输入 BGR 格式)
        faces = detector.detect(frame)

        result = _faces_to_result(faces, frame_w, frame_h)
        result["detector"] = detector_type

        # 预览图
        if return_preview:
            preview_frame = _draw_face_boxes(frame.copy(), faces, frame_w, frame_h)
            preview_b64 = _encode_frame_preview(preview_frame)
            if preview_b64:
                result["preview"] = preview_b64

        _cleanup_tmp(frame_path)
        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        _cleanup_tmp(frame_path)
        return json.dumps({"error": f"人脸检测失败: {str(e)}"}, ensure_ascii=False)


@tool(
    name="analyze_faces",
    description="扫描整个视频分析人脸统计信息(出现比例,镜头类型分布,talking head 判断),基于 UniFace 高精度检测",
    phase="analyze",
    category="face",
    tags=["face", "analyze", "statistics"],
    group="画面与场景",
)
def analyze_faces(
    video_path: str,
    interval: float = 5.0,
    detector_type: str = "scrfd",
) -> str:
    """
    扫描整个视频,分析人脸出现统计信息.

    按固定间隔采样帧进行 UniFace 人脸检测,返回包含人脸出现比例,
    最大同帧人数,镜头类型分布,每帧人脸详情等统计信息.

    Args:
        video_path: 视频文件路径
        interval: 采样间隔(秒),默认 5.0
        detector_type: 检测器类型,"scrfd"(默认)或 "retinaface"

    Returns:
        JSON 格式的分析结果
    """
    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"}, ensure_ascii=False)

    import cv2
    detector = _get_detector(detector_type)

    duration = _detect_video_duration(video_path)
    if duration <= 0:
        return json.dumps({"error": "无法获取视频时长"}, ensure_ascii=False)

    w, h = _get_video_dimensions(video_path)
    if not w or not h:
        w, h = 1920, 1080
    frame_area = w * h

    # 生成采样时间点
    time_points = []
    t = 0.0
    while t < duration:
        time_points.append(t)
        t += interval

    if not time_points:
        time_points = [0.0]

    tmp_dir = tempfile.gettempdir()

    frames_with_faces = 0
    max_faces = 0
    total_faces = 0
    shot_type_counts = {"close_up": 0, "medium_shot": 0, "wide_shot": 0, "no_faces": 0}
    samples = []

    for tp in time_points:
        hash_s = hashlib.md5(f"{video_path}_{tp}_{detector_type}".encode()).hexdigest()[:8]
        frame_path = os.path.join(tmp_dir, f"face_analyze_{hash_s}.jpg")

        if not _extract_frame(video_path, tp, frame_path):
            continue

        try:
            frame = cv2.imread(frame_path)
            if frame is None:
                continue

            faces = detector.detect(frame)
            face_count = len(faces)
            total_faces += face_count

            if face_count > 0:
                frames_with_faces += 1

            max_faces = max(max_faces, face_count)

            # 计算面积比判断镜头类型
            face_areas = []
            for f in faces:
                bbox = f.bbox
                face_areas.append((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
            shot_type = _classify_shot_type(face_areas, frame_area)
            shot_type_counts[shot_type] = shot_type_counts.get(shot_type, 0) + 1

            # 每帧的人脸详情
            frame_faces = []
            for f in faces:
                d = _face_to_dict(f)
                # 归一化坐标(用于统计对比)
                d["nx"] = round(d["x"] / w, 4)
                d["ny"] = round(d["y"] / h, 4)
                d["nw"] = round(d["w"] / w, 4)
                d["nh"] = round(d["h"] / h, 4)
                frame_faces.append(d)

            samples.append({
                "time": round(tp, 1),
                "face_count": face_count,
                "shot_type": shot_type,
                "faces": frame_faces,
            })

        except Exception:
            pass
        finally:
            _cleanup_tmp(frame_path)

    total_analyzed = len(samples)
    if total_analyzed == 0:
        return json.dumps({"error": "无法分析任何帧"}, ensure_ascii=False)

    face_presence_ratio = round(frames_with_faces / total_analyzed, 4)
    avg_face_count = round(total_faces / total_analyzed, 2)

    # 判断主导镜头类型
    dominant = "no_faces"
    max_count = 0
    for st, count in shot_type_counts.items():
        if count > max_count:
            max_count = count
            dominant = st

    # 是否是 talking head 视频
    has_talking_head = face_presence_ratio > 0.5 and dominant in ("close_up", "medium_shot")

    result = {
        "detector": detector_type,
        "duration": round(duration, 1),
        "total_frames_analyzed": total_analyzed,
        "frames_with_faces": frames_with_faces,
        "face_presence_ratio": face_presence_ratio,
        "max_faces_in_frame": max_faces,
        "avg_face_count": avg_face_count,
        "shot_type_distribution": shot_type_counts,
        "dominant_shot_type": dominant,
        "has_talking_head": has_talking_head,
        "samples": samples,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(
    name="face_track",
    description="对视频进行人脸追踪,跨帧保持同一人物的 track_id,返回每帧检测结果和追踪统计",
    phase="analyze",
    category="face",
    tags=["face", "track", "tracking"],
    group="画面与场景",
)
def face_track(
    video_path: str,
    interval: float = 2.0,
    detector_type: str = "scrfd",
) -> str:
    """
    对视频进行人脸追踪,识别同一人物在不同帧中的出现.

    使用 BYTETracker 跨帧关联同一人脸,返回每帧的检测结果
    和每个人物(track_id)的出现统计.

    Args:
        video_path: 视频文件路径
        interval: 采样间隔(秒),默认 2.0(追踪需要更密的采样)
        detector_type: 检测器类型,"scrfd"(默认)或 "retinaface"

    Returns:
        JSON 格式的追踪结果,包含 tracks 统计和 frame_results
    """
    if not os.path.exists(video_path):
        return json.dumps({"error": f"文件不存在: {video_path}"}, ensure_ascii=False)

    import cv2
    import numpy as np
    from uniface.tracking import BYTETracker

    detector = _get_detector(detector_type)

    duration = _detect_video_duration(video_path)
    if duration <= 0:
        return json.dumps({"error": "无法获取视频时长"}, ensure_ascii=False)

    w, h = _get_video_dimensions(video_path)
    if not w or not h:
        return json.dumps({"error": "无法获取视频尺寸"}, ensure_ascii=False)

    # 初始化追踪器
    tracker = BYTETracker(
        track_thresh=0.5,
        track_buffer=30,
        match_thresh=0.8,
    )

    # 生成采样时间点
    time_points = []
    t = 0.0
    while t < duration:
        time_points.append(t)
        t += interval

    if not time_points:
        time_points = [0.0]

    tmp_dir = tempfile.gettempdir()
    frame_results = []
    track_info: dict[int, dict] = {}  # track_id -> 出现统计

    for frame_idx, tp in enumerate(time_points):
        hash_s = hashlib.md5(f"{video_path}_{tp}_track_{detector_type}".encode()).hexdigest()[:8]
        frame_path = os.path.join(tmp_dir, f"face_track_{hash_s}.jpg")

        if not _extract_frame(video_path, tp, frame_path):
            continue

        try:
            frame = cv2.imread(frame_path)
            if frame is None:
                continue

            # 检测
            faces = detector.detect(frame)

            if len(faces) > 0:
                # 构造追踪输入: (N, 5) 数组 [x1, y1, x2, y2, confidence]
                detections = np.array([
                    [float(f.bbox[0]), float(f.bbox[1]), float(f.bbox[2]), float(f.bbox[3]), float(f.confidence)]
                    for f in faces
                ])

                # 追踪关联
                tracked = tracker.update(detections, (h, w), (h, w))

                # 构建结果
                frame_faces = []
                for tdet in tracked:
                    tlbr = tdet.tlbr  # [x1, y1, x2, y2]
                    tid = int(tdet.track_id)
                    face_dict = {
                        "x": round(float(tlbr[0]), 1),
                        "y": round(float(tlbr[1]), 1),
                        "w": round(float(tlbr[2] - tlbr[0]), 1),
                        "h": round(float(tlbr[3] - tlbr[1]), 1),
                        "confidence": round(float(tdet.score), 4),
                        "track_id": tid,
                    }
                    frame_faces.append(face_dict)

                    # 累计 track 统计
                    if tid not in track_info:
                        track_info[tid] = {
                            "track_id": tid,
                            "first_seen": round(tp, 1),
                            "last_seen": round(tp, 1),
                            "frame_count": 0,
                        }
                    track_info[tid]["last_seen"] = round(tp, 1)
                    track_info[tid]["frame_count"] += 1

                frame_results.append({
                    "frame": frame_idx,
                    "time": round(tp, 1),
                    "face_count": len(frame_faces),
                    "faces": frame_faces,
                })
            else:
                frame_results.append({
                    "frame": frame_idx,
                    "time": round(tp, 1),
                    "face_count": 0,
                    "faces": [],
                })

        except Exception:
            pass
        finally:
            _cleanup_tmp(frame_path)

    if not frame_results:
        return json.dumps({"error": "无法分析任何帧"}, ensure_ascii=False)

    # 汇总追踪统计
    tracked_frames = sum(1 for fr in frame_results if fr["face_count"] > 0)
    tracks_list = list(track_info.values())
    tracks_list.sort(key=lambda x: x["frame_count"], reverse=True)

    result = {
        "detector": detector_type,
        "duration": round(duration, 1),
        "total_frames": len(frame_results),
        "frames_with_faces": tracked_frames,
        "unique_persons": len(tracks_list),
        "tracks": tracks_list[:20],  # 最多返回前20个 track
        "frame_results": frame_results,
    }

    return json.dumps(result, ensure_ascii=False, indent=2)


@tool(
    name="crop_to_face",
    description="以人脸为中心裁剪视频到目标比例(如 9:16 竖屏),基于 UniFace 高精度检测,适用于有人脸的视频快速裁剪",
    phase="all",
    category="face",
    tags=["face", "crop", "center"],
    group="画面与场景",
)
def crop_to_face(
    video_path: str,
    time_pos: float = 1.0,
    target_ratio: str = "9:16",
    padding: float = 0.1,
    output_path: str = "",
    detector_type: str = "scrfd",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    以人脸为中心裁剪视频到目标比例.

    使用 UniFace 检测指定时间点的人脸,计算包含所有人脸及其周围边距
    的裁剪框,调整到目标宽高比后使用 ffmpeg 执行裁剪.

    Args:
        video_path: 视频文件路径
        time_pos: 参考时间点(秒),默认 1.0
        target_ratio: 目标比例,支持 9:16/16:9/1:1/4:3/3:4,默认 9:16
        padding: 人脸周围边距比例 [0-1],默认 0.1
        output_path: 输出路径(可选)
        detector_type: 检测器类型,"scrfd"(默认)或 "retinaface"
        draft_id: 草稿 ID(用于同步写入)
        clip_id: 素材索引(用于同步写入)

    Returns:
        结果信息或错误描述
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    import cv2
    detector = _get_detector(detector_type)

    w, h = _get_video_dimensions(video_path)
    if not w or not h:
        return "无法获取视频尺寸"

    # 提取帧
    tmp_dir = tempfile.gettempdir()
    hash_s = hashlib.md5(f"{video_path}_crop_{time_pos}_{detector_type}".encode()).hexdigest()[:8]
    frame_path = os.path.join(tmp_dir, f"face_crop_{hash_s}.jpg")

    if not _extract_frame(video_path, time_pos, frame_path):
        return "帧提取失败,请检查视频路径和时间点"

    try:
        frame = cv2.imread(frame_path)
        if frame is None:
            return "无法读取提取的帧"

        faces = detector.detect(frame)

        if len(faces) == 0:
            _cleanup_tmp(frame_path)
            return "No faces detected, cannot crop to face"

        # 计算包含所有人脸的包围框(像素坐标)
        min_x, min_y = float("inf"), float("inf")
        max_x, max_y = float("-inf"), float("-inf")

        for face in faces:
            bbox = face.bbox
            min_x = min(min_x, bbox[0])
            min_y = min(min_y, bbox[1])
            max_x = max(max_x, bbox[2])
            max_y = max(max_y, bbox[3])

        # 添加边距
        face_w = max_x - min_x
        face_h = max_y - min_y
        pad_px_w = face_w * padding
        pad_px_h = face_h * padding

        crop_x = max(0, min_x - pad_px_w)
        crop_y = max(0, min_y - pad_px_h)
        crop_w = min(w - crop_x, face_w + pad_px_w * 2)
        crop_h = min(h - crop_y, face_h + pad_px_h * 2)

        # 调整到目标比例
        target_w_ratio, target_h_ratio = _parse_target_ratio(target_ratio)
        target_aspect = target_w_ratio / target_h_ratio

        current_aspect = crop_w / crop_h

        if current_aspect < target_aspect:
            # 当前太窄,需要扩展宽度
            new_w = crop_h * target_aspect
            if new_w > w:
                new_w = w
                new_h = new_w / target_aspect
                crop_x = 0
                crop_y = max(0, crop_y - (new_h - crop_h) / 2)
                crop_h = min(h - crop_y, new_h)
            else:
                extra_w = new_w - crop_w
                crop_x = max(0, crop_x - extra_w / 2)
                crop_w = new_w
                if crop_x + crop_w > w:
                    crop_x = w - crop_w
        else:
            # 当前太宽,需要扩展高度
            new_h = crop_w / target_aspect
            if new_h > h:
                new_h = h
                new_w = new_h * target_aspect
                crop_y = 0
                crop_x = max(0, crop_x - (new_w - crop_w) / 2)
                crop_w = min(w - crop_x, new_w)
            else:
                extra_h = new_h - crop_h
                crop_y = max(0, crop_y - extra_h / 2)
                crop_h = new_h
                if crop_y + crop_h > h:
                    crop_y = h - crop_h

        crop_x = int(round(crop_x))
        crop_y = int(round(crop_y))
        crop_w = int(round(crop_w))
        crop_h = int(round(crop_h))

        crop_w = max(2, min(crop_w, w - crop_x))
        crop_h = max(2, min(crop_h, h - crop_y))

        _cleanup_tmp(frame_path)

        # 输出路径
        if not output_path:
            hash_out = hashlib.md5(video_path.encode()).hexdigest()[:8]
            output_path = os.path.join(_PROJECT_DIR, "output", f"crop_to_face_{hash_out}.mp4")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}",
            "-c:v", "libx264", "-crf", "20",
            "-c:a", "copy", "-movflags", "+faststart", output_path,
        ]
        subprocess.run(cmd, capture_output=True, timeout=600, check=False)

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if draft_id:
                from director.draft import _write_to_draft
                _write_to_draft(draft_id, clip_id, "crop", {"mode": "face", "detector": detector_type}, label="人脸裁切完成")
            return (
                f"[OK] 人脸居中裁剪完成({target_ratio}, {detector_type}): {output_path} ({size_mb:.1f}MB)\n"
                f"裁剪区域: {crop_w}x{crop_h} @ ({crop_x},{crop_y})"
            )
        return "[FAIL] 裁剪失败"

    except Exception as e:
        _cleanup_tmp(frame_path)
        return f"人脸裁剪失败: {str(e)}"
