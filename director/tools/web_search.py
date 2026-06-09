"""
AI 搜索模块 — DuckDuckGo HTML (curl_cffi TLS 指纹) + open-websearch 兜底

核心策略:
  1. DuckDuckGo HTML 模式 + curl_cffi TLS 指纹伪装（无 API Key）
     — curl_cffi 模拟 Chrome 131 真实 TLS 指纹，绕过搜索引擎反爬检测
     — 同时支持中文和英文，能找到知乎、CSDN、技术博客等文字文章
     — 此方案经测试验证，比 urllib/subprocess+curl 成功率更高
  2. open-websearch 本地守护进程兜底（备选）
  3. 不再用 B站 API（全是视频，没有文字内容，已被用户确认无用）

依赖:
  curl_cffi (pip install curl_cffi) — 提供浏览器 TLS 指纹伪装
"""

import json
import re as _re
import subprocess
import sys
import time
import urllib.parse

try:
    from curl_cffi import requests as _curl_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

# 全局 Session（复用连接池 + TLS 指纹）
_HTTP_SESSION = None

_DAEMON_URL = "http://127.0.0.1:3100"
_DAEMON_PROCESS = None


# ══════════════════════════════════════════
# HTTP 客户端（curl_cffi TLS 指纹伪装）
# ══════════════════════════════════════════


def _get_session():
    """获取全局 curl_cffi Session，带 Chrome TLS 指纹伪装。"""
    global _HTTP_SESSION
    if _HTTP_SESSION is None and _HAS_CURL_CFFI:
        _HTTP_SESSION = _curl_requests.Session(impersonate="chrome131")
    return _HTTP_SESSION


def _http_get(url: str, timeout: int = 15) -> str | None:
    """用 curl_cffi（Chrome TLS 指纹）发 GET 请求。

    相比标准 urllib / 裸 curl：
      - 模拟 Chrome 131 的 TLS 握手指纹（密码套件、TLS 扩展顺序等）
      - 绕过依赖 TLS 指纹检测的搜索引擎反爬（DuckDuckGo、Google 等）
      - 自动处理 Windows 上的 SSL 证书问题
    """
    session = _get_session()
    if not session:
        return None
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        resp = session.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and resp.text:
            return resp.text
        return None
    except Exception:
        return None


def _extract_ddg_html(html: str, limit: int = 8) -> list[dict]:
    """从 DuckDuckGo HTML 响应中提取搜索结果。"""
    results = []

    # DuckDuckGo HTML 模式输出结构:
    #   <div class="result result--..." id="r1-...">
    #     <div class="result__body">
    #       <a class="result__a" href="...">标题</a>
    #       <a class="result__url" href="...">显示URL</a>
    #       <a class="result__snippet">摘要文字</a>
    #
    # 或者:
    #   <div class="result result-default">
    #     <div class="result__body">...</div>
    #   </div>

    # 方案A: 按 result__body 分块（最稳定）
    blocks = _re.findall(
        r'<div class="result__body[^"]*"[^>]*>(.*?)</div>\s*</div>',
        html, _re.DOTALL
    )

    # 方案B: 按 result 外层分块
    if not blocks:
        blocks = _re.findall(
            r'<div class="result[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            html, _re.DOTALL
        )

    # 方案C: 按 article 标签分块（某些 DDG 版本）
    if not blocks:
        blocks = _re.findall(
            r'<article[^>]*>(.*?)</article>',
            html, _re.DOTALL
        )

    for block in blocks[:limit]:
        # 标题
        title_match = _re.search(
            r'<a[^>]*class="result__a[^"]*"[^>]*>(.*?)</a>', block, _re.DOTALL
        )
        if not title_match:
            # 有些版本没有 result__a class
            title_match = _re.search(
                r'<a[^>]*rel="nofollow"[^>]*>(.*?)</a>', block, _re.DOTALL
            )
        if not title_match:
            continue
        title = _re.sub(r'<[^>]+>', "", title_match.group(1)).strip()
        if not title:
            continue

        # 链接 - 从 DDG 跳转链接中提取真实 URL
        link_match = _re.search(
            r'<a[^>]*class="result__url[^"]*"[^>]*href="(.*?)"', block, _re.DOTALL
        )
        if not link_match:
            link_match = _re.search(
                r'<a[^>]*href="(//duckduckgo\.com/l/[^"]*)"', block, _re.DOTALL
            )
        if not link_match:
            link_match = _re.search(
                r'<a[^>]*href="(https?://[^"]+)"', block, _re.DOTALL
            )
        link = link_match.group(1) if link_match else ""

        # 解码 DDG 跳转 URL
        real_url = _decode_ddg_url(link)

        # 摘要
        snippet_match = _re.search(
            r'class="result__snippet[^"]*"[^>]*>(.*?)</(?:a|span|div)>',
            block, _re.DOTALL
        )
        snippet = ""
        if snippet_match:
            snippet = _re.sub(r'<[^>]+>', "", snippet_match.group(1)).strip()

        results.append({
            "title": title,
            "url": real_url or link,
            "description": snippet[:500],
            "engine": "duckduckgo",
        })

    return results


def _decode_ddg_url(link: str) -> str:
    """从 DuckDuckGo 跳转链接中解码真实 URL。"""
    if "uddg=" not in link:
        return link
    try:
        parsed = urllib.parse.urlparse(link)
        qd = urllib.parse.parse_qs(parsed.query)
        if "uddg" in qd:
            return qd["uddg"][0]
    except Exception:
        pass
    return link


def search_ddg(query: str, limit: int = 8) -> list[dict]:
    """通过 DuckDuckGo HTML 模式搜索（主力，无 API Key 要求）。

    使用 curl_cffi 的 Chrome TLS 指纹伪装绕过搜索引擎反爬。
    同时支持中文和英文查询，能找到知乎、CSDN、博客等文字内容。
    经过验证，中文搜索可找到知乎专栏文章、CSDN 博客等技术文章。
    """
    encoded = urllib.parse.quote(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"

    html = _http_get(url)
    if not html:
        return []

    results = _extract_ddg_html(html, limit=limit)
    return results


def search_ddg_text(query: str, limit: int = 8) -> str:
    """搜索 DuckDuckGo 并返回格式化文本（给 LLM 直接用）。"""
    results = search_ddg(query, limit=limit)
    if not results:
        return ""

    # 按站点类型分组
    text_articles = []
    video_results = []
    for r in results:
        url = r.get("url", "")
        domain = urllib.parse.urlparse(url).netloc if url else ""
        is_video = any(
            v in domain
            for v in ["youtube.com", "bilibili.com", "douyin.com", "tiktok.com"]
        )
        if is_video:
            video_results.append(r)
        else:
            text_articles.append(r)

    parts = [f"🔍 DuckDuckGo搜索结果（共{len(results)}条）:", ""]

    # 先列文字文章
    if text_articles:
        if video_results:
            parts.append("📄 文字文章:")
        for i, r in enumerate(text_articles, 1):
            title = r.get("title", "").strip()
            desc = (r.get("description", "") or "").strip()[:400]
            url = r.get("url", "")
            parts.append(f"{i}. {title}")
            if desc:
                parts.append(f"   {desc}")
            if url:
                parts.append(f"   {url}")
            parts.append("")

    # 再列视频结果
    if video_results:
        parts.append(f"🎬 视频（{len(video_results)}条）:")
        for i, r in enumerate(video_results, 1):
            title = r.get("title", "").strip()
            url = r.get("url", "")
            parts.append(f"{i}. {title}")
            if url:
                parts.append(f"   {url}")
            parts.append("")

    return "\n".join(parts).strip()


# ══════════════════════════════════════════
# open-websearch 守护进程（兜底）
# ══════════════════════════════════════════


def ensure_daemon() -> str:
    """确保 open-websearch 守护进程在运行，返回 base URL。"""
    if _check_daemon():
        return _DAEMON_URL
    _start_daemon_background()
    for i in range(6):
        time.sleep(1)
        if _check_daemon():
            return _DAEMON_URL
    return None


def _check_daemon() -> bool:
    try:
        req = urllib.request.Request(f"{_DAEMON_URL}/health", method="GET")
        urllib.request.urlopen(req, timeout=2)
        return True
    except Exception:
        return False


def _start_daemon_background():
    global _DAEMON_PROCESS
    if _DAEMON_PROCESS is not None:
        try:
            _DAEMON_PROCESS.poll()
            if _DAEMON_PROCESS.returncode is None:
                return
        except Exception:
            pass
    try:
        _DAEMON_PROCESS = subprocess.Popen(
            [sys.executable, "-m", "open_websearch", "serve",
             "--port", "3100", "--host", "127.0.0.1"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except FileNotFoundError:
        try:
            _DAEMON_PROCESS = subprocess.Popen(
                ["npx", "open-websearch", "serve",
                 "--port", "3100", "--host", "127.0.0.1"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except FileNotFoundError:
            _DAEMON_PROCESS = None


def stop_daemon():
    global _DAEMON_PROCESS
    if _DAEMON_PROCESS is not None:
        try:
            _DAEMON_PROCESS.terminate()
            _DAEMON_PROCESS.wait(timeout=5)
        except Exception:
            try:
                _DAEMON_PROCESS.kill()
            except Exception:
                pass
        _DAEMON_PROCESS = None


def search_daemon(query: str, engine: str = "duckduckgo", limit: int = 8) -> list[dict]:
    """通过 open-websearch 本地守护进程搜索（兜底方案）。"""
    if not _check_daemon():
        _start_daemon_background()
        for i in range(6):
            time.sleep(1)
            if _check_daemon():
                break
        else:
            return []

    try:
        body = json.dumps({
            "query": query, "engines": [engine], "limit": limit,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{_DAEMON_URL}/search",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("status") == "ok":
            raw_results = data.get("data", {}).get("results", [])
            if raw_results:
                return raw_results
    except Exception:
        pass
    return []


def search_daemon_text(query: str, engine: str = "duckduckgo", limit: int = 8) -> str:
    """守护进程搜索并返回格式化文本。"""
    results = search_daemon(query, engine=engine, limit=limit)
    if not results:
        return ""

    engine_name = {"duckduckgo": "DuckDuckGo", "bing": "Bing"}.get(engine, engine)
    parts = [f"🌐 {engine_name}搜索结果（共{len(results)}条）:", ""]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        desc = (r.get("description", "") or "").strip()[:500]
        url = r.get("url", "")
        source = r.get("source", "")
        parts.append(f"{i}. {title}")
        if desc:
            parts.append(f"   {desc}")
        if source:
            parts.append(f"   来源: {source}")
        elif url:
            parts.append(f"   链接: {url}")
        parts.append("")
    return "\n".join(parts).strip()


# ══════════════════════════════════════════
# B站 专栏文章搜索（只搜文章，不搜视频）
# ══════════════════════════════════════════


def search_bilibili_article(query: str, limit: int = 5) -> list[dict]:
    """搜索 B站 专栏文章（不是视频）。

    注意：B站 专栏/文章内容较少，通常搜不到结果。
    此函数仅作为兜底方案，且只返回专栏文章，绝不返回视频。

    使用 B站搜索 API，只筛选 article 类型。
    """
    import urllib.parse, urllib.request

    encoded = urllib.parse.quote(query)
    # B站搜索 API — 指定 search_type=article 只搜专栏
    url = f"https://api.bilibili.com/x/web-interface/search/type?search_type=article&keyword={encoded}&page=1"

    session = _get_session()
    if not session:
        return []

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://search.bilibili.com/",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        resp = session.get(url, headers=headers, timeout=15)
        if resp.status_code != 200 or not resp.text:
            return []

        data = resp.json()
        if data.get("code") != 0:
            return []

        articles = data.get("data", {}).get("result", [])
        if not articles:
            return []

        results = []
        for art in articles[:limit]:
            title = art.get("title", "")
            # B站 API 标题带 <em> 标签
            title = _re.sub(r"<[^>]+>", "", title).strip()
            desc = (art.get("description", "") or "").strip()[:300]
            # 专栏链接格式: https://www.bilibili.com/read/cv...
            rid = art.get("id", art.get("rid", ""))
            url = f"https://www.bilibili.com/read/cv{rid}" if rid else ""

            results.append({
                "title": title,
                "url": url,
                "description": desc,
                "engine": "bilibili_article",
            })

        return results
    except Exception:
        pass

    # API 失败，尝试 HTML 页面（但有 JS 渲染问题，成功率低）
    try:
        html_url = f"https://search.bilibili.com/article?keyword={encoded}"
        resp = session.get(html_url, headers=headers, timeout=15)
        if resp.status_code == 200 and resp.text:
            html = resp.text
            results = []
            # 尝试从 HTML 中提取专栏卡片
            # B站 article 搜索结果卡片的 URL 模式: //www.bilibili.com/read/cv...
            cards = _re.findall(
                r'<a[^>]*href="(//www\.bilibili\.com/read/cv\d+)"[^>]*>(.*?)</a>',
                html, _re.DOTALL
            )
            for href, title_html in cards[:limit]:
                title = _re.sub(r"<[^>]+>", "", title_html).strip()
                if title:
                    results.append({
                        "title": title,
                        "url": "https:" + href,
                        "description": "",
                        "engine": "bilibili_article",
                    })
            return results
    except Exception:
        pass

    return []


def search_bilibili_article_text(query: str, limit: int = 5) -> str:
    """搜索 B站 专栏文章并返回格式化文本。"""
    results = search_bilibili_article(query, limit=limit)
    if not results:
        return ""

    parts = [f"📝 B站专栏文章搜索结果（共{len(results)}条）:", ""]
    for i, r in enumerate(results, 1):
        title = r.get("title", "").strip()
        desc = (r.get("description", "") or "").strip()[:300]
        url = r.get("url", "")
        parts.append(f"{i}. {title}")
        if desc:
            parts.append(f"   {desc}")
        if url:
            parts.append(f"   {url}")
        parts.append("")
    return "\n".join(parts).strip()


# ══════════════════════════════════════════
# 统一的组合搜索（主入口）
# ══════════════════════════════════════════


def search_all(query: str, engine: str = "duckduckgo", limit: int = 8) -> str:
    """多引擎组合搜索 — 主入口。

    优先级:
      1. DuckDuckGo HTML 直连（主力，无 API Key 要求）
         — 同时支持中英文，能找到文字文章（知乎、CSDN 等）
      2. open-websearch 守护进程兜底（英文查询专用）
      3. B站 专栏文章搜索（兜底，只搜文章不搜视频）
      4. 失败消息

    Args:
        query: 搜索关键词（中英文均可）
        engine: 兜底引擎 (duckduckgo/bing)
        limit: 结果数量

    Returns:
        格式化的搜索结果文本，区分文字文章和视频来源
    """
    # 方法1: DuckDuckGo HTML 直连（主力）
    result = search_ddg_text(query, limit=limit)
    if result:
        return result

    # 方法2: open-websearch 守护进程兜底
    # 主要对英文查询有效，中文查询 DDG 已经兜底了
    result = search_daemon_text(query, engine=engine, limit=limit)
    if result:
        return result

    # 方法3: B站 专栏文章搜索（兜底，只搜文章不搜视频）
    result = search_bilibili_article_text(query, limit=limit)
    if result:
        return result

    return "(搜索无结果)"
