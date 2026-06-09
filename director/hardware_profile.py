"""
硬件画像与自适应配置 — 一次检测,全程最优
========================================
硬件画像的原理类似系统安装时的硬件检测:
扫描你的硬件能力,然后把软件参数调到最适配你的机器.

我们管线的所有环节(压缩/分析/渲染)都从这里读取配置,
而不是写死参数.

用法:
    from director.hardware_profile import get_pipeline_config

    cfg = get_pipeline_config()
    # cfg["compress"]["encoder"]  → 最优编码器
    # cfg["compress"]["workers"]  → 最优并行路数
    # cfg["render"]["quality"]    → 最优渲染质量
"""

import json, os, subprocess, time, shutil, platform, re
from pathlib import Path

# ── 全局配置路径 ──────────────────────────────────────────
_CONFIG_DIR = Path(os.environ.get(
    "CLIPMIND_CONFIG_DIR",
    str(Path.home() / ".clipmind"),
))
_PROFILE_PATH = _CONFIG_DIR / "hardware_profile.json"


# ═══════════════════════════════════════════════════════════
#  Step 1: 硬件检测（纯查询,不写文件）
# ═══════════════════════════════════════════════════════════

def _detect_gpu() -> dict:
    """检测 GPU 型号和可用的硬件编码器"""
    result = {
        "name": None,
        "hw_encoder": None,   # "h264_nvenc" | "h264_amf" | "h264_qsv" | None
        "hw_decoder": False,
    }
    try:
        r = subprocess.run(
            ["ffmpeg", "-encoders"], capture_output=True, timeout=30, text=True,
        )
        enc_out = r.stdout
        if "h264_nvenc" in enc_out:
            result["hw_encoder"] = "h264_nvenc"
        elif "h264_amf" in enc_out:
            result["hw_encoder"] = "h264_amf"
        elif "h264_qsv" in enc_out:
            result["hw_encoder"] = "h264_qsv"
        result["hw_decoder"] = result["hw_encoder"] is not None
    except Exception:
        pass

    # 查 GPU 型号（Windows: wmic / nvidia-smi / dxdiag）
    try:
        if platform.system() == "Windows":
            r = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name"],
                capture_output=True, timeout=10, text=True,
            )
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line and "name" not in line.lower():
                    result["name"] = line
                    break
    except Exception:
        pass

    return result


def _detect_cpu() -> dict:
    """检测 CPU 核心数"""
    result = {
        "name": None,
        "cores_logical": os.cpu_count() or 1,
        "cores_physical": None,
    }
    try:
        if platform.system() == "Windows":
            r = subprocess.run(
                ["wmic", "cpu", "get", "name,NumberOfCores"],
                capture_output=True, timeout=10, text=True,
            )
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line and "name" not in line.lower() and "NumberOfCores" not in line.lower():
                    # 解析: "Intel(R) Core(TM) i7-14700K  2  (14 cores)"
                    # 但 wmic 输出格式不稳定,先简单提取
                    result["name"] = line[:80]
                    break
            # 物理核心数
            r2 = subprocess.run(
                ["wmic", "cpu", "get", "NumberOfCores"],
                capture_output=True, timeout=10, text=True,
            )
            for line in r2.stdout.split("\n"):
                line = line.strip()
                if line.isdigit():
                    result["cores_physical"] = int(line)
                    break
        else:
            # Linux / macOS
            result["cores_physical"] = os.cpu_count() or 1
    except Exception:
        pass
    if not result["cores_physical"]:
        result["cores_physical"] = result["cores_logical"]

    return result


def _detect_memory() -> dict:
    """检测物理内存"""
    result = {"total_gb": 0}
    try:
        if platform.system() == "Windows":
            r = subprocess.run(
                ["wmic", "memorychip", "get", "Capacity"],
                capture_output=True, timeout=10, text=True,
            )
            total_bytes = 0
            for line in r.stdout.split("\n"):
                line = line.strip()
                if line.isdigit():
                    total_bytes += int(line)
            result["total_gb"] = round(total_bytes / (1024**3), 1)
        else:
            # Linux: /proc/meminfo
            import os as _os
            if _os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            result["total_gb"] = round(kb / (1024 * 1024), 1)
                            break
    except Exception:
        pass
    if result["total_gb"] == 0:
        result["total_gb"] = 8  # fallback: 假设8G
    return result


def _detect_disk(workspace_path: str = None) -> dict:
    """检测工作区所在的磁盘类型（SSD/HDD）"""
    result = {"type": "unknown", "path": workspace_path or os.getcwd()}
    try:
        import ctypes
        if platform.system() == "Windows":
            # 在临时目录创建测试文件（避免根目录权限问题）
            import tempfile
            test_dir = tempfile.gettempdir()
            test_file = os.path.join(test_dir, "_clipmind_disk_test.tmp")
            try:
                size_mb = 64
                with open(test_file, "wb") as f:
                    f.write(os.urandom(size_mb * 1024 * 1024))
                start = time.time()
                with open(test_file, "rb") as f:
                    for _ in range(256):
                        f.seek(os.urandom(1)[0] * size_mb // 2 * 1024 * 1024, 0)
                        f.read(4096)
                elapsed = time.time() - start
                os.remove(test_file)
                speed = (256 * 4096) / (1024 * 1024) / max(elapsed, 0.001)
                result["type"] = "SSD" if speed > 10 else "HDD"
                result["random_read_mb_s"] = round(speed, 1)
            except Exception:
                result["type"] = "unknown"
        else:
            result["type"] = "unknown"
    except Exception:
        pass
    return result


# ═══════════════════════════════════════════════════════════
#  Step 2: 基准测试 — 实际编码速度
# ═══════════════════════════════════════════════════════════

def _benchmark_encode(duration_s: float = 2.0) -> dict:
    """
    生成一段测试视频并编码,测出这台机器的真实编码能力.

    返回:
        {"fps_sw": float, "fps_hw": float | None, "encoder": str | None}
        fps_sw: 纯CPU软件编码速度 (fps)
        fps_hw: 硬件编码速度 (fps), None 如果没有硬件编码器
        encoder: 可用的硬件编码器名
    """
    import hashlib, struct
    result = {"fps_sw": 0.0, "fps_hw": None, "encoder": None}

    try:
        tmp_dir = _CONFIG_DIR / "_bench_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        clean = lambda: shutil.rmtree(tmp_dir, ignore_errors=True)

        # 生成一段测试视频: 1280x720, 30fps, 2秒, color bars + moving box
        test_raw = str(tmp_dir / "test_raw.yuv")
        test_sw = str(tmp_dir / "test_sw.mp4")
        fps = 30
        total_frames = int(fps * duration_s)
        w, h = 1280, 720
        frame_size = w * h * 3 // 2  # YUV420p

        # 跳过YUV生成,直接用 ffmpeg 的 testsrc2 源
        test_src = (
            f"testsrc2=size={w}x{h}:rate={fps}:duration={duration_s}"
        )

        # ── 软件编码测试 ──
        cmd_sw = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", test_src,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "32",
            "-an", test_sw,
        ]
        start = time.time()
        subprocess.run(cmd_sw, capture_output=True, timeout=120)
        elapsed = time.time() - start
        if os.path.exists(test_sw) and os.path.getsize(test_sw) > 0:
            result["fps_sw"] = round(total_frames / max(elapsed, 0.001), 1)

        # ── 硬件编码测试（如果有） ──
        gpu = _detect_gpu()
        if gpu["hw_encoder"]:
            test_hw = str(tmp_dir / "test_hw.mp4")
            cmd_hw = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", test_src,
                "-c:v", gpu["hw_encoder"],
            ]
            if gpu["hw_encoder"] == "h264_nvenc":
                cmd_hw += ["-preset", "p1", "-cq", "32"]
            elif gpu["hw_encoder"] == "h264_amf":
                cmd_hw += ["-quality", "speed", "-qp_i", "32", "-qp_p", "32"]
            elif "qsv" in gpu["hw_encoder"]:
                cmd_hw += ["-preset", "veryfast", "-global_quality", "32"]
            cmd_hw += ["-an", test_hw]

            start = time.time()
            subprocess.run(cmd_hw, capture_output=True, timeout=120)
            elapsed = time.time() - start
            if os.path.exists(test_hw) and os.path.getsize(test_hw) > 0:
                result["fps_hw"] = round(total_frames / max(elapsed, 0.001), 1)
                result["encoder"] = gpu["hw_encoder"]

        clean()
    except Exception:
        try:
            clean()
        except Exception:
            pass

    return result


# ═══════════════════════════════════════════════════════════
#  Step 3: 配置生成 — 原始数据 → 管线配置
# ═══════════════════════════════════════════════════════════

def _profile_to_pipeline_config(profile: dict) -> dict:
    """
    根据硬件画像生成最优管线配置.

    决策逻辑:
    - 编码器: 有硬件就用硬件,否则软件
    - 压缩参数: 按编码速度分级
    - 并行路数: 按核心数和磁盘类型
    """
    gpu = profile.get("gpu", {})
    cpu = profile.get("cpu", {})
    mem = profile.get("memory", {})
    disk = profile.get("disk", {})
    bench = profile.get("benchmark", {})

    hw_encoder = gpu.get("hw_encoder")
    cores = cpu.get("cores_logical", 1)
    is_ssd = disk.get("type") == "SSD"
    mem_gb = mem.get("total_gb", 8)
    fps_sw = bench.get("fps_sw", 0)
    fps_hw = bench.get("fps_hw")

    # ── 压缩配置 ──────────────────────────────────────
    if hw_encoder:
        # 有硬件编码器 → 直接启用,基准测试用合成源不准确,真视频时硬件更快
        compress = {
            "encoder": hw_encoder,
            "preset": "p1" if hw_encoder == "h264_nvenc" else "speed",
            "cq_or_crf": 28,
            "max_dim": 1280,
            "workers": 1,
            "timeout": 1800,
        }
    elif fps_sw > 80:
        # 软件编码很快 → 高质量
        compress = {
            "encoder": None,
            "preset": "ultrafast",
            "cq_or_crf": 28,
            "max_dim": 1280,
            "workers": min(3, max(1, cores // 2)) if is_ssd else 2,
            "timeout": 1800,
        }
    elif fps_sw > 30:
        # 软件编码中等 → 平衡
        compress = {
            "encoder": None,
            "preset": "ultrafast",
            "cq_or_crf": 32,
            "max_dim": 1280,
            "workers": min(3, max(1, cores // 2)),
            "timeout": 1800,
        }
    else:
        # 慢机器 → 保底,降分辨率
        compress = {
            "encoder": None,
            "preset": "ultrafast",
            "cq_or_crf": 35,
            "max_dim": 960,
            "workers": 2,
            "timeout": 3600,
        }

    # ── 渲染配置 ──────────────────────────────────────
    if hw_encoder:
        render_encoder = hw_encoder
        render_preset = "p4" if hw_encoder == "h264_nvenc" else "balanced"
        render_quality = "high"
    elif fps_sw > 60:
        render_encoder = "libx264"
        render_preset = "fast"
        render_quality = "high"
    else:
        render_encoder = "libx264"
        render_preset = "ultrafast"
        render_quality = "medium"

    # ── 并行配置 ──────────────────────────────────────
    parallel = {
        "compress_workers": compress["workers"],
        "cut_workers": min(4, cores // 2) if is_ssd else 2,
        "render_workers": min(2, max(1, cores // 4)),
        "vl_workers": 1,  # VL 受 API 速率限制,跟机器无关
    }

    return {
        "compress": compress,
        "render": {
            "encoder": render_encoder,
            "preset": render_preset,
            "quality": render_quality,
            "crf": 22 if render_quality == "high" else 28,
            "max_dim": 1920 if render_quality == "high" else 1280,
        },
        "parallel": parallel,
        "disk": {
            "is_ssd": is_ssd,
            "type": disk.get("type", "unknown"),
        },
    }


# ═══════════════════════════════════════════════════════════
#  对外接口
# ═══════════════════════════════════════════════════════════

def run_benchmark(force: bool = False) -> dict:
    """
    完整检测+基准测试,生成配置并保存.

    Args:
        force: 强制重新检测（忽略已有配置）

    Returns:
        完整的 hardware_profile dict
    """
    if not force and _PROFILE_PATH.exists():
        try:
            with open(_PROFILE_PATH, encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("version") == 1 and existing.get("pipeline_config"):
                return existing
        except Exception:
            pass  # 配置损坏,重新检测

    print("[硬件画像] 开始检测硬件能力...")
    print("[硬件画像] 检测 CPU...")
    cpu = _detect_cpu()
    print(f"[硬件画像] CPU: {cpu['name'] or 'unknown'} ({cpu['cores_logical']} 逻辑核)")
    print("[硬件画像] 检测 GPU...")
    gpu = _detect_gpu()
    print(f"[硬件画像] GPU: {gpu['name'] or 'unknown'}  编码器: {gpu['hw_encoder'] or '无(纯CPU)'}")
    print("[硬件画像] 检测内存...")
    memory = _detect_memory()
    print(f"[硬件画像] 内存: {memory['total_gb']}GB")
    print("[硬件画像] 检测磁盘...")
    disk = _detect_disk()
    print(f"[硬件画像] 磁盘: {disk['type']}  随机读取: {disk.get('random_read_mb_s', 'N/A')}MB/s")

    print("[硬件画像] 运行编码基准测试(约5秒)...")
    bench = _benchmark_encode(2.0)
    print(f"[硬件画像] 软件编码: {bench['fps_sw']}fps", end="")
    if bench.get("fps_hw"):
        print(f"  硬件编码: {bench['fps_hw']}fps")
    else:
        print()

    profile = {
        "version": 1,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpu": gpu,
        "cpu": cpu,
        "memory": memory,
        "disk": disk,
        "benchmark": bench,
        "pipeline_config": None,  # 下面再填
    }
    profile["pipeline_config"] = _profile_to_pipeline_config(profile)

    # 保存
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_PROFILE_PATH, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    print(f"[硬件画像] OK 配置已保存到 {_PROFILE_PATH}")
    return profile


def load_profile() -> dict | None:
    """加载已保存的硬件配置,没有则返回 None"""
    if not _PROFILE_PATH.exists():
        return None
    try:
        with open(_PROFILE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def first_run_setup() -> dict:
    """
    首次运行设置:硬件检测 + 自适应配置.

    在用户电脑上首次启动时调用,显式展示硬件检测过程,
    让用户看到工具是如何适配其电脑的.

    流程:
      1. 展示欢迎信息
      2. 逐项检测硬件(CPU/GPU/内存/磁盘)
      3. 运行编码基准测试
      4. 生成最优管线配置
      5. 展示配置摘要
      6. 保存配置

    返回:
        完整的 hardware_profile dict
    """
    import sys as _sys

    print("=" * 60)
    print("  剪意 (ClipMind) — 首次运行硬件适配")
    print("  正在检测你的电脑配置,为你量身优化...")
    print("=" * 60)
    print()

    # 调用完整检测流程(已有详细打印)
    profile = run_benchmark(force=True)

    print()
    print("=" * 60)
    print("  ✅ 硬件适配完成! 配置已保存")
    print("=" * 60)
    print()

    # 展示人类可读摘要
    print(profile_summary())

    print()
    print("-" * 60)
    print("  你的电脑配置评估:")
    cfg = profile.get("pipeline_config", {})
    gpu = profile.get("gpu", {})
    mem = profile.get("memory", {})
    disk = profile.get("disk", {})

    # 性能评级
    ratings = []
    if gpu.get("hw_encoder"):
        ratings.append("✅ GPU硬件编码加速: 可用")
    else:
        ratings.append("⚠️ GPU硬件编码: 不可用(使用CPU编码,速度较慢)")

    if mem.get("total_gb", 0) >= 32:
        ratings.append("✅ 内存充足(≥32GB): 可处理4K视频")
    elif mem.get("total_gb", 0) >= 16:
        ratings.append("✅ 内存良好(≥16GB): 可处理1080p视频")
    else:
        ratings.append(f"⚠️ 内存较少({mem.get('total_gb', '?')}GB): 建议关闭其他程序")

    if disk.get("type") == "SSD":
        ratings.append("✅ SSD固态硬盘: 读写速度快")
    else:
        ratings.append("⚠️ 非SSD硬盘: 大文件处理可能较慢")

    for r in ratings:
        print(f"  {r}")

    print()
    compress = cfg.get("compress", {})
    render = cfg.get("render", {})
    parallel = cfg.get("parallel", {})
    print(f"  ▶ 压缩编码: {compress.get('encoder') or 'libx264'}  "
          f"质量={compress.get('cq_or_crf')}  并行路数={compress.get('workers')}")
    print(f"  ▶ 渲染编码: {render.get('encoder')}  质量={render.get('quality')}")
    print(f"  ▶ 并行配置: 压缩{parallel.get('compress_workers')}路  "
          f"裁切{parallel.get('cut_workers')}路  渲染{parallel.get('render_workers')}路")
    print(f"  ▶ 磁盘类型: {disk.get('type', 'unknown')}")
    print()
    print("  💡 如需重新检测,运行: python hardware_profile.py --force")
    print("=" * 60)

    return profile


def get_pipeline_config() -> dict:
    """
    获取最优管线配置.
    有缓存配置则直接返回,没有则自动检测.

    返回格式:
    {
        "compress": {"encoder": ..., "preset": ..., "cq_or_crf": ...},
        "render": {...},
        "parallel": {...},
        "disk": {...},
    }
    """
    profile = load_profile()
    if profile and profile.get("pipeline_config"):
        return profile["pipeline_config"]

    # 首次运行(没有配置) → 显式运行首次设置向导
    profile = first_run_setup()
    return profile["pipeline_config"]


def profile_summary() -> str:
    """生成人类可读的硬件配置摘要"""
    profile = load_profile()
    if not profile:
        return "（尚未检测,首次启动管线时自动检测）"

    cfg = profile.get("pipeline_config", {})
    gpu = profile.get("gpu", {})
    bench = profile.get("benchmark", {})
    compress = cfg.get("compress", {})
    render = cfg.get("render", {})
    parallel = cfg.get("parallel", {})

    lines = [
        "=== 硬件画像 ===",
        f"GPU: {gpu.get('name', 'N/A')}  ({gpu.get('hw_encoder') or '纯CPU'})",
        f"编码速度: 软件 {bench.get('fps_sw', '?')}fps"
            + (f" | 硬件 {bench['fps_hw']}fps" if bench.get('fps_hw') else ""),
        "",
        "=== 管线自适应配置 ===",
        f"压缩: {compress.get('encoder') or 'libx264 ultrafast'} crf={compress.get('cq_or_crf')} "
            f"{compress.get('max_dim')}p  {compress.get('workers')}路并行",
        f"渲染: {render.get('encoder')} {render.get('preset')} "
            f"质量={render.get('quality')}",
        f"并行: 压缩{parallel.get('compress_workers')}路 "
            f"裁切{parallel.get('cut_workers')}路 "
            f"渲染{parallel.get('render_workers')}路",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if "--force" in sys.argv:
        run_benchmark(force=True)
    elif "--setup" in sys.argv:
        first_run_setup()
    else:
        profile = run_benchmark()
        print()
        print(profile_summary())
