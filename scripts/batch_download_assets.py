"""
从素材缓存中批量下载音效和 BGM 到项目库
=============================================
直接从 rp.db 的 http_cache 中提取下载链接，批量下载所有资源。
支持断点续传（已下载的不重复下载）。

使用方法:
  python scripts/batch_download_assets.py
"""

import sqlite3
import json, os, re, sys, time, hashlib
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

LOCAL_APP_DATA = os.getenv("LOCALAPPDATA", "")
JY_USER_DATA = os.path.join(LOCAL_APP_DATA, "JianyingPro", "User Data")
JY_RP_DB = os.path.join(JY_USER_DATA, "Cache", "rp.db")

SCRIPT_DIR = Path(__file__).parent.parent
SFX_DIR = SCRIPT_DIR / "downloads" / "sfx"
MUSIC_DIR = SCRIPT_DIR / "downloads" / "music"
SYNC_LOG = SCRIPT_DIR / "downloads" / "_sync_log.json"


def ensure_dirs():
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)


def load_sync_log() -> dict:
    if SYNC_LOG.exists():
        try:
            return json.loads(SYNC_LOG.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"music": {}, "sfx": {}}


def save_sync_log(log: dict):
    SYNC_LOG.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def download_file(url: str, dst_path: Path, timeout: int = 30) -> bool:
    """下载文件，返回是否成功"""
    try:
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        data = urlopen(req, timeout=timeout).read()
        dst_path.write_bytes(data)
        return True
    except Exception as e:
        print(f"    ⚠ 下载失败: {e}")
        return False


def extract_sfx_from_cache() -> list[dict]:
    """从 http_cache 提取所有音效的下载信息"""
    if not os.path.exists(JY_RP_DB):
        print("  ⚠ rp.db 不存在")
        return []

    conn = sqlite3.connect(JY_RP_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT url, response_body FROM http_cache WHERE url LIKE '%_audio_%'")

    items = []
    seen_ids = set()
    for url, body in cursor.fetchall():
        try:
            data = json.loads(body)
            item_list = data.get("data", {}).get("effect_item_list", [])
        except (json.JSONDecodeError, AttributeError):
            continue

        for item in item_list:
            attr = item.get("common_attr", {})
            effect_id = attr.get("effect_id", "")
            if effect_id in seen_ids:
                continue
            seen_ids.add(effect_id)

            download_info = attr.get("download_info", {})
            dl_url = download_info.get("url", "") if isinstance(download_info, dict) else ""

            if not dl_url:
                continue

            title = attr.get("title", "未知音效")
            author_info = item.get("author", {})
            author = author_info.get("name", "") if isinstance(author_info, dict) else ""
            audio_effect = item.get("audio_effect", {})
            duration_ms = audio_effect.get("duration_ms", 0) if isinstance(audio_effect, dict) else 0
            md5 = attr.get("md5", "")

            items.append({
                "id": effect_id,
                "title": title,
                "author": author,
                "duration_ms": duration_ms,
                "url": dl_url,
                "md5": md5,
            })
    conn.close()
    return items


def download_sfx(items: list[dict]) -> int:
    """批量下载音效"""
    sync_log = load_sync_log()
    synced = sync_log.get("sfx", {})
    new_count = 0
    total = len(items)

    print(f"  共找到 {total} 个音效")

    for idx, item in enumerate(items, 1):
        title = item["title"]
        author = item["author"]
        dl_url = item["url"]
        md5 = item.get("md5", "")

        # 用 md5 或 id 作为唯一标识
        file_key = md5 or item["id"]
        if file_key in synced:
            continue

        # 安全文件名
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-()，。、·").strip()
        safe_title = re.sub(r'[<>:"/\\|?*]', "", safe_title)
        if not safe_title:
            safe_title = f"sfx_{item["id"][:8]}"
        if author:
            safe_title = f"{safe_title}"

        # 确定扩展名（从 URL 猜测）
        url_lower = dl_url.lower()
        if ".mp3" in url_lower:
            ext = ".mp3"
        elif ".wav" in url_lower or ".wav?" in url_lower:
            ext = ".wav"
        elif ".m4a" in url_lower:
            ext = ".m4a"
        elif ".ogg" in url_lower:
            ext = ".ogg"
        else:
            ext = ".mp3"  # 默认

        dst_name = f"{safe_title}{ext}"
        dst_path = SFX_DIR / dst_name

        # 处理重名
        if dst_path.exists():
            base = safe_title[:70]
            n = 1
            while dst_path.exists():
                dst_name = f"{base}_{n}{ext}"
                dst_path = SFX_DIR / dst_name
                n += 1

        # 下载
        print(f"  [{idx}/{total}] {safe_title} ...", end=" ", flush=True)
        success = download_file(dl_url, dst_path)
        if success:
            fsize = dst_path.stat().st_size
            synced[file_key] = {
                "file": dst_name,
                "size": fsize,
                "title": title,
                "author": author,
            }
            new_count += 1
            dur_str = f"{item['duration_ms']//1000}s" if item['duration_ms'] else "?"
            print(f"✅ {fsize//1024}KB ({dur_str})")
        else:
            print("❌")

        # 每 20 个保存一次进度
        if new_count % 20 == 0:
            sync_log["sfx"] = synced
            save_sync_log(sync_log)

    # 最终保存
    sync_log["sfx"] = synced
    save_sync_log(sync_log)
    return new_count


def generate_metadata():
    """生成 _metadata.json 供 search_sfx 使用"""
    metadata_path = SFX_DIR / "_metadata.json"
    items = []
    for fname in sorted(os.listdir(str(SFX_DIR))):
        if not fname.endswith((".mp3", ".wav", ".m4a", ".ogg")):
            continue
        fp = SFX_DIR / fname
        items.append({
            "file": fname,
            "title": Path(fname).stem,
            "author": "",
            "duration_ms": 0,
            "file_size": fp.stat().st_size,
            "md5": Path(fname).stem,
        })
    metadata_path.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return len(items)


def extract_bgm_from_cache() -> list[dict]:
    """从 http_cache 提取所有 BGM 的下载信息"""
    if not os.path.exists(JY_RP_DB):
        print("  ⚠ rp.db 不存在")
        return []

    conn = sqlite3.connect(JY_RP_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT url, response_body FROM http_cache WHERE url LIKE '%get_collection_songs%'")

    items = []
    seen_ids = set()
    for url, body in cursor.fetchall():
        try:
            data = json.loads(body)
            songs = data.get("data", {}).get("songs", [])
        except (json.JSONDecodeError, AttributeError):
            continue

        for song in songs:
            sid = song.get("id") or song.get("web_id", "")
            if not sid or sid in seen_ids:
                continue
            seen_ids.add(sid)

            preview_url = song.get("preview_url", "")
            if not preview_url:
                continue

            items.append({
                "id": str(sid),
                "title": song.get("title", "未知歌曲"),
                "author": song.get("author", ""),
                "duration": song.get("duration", 0),
                "url": preview_url,
            })
    conn.close()
    return items


def download_bgm(items: list[dict]) -> int:
    """批量下载 BGM"""
    sync_log = load_sync_log()
    synced = sync_log.get("music", {})
    new_count = 0
    total = len(items)

    print(f"  共找到 {total} 首 BGM")

    for idx, item in enumerate(items, 1):
        title = item["title"]
        author = item["author"]
        dl_url = item["url"]
        sid = item["id"]

        if sid in synced:
            continue

        # 安全文件名
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-()，。").strip()
        if not safe_title:
            safe_title = f"bgm_{sid[:8]}"
        safe_author = author.strip() if author else ""
        # 过滤作者名中的非法字符
        safe_author = re.sub(r'[<>:"/\\|?*\t]', ' ', safe_author).strip()
        if safe_author:
            safe_title = f"{safe_author} - {safe_title}"

        dst_name = f"{safe_title}.mp3"
        dst_path = MUSIC_DIR / dst_name

        # 处理重名
        if dst_path.exists():
            base = safe_title[:70]
            n = 1
            while dst_path.exists():
                dst_name = f"{base}_{n}.mp3"
                dst_path = MUSIC_DIR / dst_name
                n += 1

        # 下载
        print(f"  [{idx}/{total}] {safe_title} ...", end=" ", flush=True)
        success = download_file(dl_url, dst_path)
        if success:
            fsize = dst_path.stat().st_size
            synced[sid] = {
                "file": dst_name,
                "size": fsize,
                "title": title,
                "author": author,
            }
            new_count += 1
            dur = item.get("duration", 0)
            dur_str = f"{dur//60}:{dur%60:02d}" if dur else "?"
            print(f"✅ {fsize//1024}KB ({dur_str})")
        else:
            print("❌")

        # 每 20 个保存一次进度
        if new_count % 20 == 0:
            sync_log["music"] = synced
            save_sync_log(sync_log)

    sync_log["music"] = synced
    save_sync_log(sync_log)
    return new_count


def main():
    import sys
    ensure_dirs()
    print("=" * 50)
    print("  剪意 ClipMind — 素材资源批量下载工具")
    print("=" * 50)

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"

    if mode in ("all", "sfx"):
        print(f"\n📂 音效目录: {SFX_DIR}")
        print()
        print("正在提取音效列表...")
        sfx_items = extract_sfx_from_cache()
        if sfx_items:
            print(f"\n开始下载 {len(sfx_items)} 个音效...")
            n = download_sfx(sfx_items)
            print(f"\n✅ 音效下载完成: 新增 {n} 个")
        else:
            print("  ⚠ 未找到音效数据")
        generate_metadata()

    if mode in ("all", "bgm"):
        print(f"\n📂 BGM 目录: {MUSIC_DIR}")
        print()
        print("正在提取 BGM 列表...")
        bgm_items = extract_bgm_from_cache()
        if bgm_items:
            print(f"\n开始下载 {len(bgm_items)} 首 BGM...")
            n = download_bgm(bgm_items)
            print(f"\n✅ BGM 下载完成: 新增 {n} 首")
        else:
            print("  ⚠ 未找到 BGM 数据")

    # 刷新项目音乐库缓存
    try:
        import sys as _sys
        _sys.path.insert(0, str(SCRIPT_DIR))
        from director.tools.audio import _clear_music_cache
        _clear_music_cache()
        print("  💿 已刷新项目音乐库缓存")
    except ImportError:
        pass

    print(f"\n{'='*50}")
    print(f"  完成！")
    print(f"  💡 使用 search_music() / search_sfx() 查看")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
