"""
音频处理工具 — 修音 / 搜歌 / 节拍分析 / 人声分离
==================================================
包含三个功能组:
  1. 修音质:EQ / 压缩 / 降噪 / 响度归一化 / 噪声门
  2. 音乐库:搜 BGM / 节拍分析 / 库概览
  3. 人声分离:Demucs 分离人声 / 替换音频 / 混合混音

基于 ffmpeg 内置音频滤镜链 + 本地音乐库 + Demucs.
设计原则:
  - 所有工具返回 descriptive error string(不是抛异常)
  - 临时文件统一放到 _PROJECT_DIR / "_tmp_render"
  - NVENC 自动探测
"""
import json, os, subprocess, re, hashlib
from pathlib import Path

from director.registry import tool

_PROJECT_DIR = Path(__file__).parent.parent.parent


# ═══════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════


def _check_nvenc() -> bool:
    """检查 NVENC 编码器是否可用"""
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, timeout=10
    )
    return "h264_nvenc" in (r.stdout + r.stderr).decode("utf-8", errors="replace")


def _parse_json(data) -> any:
    """安全解析 JSON 字符串或直接返回已解析对象"""
    if not data:
        return None
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None
    return data


def _cleanup_tmp(*files):
    """清理临时文件"""
    for f in files:
        if not f:
            continue
        if isinstance(f, list):
            for sub in f:
                _cleanup_tmp(sub)
            continue
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            pass


def _detect_audio_stream(video_path: str) -> bool:
    """检测视频是否有音频流(兼容 Windows ffmpeg 7.1)"""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", video_path],
            capture_output=True, timeout=15, check=False,
        )
        out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        # 查找 Audio: 关键字(ffmpeg -i 输出格式)
        return "Audio:" in out
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════
#  共享渲染逻辑
# ═══════════════════════════════════════════════════════════


def _render_audio(video_path: str, audio_filter_parts: list, output_path: str) -> str:
    """
    共享渲染逻辑:视频流复制 + 音频滤镜链.

    Args:
        video_path: 源视频路径
        audio_filter_parts: 音频 filter 字符串列表(每个元素是一个完整的 filter 描述)
        output_path: 输出路径,为空时自动生成

    Returns:
        结果描述字符串(成功以 ✅ 开头,失败为错误信息)
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    if not _detect_audio_stream(video_path):
        return "视频没有音频流"

    if not audio_filter_parts:
        # 无音频滤镜需要处理,直接复制
        return "无需处理"

    if not output_path:
        tag = hashlib.md5(video_path.encode()).hexdigest()[:8]
        tmp_dir = _PROJECT_DIR / "_tmp_render"
        os.makedirs(str(tmp_dir), exist_ok=True)
        output_path = str(tmp_dir / f"audio_{tag}.mp4")
    else:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # 构建 audio filter 字符串:用逗号链接多个 filter
    af_str = ",".join(audio_filter_parts)

    nvenc_ok = _check_nvenc()
    vcodec = "h264_nvenc" if nvenc_ok else "libx264"
    vparams = ["-qp", "18", "-preset", "p4"] if nvenc_ok else ["-crf", "18", "-preset", "slow"]

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-c:v", vcodec, *vparams,
        "-af", af_str,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-500:]
        return f"音频处理失败: {err}"

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        return f"✅ 音频处理完成: {output_path} ({size:.1f}MB)"

    return "音频处理失败:输出文件为空"


# ═══════════════════════════════════════════════════════════
#  EQ — 均衡器
# ═══════════════════════════════════════════════════════════


@tool(
    name="apply_audio_eq",
    description="应用均衡器(EQ).使用 ffmpeg 的 equalizer 滤波器.eq_json 格式: [{\"freq\": 100, \"gain\": -3, \"width\": 1}, ...]freq 中心频率 20~20000 Hz, gain 增益 -30~+30 dB, width 带宽 0.1~4 个八度.可用于增强人声清晰度(提升 2~4kHz),减少低频噪音(衰减 50~200Hz)等.",
    phase="edit",
    category="audio",
    tags=["eq", "audio", "filter"],
    group="音频处理",
)
def apply_audio_eq(video_path: str, eq_json: str, output_path: str = "") -> str:
    """
    应用均衡器(EQ).使用 ffmpeg 的 equalizer 滤波器.

    Args:
        video_path: 源视频路径
        eq_json: EQ 参数 JSON.格式:
            [{"freq": 100, "gain": -3, "width": 1},
             {"freq": 1000, "gain": 2, "width": 0.5}, ...]
            freq: 中心频率 20~20000 Hz
            gain: 增益 -30~+30 dB
            width: 带宽 0.1~4 个八度
        output_path: 输出路径(可选)

    Returns:
        结果描述
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    bands = _parse_json(eq_json)
    if not bands or not isinstance(bands, list):
        return "EQ 参数无效:需要 JSON 数组"
    if len(bands) == 0:
        return "EQ 参数无效:至少需要一个频段"

    # 构建 equalizer 滤镜链
    filter_parts = []
    for i, band in enumerate(bands):
        freq = band.get("freq", 1000)
        gain = band.get("gain", 0)
        width = band.get("width", 1)

        # 参数校验
        if not (20 <= freq <= 20000):
            return f"频段 {i} freq 无效: {freq}(需 20~20000 Hz)"
        if not (-30 <= gain <= 30):
            return f"频段 {i} gain 无效: {gain}(需 -30~+30 dB)"
        if not (0.1 <= width <= 4):
            return f"频段 {i} width 无效: {width}(需 0.1~4 个八度)"

        filter_parts.append(f"equalizer=f={freq}:width={width}:g={gain}")

    return _render_audio(video_path, filter_parts, output_path)


# ═══════════════════════════════════════════════════════════
#  压缩器 — 动态范围压缩
# ═══════════════════════════════════════════════════════════


@tool(
    name="apply_audio_compressor",
    description="应用动态范围压缩器.使用 ffmpeg 的 acompressor 滤波器.threshold 值越小压缩越狠(-60~0 dB, 默认 -20),ratio 压缩比越大压缩越强(1~20, 默认 4),attack/release 控制响应速度.适合让人声更饱满,音量更稳定.",
    phase="edit",
    category="audio",
    tags=["compressor", "audio", "dynamic"],
    group="音频处理",
)
def apply_audio_compressor(
    video_path: str,
    threshold: float = -20,
    ratio: float = 4,
    attack: float = 5,
    release: float = 100,
    makeup: float = 0,
    output_path: str = "",
) -> str:
    """
    应用动态范围压缩器.使用 ffmpeg 的 acompressor 滤波器.
    值越小压缩越狠(threshold 越低,被压缩的动态范围越大).

    Args:
        video_path: 源视频路径
        threshold: 阈值 -60~0 dB(默认 -20).值越小压缩越狠.
        ratio: 压缩比 1~20(默认 4:1)
        attack: 启动时间 0.1~100 ms(默认 5)
        release: 释放时间 10~1000 ms(默认 100)
        makeup: 补偿增益 0~20 dB(默认 0)
        output_path: 输出路径(可选)

    Returns:
        结果描述
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 参数校验
    if not (-60 <= threshold <= 0):
        return f"threshold 无效: {threshold}(需 -60~0 dB,必须为负数)"
    if threshold >= 0:
        return f"threshold 必须为负数(当前: {threshold}),值越小压缩越狠"
    if not (1 <= ratio <= 20):
        return f"ratio 无效: {ratio}(需 1~20)"
    if not (0.1 <= attack <= 100):
        return f"attack 无效: {attack}(需 0.1~100 ms)"
    if not (10 <= release <= 1000):
        return f"release 无效: {release}(需 10~1000 ms)"
    if not (0 <= makeup <= 20):
        return f"makeup 无效: {makeup}(需 0~20 dB)"

    # ffmpeg acompressor 的 makeup 范围是 1~64,0 不合法
    makeup_part = f":makeup={makeup}" if makeup > 0 else ""
    filter_parts = [
        f"acompressor=threshold={threshold}dB:ratio={ratio}:"
        f"attack={attack}:release={release}{makeup_part}"
    ]

    return _render_audio(video_path, filter_parts, output_path)


# ═══════════════════════════════════════════════════════════
#  降噪 — 噪声抑制
# ═══════════════════════════════════════════════════════════


@tool(
    name="apply_audio_noise_reduction",
    description="应用噪声抑制.支持两种方法:afftdn(FFT 频域降噪,适合通用场景,默认),anlmdn(非局部均值降噪,适合恒定噪声如电流声/风扇声).strength 控制降噪强度 0.0~1.0(默认 0.5).",
    phase="edit",
    category="audio",
    tags=["noise", "audio", "filter"],
    group="音频处理",
)
def apply_audio_noise_reduction(
    video_path: str,
    method: str = "afftdn",
    strength: float = 0.5,
    output_path: str = "",
) -> str:
    """
    应用噪声抑制.支持 afftdn(FFT 通用降噪)和 anlmdn(非局部均值降噪).

    Args:
        video_path: 源视频路径
        method: 降噪方法."afftdn"(FFT 频域降噪,通用型)或
                "anlmdn"(非局部均值,适合恒定噪声,如电流声/风扇声)
        strength: 降噪强度 0.0~1.0
            - afftdn: 映射为 nr=strength*20(0~20)
            - anlmdn: 映射为 p=strength*0.5(0~0.5)
        output_path: 输出路径(可选)

    Returns:
        结果描述
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 参数校验
    if method not in ("afftdn", "anlmdn"):
        return f"method 无效: {method}(需为 afftdn 或 anlmdn)"
    if not (0.0 <= strength <= 1.0):
        return f"strength 无效: {strength}(需 0.0~1.0)"

    if method == "afftdn":
        nr_value = strength * 20.0
        filter_parts = [
            f"afftdn=nr={nr_value:.1f}:om=o"
        ]
    else:
        # anlmdn 的 p 有效范围 0.001~0.1
        p_value = max(0.001, min(0.1, strength * 0.1))
        filter_parts = [
            f"anlmdn=p={p_value:.3f}"
        ]

    return _render_audio(video_path, filter_parts, output_path)


# ═══════════════════════════════════════════════════════════
#  响度归一化 — LUFS 标准
# ═══════════════════════════════════════════════════════════


@tool(
    name="apply_audio_loudnorm",
    description="应用响度归一化(LUFS 标准).使用 ffmpeg 的 loudnorm 滤波器,采用双通道精确测量后归一化.loudness_target: -24 LUFS(影院)/ -23(广播)/ -16(短视频,默认)/ -14(流媒体).确保所有视频片段输出响度一致.",
    phase="edit",
    category="audio",
    tags=["loudness", "audio", "normalize"],
    group="音频处理",
)
def apply_audio_loudnorm(
    video_path: str,
    loudness_target: float = -16,
    output_path: str = "",
) -> str:
    """
    应用响度归一化(LUFS 标准).使用 ffmpeg 的 loudnorm 滤波器.
    采用双通道路径:先测量响度参数,再用测量值精确归一化.

    响度参考:
        -24 LUFS = 影院标准
        -23 LUFS = 广播电视标准
        -16 LUFS = 短视频平台(默认)
        -14 LUFS = 流媒体/社交媒体

    Args:
        video_path: 源视频路径
        loudness_target: I(Integrated)响度 -70~-5 LUFS(默认 -16)
        output_path: 输出路径(可选)

    Returns:
        结果描述
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 参数校验
    if not (-70 <= loudness_target <= -5):
        return f"loudness_target 无效: {loudness_target}(需 -70~-5 LUFS)"

    if not output_path:
        tag = hashlib.md5(video_path.encode()).hexdigest()[:8]
        tmp_dir = _PROJECT_DIR / "_tmp_render"
        os.makedirs(str(tmp_dir), exist_ok=True)
        output_path = str(tmp_dir / f"loudnorm_{tag}.mp4")
    else:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # ── 第一遍:测量响度参数 ──
    measure_path = str(_PROJECT_DIR / "_tmp_render" / f"loudnorm_measure_{hashlib.md5(video_path.encode()).hexdigest()[:8]}.wav")
    os.makedirs(os.path.dirname(measure_path), exist_ok=True)

    measure_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-af", f"loudnorm=I={loudness_target}:LRA=11:TP=-1.5:print_format=json",
        "-vn",
        "-c:a", "pcm_s16le",
        "-f", "wav",
        measure_path,
    ]

    measure_result = subprocess.run(measure_cmd, capture_output=True, timeout=300, check=False)
    measure_output = (measure_result.stdout + measure_result.stderr).decode("utf-8", errors="replace")

    # 从输出中提取 JSON 响度测量结果
    measured_params = {}
    try:
        # loudnorm 输出 JSON 在 stderr 中,查找第一个 { 到最后一个 }
        json_start = measure_output.find("{")
        json_end = measure_output.rfind("}")
        if json_start != -1 and json_end != -1 and json_end > json_start:
            json_str = measure_output[json_start:json_end + 1]
            measured = json.loads(json_str)
            measured_params = {
                "input_i": measured.get("input_i", str(loudness_target)),
                "input_tp": measured.get("input_tp", "-1.5"),
                "input_lra": measured.get("input_lra", "11"),
                "input_thresh": measured.get("input_thresh", "-30"),
                "target_offset": measured.get("target_offset", "0"),
            }
    except (json.JSONDecodeError, ValueError, AttributeError):
        pass

    # 清理测量文件
    _cleanup_tmp(measure_path)

    # ── 第二遍:应用测量值精确归一化 ──
    if measured_params:
        # 使用测量值进行精确归一化
        loudnorm_filter = (
            f"loudnorm=I={loudness_target}:LRA=11:TP=-1.5:"
            f"measured_I={measured_params['input_i']}:"
            f"measured_TP={measured_params['input_tp']}:"
            f"measured_LRA={measured_params['input_lra']}:"
            f"measured_thresh={measured_params['input_thresh']}:"
            f"offset={measured_params['target_offset']}:"
            f"print_format=summary"
        )
    else:
        # 第一遍失败,回退到单通道模式
        loudnorm_filter = f"loudnorm=I={loudness_target}:LRA=11:TP=-1.5"

    nvenc_ok = _check_nvenc()
    vcodec = "h264_nvenc" if nvenc_ok else "libx264"
    vparams = ["-qp", "18", "-preset", "p4"] if nvenc_ok else ["-crf", "18", "-preset", "slow"]

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-c:v", vcodec, *vparams,
        "-af", loudnorm_filter,
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)

    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-500:]
        return f"响度归一化失败: {err}"

    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
        size = os.path.getsize(output_path) / (1024 * 1024)
        mode = "双通道(精确)" if measured_params else "单通道(回退)"
        return f"✅ 响度归一化完成 [{mode}]: {output_path} ({size:.1f}MB)"

    return "响度归一化失败:输出文件为空"


# ═══════════════════════════════════════════════════════════
#  噪声门 — 消除无声段的背景噪音
# ═══════════════════════════════════════════════════════════


@tool(
    name="apply_audio_gate",
    description="应用噪声门.使用 ffmpeg 的 agate 滤波器.当信号电平低于 threshold 时自动静音,适合消除语音段之间的背景噪音(如空调声,环境底噪).",
    phase="edit",
    category="audio",
    tags=["gate", "audio", "noise"],
    group="音频处理",
)
def apply_audio_gate(
    video_path: str,
    threshold: float = -30,
    ratio: float = 10,
    attack: float = 10,
    release: float = 100,
    output_path: str = "",
) -> str:
    """
    应用噪声门.使用 ffmpeg 的 agate 滤波器.
    当信号电平低于阈值时自动静音,适合消除语音段之间的背景噪音.

    Args:
        video_path: 源视频路径
        threshold: 门限 -80~0 dB(默认 -30).信号低于此值时静音.
        ratio: 压缩比(默认 10)
        attack: 启动时间 ms(默认 10)
        release: 释放时间 ms(默认 100)
        output_path: 输出路径(可选)

    Returns:
        结果描述
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"

    # 参数校验
    if not (-80 <= threshold <= 0):
        return f"threshold 无效: {threshold}(需 -80~0 dB)"
    if not (1 <= ratio <= 20):
        return f"ratio 无效: {ratio}(需 >= 1)"
    if not (0.1 <= attack <= 100):
        return f"attack 无效: {attack}(需 0.1~100 ms)"
    if not (10 <= release <= 1000):
        return f"release 无效: {release}(需 10~1000 ms)"

    filter_parts = [
        f"agate=threshold={threshold}dB:ratio={ratio}:"
        f"attack={attack}:release={release}"
    ]

    return _render_audio(video_path, filter_parts, output_path)


# ═══════════════════════════════════════════════════════════
#  音乐库 — 搜 BGM / 节拍分析 / 库概览
# ═══════════════════════════════════════════════════════════

_MUSIC_DIR = _PROJECT_DIR / "downloads" / "music"
_MUSIC_CACHE = None
_USAGE_FILE = _PROJECT_DIR / "downloads" / "music_usage.json"


# ═══════════════════════════════════════════════════════════
#  音乐使用次数统计
# ═══════════════════════════════════════════════════════════

def _load_usage_data() -> dict:
    """读取音乐使用次数统计"""
    if _USAGE_FILE.exists():
        try:
            with open(str(_USAGE_FILE), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_usage_data(data: dict):
    """保存音乐使用次数统计"""
    _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(str(_USAGE_FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


@tool(
    name="increment_music_usage",
    description="记录一首歌被选中使用了一次.AI 每次选择某首背景音乐后调用此工具,累计使用次数越多,该歌在 list_hot_music 中排名越靠前.数据存储在本地,后续可同步到云端(跨用户全局热门).",
    phase="all",
    category="audio",
    tags=["music", "usage", "tracking"],
    group="背景音乐与音效",
)
def increment_music_usage(music_name: str) -> str:
    """
    增加一首歌的累计使用次数.AI 每次选中某首音乐后调用此工具,次数越多越热门.
    可用于 search_music(hot=True) 筛选热门音乐.支持本地统计,未来可对接云端.

    Args:
        music_name: 歌名(不包含 .mp3 后缀)

    Returns:
        操作结果描述
    """
    data = _load_usage_data()
    data[music_name] = data.get(music_name, 0) + 1
    _save_usage_data(data)
    _clear_music_cache()
    return f"✅ 已记录「{music_name}」的使用次数(累计 {data[music_name]} 次)"


# ═══════════════════════════════════════════════════════════


def _load_music_index() -> list[dict]:
    """扫描音乐目录,建立索引缓存.懒加载."""
    global _MUSIC_CACHE
    if _MUSIC_CACHE is not None:
        return _MUSIC_CACHE

    _MUSIC_CACHE = []
    if not _MUSIC_DIR.exists():
        return _MUSIC_CACHE

    mp3_files = {}
    for f in os.listdir(str(_MUSIC_DIR)):
        if f.endswith(".mp3"):
            name = f[:-4]
            fsize = os.path.getsize(str(_MUSIC_DIR / f))
            mp3_files[name] = {
                "path": str(_MUSIC_DIR / f),
                "size": fsize,
                "has_beat": False,
                "beat_path": "",
                "bpm": 0,
                "duration": 0,
                "beats": 0,
                "energy_min": 0,
                "energy_max": 0,
            }

    beat_names = set()
    for f in os.listdir(str(_MUSIC_DIR)):
        if f.endswith(".beat"):
            beat_names.add(f[:-5])

    for name, info in mp3_files.items():
        if name in beat_names:
            beat_path = str(_MUSIC_DIR / f"{name}.beat")
            info["has_beat"] = True
            info["beat_path"] = beat_path
            try:
                with open(beat_path, "r", encoding="utf-8") as bf:
                    bdata = json.load(bf)
                times = bdata.get("time", [])
                energy = bdata.get("energy", [])
                if len(times) > 1:
                    intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
                    avg_interval = sum(intervals) / len(intervals)
                    info["bpm"] = round(60000 / avg_interval) if avg_interval > 0 else 0
                    info["duration"] = round(times[-1] / 1000, 1)
                    info["beats"] = len(times)
                if energy:
                    info["energy_min"] = round(min(energy), 1)
                    info["energy_max"] = round(max(energy), 1)
            except (json.JSONDecodeError, KeyError, OSError):
                pass

    # 加载热歌列表 + 使用次数统计
    hot_songs = _load_hot_list()
    usage_data = _load_usage_data()
    for info in mp3_files.values():
        name = Path(info["path"]).stem
        info["hot"] = name in hot_songs or any(h in name for h in hot_songs)
        info["use_count"] = usage_data.get(name, 0)

    _MUSIC_CACHE = list(mp3_files.values())
    return _MUSIC_CACHE


def _load_hot_list() -> set:
    """加载热歌列表(hot_list.json),返回匹配的歌名集合"""
    hot_path = _MUSIC_DIR / "hot_list.json"
    if not hot_path.exists():
        return set()
    try:
        with open(str(hot_path), "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return set(data)
            if isinstance(data, dict) and "hot_songs" in data:
                return set(data["hot_songs"])
    except (json.JSONDecodeError, OSError):
        pass
    return set()


def _extract_tags(name: str) -> dict:
    """从文件名提取标签信息(歌手/曲风/情绪等)"""
    tags = {
        "artist": "", "title": name, "raw_tags": [],
        "is_vip": False, "is_remix": False, "is_short": False,
        "mood_hints": [],
    }
    mood_keywords = {
        "燃向": "燃", "伤感": "伤感", "治愈": "治愈", "轻快": "轻快",
        "舒缓": "舒缓", "DJ": "动感", "慢速": "舒缓", "慢摇": "舒缓",
        "快节奏": "动感", "卡点": "动感", "氛围": "氛围", "古风": "古风",
        "电音": "动感", "说唱": "动感", "摇滚": "燃",
    }
    if "(VIP)" in name:
        tags["is_vip"] = True
    bracket_tags = re.findall(r"((.*?))|\(([^)]*)\)", name)
    for bt in bracket_tags:
        tag = (bt[0] or bt[1]).strip()
        if tag:
            tags["raw_tags"].append(tag)
            for kw, mood in mood_keywords.items():
                if kw in tag and mood not in tags["mood_hints"]:
                    tags["mood_hints"].append(mood)
            if "Remix" in tag or "remix" in tag:
                tags["is_remix"] = True
            if "剪辑版" in tag or "短版" in tag:
                tags["is_short"] = True
    clean_name = name
    for bt in bracket_tags:
        tag = (bt[0] or bt[1]).strip()
        clean_name = clean_name.replace(f"({tag})", "").replace(f"({tag})", "")
    artist_title = re.split(r"\s*-\s*", clean_name, maxsplit=1)
    if len(artist_title) == 2:
        tags["artist"] = artist_title[0].strip()
        tags["title"] = artist_title[1].strip()
    else:
        tags["title"] = clean_name.strip()
    for kw, mood in mood_keywords.items():
        if kw in name and mood not in tags["mood_hints"]:
            tags["mood_hints"].append(mood)
    return tags


@tool(
    name="search_music",
    description="搜索背景音乐库.支持关键词(歌手/曲名),情绪(燃/伤感/治愈/轻快/舒缓/动感/氛围/古风),BPM范围筛选.AI 可以根据视频类型选择合适的 BGM.",
    phase="all",
    category="audio",
    tags=["music", "search", "bgm"],
    group="背景音乐与音效",
)
def search_music(
    query: str = "",
    mood: str = "",
    bpm_min: int = 0,
    bpm_max: int = 0,
    hot: bool = False,
    limit: int = 20,
    draft_id: str = "",
) -> str:
    """
    搜索背景音乐.通过关键词/情绪/BPM 范围/热度筛选已有音乐库.

    Args:
        query: 搜索关键词(搜索歌手名和曲名)
        mood: 情绪筛选,可选:燃/伤感/治愈/轻快/舒缓/动感/氛围/古风
        bpm_min: 最低 BPM(如 80)
        bpm_max: 最高 BPM(如 120)
        hot: 是否只返回热歌(默认 False)
        limit: 最大返回数量,默认20

    Returns:
        音乐列表文本
    """
    tracks = _load_music_index()
    if not tracks:
        return "音乐库为空或目录不存在"

    results = []
    for t in tracks:
        fname = Path(t["path"]).stem
        tags = _extract_tags(fname)
        score = 0
        if query:
            q = query.lower()
            name_match = q in fname.lower()
            artist_match = q in tags["artist"].lower()
            title_match = q in tags["title"].lower()
            if not (name_match or artist_match or title_match):
                continue
            if name_match: score += 10
            if artist_match: score += 5
            if title_match: score += 8
        if mood:
            mood_lower = mood.lower()
            if not any(mood_lower in m.lower() for m in tags["mood_hints"]):
                continue
            score += 3
        if hot and not t.get("hot", False):
            continue
        if hot:
            score += 5
        bpm = t.get("bpm", 0)
        if bpm_min > 0 and bpm < bpm_min:
            continue
        if bpm_max > 0 and bpm > bpm_max:
            continue
        if t["has_beat"]:
            score += 2
        results.append({
            "name": fname, "artist": tags["artist"], "title": tags["title"],
            "path": t["path"], "bpm": t.get("bpm", 0),
            "duration": t.get("duration", 0), "beats": t.get("beats", 0),
            "has_beat": t["has_beat"], "tags": tags["raw_tags"],
            "mood_hints": tags["mood_hints"],
            "size_mb": round(t["size"] / (1024 * 1024), 1),
            "hot": t.get("hot", False),
            "score": score,
        })
    if query or mood:
        results.sort(key=lambda x: -x["score"])
    else:
        results.sort(key=lambda x: -x["bpm"])
    results = results[:limit]
    if not results:
        return f"未找到匹配的音乐(关键词={query}, 情绪={mood}, BPM={bpm_min}-{bpm_max})"
    output = []
    for r in results:
        bpm_str = f"{r['bpm']}BPM" if r['bpm'] else "未知BPM"
        dur_str = f"{r['duration']}s" if r['duration'] else ""
        mood_str = f" [{', '.join(r['mood_hints'])}]" if r['mood_hints'] else ""
        tag_str = f" {' '.join(r['tags'])}" if r['tags'] else ""
        hot_badge = " [HOT]" if r.get("hot") else ""
        output.append(f"  {r['path']} - {r['name']} ({bpm_str}, {dur_str}, {r['size_mb']}MB){hot_badge}{mood_str}{tag_str}")
    if draft_id and results:
        bgm_path = results[0]["path"]
        from director.draft import _write_to_draft
        _write_to_draft(
            draft_id, 0, "audio",
            {"bgm_source": bgm_path},
            label="BGM添加完成",
            audio_config={"source": bgm_path, "volume": -15, "ducking": True},
        )
    return f"找到 {len(results)} 首匹配音乐:\n" + "\n".join(output)


@tool(
    name="get_beat_info",
    description="获取指定音乐的详细节拍信息(BPM,节拍时间点,能量分布,卡点策略建议).AI 拿到 music 后应调用此工具获取节拍数据,用于节奏卡点编排.",
    phase="analyze",
    category="audio",
    tags=["beat", "music", "analysis"],
    group="音频处理",
)
def get_beat_info(track_name: str) -> str:
    """
    获取指定音乐的节拍信息(BPM,节拍时间点,能量数据).
    用于卡点编排——知道每个鼓点在什么时间.

    Args:
        track_name: 音乐文件名或路径

    Returns:
        节拍信息(BPM + 节拍时间点 + 推荐卡点策略)
    """
    if not track_name:
        return "请提供音乐名称"
    base = track_name.replace(".mp3", "").replace(".beat", "")
    beat_path = _MUSIC_DIR / f"{base}.beat"
    mp3_path = _MUSIC_DIR / f"{base}.mp3"
    if not beat_path.is_file():
        tracks = _load_music_index()
        for t in tracks:
            if base.lower() in Path(t["path"]).stem.lower():
                bp_str = t.get("beat_path", "")
                if bp_str:
                    beat_path = Path(bp_str)
                mp3_path = Path(t["path"])
                break
    if not beat_path.is_file():
        return f"未找到节拍文件: {track_name}（请先生成节拍文件）"
    try:
        with open(str(beat_path), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return f"节拍文件解析失败: {e}"
    times = data.get("time", [])
    values = data.get("value", [])
    energy = data.get("energy", [])
    if len(times) < 4:
        return "节拍数据不完整(少于4个节拍点)"
    intervals = [times[i+1] - times[i] for i in range(len(times)-1)]
    avg_interval = sum(intervals) / len(intervals)
    bpm = round(60000 / avg_interval) if avg_interval > 0 else 0
    downbeats = [times[i] for i, v in enumerate(values) if v == 1]
    peak_time = 0
    if energy:
        max_energy_idx = energy.index(max(energy))
        peak_time = times[max_energy_idx] if max_energy_idx < len(times) else 0
    total_sec = times[-1] / 1000
    segments = []
    segment_count = max(1, int(total_sec / 30))
    for seg in range(segment_count):
        seg_start = seg * 30000
        seg_end = min((seg + 1) * 30000, times[-1])
        seg_beats = [t for t in times if seg_start <= t < seg_end]
        if seg_beats:
            first_beat = seg_beats[0] / 1000
            segments.append(f"    第{seg+1}段: 首拍在 {first_beat:.2f}s, {len(seg_beats)} 个节拍")
    result = (
        f"📊 {base}\n"
        f"  BPM: {bpm}\n"
        f"  时长: {total_sec:.1f}s\n"
        f"  总节拍: {len(times)}\n"
        f"  强拍数(downbeat): {len(downbeats)}\n"
        f"  能量峰值位置: {peak_time/1000:.1f}s\n"
        f"  平均间隔: {avg_interval:.1f}ms\n"
        f"\n  节拍时间点(前20个,秒):\n"
        f"    {[round(t/1000, 2) for t in times[:20]]}\n"
    )
    if len(times) > 20:
        result += f"    ... 共 {len(times)} 个节拍\n"
    result += f"\n  段落节拍分布:\n" + "\n".join(segments)
    result += (
        f"\n\n  卡点策略建议:\n"
        f"    快切(镜头0.5-1s): 每拍切换\n"
        f"    中速(镜头1-2s): 每2拍切换\n"
        f"    慢速(镜头2-4s): 每4拍(1小节)切换\n"
        f"    高潮段: 在 {peak_time/1000:.1f}s 能量峰值处对齐最强镜头\n"
    )
    return result


@tool(
    name="list_music_categories",
    description="列出音乐库概览——总数,BPM分布,情绪标签分布.AI 在需要选BGM但不确定用什么时,先看概览再精准搜索.",
    phase="analyze",
    category="audio",
    tags=["music", "library", "overview"],
    group="背景音乐与音效",
)
def list_music_categories() -> str:
    """列出音乐库的概览——按情绪/风格分类的统计信息."""
    tracks = _load_music_index()
    if not tracks:
        return "音乐库为空"
    total = len(tracks)
    with_beat = sum(1 for t in tracks if t["has_beat"])
    total_size = sum(t["size"] for t in tracks) / (1024 * 1024 * 1024)
    bpms = [t["bpm"] for t in tracks if t["bpm"] > 0]
    bpm_ranges = {"慢(≤70)": 0, "中(71-100)": 0, "快(101-130)": 0, "极快(>130)": 0}
    for b in bpms:
        if b <= 70: bpm_ranges["慢(≤70)"] += 1
        elif b <= 100: bpm_ranges["中(71-100)"] += 1
        elif b <= 130: bpm_ranges["快(101-130)"] += 1
        else: bpm_ranges["极快(>130)"] += 1
    mood_count = {}
    for t in tracks:
        ttags = _extract_tags(Path(t["path"]).stem)
        for m in ttags["mood_hints"]:
            mood_count[m] = mood_count.get(m, 0) + 1
    result = (
        f"📀 音乐库概览\n"
        f"  总数: {total} 首\n"
        f"  有节拍数据: {with_beat} 首(可用于卡点编排)\n"
        f"  总大小: {total_size:.1f}GB\n\n"
        f"  BPM 分布:\n"
    )
    for rng, cnt in bpm_ranges.items():
        result += f"    {rng}: {cnt:4d} 首 {'█' * max(1, cnt // 50)}\n"
    result += f"\n  情绪/风格标签分布:\n"
    for mood, cnt in sorted(mood_count.items(), key=lambda x: -x[1]):
        result += f"    {mood}: {cnt:4d} 首 {'█' * max(1, cnt // 20)}\n"
    result += (
        f"\n  使用示例:\n"
        f"    search_music(query=\"治愈\") — 搜索治愈系音乐\n"
        f"    search_music(mood=\"燃\") — 搜索燃向音乐\n"
        f"    search_music(bpm_min=80, bpm_max=120) — 搜索中速音乐\n"
        f"    search_music(hot=True) — 搜索当前热门音乐\n"
        f"    get_beat_info(\"10XMusic - 幸运日\") — 获取特定音乐节拍\n"
    )
    return result


def _clear_music_cache():
    """清除音乐库缓存,下次调用 _load_music_index 时重新扫描"""
    global _MUSIC_CACHE
    _MUSIC_CACHE = None


# ═══════════════════════════════════════════════════════════
#  音乐库管理 — 用户上传 / 网上扒歌 / 热歌列表
# ═══════════════════════════════════════════════════════════


@tool(
    name="add_music_to_library",
    description="将用户提供的音频或视频文件加入音乐库.如果是视频则自动提取音频.入库后即可被 search_music 搜索到.支持 mp3 和常见视频格式.",
    phase="edit",
    category="audio",
    tags=["music", "library", "manage"],
    group="背景音乐与音效",
)
def add_music_to_library(
    source_path: str,
    artist: str = "",
    title: str = "",
) -> str:
    """
    将用户提供的音频或视频文件加入音乐库.
    如果是视频,自动提取音频转 MP3.
    入库后自动建索引,下次搜索即可找到.

    Args:
        source_path: 本地文件路径(.mp3 或视频文件)
        artist: 歌手名(可选,留空则从文件名解析)
        title: 曲名(可选,留空则从文件名解析)

    Returns:
        操作结果描述
    """
    if not os.path.exists(source_path):
        return f"文件不存在: {source_path}"
    src = Path(source_path)
    stem = src.stem
    dst_name = f"{stem}.mp3"
    dst_path = _MUSIC_DIR / dst_name

    # 如果目标已存在,加后缀区分
    if dst_path.exists():
        base = stem
        idx = 1
        while dst_path.exists():
            dst_name = f"{base}_新增{idx}.mp3"
            dst_path = _MUSIC_DIR / dst_name
            idx += 1

    # 检查源文件类型
    ext = src.suffix.lower()
    try:
        if ext == ".mp3":
            # 直接复制
            import shutil
            shutil.copy2(str(src), str(dst_path))
        else:
            # 视频 -> 提取音频转 MP3
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), "-vn",
                 "-acodec", "libmp3lame", "-ab", "192k",
                 "-ar", "44100", "-ac", "2", str(dst_path)],
                capture_output=True, timeout=600, check=False,
            )
            if r.returncode != 0:
                err = (r.stdout + r.stderr).decode("utf-8", errors="replace")[-300:]
                if dst_path.exists():
                    dst_path.unlink()
                return f"音频提取失败: {err}"
    except Exception as e:
        return f"处理失败: {e}"

    _clear_music_cache()
    sz = dst_path.stat().st_size / (1024 * 1024)
    return (f"已入库: {dst_name} ({sz:.1f}MB)\n"
            f"路径: {dst_path}\n"
            f"提示:如果歌名不准确,可以重命名文件,或通过 add_music_to_library "
            f"重新导入并指定 artist/title 参数.")


@tool(
    name="download_music",
    description="从网上搜索并下载背景音乐到本地库.支持歌名/歌手/描述等方式搜索.使用 yt-dlp 从公开平台下载,下载后自动纳入搜索范围.当本地库没有用户需要的 BGM 时,AI 应调用此工具尝试获取.",
    phase="edit",
    category="audio",
    tags=["music", "download", "library"],
    group="背景音乐与音效",
)
def download_music(query: str) -> str:
    """
    从网上搜索并下载背景音乐到本地库.
    使用 yt-dlp 搜索 YouTube 等公开资源.
    下载后自动建立索引,可用于搜索和节拍分析.

    Args:
        query: 搜索关键词(歌名/歌手/描述)

    Returns:
        下载结果描述
    """
    try:
        import yt_dlp
    except ImportError:
        return "yt-dlp 未安装,请运行: pip install yt-dlp"

    try:
        # 用 yt-dlp 搜索
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "force_generic_extractor": False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_result = ydl.extract_info(f"ytsearch5:{query}", download=False)

        if not search_result or "entries" not in search_result:
            return f"未找到匹配结果: {query}"

        entries = search_result["entries"]
        # 取第一个结果下载
        best = entries[0]
        video_url = best.get("webpage_url", f"https://youtube.com/watch?v={best['id']}")
        video_title = best.get("title", query)
        # 清理文件名
        safe_title = re.sub(r'[\\/*?:"<>|]', "", video_title)[:80]
        dst_name = f"{safe_title}.mp3"
        dst_path = _MUSIC_DIR / dst_name

        # 如果有同名,加后缀
        if dst_path.exists():
            base = safe_title[:70]
            idx = 1
            while dst_path.exists():
                dst_name = f"{base}_{idx}.mp3"
                dst_path = _MUSIC_DIR / dst_name
                idx += 1

        # 下载(不经过 yt-dlp 的 post-processor,绕过 ffprobe 兼容性问题)
        # 先下载原始音频,再用 ffmpeg 手动转 mp3
        dl_opts = {
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "outtmpl": str(_MUSIC_DIR / f"{dst_path.stem}.%(ext)s"),
        }
        with yt_dlp.YoutubeDL(dl_opts) as ydl:
            ydl.download([video_url])

        # 手动用 ffmpeg 转 mp3(绕过 ffprobe)
        import glob
        for ext in ["webm", "m4a", "opus", "aac"]:
            raw_path = _MUSIC_DIR / f"{dst_path.stem}.{ext}"
            if raw_path.exists():
                mp3_path = str(_MUSIC_DIR / f"{dst_path.stem}.mp3")
                r = subprocess.run(
                    ["ffmpeg", "-y", "-i", str(raw_path),
                     "-acodec", "libmp3lame", "-ab", "192k",
                     "-ar", "44100", "-ac", "2", mp3_path],
                    capture_output=True, timeout=120, check=False,
                )
                # 删除原始文件
                raw_path.unlink()
                if r.returncode == 0:
                    dst_path = Path(mp3_path)
                    dst_name = dst_path.name
                else:
                    err = (r.stdout + r.stderr).decode("utf-8", errors="replace")[-200:]
                    return f"音频转码失败: {err}"
                break

        # 确认最终 mp3 存在(yt-dlp 可能输出 .mp3 或 .webm)
        if not dst_path.exists():
            # 找最近创建的 mp3
            candidates = list(_MUSIC_DIR.glob("*.mp3"))
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            for c in candidates:
                if c.name.startswith(dst_path.stem[:10]) or dst_path.stem in c.name:
                    dst_path = c
                    dst_name = c.name
                    break

        _clear_music_cache()
        sz = dst_path.stat().st_size / (1024 * 1024)
        return (f"已下载入库: {safe_title}\n"
                f"文件: {dst_path.name} ({sz:.1f}MB)\n"
                f"来源: {video_url}\n"
                f"提示:可通过 get_beat_info(\"{dst_path.stem}\") 获取节拍数据")
    except Exception as e:
        return f"下载失败: {e}"


@tool(
    name="list_hot_music",
    description="列出当前热门音乐列表.综合两个维度排序:1. 用户实际使用次数(用 increment_music_usage 累积);2. hot_list.json 编辑推荐.使用次数越多的歌越靠前.AI 应优先推荐靠前的音乐.",
    phase="analyze",
    category="audio",
    tags=["music", "hot", "ranking"],
    group="背景音乐与音效",
)
def list_hot_music(limit: int = 30) -> str:
    """
    列出热门音乐.
    综合两个维度排序:
      1. hot_list.json 定义的热门标记(编辑推荐)
      2. 用户实际使用次数(自动热门,increment_music_usage 累积)
    使用次数越多的歌越靠前,AI 应优先推荐.

    Args:
        limit: 最大返回数量

    Returns:
        热门音乐列表
    """
    tracks = _load_music_index()
    hot_songs = _load_hot_list()

    # 按热度排序:编辑推荐优先,再按使用次数降序
    def sort_key(t):
        name = Path(t["path"]).stem
        is_hot = name in hot_songs or any(h in name for h in hot_songs) if hot_songs else False
        return (-(t.get("use_count", 0) * 10 + (5 if is_hot else 0)),
                -t.get("has_beat", False),
                t.get("bpm", 0))

    sorted_tracks = sorted(tracks, key=sort_key)
    hot_tracks = sorted_tracks[:limit]

    if not hot_tracks:
        return "音乐库为空,暂无热门音乐"

    output = [f"热门音乐 Top {len(hot_tracks)}:"]
    for t in hot_tracks:
        name = Path(t["path"]).stem
        use_count = t.get("use_count", 0)
        bpm_str = f"{t['bpm']}BPM" if t['bpm'] else ""
        beat_str = " [有节拍]" if t.get("has_beat") else ""
        hot_badge = " 🔥" if use_count > 0 else ""
        output.append(f"  [{use_count}次]{hot_badge} {name} ({bpm_str}){beat_str}")
    output.append(f"\n使用 increment_music_usage(歌名) 累计使用次数")
    return "\n".join(output)
#  人声分离 — Demucs 分离 / 替换 / 混音
# ═══════════════════════════════════════════════════════════


def _check_demucs() -> tuple[bool, str]:
    """检查 Demucs 是否可用."""
    try:
        r = subprocess.run(
            ["python", "-m", "demucs", "--help"],
            capture_output=True, timeout=15, check=False,
        )
        if r.returncode == 0:
            return True, ""
        output = (r.stdout + r.stderr).decode("utf-8", errors="replace")
        if "usage:" in output.lower() or "demucs" in output.lower():
            return True, ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        import demucs.separate  # noqa
        return True, ""
    except ImportError:
        pass
    install_guide = (
        "Demucs 未安装.请执行以下命令安装:\n"
        "  pip install demucs\n\n"
        "如果已安装 PyTorch(当前环境已预装 PyTorch 2.11.0),"
        "Demucs 会自动利用 GPU 加速(如有 NVIDIA GPU).\n"
        "安装后重新运行即可."
    )
    return False, install_guide


def _extract_audio(video_path: str, output_wav: str) -> str:
    """从视频中提取音频为 44.1kHz WAV.成功返回空字符串."""
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"
    os.makedirs(os.path.dirname(output_wav) or ".", exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn", "-acodec", "pcm_s16le",
        "-ar", "44100", "-ac", "2",
        output_wav,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300, check=False)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-300:]
        return f"音频提取失败: {err}"
    if not os.path.exists(output_wav) or os.path.getsize(output_wav) == 0:
        return "音频提取失败:输出文件为空"
    return ""


@tool(
    name="separate_audio",
    description="从视频中分离人声和背景音乐/音效.使用 Demucs 深度学习模型.提取视频音频后运行 Demucs 推理,返回人声(vocals)和背景音(no_vocals)的 WAV 路径.支持三种模型: htdemucs(默认,最快), htdemucs_ft(fine-tuned,质量稍好), mdx_extra(最重但可能更好).输出目录结构: {output_dir}/{model}/{视频名}/vocals.wav",
    phase="edit",
    category="audio",
    tags=["demucs", "separation", "vocal"],
    group="音频处理",
)
def separate_audio(
    video_path: str,
    model: str = "htdemucs",
    output_dir: str = "",
    draft_id: str = "",
    clip_id: int = 0,
) -> str:
    """
    从视频中分离人声和背景音乐/音效.
    使用 Demucs 深度学习模型.

    Args:
        video_path: 视频文件路径
        model: Demucs 模型 "htdemucs"(默认) / "htdemucs_ft" / "mdx_extra"
        output_dir: 输出目录,为空时自动创建

    Returns:
        JSON 字符串含 vocals_path, no_vocals_path, mixture_path,或错误信息
    """
    if not os.path.exists(video_path):
        return f"文件不存在: {video_path}"
    valid_models = {"htdemucs", "htdemucs_ft", "mdx_extra"}
    if model not in valid_models:
        return f"不支持的模型: {model},可选: {', '.join(sorted(valid_models))}"
    demucs_ok, msg = _check_demucs()
    if not demucs_ok:
        return msg
    video_stem = Path(video_path).stem
    if not output_dir:
        output_dir = str(_PROJECT_DIR / "_tmp_render" / "demucs")
    os.makedirs(output_dir, exist_ok=True)
    tmp_wav = str(_PROJECT_DIR / "_tmp_render" / f"demucs_input_{hashlib.md5(video_path.encode()).hexdigest()[:8]}.wav")
    os.makedirs(os.path.dirname(tmp_wav), exist_ok=True)
    extract_err = _extract_audio(video_path, tmp_wav)
    if extract_err:
        _cleanup_tmp(tmp_wav)
        return extract_err
    patch_script = str(_PROJECT_DIR / "_tmp_render" / "_demucs_patch.py")
    cmd = [
        "python", patch_script,
        "--two-stems=vocals", "-n", model,
        "-o", output_dir, tmp_wav,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=3600, check=False)
    except subprocess.TimeoutExpired:
        _cleanup_tmp(tmp_wav)
        return "Demucs 处理超时(超过 3600 秒)"
    _cleanup_tmp(tmp_wav)
    if result.returncode != 0:
        err = (result.stdout + result.stderr).decode("utf-8", errors="replace")[-800:]
        return f"Demucs 分离失败: {err}"
    audio_basename = Path(tmp_wav).stem
    demucs_out_dir = Path(output_dir) / model / audio_basename
    vocals_path = str(demucs_out_dir / "vocals.wav")
    no_vocals_path = str(demucs_out_dir / "no_vocals.wav")
    mixture_path = str(demucs_out_dir / "mixture.wav")
    missing = []
    if not os.path.exists(vocals_path):
        missing.append("vocals.wav")
    if not os.path.exists(no_vocals_path):
        missing.append("no_vocals.wav")
    if missing:
        alt_basename = Path(tmp_wav).name
        alt_dir = Path(output_dir) / model / alt_basename
        if os.path.exists(str(alt_dir / "vocals.wav")):
            vocals_path = str(alt_dir / "vocals.wav")
            missing.remove("vocals.wav") if "vocals.wav" in missing else None
        if os.path.exists(str(alt_dir / "no_vocals.wav")):
            no_vocals_path = str(alt_dir / "no_vocals.wav")
            missing.remove("no_vocals.wav") if "no_vocals.wav" in missing else None
        if os.path.exists(str(alt_dir / "mixture.wav")):
            mixture_path = str(alt_dir / "mixture.wav")
        if missing:
            return f"Demucs 输出文件未找到: {', '.join(missing)}"
    vocals_size = os.path.getsize(vocals_path) / (1024 * 1024)
    no_vocals_size = os.path.getsize(no_vocals_path) / (1024 * 1024)
    mixture_size = os.path.getsize(mixture_path) / (1024 * 1024) if os.path.exists(mixture_path) else 0
    result_info = {
        "vocals_path": vocals_path, "no_vocals_path": no_vocals_path,
        "mixture_path": mixture_path,
        "vocals_size_mb": round(vocals_size, 1),
        "no_vocals_size_mb": round(no_vocals_size, 1),
        "mixture_size_mb": round(mixture_size, 1),
        "model": model,
    }
    if draft_id:
        from director.draft import Draft
        d = Draft(draft_id)
        if d.load():
            d.set_vocal_track(result_info["vocals_path"])
            d.save("人声分离完成")
    return json.dumps(result_info, ensure_ascii=False, indent=2)


@tool(
    name="replace_audio",
    description="替换视频的音频轨道.用新的音频文件替换视频原有音频,保留原视频画面.自动处理音频比视频长或短的情况(用 -shortest 截断).自动检测 NVENC 编码器加速输出.输出路径为空时自动在源文件旁生成.",
    phase="edit",
    category="audio",
    tags=["audio", "replace", "track"],
    group="音频处理",
)
def replace_audio(
    video_path: str,
    audio_path: str,
    output_path: str = "",
) -> str:
    """
    替换视频中的音频轨道.
    用新的音频文件替换视频的原有音频,保留原视频画面.

    Args:
        video_path: 原视频路径(保留画面)
        audio_path: 新音频路径(替换原有音频)
        output_path: 输出路径(可选,自动生成)

    Returns:
        结果描述
    """
    if not os.path.exists(video_path):
        return f"视频文件不存在: {video_path}"
    if not os.path.exists(audio_path):
        return f"音频文件不存在: {audio_path}"
    if not output_path:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_replaced{ext}"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    nvenc_ok = _check_nvenc()
    vcodec = "h264_nvenc" if nvenc_ok else "libx264"
    vparams = ["-qp", "18", "-preset", "p4"] if nvenc_ok else ["-crf", "18", "-preset", "slow"]
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path, "-i", audio_path,
        "-c:v", vcodec, *vparams,
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0",
        "-shortest", "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-500:]
        return f"音频替换失败: {err}"
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return "音频替换失败:输出文件为空"
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    return f"音频替换完成: {output_path} ({size_mb:.1f}MB)"


@tool(
    name="mix_audio",
    description="混合人声和背景音乐并替换回视频.将 separate_audio 分离出的 vocals 和 no_vocals 以指定音量比例混合,替换原视频的音频轨道.典型用途:分离后调低背景音量使人声更突出.工作流: separate_audio() -> mix_audio(vocal_volume=1.0, music_volume=0.3)",
    phase="edit",
    category="audio",
    tags=["audio", "mix", "vocal"],
    group="音频处理",
)
def mix_audio(
    video_path: str,
    vocal_path: str,
    music_path: str,
    vocal_volume: float = 1.0,
    music_volume: float = 0.4,
    output_path: str = "",
) -> str:
    """
    混合人声和背景音乐,替换回视频.

    Args:
        video_path: 原视频路径
        vocal_path: 人声 WAV 路径(separate_audio 的 vocals_path)
        music_path: 背景音乐 WAV 路径(separate_audio 的 no_vocals_path)
        vocal_volume: 人声音量 0.0~2.0(默认 1.0)
        music_volume: 背景音量 0.0~2.0(默认 0.4)
        output_path: 输出路径(可选)

    Returns:
        结果描述
    """
    if not os.path.exists(video_path):
        return f"视频文件不存在: {video_path}"
    if not os.path.exists(vocal_path):
        return f"人声文件不存在: {vocal_path}"
    if not os.path.exists(music_path):
        return f"背景音乐文件不存在: {music_path}"
    vocal_volume = max(0.0, min(2.0, vocal_volume))
    music_volume = max(0.0, min(2.0, music_volume))
    if not output_path:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_mixed{ext}"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    nvenc_ok = _check_nvenc()
    vcodec = "h264_nvenc" if nvenc_ok else "libx264"
    vparams = ["-qp", "18", "-preset", "p4"] if nvenc_ok else ["-crf", "18", "-preset", "slow"]
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path, "-i", vocal_path, "-i", music_path,
        "-filter_complex",
        f"[1:a]volume={vocal_volume}[vocal];"
        f"[2:a]volume={music_volume}[music];"
        f"[vocal][music]amix=inputs=2:duration=first:dropout_transition=2[mixed]",
        "-map", "0:v:0", "-map", "[mixed]",
        "-c:v", vcodec, *vparams,
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="replace")[-500:]
        return f"音频混合失败: {err}"
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return "音频混合失败:输出文件为空"
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    return f"音频混合完成\n  人声音量: {vocal_volume}x, 背景音量: {music_volume}x\n  输出: {output_path} ({size_mb:.1f}MB)"


# ═══════════════════════════════════════════════════════════
#  音效库 — 搜音效 / 取路径
# ═══════════════════════════════════════════════════════════

_SFX_DIR = _PROJECT_DIR / "downloads" / "sfx"
_SFX_METADATA = _SFX_DIR / "_metadata.json"
_SFX_CACHE = None


def _load_sfx_index() -> list[dict]:
    """加载音效元数据索引.懒加载."""
    global _SFX_CACHE
    if _SFX_CACHE is not None:
        return _SFX_CACHE

    _SFX_CACHE = []
    if not _SFX_METADATA.exists():
        return _SFX_CACHE

    try:
        with open(str(_SFX_METADATA), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _SFX_CACHE

    for item in raw:
        file_name = item.get("file", "")
        file_path = str(_SFX_DIR / file_name)
        cover_path = str(_SFX_DIR / item.get("cover_file", "")) if item.get("cover_file") else ""
        _SFX_CACHE.append({
            "id": item.get("md5", file_name.replace(".mp3", "")),
            "title": item.get("title", ""),
            "author": item.get("author", ""),
            "duration_ms": item.get("duration_ms", 0),
            "file": file_name,
            "path": file_path,
            "cover": cover_path,
            "size": item.get("file_size", 0),
            "exists": os.path.exists(file_path),
        })
    return _SFX_CACHE


@tool(
    name="search_sfx",
    description="搜索音效库.输入关键词查找匹配的音效,返回标题,时长,路径等信息.音效适用场景:woosh转场,提示音,过渡音,氛围音等.",
    phase="edit",
    category="audio",
    tags=["sfx", "search", "sound"],
    group="背景音乐与音效",
)
def search_sfx(keyword: str = "", count: int = 10) -> str:
    """
    搜索音效库.按标题关键词匹配,返回匹配结果列表.

    音效类型包含:转场音(Woosh/嗖),提示音(Ding),过渡音,氛围音,人声,动物声,自然音,机械音等.

    Args:
        keyword: 搜索关键词.如"woosh","过渡","提示","氛围","爆炸","风声".
        count: 返回结果数量(默认10,最多30)

    Returns:
        JSON 格式的音效列表 [{id, title, duration_ms, path, ...}]
    """
    index = _load_sfx_index()
    if not index:
        return json.dumps({"error": "音效库为空或不存在"}, ensure_ascii=False)

    keyword_lower = keyword.lower().strip()
    if not keyword_lower:
        # 无关键词返回全部(取前 count 个)
        results = index[:min(count, 30)]
    else:
        results = []
        for item in index:
            title = item.get("title", "").lower()
            author = item.get("author", "").lower()
            if keyword_lower in title or keyword_lower in author:
                results.append(item)
        results = results[:min(count, 30)]

    if not results:
        return json.dumps({"warning": f"未找到匹配「{keyword}」的音效,试试其他关键词"}, ensure_ascii=False)

    output = []
    for item in results:
        output.append({
            "id": item["id"],
            "title": item["title"],
            "duration_s": round(item["duration_ms"] / 1000, 1),
            "author": item["author"],
            "path": item["path"],
            "exists": item["exists"],
        })

    return json.dumps(output, ensure_ascii=False, indent=2)


@tool(
    name="get_sfx_path",
    description="根据音效 ID 或标题获取完整的音效文件路径,供渲染使用.需先调用 search_sfx 获取音效 ID.",
    phase="edit",
    category="audio",
    tags=["sfx", "path", "render"],
    group="背景音乐与音效",
)
def get_sfx_path(sfx_id: str) -> str:
    """
    根据音效 ID 或标题获取完整路径.

    音效路径可用于 render_from_draft / render_final 的音频参数,
    或用于 mix_audio 的混合操作.

    Args:
        sfx_id: 音效 ID(从 search_sfx 结果中获取的 id 字段)或标题关键词

    Returns:
        音效文件路径
    """
    index = _load_sfx_index()
    if not index:
        return "音效库为空"

    # 精确 match id
    for item in index:
        if item.get("id") == sfx_id:
            if item.get("exists"):
                return item["path"]
            return f"音效文件不存在: {item['path']}"

    # 退一步按标题 match
    for item in index:
        if item.get("title") == sfx_id:
            if item.get("exists"):
                return item["path"]

    return f"未找到音效: {sfx_id}.请先调用 search_sfx 搜索"


@tool(
    name="list_sfx_categories",
    description="列出音效库中所有分类及每个分类的代表性音效.帮助 AI 了解音效库中有什么类型的音效可用.",
    phase="plan",
    category="audio",
    tags=["sfx", "categories", "browse"],
    group="背景音乐与音效",
)
def list_sfx_categories() -> str:
    """
    列出音效库中的所有可用分类.

    分类基于音效标题的关键词自动分组,包括:转场/过渡,提示音,氛围音,
    人声/表情,自然/动物,机械/科技,打击/碰撞,特效等.

    Returns:
        分类概览文本
    """
    index = _load_sfx_index()
    if not index:
        return "音效库为空"

    # 从标题推断分类(根据关键词分组)
    cat_keywords = {
        "转场/过渡": ["woosh", "嗖", "转场", "过渡", "滑动", "翻页", "whoosh", "swoosh"],
        "提示音": ["ding", "叮", "提示", "通知", "消息", "提醒", "bell", "beep", "notification"],
        "氛围": ["氛围", "环境", "背景", "空气", "风吹", "ambient", "atmosphere"],
        "人声/表情": ["哈哈", "笑", "啊", "哦", "哇", "嘿", "嗯", "yeah", "oh", "wow", "鼓掌", "欢呼"],
        "自然": ["鸟", "水", "雨", "风", "雷", "海浪", "森林", "动物", "猫", "狗"],
        "机械/科技": ["按键", "点击", "确认", "取消", "加载", "弹窗", "扫描", "robot", "digital", "click"],
        "打击/碰撞": ["爆炸", "撞击", "打击", "破碎", "枪", "爆炸", "boom", "hit", "punch", "crash"],
        "特效": ["魔法", "仙", "精灵", "魔幻", "魔法", "升格", "降格", "glitch", "sci-fi"],
    }

    # 分配每个音效到第一个匹配的分类
    cat_items = {k: [] for k in cat_keywords}
    other_items = []

    for item in index:
        title = item.get("title", "").lower()
        matched = False
        for cat_name, kws in cat_keywords.items():
            if any(kw in title for kw in kws):
                cat_items[cat_name].append(item["title"])
                matched = True
                break
        if not matched:
            other_items.append(item["title"])

    lines = [f"音效库共 {len(index)} 条\n"]
    for cat_name, items in cat_items.items():
        if items:
            total = len(items)
            examples = items[:5]
            lines.append(f"  {cat_name}({total}个): {', '.join(examples)}")
    if other_items:
        lines.append(f"\n  其他({len(other_items)}个): {', '.join(other_items[:5])}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  波形生成
# ═══════════════════════════════════════════════════════════

def _get_waveform_raw(audio_path: str, num_bars: int = 200) -> dict:
    """
    生成音频波形数据.
    用 ffmpeg 解码为 PCM float32 -> 分片 -> 算每片峰值/RMS.
    返回归一化的波形数据,可直接给前端 Canvas 渲染.
    """
    import struct

    if not os.path.exists(audio_path):
        return {"error": f"文件不存在: {audio_path}"}

    # ffmpeg: 输出 raw PCM float32, mono, 8000Hz(足够波形用了)
    proc = subprocess.run([
        "ffmpeg", "-y",
        "-i", audio_path,
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ar", "8000", "-ac", "1",
        "-",
    ], capture_output=True, timeout=120)

    if proc.returncode != 0 or len(proc.stdout) == 0:
        return {"error": f"ffmpeg 音频解码失败"}

    # 解析 float32 样本
    num_samples = len(proc.stdout) // 4
    samples = struct.unpack(f"{num_samples}f", proc.stdout)
    duration = num_samples / 8000.0

    if duration <= 0:
        return {"error": "音频时长为 0"}

    # 分片算峰值
    segment_size = max(1, num_samples // num_bars)
    bars = []
    for i in range(num_bars):
        start = i * segment_size
        end = min(start + segment_size, num_samples)
        chunk = samples[start:end]
        if not chunk:
            break
        peak = max(abs(s) for s in chunk)
        rms = (sum(s * s for s in chunk) / len(chunk)) ** 0.5
        t = (start + len(chunk) / 2) / 8000.0
        bars.append({
            "t": round(t, 2),
            "peak": round(min(peak, 1.0), 3),
            "rms": round(min(rms, 1.0), 3),
        })

    # 归一化(确保最大值 = 1.0)
    max_peak = max(b["peak"] for b in bars) if bars else 1.0
    if max_peak > 0:
        for b in bars:
            b["peak"] = round(b["peak"] / max_peak, 3)
            b["rms"] = round(b["rms"] / max_peak, 3)

    return {
        "duration": round(duration, 2),
        "num_bars": len(bars),
        "bars": bars,
    }


@tool(
    name="get_waveform",
    description=(
        "获取音频的波形数据.返回每个时间片的峰值和RMS值."
        "用于做卡点,找节拍,判断哪段有声音哪段是静音."
        "返回 JSON 包含 bars 数组,每个 bar 有 t(时间)/peak(峰值)/rms(均方根)."
    ),
    phase="analyze",
    category="audio",
    tags=["waveform", "audio", "analyze"],
    group="音频处理",
)
def get_waveform(audio_path: str, num_bars: int = 200) -> str:
    """
    获取音频波形数据.

    Args:
        audio_path: 音频文件路径(mp3/wav/m4a 等)
        num_bars: 分辨率(波形柱数),默认 200

    Returns:
        JSON 字符串,包含 duration,num_bars,bars 数组
    """
    result = _get_waveform_raw(audio_path, num_bars)
    if "error" in result:
        return result["error"]
    # 返回紧凑版:只有 bars 数据和 duration
    return json.dumps(result, ensure_ascii=False)


@tool(
    name="add_bgm_to_draft",
    description="向草稿(draft)添加背景音乐(BGM)。BGM 会在最终渲染时自动混入视频。支持音量调节和音频闪避(人声时自动降低BGM音量)。传入已有音频文件路径即可。",
    phase="edit",
    category="audio",
    tags=["audio", "bgm", "draft"],
    group="背景音乐与音效",
)
def add_bgm_to_draft(
    draft_id: str = "",
    source: str = "",
    volume: float = -15,
    ducking: bool = True,
) -> str:
    """
    向草稿添加背景音乐。

    Args:
        draft_id: 草稿 ID(必填)
        source: 音频文件路径(必填),支持 mp3/wav/flac/aac 等格式
        volume: 音量(dB),默认 -15(0=原始音量,负值=减小)
        ducking: 是否启用音频闪避(人声时自动降低BGM),默认 True

    Returns:
        结果描述
    """
    if not draft_id:
        return "❌ 请指定 draft_id"
    if not source or not os.path.exists(source):
        return f"❌ 音频文件不存在: {source}"

    from director.draft import Draft
    d = Draft(draft_id)
    data = d.load()
    if data is None:
        return f"❌ 草稿 {draft_id} 不存在"

    d.set_bgm(source=source, volume=volume, ducking=ducking)
    d.save("添加BGM")

    return (
        f"✅ BGM已添加到草稿 [{draft_id}]\n"
        f"   - 来源: {os.path.basename(source)}\n"
        f"   - 音量: {volume}dB\n"
        f"   - 闪避: {'开' if ducking else '关'}\n"
        f"   最终渲染时将自动混入此BGM。"
    )


# ═══════════════════════════════════════════════════════════
#  工具定义
# ═══════════════════════════════════════════════════════════

# 工具已通过 @tool 装饰器自动注册到 Registry
