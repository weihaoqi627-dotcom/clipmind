"""
从素材缓存中同步 BGM 和音效到项目库
=================================================
工作原理:
  1. 用户在素材应用中浏览/播放音乐时，MP3 文件会被缓存到本地
  2. 此脚本检测缓存变化，将新文件复制到项目的 downloads/music/ 和 downloads/sfx/
  3. 同时从 rp.db 中读取歌曲元数据（标题、作者、时长等）
  4. 支持增量同步，可反复运行

使用方法:
  python scripts/sync_assets.py
  python scripts/sync_assets.py --watch    # 持续监听模式
"""

import sqlite3
import os, sys, json, shutil, time, glob
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────
LOCAL_APP_DATA = os.getenv("LOCALAPPDATA", "")
JY_USER_DATA = os.path.join(LOCAL_APP_DATA, "JianyingPro", "User Data")
JY_CACHE_MUSIC = os.path.join(JY_USER_DATA, "Cache", "music")
JY_CACHE_EFFECT = os.path.join(JY_USER_DATA, "Cache", "effect")
JY_RP_DB = os.path.join(JY_USER_DATA, "Cache", "rp.db")

# 项目目录
SCRIPT_DIR = Path(__file__).parent.parent
DOWNLOADS_DIR = SCRIPT_DIR / "downloads"
MUSIC_DIR = DOWNLOADS_DIR / "music"
SFX_DIR = DOWNLOADS_DIR / "sfx"
SYNC_LOG = DOWNLOADS_DIR / "_sync_log.json"


def ensure_dirs():
    """确保目标目录存在"""
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    SFX_DIR.mkdir(parents=True, exist_ok=True)


def load_sync_log() -> dict:
    """加载同步日志（已同步的文件列表）"""
    if SYNC_LOG.exists():
        try:
            return json.loads(SYNC_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"music": {}, "sfx": {}}


def save_sync_log(log: dict):
    """保存同步日志"""
    SYNC_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def read_music_metadata() -> dict:
    """从 rp.db 读取音乐元数据，返回 {mid: {title, author, duration}}"""
    meta = {}
    if not os.path.exists(JY_RP_DB):
        print("  ⚠ rp.db 不存在，跳过元数据读取")
        return meta
    try:
        conn = sqlite3.connect(JY_RP_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, author, duration FROM music")
        for row in cursor.fetchall():
            mid, title, author, duration = row
            if title:
                meta[str(mid)] = {
                    "title": title,
                    "author": author or "",
                    "duration": duration or 0,
                }
        conn.close()
    except Exception as e:
        print(f"  ⚠ 读取元数据失败: {e}")
    return meta


def get_file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def sync_music() -> int:
    """
    从素材缓存中同步 BGM 到项目库。
    返回新增文件数。
    """
    sync_log = load_sync_log()
    synced_ids = sync_log.get("music", {})
    metadata = read_music_metadata()
    new_count = 0

    # 读取 downLoadcfg
    cfg_path = os.path.join(JY_CACHE_MUSIC, "downLoadcfg")
    cached_files = []
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cached_files = cfg.get("list", [])
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠ 读取 downLoadcfg 失败: {e}")

    # 检查缓存目录中的实际文件
    if os.path.exists(JY_CACHE_MUSIC):
        for fname in os.listdir(JY_CACHE_MUSIC):
            fp = os.path.join(JY_CACHE_MUSIC, fname)
            if os.path.isfile(fp) and fname.endswith(".mp3"):
                # 按文件名追踪
                if fname not in synced_ids or synced_ids[fname] != get_file_size(fp):
                    cached_files.append({"path": fname, "hex": fname.replace(".mp3", "")})

    if not cached_files:
        print("  ℹ 缓存中没有新的音乐文件")
        return 0

    print(f"  缓存中找到 {len(cached_files)} 个文件，正在同步...")

    for item in cached_files:
        mid_hex = item.get("hex", "")
        file_name = item.get("path", "")
        src_path = os.path.join(JY_CACHE_MUSIC, file_name)

        if not os.path.exists(src_path):
            continue

        # 文件名相同且大小未变 → 已同步
        if file_name in synced_ids and synced_ids[file_name] == get_file_size(src_path):
            continue

        # 获取元数据
        meta = metadata.get(mid_hex, {})
        title = meta.get("title", f"jy_music_{mid_hex[:8]}")
        author = meta.get("author", "")

        # 安全文件名
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-()，。").strip()
        if not safe_title:
            safe_title = f"JY_Music_{mid_hex[:8]}"
        if author:
            safe_title = f"{author} - {safe_title}"

        # 避免覆盖
        dst_name = f"{safe_title}.mp3"
        dst_path = MUSIC_DIR / dst_name
        if dst_path.exists():
            base = safe_title[:70]
            idx = 1
            while dst_path.exists():
                dst_name = f"{base}_{idx}.mp3"
                dst_path = MUSIC_DIR / dst_name
                idx += 1

        # 复制文件
        try:
            shutil.copy2(src_path, str(dst_path))
            synced_ids[file_name] = get_file_size(src_path)
            duration = meta.get("duration", 0)
            dur_str = f"{duration//60}:{duration%60:02d}" if duration else "?"
            print(f"  ✅ {safe_title} ({dur_str})")
            new_count += 1
        except Exception as e:
            print(f"  ❌ 复制失败 {file_name}: {e}")

    # 保存同步日志
    sync_log["music"] = synced_ids
    save_sync_log(sync_log)
    return new_count


def sync_sfx() -> int:
    """
    从素材缓存中同步音效文件。
    音效可能以多种形式存在，本函数会扫描 effect 缓存目录，
    尝试识别并复制音效文件。
    """
    sync_log = load_sync_log()
    synced_sfx = sync_log.get("sfx", {})
    new_count = 0

    # 检查 effect 目录 — 每个子目录可能包含音效文件
    if not os.path.exists(JY_CACHE_EFFECT):
        print("  ⚠ 音效缓存目录不存在")
        return 0

    sfx_files_found = 0
    for subdir in sorted(os.listdir(JY_CACHE_EFFECT)):
        subpath = os.path.join(JY_CACHE_EFFECT, subdir)
        if not os.path.isdir(subpath):
            continue
        # 找 hash 子目录
        for inner in os.listdir(subpath):
            inner_path = os.path.join(subpath, inner)
            if not os.path.isdir(inner_path):
                continue
            for fname in os.listdir(inner_path):
                fp = os.path.join(inner_path, fname)
                if not os.path.isfile(fp):
                    continue
                # 检查文件头是否为 MP3 或常见音频格式
                try:
                    with open(fp, "rb") as fh:
                        header = fh.read(3)
                except Exception:
                    continue

                is_audio = False
                ext = ""
                if header[:3] == b"\xff\xfb" or header[:2] == b"\xff\xf3":
                    is_audio, ext = True, ".mp3"
                elif header[:3] == b"Ogg":
                    is_audio, ext = True, ".ogg"
                elif header[:4] == b"RIFF" and fname.endswith(".wav"):
                    is_audio, ext = True, ".wav"
                elif header[:4] == b"ftyp":
                    is_audio, ext = True, ".m4a"

                if is_audio:
                    sfx_files_found += 1
                    fsize = os.path.getsize(fp)
                    # 跳过已同步的同大小文件
                    key = f"{subdir}_{fname}"
                    if key in synced_sfx and synced_sfx[key] == fsize:
                        continue
                    # 复制到音效库
                    safe_name = "".join(c for c in fname if c.isalnum() or c in " _-").strip()
                    safe_name = safe_name.replace(Path(fname).suffix, "")
                    if not safe_name:
                        safe_name = f"jy_sfx_{subdir}"
                    dst_name = f"{safe_name}{ext}"
                    dst_path = SFX_DIR / dst_name
                    try:
                        shutil.copy2(fp, str(dst_path))
                        synced_sfx[key] = fsize
                        new_count += 1
                        print(f"  🔊 {safe_name}{ext} ({fsize//1024}KB)")
                    except Exception as e:
                        print(f"  ❌ 复制失败 {fname}: {e}")

    if sfx_files_found == 0:
        print("  ℹ 缓存中未找到音效文件")
    return new_count


def scan_installed_fonts() -> int:
    """
    检查素材安装目录中的字体，同步到项目。
    返回新增字体数。
    """
    font_src = os.path.join(LOCAL_APP_DATA, "JianyingPro", "Apps",
                            "5.9.0.11632", "Resources", "Font")
    font_dst = SCRIPT_DIR / "downloads" / "fonts"
    font_dst.mkdir(parents=True, exist_ok=True)

    sync_log = load_sync_log()
    synced_fonts = sync_log.get("fonts", {})
    new_count = 0

    # 检查系统字体目录
    for font_dir in [font_src,
                     os.path.join(LOCAL_APP_DATA, "JianyingPro", "User Data", "Resources", "Font")]:
        if not os.path.exists(font_dir):
            continue
        for fname in os.listdir(font_dir):
            if not fname.lower().endswith((".ttf", ".otf")):
                continue
            fp = os.path.join(font_dir, fname)
            fsize = os.path.getsize(fp)
            if fname in synced_fonts and synced_fonts[fname] == fsize:
                continue
            try:
                shutil.copy2(fp, str(font_dst / fname))
                synced_fonts[fname] = fsize
                new_count += 1
                print(f"  🔤 {fname} ({fsize//1024}KB)")
            except Exception as e:
                print(f"  ❌ 复制字体失败 {fname}: {e}")

    sync_log["fonts"] = synced_fonts
    save_sync_log(sync_log)
    return new_count


def watch_mode():
    """
    持续监听模式：每 10 秒检查一次新缓存文件。
    """
    print("\n👀 监听模式已启动，每 10 秒检查一次...")
    print("   浏览/播放音乐即可自动同步\n")
    while True:
        try:
            n_music = sync_music()
            n_sfx = sync_sfx()
            if n_music > 0 or n_sfx > 0:
                # 清除项目音乐库缓存，使新文件立即可用
                _clear_project_music_cache()
                print(f"  ✨ 本次同步: {n_music} 首 BGM + {n_sfx} 个音效\n")
            time.sleep(10)
        except KeyboardInterrupt:
            print("\n  监听已停止")
            break
        except Exception as e:
            print(f"  ⚠ 监听异常: {e}")
            time.sleep(10)


def _clear_project_music_cache():
    """清除项目音乐库缓存，让新文件可被 search_music 搜索到"""
    import sys as _sys
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from director.tools.audio import _clear_music_cache
        _clear_music_cache()
        print("  💿 已刷新项目音乐库缓存")
    except ImportError:
        pass


def main():
    ensure_dirs()
    print("=" * 50)
    print("  剪意 ClipMind — 素材资源同步工具")
    print("=" * 50)
    print(f"\n📂 缓存目录: {JY_CACHE_MUSIC}")
    print(f"📂 目标 BGM 目录: {MUSIC_DIR}")
    print(f"📂 目标 SFX 目录: {SFX_DIR}")
    print()

    if "--watch" in sys.argv:
        watch_mode()
        return

    print("正在同步 BGM...")
    n_music = sync_music()
    print(f"\n正在同步音效...")
    n_sfx = sync_sfx()
    print(f"\n正在同步字体...")
    n_font = scan_installed_fonts()

    total = n_music + n_sfx + n_font
    if total > 0:
        _clear_project_music_cache()

    print(f"\n{'='*50}")
    print(f"  ✅ 同步完成! 新增: {n_music} 首 BGM / {n_sfx} 个音效 / {n_font} 个字体")
    print(f"  💡 提示: 在素材应用中播放音乐后重新运行此脚本获取新缓存")
    print(f"  💡 提示: 使用 --watch 参数启动监听模式，自动同步")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
