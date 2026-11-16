#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, sys, time, random, requests, subprocess, ctypes
from datetime import datetime
from seleniumbase import SB

# 环境变量配置(可以直接私库在双引号里填写)
EMAIL         = os.environ.get("EMAIL") or ""           # 邮箱必填，登录和通知使用
PASSWORD      = os.environ.get("PASSWORD") or ""        # 主用密码登录方式,必须填写
TG_CHAT_ID    = os.environ.get("TG_CHAT_ID") or ""      # TG chat id,不填写不通知，需和bot token一起填写生效
TG_BOT_TOKEN  = os.environ.get("TG_BOT_TOKEN") or ""    # TG bot token 

BASE_URL = "https://bot-hosting.net"

if not (EMAIL and PASSWORD):
    print("ℹ️ 未配置 EMAIL 和 PASSWORD,脚本终止。")
    sys.exit(1)

# 发送tg通知
def send_telegram_message(message: str):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram 未配置，跳过通知")
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": message}, timeout=10)
        print("✅ Telegram 通知已发送")
    except Exception as e:
        print(f"❌ Telegram 发送失败: {e}")

# 通知格式
def format_notification(status: str, extra: str = "", error: str = "", expiry_date: str = "") -> str:
    local_time = time.gmtime(time.time() + 8 * 3600)
    now = time.strftime("%Y-%m-%d %H:%M:%S", local_time)
    if '@' in EMAIL:
        name, domain = EMAIL.split('@', 1)
        if len(name) > 4:
            masked_email = f"{name[:2]}****{name[-2:]}@{domain}"
        else:
            masked_email = f"{name}@{domain}"
    else:
        masked_email = EMAIL[:2] + '****' 
    
    lines = [
        "🇫🇮 Bot-hosting 续期通知",
        "",
        f"{status}",
        f"👤 登录账户: {masked_email}",
    ]
    if expiry_date:
        lines.append(f"📅 到期时间: {expiry_date}")
    if extra:
        lines.append(extra)
    if error:
        lines.append(f"⚠️ 错误信息: {error}")
    lines.append(f"⏱️ 登录时间: {now}")
    return "\n".join(lines)

# 将 sing-box 的纯 socks 入站升级为 mixed (同时支持 Chrome 的 HTTP 代理和 SOCKS5)
def ensure_mixed_proxy():
    cfg_path = "sing-box-config.json"
    if not os.path.exists(cfg_path):
        return
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            content = f.read()
        if re.search(r'"type"\s*:\s*"socks"', content):
            print("🔧 将 sing-box 本地端口从纯 socks 升级为 mixed 混合模式 (兼容 Chrome HTTP 代理)...")
            new_content = re.sub(r'"type"\s*:\s*"socks"', '"type": "mixed"', content)
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            subprocess.run(["pkill", "-f", "sing-box"], stderr=subprocess.DEVNULL)
            time.sleep(1)
            sb_bin = "./sing-box" if os.path.exists("./sing-box") else "sing-box"
            subprocess.Popen(
                [sb_bin, "run", "-c", cfg_path],
                stdout=open("sing-box.log", "a"),
                stderr=subprocess.STDOUT,
            )
            time.sleep(2)
            print("✅ sing-box mixed 代理已重启就绪")
    except Exception as e:
        print(f"⚠️ 升级 sing-box mixed 模式出错: {e}")

# ---------- 基础工具 ----------
def js(sb, script, default=None, errors=None):
    try:
        result = sb.execute_script(script)
        return default if result is None else result
    except Exception as e:
        if errors is not None:
            errors.append(f"execute_script: {type(e).__name__}: {e}")
        try:
            result = sb.cdp.evaluate("(function(){ " + script + " })()")
            return default if result is None else result
        except Exception as e2:
            if errors is not None:
                errors.append(f"cdp.evaluate: {type(e2).__name__}: {e2}")
            return default

# 清理 Google Funding Choices (GDPR Consent) 遮罩弹窗
def dismiss_overlays(sb):
    js(sb, """
        try {
            var consentBtn = document.querySelector('.fc-cta-consent, button.fc-button.fc-cta-consent');
            if (consentBtn) { consentBtn.click(); }
            var overlays = document.querySelectorAll('.fc-consent-root, .fc-dialog-overlay, .fc-dialog-container');
            overlays.forEach(function(el) { el.remove(); });
            if (document.body) {
                document.body.style.overflow = 'auto';
            }
        } catch(e) {}
    """)

# 安全点击：优先常规点击，如果被遮挡则清理遮罩并用 JS 强制点击
def safe_click(sb, selector, timeout=8):
    dismiss_overlays(sb)
    try:
        sb.click(selector, timeout=timeout)
        return True
    except Exception as e:
        print(f"⚠️ 常规点击 {selector} 被拦截或失败，尝试移除遮罩并用 JS 点击: {str(e)[:100]}")
        dismiss_overlays(sb)
        time.sleep(0.5)
        try:
            sb.js_click(selector)
            return True
        except Exception as e2:
            clicked = js(sb, f"""
                var el = document.querySelector('{selector.replace("'", "\\'")}');
                if (el) {{ el.click(); return true; }}
                return false;
            """, False)
            if clicked:
                return True
            raise e2

# 挂载 Fetch / XHR 拦截器，记录点击确认按钮后后端接口的真实返回状态与内容
def install_network_sniffer(sb):
    js(sb, """
        try {
            window.__renew_api_logs = [];
            if (!window.__sniffer_installed) {
                window.__sniffer_installed = true;
                var origFetch = window.fetch;
                window.fetch = async function() {
                    var url = arguments[0] ? (typeof arguments[0] === 'string' ? arguments[0] : arguments[0].url) : '';
                    try {
                        var resp = await origFetch.apply(this, arguments);
                        if (url && !url.includes('google') && !url.includes('cloudflare') && !url.includes('analytics')) {
                            var clone = resp.clone();
                            clone.text().then(function(body) {
                                window.__renew_api_logs.push('FETCH ' + resp.status + ' ' + url.slice(0, 90) + ' -> ' + (body || '').slice(0, 200));
                            }).catch(function(){});
                        }
                        return resp;
                    } catch (err) {
                        window.__renew_api_logs.push('FETCH_ERR ' + url.slice(0, 90) + ' -> ' + String(err));
                        throw err;
                    }
                };
                var origOpen = XMLHttpRequest.prototype.open;
                var origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(method, url) {
                    this.__url = url || '';
                    this.__method = method || '';
                    return origOpen.apply(this, arguments);
                };
                XMLHttpRequest.prototype.send = function() {
                    var self = this;
                    this.addEventListener('load', function() {
                        if (self.__url && !self.__url.includes('google') && !self.__url.includes('cloudflare')) {
                            window.__renew_api_logs.push('XHR ' + self.status + ' ' + self.__method + ' ' + self.__url.slice(0, 90) + ' -> ' + (self.responseText || '').slice(0, 200));
                        }
                    });
                    return origSend.apply(this, arguments);
                };
            }
        } catch(e) {}
    """)

def dump_network_sniffer(sb):
    logs = js(sb, "return (window.__renew_api_logs || []).join('\\n');", "")
    if logs:
        print("📡 捕获到后端网络请求日志:")
        for line in str(logs).splitlines()[-10:]:
            print("   [API] " + line)
    else:
        print("📡 暂未捕获到新的后端业务请求")

# Turnstile 定位失败时的现场诊断快照
def diag_turnstile_dom(sb, errors=None):
    snap = js(sb, """
        var out = [];
        var ifr = document.querySelectorAll('iframe');
        out.push('top-doc iframes: ' + ifr.length);
        for (var i = 0; i < ifr.length; i++) {
            var r = ifr[i].getBoundingClientRect();
            out.push('iframe w=' + Math.round(r.width) + ' h=' + Math.round(r.height) + ' src=' + (ifr[i].src || '').slice(0, 90));
        }
        var ts = document.querySelectorAll('[class*="turnstile"], [id*="turnstile"], input[name="cf-turnstile-response"]');
        out.push('turnstile-ish elements: ' + ts.length);
        for (var j = 0; j < ts.length; j++) {
            var rr = ts[j].getBoundingClientRect();
            var cls = (typeof ts[j].className === 'string') ? ts[j].className : (ts[j].getAttribute('class') || '');
            out.push(' - ' + ts[j].tagName + ' class=' + cls + ' w=' + Math.round(rr.width) + ' h=' + Math.round(rr.height));
        }
        out.push('window.turnstile=' + (typeof window.turnstile) + ' readyState=' + document.readyState);
        return out.join('\\n');
    """, "")
    for line in str(snap).splitlines():
        print("   [diag] " + line)
    if errors:
        for err in errors:
            print("   [diag][exec-error] " + str(err)[:200])

# 页面注入脚本
TURNSTILE_TOKEN_JS = """
try {
    if (window.turnstile && window.turnstile.getResponse) {
        var t = window.turnstile.getResponse();
        if (t) return t;
    }
} catch (e) {}
var els = document.querySelectorAll('input[name="cf-turnstile-response"]');
for (var i = els.length - 1; i >= 0; i--) {
    if (els[i].value) return els[i].value;
}
return '';
"""

TURNSTILE_EXISTS_JS = """
return document.querySelector(
    'input[name="cf-turnstile-response"], iframe[src*="challenges.cloudflare.com"], .cf-turnstile'
) !== null;
"""

TS_COORDS_JS = """
function findWidget(root) {
    var els = root.querySelectorAll(
        'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, [id*="cf-chl"]');
    for (var i = els.length - 1; i >= 0; i--) {
        var r = els[i].getBoundingClientRect();
        if (r.width > 50 && r.height > 20) return els[i];
    }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
        if (all[j].shadowRoot) {
            var f = findWidget(all[j].shadowRoot);
            if (f) return f;
        }
    }
    return null;
}
function boxFromTokenInput() {
    var els = document.querySelectorAll('input[name="cf-turnstile-response"]');
    for (var i = els.length - 1; i >= 0; i--) {
        var cur = els[i].parentElement;
        for (var k = 0; k < 6 && cur; k++) {
            var r = cur.getBoundingClientRect();
            if (r.width > 100 && r.width < 600 && r.height > 30 && r.height < 140) return cur;
            cur = cur.parentElement;
        }
    }
    return null;
}
var hit = findWidget(document) || boxFromTokenInput();
if (!hit) return null;
var hr = hit.getBoundingClientRect();
return {cx: Math.round(hr.x + 30), cy: Math.round(hr.y + hr.height / 2)};
"""

def wait_turnstile_present(sb, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        dismiss_overlays(sb)
        if js(sb, TURNSTILE_EXISTS_JS, False):
            return True
        time.sleep(1)
    return False

def _activate_window():
    if os.name == "nt":
        return

    for cls in ["chrome", "chromium", "Chromium", "Chrome", "google-chrome"]:
        try:
            r = subprocess.run(["xdotool", "search", "--onlyvisible", "--class", cls], capture_output=True, text=True, timeout=3)
            wids = [w for w in r.stdout.strip().split("\n") if w.strip()]
            if wids:
                subprocess.run(["xdotool", "windowactivate", "--sync", wids[0]], timeout=3, stderr=subprocess.DEVNULL)
                time.sleep(0.2)
                return
        except Exception:
            pass
    try:
        subprocess.run(["xdotool", "getactivewindow", "windowactivate"], timeout=3, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def _windows_mouse_click(x: int, y: int):
    try:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.08)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)
    except Exception:
        pass

def _xdotool_click(x: int, y: int):
    if os.name == "nt":
        _windows_mouse_click(x, y)
        return

    _activate_window()
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)], timeout=3, stderr=subprocess.DEVNULL)
        time.sleep(0.15)
        subprocess.run(["xdotool", "click", "1"], timeout=2, stderr=subprocess.DEVNULL)
    except Exception:
        os.system(f"xdotool mousemove {x} {y} click 1 2>/dev/null")

def click_screen_coords(sb, cx: int, cy: int):
    try:
        wi = js(sb, "return '' + (window.screenX || 0) + '|' + (window.screenY || 0) + '|' +"
                    " window.outerHeight + '|' + window.innerHeight;", "")
        parts = str(wi).split("|")
        sx, sy = int(parts[0] or 0), int(parts[1] or 0)
        bar = int(parts[2] or 800) - int(parts[3] or 768)
        ax = int(cx) + sx
        ay = int(cy) + sy + bar
        print(f"🖱️ 底层物理坐标点击 ({ax}, {ay}) [视口: ({cx}, {cy})]")
        _xdotool_click(ax, ay)
        return True
    except Exception as e:
        print(f"⚠️ 底层物理坐标点击异常: {e}")
        return False

def turnstile_token(sb) -> str:
    token = js(sb, TURNSTILE_TOKEN_JS, "")
    return token if isinstance(token, str) else ""

def _click_turnstile(sb, locate_timeout=10) -> bool:
    dismiss_overlays(sb)
    coords = None
    errors = []
    deadline = time.time() + locate_timeout
    while time.time() < deadline:
        coords = js(sb, TS_COORDS_JS, None, errors)
        if coords:
            break
        time.sleep(1)
    if not coords:
        print("⚠️ 无法定位 Turnstile 坐标, 打印现场诊断:")
        diag_turnstile_dom(sb, errors)
        return False

    try:
        wi = js(sb, "return '' + (window.screenX || 0) + '|' + (window.screenY || 0) + '|' +"
                    " window.outerHeight + '|' + window.innerHeight;", "")
        parts = str(wi).split("|")
        sx, sy = int(parts[0] or 0), int(parts[1] or 0)
        bar = int(parts[2] or 800) - int(parts[3] or 768)
        ax = int(coords["cx"]) + sx
        ay = int(coords["cy"]) + sy + bar
        print(f"🖱️ 底层点击 Turnstile ({ax}, {ay})")
        _xdotool_click(ax, ay)
        time.sleep(3)
        return True
    except Exception as e:
        print(f"⚠️ 底层点击失败: {e}")
        return False

def click_turnstile(sb) -> bool:
    if _click_turnstile(sb):
        return True
    coords = js(sb, TS_COORDS_JS)
    if coords:
        try:
            if hasattr(sb, "cdp") and hasattr(sb.cdp, "gui_click_x_y"):
                print(f"🖱️ 坐标点击 Turnstile ({coords['cx']},{coords['cy']})")
                sb.cdp.gui_click_x_y(int(coords["cx"]), int(coords["cy"]))
                return True
        except Exception as e:
            print(f"⚠️ 坐标点击失败: {e}")
    try:
        if hasattr(sb, "cdp") and hasattr(sb.cdp, "click_captcha"):
            print("🖱️ 兜底调用cdp点击 ...")
            sb.cdp.click_captcha()
            return True
        print("⚠️ 当前 SeleniumBase 版本无 sb.cdp.click_captcha")
    except Exception as e:
        print(f"⚠️ click_captcha 出错: {e}")
    return False

def solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8) -> bool:
    if turnstile_token(sb):
        return True

    deadline = time.time() + timeout
    next_click = time.time() + first_wait
    clicks = 0
    while time.time() < deadline:
        if turnstile_token(sb):
            print(f"✅ {tag}已通过(已获取 token)")
            return True
        if time.time() >= next_click:
            clicks += 1
            print(f"🖱️ 第 {clicks} 次点击 Turnstile 验证框...")
            click_turnstile(sb)
            next_click = time.time() + 10 + random.uniform(0, 3)
        time.sleep(2)
    ok = bool(turnstile_token(sb))
    print(f"{'✅' if ok else '❌'} {tag}{'已通过' if ok else '未通过'}")
    return ok

def handle_turnstile(sb) -> bool:
    print("🛡️ 处理 Turnstile 验证...")
    return solve_turnstile(sb, timeout=90, tag="Turnstile", first_wait=8)
    
# 获取当前出口ip
def get_current_ip(proxy_server: str = "") -> str:
    proxies = None
    if proxy_server:
        proxies = {"http": proxy_server, "https": proxy_server}
    response = requests.get("https://api.ip.sb/ip", proxies=proxies, timeout=15)
    response.raise_for_status()
    return response.text.strip()

# 时间格式化
def format_countdown(countdown_str: str) -> str:
    try:
        h, m, _ = countdown_str.split(':')
        h = int(h)
        m = int(m)
        if h > 0:
            return f"{h}h{m}min"
        else:
            return f"{m}min"
    except Exception:
        return countdown_str

# 归一化日期为 YYYY/MM/DD
def normalize_date(date_str: str) -> str:
    if not date_str:
        return date_str
    date_str = date_str.strip()

    if '/' in date_str:
        parts = date_str.split('/')
        if len(parts) == 3:
            if len(parts[2]) == 4:   # MM/DD/YYYY
                return f"{parts[2]}/{int(parts[0]):02d}/{int(parts[1]):02d}"
            if len(parts[0]) == 4:   # YYYY/MM/DD
                return f"{parts[0]}/{int(parts[1]):02d}/{int(parts[2]):02d}"

    if '-' in date_str:
        parts = date_str.split('-')
        if len(parts) == 3 and len(parts[0]) == 4:
            return f"{parts[0]}/{int(parts[1]):02d}/{int(parts[2]):02d}"

    for fmt in ("%d %b %Y", "%d %B %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y/%m/%d")
        except ValueError:
            pass

    return date_str

DATE_RE = r"(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|\d{4}/\d{1,2}/\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})"

# 获取过期日期
def extract_expiry_date(page_source: str) -> str:
    patterns = [
        r"[Ee]xpires\s*(?:on)?\s*[:\-]?\s*" + DATE_RE,
        r"将于\s*" + DATE_RE + r"\s*到期",
        r"到期\s*[:\-]?\s*" + DATE_RE,
        DATE_RE,
    ]
    
    for pattern in patterns:
        match = re.search(pattern, page_source)
        if match:
            return normalize_date(match.group(1))
            
    return None

# 优先从 DOM 的 st-meta 元素提取到期日期
def get_expiry_from_dom(sb) -> str:
    for selector in ['span.st-meta', '.st-meta', '[class*="st-meta"]']:
        try:
            if sb.is_element_present(selector):
                match = re.search(DATE_RE, sb.get_text(selector))
                if match:
                    return normalize_date(match.group(1))
        except Exception:
            pass
    return None

# 定位弹窗内的确认续期按钮 (例如 "Renew for 4 days")，排除外部的 "Renew free plan"
FIND_MODAL_BTN_JS = """
var btns = document.querySelectorAll('button, [role="button"], input[type="submit"]');
for (var i = btns.length - 1; i >= 0; i--) {
    var b = btns[i];
    var r = b.getBoundingClientRect();
    if (r.width < 30 || r.height < 20) continue;
    if (b.disabled || b.getAttribute('aria-disabled') === 'true' || b.classList.contains('disabled')) continue;
    var txt = (b.innerText || b.textContent || '').trim();
    var low = txt.toLowerCase();
    if (low === 'renew free plan' || low.includes('cancel') || low.includes('close') || low.includes('see plans')) continue;
    if (low.includes('renew for') || low.includes('extend') || low.includes('confirm') || (low.includes('renew') && low.includes('day'))) {
        b.setAttribute('data-modal-renew-btn', '1');
        return {
            found: true,
            text: txt,
            cx: Math.round(r.left + r.width / 2),
            cy: Math.round(r.top + r.height / 2)
        };
    }
}
return {found: false};
"""

# 点击弹窗内的确认续期按钮
def click_modal_renew_button(sb, timeout=20) -> bool:
    btn_diag = js(sb, """
        var info = [];
        var btns = document.querySelectorAll('button');
        for (var j = 0; j < btns.length; j++) {
            var b = btns[j];
            var r = b.getBoundingClientRect();
            if (r.width > 15 && r.height > 15) {
                var txt = (b.innerText || b.textContent || '').trim();
                info.push('[' + txt + '|disabled=' + b.disabled + '|cls=' + b.className + ']');
            }
        }
        return info.join(', ');
    """, "")
    print(f"🔎 当前可见按钮列表: {btn_diag}")

    deadline = time.time() + timeout
    while time.time() < deadline:
        dismiss_overlays(sb)
        target = js(sb, FIND_MODAL_BTN_JS, {"found": False})

        if target and target.get("found"):
            btn_text = target.get("text", "")
            cx, cy = int(target.get("cx", 0)), int(target.get("cy", 0))
            print(f"🖱️ 定位到弹窗确认按钮 '{btn_text}' ({cx}, {cy})，执行点击...")

            try:
                sb.click('[data-modal-renew-btn="1"]', timeout=4)
            except Exception as e:
                print(f"⚠️ 常规点击确认按钮异常，改用底层物理坐标点击: {str(e)[:80]}")
                click_screen_coords(sb, cx, cy)

            time.sleep(4)
            api_count = js(sb, "return (window.__renew_api_logs || []).length;", 0)
            if api_count == 0:
                t2 = js(sb, FIND_MODAL_BTN_JS, {"found": False})
                if t2 and t2.get("found"):
                    print("🖱️ 未检测到后端请求且按钮仍在，补充底层物理坐标点击...")
                    click_screen_coords(sb, int(t2.get("cx", cx)), int(t2.get("cy", cy)))
                    time.sleep(4)

            modal_txt = js(sb, """
                var d = document.querySelector('div[role="dialog"], .modal, [class*="modal"]');
                return d ? (d.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 200) : '';
            """, "")
            if modal_txt:
                print(f"📋 点击后弹窗内容状态: {modal_txt}")
            return True

        time.sleep(1)

    return False

# 执行单次浏览器会话
def run_session(use_proxy: bool, raw_proxy: str, headless: bool) -> bool:
    sb_kwargs = {"uc": True, "headless": headless, "locale_code": "en-US"}

    if use_proxy and raw_proxy:
        ensure_mixed_proxy()
        clean_proxy = re.sub(r"^[a-zA-Z0-9]+://", "", raw_proxy).strip()
        print(f"🔗 挂载代理: {clean_proxy}")
        sb_kwargs["proxy"] = clean_proxy
        try:
            ip = get_current_ip(f"http://{clean_proxy}")
            print(f"📍 当前代理出口IP: {ip}")
        except Exception as e:
            print(f"⚠️ HTTP 代理连通性测试失败 ({e})，放弃代理模式")
            return False
    else:
        print("🍭 未使用代理，直连访问")
        try:
            ip = get_current_ip("")
            print(f"📍 当前出口IP: {ip}")
        except Exception as e:
            print(f"⚠️ 获取出口 IP 失败: {e}")

    with SB(**sb_kwargs) as sb:
        login_ok = False
        print("🚀 启动浏览器...")
        try:
            print(f"🌐 访问 {BASE_URL}/login")
            sb.open(f"{BASE_URL}/login")
            sb.wait_for_ready_state_complete()
            sb.sleep(3)
            dismiss_overlays(sb)

            # 如果遇到前置全屏 Cloudflare 盾，先过盾
            if not sb.is_element_present('button[type="submit"]') and wait_turnstile_present(sb, timeout=5):
                print("🛡️ 登录页检测到前置 Cloudflare 验证，先处理验证...")
                handle_turnstile(sb)
                sb.sleep(5)
                dismiss_overlays(sb)

            safe_click(sb, 'button[type="submit"]') # 点击 Continue with email 按钮
            sb.sleep(10)  # 等待密码框加载
            dismiss_overlays(sb)

            print("📧 填写邮箱...")
            sb.input('input[type="email"]', EMAIL)
            sb.sleep(1)
            print("🔑 填写密码...")
            sb.input('input[type="password"]', PASSWORD)
            sb.sleep(3)

            if wait_turnstile_present(sb):
                if not handle_turnstile(sb):
                    print("❌ 登录界面的 Turnstile 验证失败，尝试兜底点击...")
                    try:
                        sb.cdp.click_captcha()
                        sb.sleep(4)
                    except Exception as e:
                        print(f"⚠️ 兜底点击失败: {e}")
            else:
                print("ℹ️ 未检测到 Turnstile")

            sb.sleep(1)
            safe_click(sb, 'button[type="submit"]') # 点击登录按钮
            print("🖱️ 提交登录按钮")
            sb.sleep(6)
            sb.wait_for_ready_state_complete()
            
            sb.open(f"{BASE_URL}/dash/billings")
            sb.wait_for_ready_state_complete()
            sb.sleep(4)
            dismiss_overlays(sb)

            current_url = sb.get_current_url()
            current_title = sb.get_title()
            print(f"📝 当前URL: {current_url}, Title: {current_title}")

            if "/dash/billings" in current_url and "/login" not in current_url and "error=" not in current_url:
                login_ok = True
                print("✅ Password 登录成功,当前已到达账单页")
            else:
                print(f"❌ Password 登录后仍未到达账单页，当前URL: {current_url}")
        except Exception as e:
            print(f"❌ Password 登录过程异常: {type(e).__name__}: {str(e)[:150]}")

        if not login_ok:
            return False

        # 清理 GDPR 隐私遮罩弹窗并提取当前到期日期
        sb.sleep(2)
        dismiss_overlays(sb)
        install_network_sniffer(sb)

        try:
            page_source = sb.get_page_text() + "\n" + sb.get_page_source()
        except Exception:
            page_source = sb.get_page_source()
        current_expiry = get_expiry_from_dom(sb) or extract_expiry_date(page_source)
        if current_expiry:
            print(f"📅 当前到期日期: {current_expiry}")
        else:
            print("⚠️ 未能提取当前到期日期")

        # 寻找外部续期按钮
        outer_renew_selector = None
        countdown_text = None
        possible_selectors = [
            'button:contains("Renew free plan")',
            'button[title*="Extend your free plan"]',
            '[class*="btn btn-primary btn-sm"]',
        ]

        for selector in possible_selectors:
            try:
                if sb.is_element_visible(selector):
                    button_text = sb.get_text(selector).strip()
                    match = re.search(r"(?:Renew in|Renewal cooldown)\s+(\d{1,2}:\d{2}:\d{2})", button_text, re.I)
                    if match:
                        countdown_text = match.group(1)
                        print(f"⏳ 续期冷却中, 未到续期时间: '{button_text}'")
                        break
                    elif "Renew" in button_text or "Extend" in button_text:
                        outer_renew_selector = selector
                        print(f"✅ 续期按钮可用: '{button_text}'")
                        break
            except Exception:
                pass

        if not outer_renew_selector and not countdown_text:
            m_cd = re.search(r"(?:Renew in|Renewal cooldown)\s+(\d{1,2}:\d{2}:\d{2})", page_source, re.I)
            if m_cd:
                countdown_text = m_cd.group(1)
                print(f"⏳ 页面检测到续期冷却倒计时: {countdown_text}")

        # 点击外部续期按钮等待弹窗
        if outer_renew_selector:
            print("🔄 点击外部续期按钮，等待验证窗口...")
            try:
                sb.sleep(1)
                js(sb, """
                    var els = document.querySelectorAll('input[name="cf-turnstile-response"]');
                    els.forEach(function(e){ e.value = ''; });
                """)
                safe_click(sb, outer_renew_selector)
                sb.sleep(8)
            except Exception as e:
                print(f"❌ 点击外部按钮失败: {e}")
                send_telegram_message(format_notification("❌ 续期失败", error="点击外部续期按钮出错"))
                return True

            turnstile_passed = True
            if wait_turnstile_present(sb, timeout=15):
                turnstile_passed = handle_turnstile(sb)
                if not turnstile_passed:
                    print("❌ Turnstile 验证失败，尝试兜底点击...")
                    try:
                        sb.cdp.click_captcha()
                        sb.sleep(5)
                        turnstile_passed = bool(turnstile_token(sb))
                    except Exception as e:
                        print(f"⚠️ 兜底点击失败: {e}")
            else:
                print("ℹ️ 未检测到 Turnstile")

            if not turnstile_passed:
                print("❌ Turnstile 验证最终未通过，脚本退出")
                send_telegram_message(format_notification("❌ 续期失败", error="Turnstile 验证未通过"))
                return True

            print("⏳ 等待弹窗内续期确认按钮可用并点击...")
            sb.sleep(2)
            if click_modal_renew_button(sb, timeout=20):
                print("✅ 已点击弹窗内续期确认按钮")
            else:
                print("⚠️ 未能定位或点击弹窗内确认按钮")

            dump_network_sniffer(sb)

            print("⏳ 等待后端处理并刷新页面获取最新到期时间...")
            sb.sleep(6)
            dump_network_sniffer(sb)

            try:
                sb.open(f"{BASE_URL}/dash/billings")
                sb.wait_for_ready_state_complete()
                sb.sleep(4)
                dismiss_overlays(sb)
            except Exception as e:
                print(f"⚠️ 刷新账单页面出错: {e}")

            try:
                new_page_text = sb.get_page_text() + "\n" + sb.get_page_source()
            except Exception:
                new_page_text = sb.get_page_source()
            new_expiry = get_expiry_from_dom(sb) or extract_expiry_date(new_page_text)
            new_match = re.search(r"(?:Renewal cooldown|Renew in)\s+(\d{1,2}:\d{2}:\d{2})", new_page_text, re.I)

            if new_match:
                new_countdown = new_match.group(1)
                print(f"✅ 续期成功！新的倒计时: {new_countdown}")
                if new_expiry:
                    print(f"📅 新的到期日期: {new_expiry}")
                send_telegram_message(
                    format_notification(
                        "✅ 续期成功",
                        extra=f"⏱️ 可续期时间: {format_countdown(new_countdown)}后",
                        expiry_date=new_expiry or current_expiry or "（未获取到）"
                    )
                )
            elif new_expiry and new_expiry != current_expiry:
                print(f"✅ 续期成功，到期日期已更新为: {new_expiry}")
                send_telegram_message(
                    format_notification(
                        "✅ 续期成功",
                        extra="⏱️ 到期日期已更新 (+4天)",
                        expiry_date=new_expiry
                    )
                )
            else:
                print("⚠️ 续期结果未知，到期日期未变化，请手动检查")
                send_telegram_message(
                    format_notification(
                        "⚠️ 续期可能未成功",
                        extra="请登录后台检查",
                        expiry_date=new_expiry or current_expiry or "（未获取到）"
                    )
                )
        else:
            if countdown_text:
                friendly = format_countdown(countdown_text)
                print(f"⏳ 未到续期时间，倒计时: {countdown_text} ({friendly})")
                send_telegram_message(
                    format_notification(
                        "⏳ 未到续期时间",
                        extra=f"⏱️ 可续期时间: {friendly}后",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )
            else:
                print("ℹ️ 未找到续期按钮或倒计时，状态未知")
                send_telegram_message(
                    format_notification(
                        "ℹ️ 无需续期",
                        extra="当前状态未知，请手动检查",
                        expiry_date=current_expiry or "（未获取到）"
                    )
                )

        print("🏁 脚本执行完毕")
        return True

def main():
    print("#" * 25)
    print("   Bot-hosting 自动续期")
    print("#" * 25)

    IS_PROXY = os.environ.get("IS_PROXY", "false").lower() == "true"
    PROXY_SERVER = os.environ.get("PROXY_SERVER", "").strip() or "http://127.0.0.1:1080"
    HEADLESS = os.environ.get("HEADLESS", "false").lower() == "true"

    if IS_PROXY:
        ok = run_session(use_proxy=True, raw_proxy=PROXY_SERVER, headless=HEADLESS)
        if not ok:
            print("🔄 代理模式下登录未成功，自动回退直连模式重试...")
            ok = run_session(use_proxy=False, raw_proxy="", headless=HEADLESS)
    else:
        ok = run_session(use_proxy=False, raw_proxy="", headless=HEADLESS)

    if not ok:
        send_telegram_message(format_notification("❌ 登录失败", error="Password 登录失败"))

if __name__ == "__main__":
    main()
