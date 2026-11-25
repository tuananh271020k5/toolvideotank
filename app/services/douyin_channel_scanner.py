"""Douyin Channel & Video Scanner Engine - Bóc tách danh sách video Douyin kèm metadata.

Tính năng:
- Bắt trực tiếp API aweme/v1/web/aweme/post & aweme/v1/web/aweme/detail qua Playwright Stealth Headless
- Quét HOÀN TOÀN NGẦM (Headless 100%), KHÔNG BẬT CỬA SỔ TRÌNH DUYỆT RA MÀN HÌNH
- Hỗ trợ cả Link video đơn lẻ (v.douyin.com, video/ID, modal_id) và Link kênh cá nhân (/user/...)
- Tự động bóc tách link sạch từ chuỗi chia sẻ dài của Douyin
- Tự động nhận diện has_more để dừng cuộn ngay khi hết video
- Hỗ trợ chọn Trình duyệt (Cốc Cốc, Chrome, Edge) và Profile cụ thể
- Tự động phát hiện Cookie hết hạn / "服务异常" và báo cho người dùng lấy lại cookie
- Chặn stream media nặng trong browser để chống giật lag và tiết kiệm băng thông tối đa
"""

import os
import re
import sys
from typing import Callable, Optional, Dict, Any, List, Tuple

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def scan_douyin_channel(
    channel_url: str,
    max_videos: int = 0,  # 0: Không giới hạn (quét sạch toàn bộ kênh)
    browser_id: str = "chrome",
    profile_id: Optional[str] = None,
    browser_exe_path: Optional[str] = None,
    progress_cb: Optional[Callable[[str, int], None]] = None,
    video_found_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    is_cancelled_cb: Optional[Callable[[], bool]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Quét toàn bộ video của kênh Douyin hoặc video đơn lẻ hoàn toàn chạy ngầm (Headless).

    Returns:
        (videos_list, channel_info_dict)
    """
    from app.services.cookie_storage_service import get_saved_douyin_cookies
    from app.services.video_downloader_service import extract_url_from_text, _resolve_douyin_short_url
    from playwright.sync_api import sync_playwright

    cookies, _, ua = get_saved_douyin_cookies()

    # 1. Trích xuất URL sạch từ chuỗi chia sẻ hoặc liên kết nhập vào
    raw_input = extract_url_from_text(channel_url)
    if "v.douyin.com" in raw_input.lower():
        resolved = _resolve_douyin_short_url(raw_input)
        clean_url = resolved or raw_input
    else:
        clean_url = raw_input.split("?")[0] if "modal_id=" not in raw_input else raw_input

    # Nhận diện nếu đây là liên kết video đơn lẻ
    is_single_video = bool(re.search(r'/(?:video|note)/(\d+)', clean_url) or "modal_id=" in clean_url)

    all_videos: Dict[str, Dict[str, Any]] = {}
    channel_info = {
        "name": "",
        "url": clean_url,
        "total": 0,
        "avatar": "",
        "error_type": "",
    }
    has_more_videos = True
    cookie_expired_detected = False

    def _log(msg: str):
        print(f"[DouyinScanner] {msg}")
        if progress_cb:
            progress_cb(msg, len(all_videos))

    def _process_aweme_item(item: dict) -> Optional[Dict[str, Any]]:
        nonlocal channel_info
        if not item or not isinstance(item, dict):
            return None
        aid = str(item.get("aweme_id") or "")
        if not aid or aid in all_videos:
            return None

        video_obj = item.get("video", {})
        cover = ""
        if video_obj.get("cover") and video_obj["cover"].get("url_list"):
            cover = video_obj["cover"]["url_list"][0]
        elif video_obj.get("origin_cover") and video_obj["origin_cover"].get("url_list"):
            cover = video_obj["origin_cover"]["url_list"][0]

        author = item.get("author", {}).get("nickname", "")
        if author and not channel_info["name"]:
            channel_info["name"] = author
        if item.get("author", {}).get("avatar_thumb", {}).get("url_list"):
            channel_info["avatar"] = item["author"]["avatar_thumb"]["url_list"][0]

        stats = item.get("statistics", {})

        # Direct video CDN stream url (No Watermark)
        direct_play_url = ""
        play_addr = video_obj.get("play_addr", {})
        if play_addr.get("url_list"):
            direct_play_url = play_addr["url_list"][0]
        elif video_obj.get("download_addr", {}).get("url_list"):
            direct_play_url = video_obj["download_addr"]["url_list"][0]

        desc = str(item.get("desc", "") or "").strip()

        v_entry = {
            "id": aid,
            "title": desc or f"Video {len(all_videos) + 1}",
            "url": f"https://www.douyin.com/video/{aid}",
            "direct_url": direct_play_url,
            "cover": cover,
            "likes": stats.get("digg_count", 0),
            "views": stats.get("play_count", 0),
            "comments": stats.get("comment_count", 0),
            "shares": stats.get("share_count", 0),
            "author": author or channel_info["name"] or "Douyin Creator",
            "duration": video_obj.get("duration", 0),
            "create_time": item.get("create_time", 0),
            "status": "Chờ tải",
        }
        all_videos[aid] = v_entry
        if video_found_cb:
            video_found_cb(v_entry)
        return v_entry

    _log("Đang khởi tạo trình duyệt quét ngầm (Headless)...")

    with sync_playwright() as p:
        browser = None
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--window-size=1200,800",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=ProfilePicker,PrivacySandboxSettings4",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
        ]
        if profile_id and profile_id != "new_session":
            launch_args.append(f"--profile-directory={profile_id}")

        # QUAN TRỌNG: Headless = True để chạy ngầm 100%, không bao giờ bật cửa sổ trình duyệt đè lên app
        launch_kwargs = {
            "headless": True,
            "args": launch_args,
        }

        # 1. Thử theo đường dẫn browser_exe_path (Cốc Cốc / Chrome)
        if browser_exe_path and os.path.exists(browser_exe_path):
            launch_kwargs["executable_path"] = browser_exe_path
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as e:
                _log(f"Không thể mở browser bằng exe_path ({e}), thử channel...")

        # 2. Thử theo channel name
        if not browser:
            channel_map = {"chrome": "chrome", "edge": "msedge", "coccoc": None}
            ch_name = channel_map.get(browser_id, "chrome")
            for candidate in [ch_name, "chrome", "msedge", None]:
                try:
                    kw = dict(launch_kwargs)
                    kw.pop("executable_path", None)
                    if candidate:
                        kw["channel"] = candidate
                    browser = p.chromium.launch(**kw)
                    break
                except Exception:
                    continue

        if not browser:
            raise RuntimeError("Không thể khởi động trình duyệt quét ngầm! Vui lòng kiểm tra Google Chrome hoặc Edge trên máy.")

        context = browser.new_context(
            viewport={"width": 1200, "height": 800},
            locale="zh-CN",
            user_agent=ua or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )

        # Nạp ATP cookies đã lưu
        pw_cookies = []
        for c in cookies:
            domain = c.get("domain", ".douyin.com")
            if not domain.startswith("."):
                domain = "." + domain
            pw_cookies.append({
                "name": c["name"],
                "value": c["value"],
                "domain": domain,
                "path": c.get("path", "/"),
            })
        try:
            context.add_cookies(pw_cookies)
            _log(f"Đã nạp {len(pw_cookies)} cookies vào phiên quét")
        except Exception as e:
            _log(f"Cảnh báo nạp cookie: {e}")

        def handle_response(response):
            nonlocal has_more_videos, cookie_expired_detected
            u = response.url or ""

            # 1. Bắt danh sách video của kênh (User post feed)
            if "aweme/v1/web/aweme/post" in u or "aweme/post" in u:
                try:
                    data = response.json()
                    status_code = data.get("status_code", 0)
                    status_msg = str(data.get("status_msg", "")).lower()
                    if status_code != 0 or "login" in status_msg:
                        _log(f"Douyin API báo mã lỗi {status_code}: {data.get('status_msg')}")
                        if status_code in [403, 10001] or "login" in status_msg or "cookie" in status_msg:
                            cookie_expired_detected = True

                    aweme_list = data.get("aweme_list", [])
                    api_has_more = data.get("has_more")
                    if api_has_more is False or api_has_more == 0:
                        has_more_videos = False
                        _log("Douyin API báo cáo: Đã lấy trọn vẹn danh sách video của kênh.")

                    new_in_page = 0
                    for it in aweme_list:
                        if _process_aweme_item(it):
                            new_in_page += 1

                    if new_in_page > 0:
                        _log(f"Bắt được {new_in_page} video (Tổng: {len(all_videos)})")
                except Exception:
                    pass

            # 2. Bắt video đơn lẻ (Video detail API)
            if "aweme/v1/web/aweme/detail" in u or "aweme/detail" in u:
                try:
                    data = response.json()
                    detail = data.get("aweme_detail")
                    if detail:
                        _process_aweme_item(detail)
                        _log("Đã trích xuất thông tin video Douyin.")
                except Exception:
                    pass

            # 3. Lấy tên tác giả từ profile
            if "user/profile" in u or "user/other" in u:
                try:
                    data = response.json()
                    user = data.get("user", {})
                    if user.get("nickname") and not channel_info["name"]:
                        channel_info["name"] = user["nickname"]
                except Exception:
                    pass

        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page.on("response", handle_response)

        # Chặn stream media video nặng để chống lag và tiết kiệm CPU/RAM
        def block_media_stream(route):
            if route.request.resource_type in ["media"]:
                route.abort()
            else:
                route.continue_()
        page.route("**/*", block_media_stream)

        # Đi THẲNG vào URL mục tiêu, TUYỆT ĐỐI không qua trang chủ douyin.com
        _log(f"Đang kết nối ngầm tới: {clean_url}")
        try:
            page.goto(clean_url, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            _log(f"Thông báo tải trang: {e}")

        page.wait_for_timeout(2500)

        # =====================================================================
        # NHÁNH 1: BÓC TÁCH VIDEO ĐƠN LẺ (SINGLE VIDEO)
        # =====================================================================
        if is_single_video:
            # Chờ thêm tối đa 3 giây nếu detail API chưa kịp phản hồi
            if not all_videos:
                page.wait_for_timeout(2000)

            # Quét RENDER_DATA nếu detail API bị miss
            if not all_videos:
                render_res = page.evaluate("""() => {
                    const s = document.querySelector('script#RENDER_DATA');
                    if (!s) return null;
                    try {
                        const data = JSON.parse(decodeURIComponent(s.textContent));
                        let found = null;
                        const scan = (o, depth) => {
                            if (found || depth > 8 || !o || typeof o !== 'object') return;
                            if (Array.isArray(o)) { o.forEach(x => scan(x, depth + 1)); return; }
                            if (o.aweme_id && typeof o.aweme_id === 'string' && o.aweme_id.length > 5) {
                                found = o;
                                return;
                            }
                            Object.values(o).forEach(v => scan(v, depth + 1));
                        };
                        scan(data, 0);
                        return found;
                    } catch(e) { return null; }
                }""")
                if render_res:
                    _process_aweme_item(render_res)

            # Quét thẻ meta hoặc title nếu vẫn chưa có
            if not all_videos:
                page_title = page.title() or ""
                vid_match = re.search(r'/(?:video|note)/(\d+)', clean_url) or re.search(r'modal_id=(\d+)', clean_url)
                if vid_match:
                    vid_id = vid_match.group(1)
                    clean_title = page_title.split(" - 抖音")[0].split("的主页")[0].strip()
                    fallback_entry = {
                        "id": vid_id,
                        "title": clean_title or f"Douyin Video {vid_id}",
                        "url": f"https://www.douyin.com/video/{vid_id}",
                        "direct_url": "",
                        "cover": "",
                        "likes": 0,
                        "views": 0,
                        "comments": 0,
                        "shares": 0,
                        "author": channel_info["name"] or "Douyin Creator",
                        "duration": 0,
                        "create_time": 0,
                        "status": "Chờ tải",
                    }
                    all_videos[vid_id] = fallback_entry
                    if video_found_cb:
                        video_found_cb(fallback_entry)

            browser.close()
            videos_list = list(all_videos.values())
            channel_info["total"] = len(videos_list)
            _log(f"✅ Hoàn tất trích xuất video đơn lẻ: {len(videos_list)} video")
            return videos_list, channel_info

        # =====================================================================
        # NHÁNH 2: QUÉT KÊNH CÁ NHÂN (CHANNEL PAGE)
        # =====================================================================
        # Tự động bấm '刷新' nếu gặp thông báo dịch vụ
        for retry_idx in range(2):
            has_err = page.evaluate("() => document.body && (document.body.innerText.includes('服务异常') || document.body.innerText.includes('重新刷新'))")
            if has_err:
                _log(f"Douyin thông báo cần làm mới, đang tự động bấm '刷新' (lần {retry_idx + 1})...")
                page.evaluate("""() => {
                    const btn = Array.from(document.querySelectorAll('span, a, button')).find(e => e.innerText && e.innerText.trim() === '刷新');
                    if (btn) btn.click();
                }""")
                page.wait_for_timeout(2500)
            else:
                break

        # Kiểm tra cảnh báo lỗi dịch vụ / hết hạn cookie
        body_text = page.evaluate("() => document.body ? document.body.innerText.substring(0, 10000) : ''")
        if "服务异常" in body_text or "重新刷新拉取数据" in body_text:
            _log("⚠️ Phát hiện Douyin báo '服务异常' -> Cookie đã hết hạn hoặc phiên đăng nhập không hợp lệ!")
            cookie_expired_detected = True

        # Quét trước RENDER_DATA nếu có video render sẵn
        render_videos = page.evaluate("""() => {
            const s = document.querySelector('script#RENDER_DATA');
            if (!s) return null;
            try {
                const data = JSON.parse(decodeURIComponent(s.textContent));
                const list = [];
                let chName = '';
                const scan = (o, depth) => {
                    if (depth > 8 || !o || typeof o !== 'object') return;
                    if (Array.isArray(o)) { o.forEach(x => scan(x, depth + 1)); return; }
                    if (o.aweme_id && typeof o.aweme_id === 'string' && o.aweme_id.length > 5) {
                        list.push(o);
                    }
                    if (o.nickname && !chName) chName = o.nickname;
                    Object.values(o).forEach(v => scan(v, depth + 1));
                };
                scan(data, 0);
                return {list, chName};
            } catch(e) { return null; }
        }""")

        if render_videos and render_videos.get("list"):
            if not channel_info["name"]:
                channel_info["name"] = render_videos.get("chName", "")
            for raw_it in render_videos["list"]:
                _process_aweme_item(raw_it)

        # Nhận diện tên kênh
        if not channel_info["name"]:
            h1 = page.evaluate("() => document.querySelector('h1')?.innerText?.trim() || ''")
            if h1:
                channel_info["name"] = h1
            else:
                t = page.title()
                if "的主页" in t:
                    channel_info["name"] = t.split("的主页")[0].strip()
                elif " - 抖音" in t:
                    channel_info["name"] = t.split(" - 抖音")[0].strip()

        _log(f"Kênh: '{channel_info['name']}'. Đang quét ngầm danh sách video...")

        # Xóa các overlay cản trở
        page.evaluate("""() => {
            document.querySelectorAll('[class*="login-guide"], [class*="LoginGuide"], [class*="modal"], [class*="mask"]').forEach(el => {
                try { el.remove(); } catch(e) {}
            });
        }""")

        # Nhận diện số lượng video ước tính từ Tab tiêu đề (ví dụ: "作品 181" hoặc "Video 181")
        channel_video_count = 0
        try:
            channel_video_count = page.evaluate("""() => {
                const tabs = Array.from(document.querySelectorAll('span, div, p, a'));
                for (const t of tabs) {
                    const txt = t.innerText ? t.innerText.trim() : '';
                    const m = txt.match(/(?:作品|Video)\s*(\d+)/i);
                    if (m) return parseInt(m[1], 10);
                }
                return 0;
            }""") or 0
        except Exception:
            channel_video_count = 0

        if channel_video_count and channel_video_count > 0:
            channel_info["expected_total"] = channel_video_count
            _log(f"Phát hiện kênh có khoảng {channel_video_count} video hiển thị trên trang.")

        # Cuộn trang thông minh trong nền ngầm (Headless)
        is_unlimited = (max_videos <= 0 or max_videos >= 99999)
        stable_rounds = 0
        max_scroll_rounds = 3000 if is_unlimited else max(10, max_videos // 8 + 10)

        limit_desc = (
            f"Không giới hạn (Quét hết kênh, dự kiến ~{channel_video_count} video)"
            if is_unlimited and channel_video_count > 0
            else ("Không giới hạn (Quét hết kênh)" if is_unlimited else f"Tối đa {max_videos} video")
        )
        _log(f"Chế độ quét ngầm: {limit_desc}. Đang tiến hành bóc tách toàn bộ video...")

        # Đặt vị trí chuột vào giữa trang để các sự kiện scroll/wheel luôn trúng vùng nội dung
        try:
            page.mouse.move(600, 450)
        except Exception:
            pass

        for scroll_idx in range(max_scroll_rounds):
            if is_cancelled_cb and is_cancelled_cb():
                _log("Người dùng đã hủy quét.")
                break

            if not has_more_videos:
                _log("✅ Douyin API báo cáo: Đã lấy trọn vẹn 100% video của kênh (has_more=0).")
                break

            cur_count = len(all_videos)

            # Nếu đã đạt mục tiêu người dùng yêu cầu
            if not is_unlimited and cur_count >= max_videos:
                _log(f"Đã đạt giới hạn yêu cầu {max_videos} video.")
                break

            # Nếu đã lấy đủ toàn bộ số video hiển thị trên tab của kênh
            if channel_video_count > 0 and cur_count >= channel_video_count:
                _log(f"✅ Đã quét đủ toàn bộ {cur_count}/{channel_video_count} video của kênh!")
                break

            prev_count = cur_count

            # 1. Cuộn đến cuối trang và cuộn thẻ video cuối cùng vào tầm nhìn để kích hoạt IntersectionObserver của Douyin
            page.evaluate("""() => {
                window.scrollTo(0, document.body.scrollHeight || document.documentElement.scrollHeight);
                const items = document.querySelectorAll('a[href*="/video/"], [data-e2e="user-post-item"], ul li');
                if (items && items.length > 0) {
                    items[items.length - 1].scrollIntoView({ behavior: 'smooth', block: 'end' });
                }
            }""")
            page.mouse.wheel(0, 2200)
            page.wait_for_timeout(1800)

            cur_count = len(all_videos)
            if cur_count > prev_count:
                stable_rounds = 0
                if channel_video_count > 0:
                    _log(f"Đang quét ngầm... Đã tìm thấy {cur_count}/{channel_video_count} video")
                else:
                    _log(f"Đang quét ngầm... Đã tìm thấy {cur_count} video")
            else:
                stable_rounds += 1

                # Kiên nhẫn đợi Douyin: Nếu đã có video thì chờ tối đa 25 vòng (~40-50s)
                # vì Douyin thường mất 5-10 giây để nạp tiếp đợt video sau mỗi 50+ clip
                max_stable = 25 if cur_count > 0 else 8

                if stable_rounds % 3 == 1:
                    # Lắc trang: cuộn ngược lên 500px rồi cuộn mạnh xuống lại đáy
                    page.mouse.wheel(0, -600)
                    page.wait_for_timeout(400)
                    page.mouse.wheel(0, 2400)
                    page.evaluate("""() => {
                        window.scrollTo(0, document.body.scrollHeight || document.documentElement.scrollHeight);
                        const items = document.querySelectorAll('a[href*="/video/"], [data-e2e="user-post-item"], ul li');
                        if (items && items.length > 0) {
                            items[items.length - 1].scrollIntoView({ behavior: 'smooth', block: 'end' });
                        }
                    }""")
                    page.wait_for_timeout(600)
                elif stable_rounds % 3 == 2:
                    # Thử click nút "Tải thêm / Thử lại" nếu có trên trang
                    page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('button, span, div, a'));
                        const retryBtn = btns.find(b => b.innerText && (
                            b.innerText.trim() === '点击重试' ||
                            b.innerText.trim() === '重新加载' ||
                            b.innerText.trim() === '加载更多' ||
                            b.innerText.trim() === '刷新'
                        ));
                        if (retryBtn) retryBtn.click();
                    }""")
                    page.wait_for_timeout(800)

                if cur_count == 0 and stable_rounds <= 2:
                    page.evaluate("""() => {
                        const tab = Array.from(document.querySelectorAll('div, span')).find(e => e.innerText && e.innerText.trim().startsWith('作品') && e.children.length === 0);
                        if (tab) tab.click();
                    }""")
                    page.wait_for_timeout(1000)

                if cur_count == 0 and stable_rounds >= 4:
                    body_check = page.evaluate("() => document.body ? document.body.innerText.substring(0, 10000) : ''")
                    if "服务异常" in body_check or "重新刷新拉取数据" in body_check:
                        _log("⚠️ Douyin hiển thị '服务异常' -> Dừng quét do cookie/phiên hết hạn.")
                        cookie_expired_detected = True
                        break

                # Báo cho người dùng biết scanner vẫn đang đợi Douyin phản hồi đợt mới
                if cur_count > 0 and stable_rounds % 3 == 0:
                    target_txt = f"/{channel_video_count}" if channel_video_count > 0 else ""
                    _log(f"Đang chờ Douyin nạp thêm đợt video tiếp theo... (Hiện đã bắt {cur_count}{target_txt} video, đang đợi {stable_rounds}/{max_stable})")

                if stable_rounds >= max_stable:
                    _log(f"Đã cuộn đến cuối trang, không còn video mới. Hoàn tất quét toàn kênh ({cur_count} video).")
                    break

        # Fallback bóc tách từ DOM thẻ video nếu API post không nạp được
        if len(all_videos) == 0:
            dom_items = page.evaluate("""() => {
                const list = [];
                document.querySelectorAll('a[href*="/video/"]').forEach(a => {
                    const m = a.href.match(/\\/video\\/(\\d+)/);
                    if (!m) return;
                    const vid = m[1];
                    const img = a.querySelector('img');
                    const cover = img ? (img.src || img.getAttribute('data-src') || '') : '';
                    const titleEl = a.querySelector('p, span, [class*="title"], [class*="desc"]');
                    const title = titleEl ? titleEl.innerText.trim() : (a.title || '');
                    if (title || cover) {
                        list.push({
                            id: vid,
                            title: title || ('Video ' + vid),
                            url: 'https://www.douyin.com/video/' + vid,
                            direct_url: '',
                            cover: cover,
                            likes: 0,
                            views: 0,
                            author: '',
                            status: 'Chờ tải'
                        });
                    }
                });
                return list;
            }""")
            if dom_items:
                for v in dom_items:
                    if v["id"] not in all_videos:
                        all_videos[v["id"]] = v
                        if video_found_cb:
                            video_found_cb(v)
                _log(f"Đã trích xuất {len(dom_items)} video từ giao diện trang.")

        # Lưu lại cookies mới nếu có
        try:
            from app.services.cookie_storage_service import save_douyin_cookies
            new_cookies = context.cookies()
            if new_cookies:
                save_douyin_cookies(new_cookies)
        except Exception:
            pass

        browser.close()

    cleaned_videos = [
        v for v in all_videos.values()
        if (v.get("title") or v.get("cover") or v.get("direct_url") or (v.get("likes", 0) > 0))
    ]

    channel_info["total"] = len(cleaned_videos)

    if len(cleaned_videos) == 0:
        if cookie_expired_detected:
            channel_info["error_type"] = "COOKIE_EXPIRED"
            _log("⚠️ Quét thất bại do Cookie Douyin đã hết hạn hoặc phiên đăng nhập không hợp lệ.")
        else:
            channel_info["error_type"] = "NO_VIDEOS_OR_COOKIE_EXPIRED"
            _log("⚠️ Không tìm thấy video nào (kiểm tra lại link kênh hoặc cập nhật cookie mới).")
    else:
        _log(f"Quét hoàn tất: Tìm thấy đúng {len(cleaned_videos)} video của kênh '{channel_info['name']}'")

    return cleaned_videos, channel_info
