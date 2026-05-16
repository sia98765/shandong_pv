# -*- coding: utf-8 -*-
"""
山东光伏情报站 - DrissionPage + 百度检索版 V36

适配场景：
1. 本地 Windows / Linux 运行；
2. GitHub Actions + Xvfb 虚拟窗口运行；
3. 不进入详情页，只收录百度搜索结果里的标题、摘要、URL、日期；
4. 默认不使用 --headless，降低百度验证概率；
5. 检测到百度安全验证 / 验证码页面时，保存 HTML + 截图，并停止任务，避免越刷越容易触发风控；
6. 默认每查询 3 个 query 后关闭浏览器，暂停 60 秒，再重新打开浏览器继续；
7. 实时写入 ./data/shandong_pv_data.json；
8. 保留近 N 年发布日期数据，默认近 2 年；
9. 只保留你配置的 11 个数据源域名结果。

安装依赖：
    pip install DrissionPage beautifulsoup4 lxml requests

本地测试：
    python crawler_baidu_actions_v36.py --overwrite --debug --limit-queries 3 --max-pages-per-query 1

GitHub Actions 推荐运行方式，不要加 --headless：
    xvfb-run -a -s "-screen 0 1366x900x24" python crawler_baidu_actions_v36.py \
      --overwrite \
      --no-file-log \
      --no-detail-logs \
      --limit-queries 0 \
      --max-pages-per-query 1 \
      --query-sleep-min 20 \
      --query-sleep-max 35 \
      --page-sleep-min 3 \
      --page-sleep-max 8 \
      --after-load-sleep 3

如果你确认本地必须无头运行，可加：
    --headless
但百度更容易触发验证，不建议在 GitHub Actions 使用 --headless。
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import random
import re
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse, urlunparse

from bs4 import BeautifulSoup

try:
    import requests
except Exception:
    requests = None

try:
    from DrissionPage import ChromiumOptions, ChromiumPage
except Exception as e:
    raise RuntimeError(
        "未安装 DrissionPage，请先执行：pip install DrissionPage beautifulsoup4 lxml requests"
    ) from e


# =========================================================
# 1. 基础配置
# =========================================================

PV_SEARCH_CONFIG = {
    "official_websites": {
        "山东能源监管办": "https://sdb.nea.gov.cn",
        "山东省能源局": "http://nyj.shandong.gov.cn",
        "国家能源局": "https://www.nea.gov.cn",
        "国家电网": "https://www.sgcc.com.cn",
        "中国招标投标公共服务平台": "https://www.cebpubservice.com",
        "山东政府采购网": "http://www.ccgp-shandong.gov.cn",
        "中能建电子采购平台": "https://ec.ceec.net.cn",
        "中国电建集采平台": "https://ec.powerchina.cn",
        "济南公共资源交易中心": "http://jnggzy.jinan.gov.cn",
        "青岛公共资源交易中心": "https://ggzy.qingdao.gov.cn",
        "北极星太阳能光伏网": "https://guangfu.bjx.com.cn",
    }
}

SEARCH_KEYWORD = "光伏"

SITE_QUERY_KEYWORDS = [
    ("光伏政策", "policy"),
    ("光伏市场分析", "market"),
    ("光伏项目", "project"),
]

GLOBAL_QUERIES = [
    ("光伏项目 山东省", "project"),
    ("光伏市场分析 山东省", "market"),
    ("光伏政策 山东省", "policy"),
]

LOCAL_SHANDONG_SOURCE_NAMES = {
    "山东能源监管办",
    "山东省能源局",
    "山东政府采购网",
    "济南公共资源交易中心",
    "青岛公共资源交易中心",
}

CATEGORY_LABEL = {
    "market": "市场分析",
    "policy": "光伏政策",
    "project": "项目信息",
}

PV_TERMS = [
    "光伏", "分布式光伏", "集中式光伏", "太阳能光伏", "光伏发电",
    "屋顶光伏", "户用光伏", "工商业光伏", "光伏电站", "光伏组件",
    "海上光伏", "农光互补", "渔光互补", "新能源", "储能",
]

SHANDONG_TERMS = [
    "山东", "山东省", "济南", "青岛", "烟台", "潍坊", "淄博", "济宁", "临沂", "德州",
    "东营", "滨州", "菏泽", "枣庄", "泰安", "日照", "威海", "聊城", "莱芜",
    "鲁发改", "鲁能源", "鲁政", "shandong", "jinan", "qingdao", "yantai",
    "weifang", "zibo", "jining", "linyi", "dezhou", "dongying", "binzhou",
    "heze", "zaozhuang", "taian", "rizhao", "weihai", "liaocheng",
    "sdb.nea", "nyj.shandong", "ccgp-shandong", "jnggzy.jinan", "ggzy.qingdao",
]

CATEGORY_KEYWORDS = {
    "policy": [
        "政策", "通知", "办法", "实施细则", "规则", "征求意见", "监管", "管理", "方案",
        "补贴", "规划", "文件", "规定", "解读", "印发", "改革", "细则", "申报",
        "用地", "配储", "整县推进", "并网政策",
    ],
    "market": [
        "市场", "分析", "报告", "装机", "发电量", "电价", "价格", "趋势", "数据",
        "统计", "预测", "消纳", "产业", "观察", "研究", "规模", "发展", "机制电价",
        "竞价", "收益", "调研", "现状", "前景", "容量统计",
    ],
    "project": [
        "项目", "招标", "中标", "采购", "成交", "EPC", "候选人", "备案", "公示",
        "开工", "并网", "集采", "标段", "合同", "结果公告", "成交公告", "竞争性磋商",
        "询价", "公告", "招标计划", "名单", "清单", "投资主体", "项目进展",
    ],
}

NEGATIVE_TITLE_KEYWORDS = [
    "旅游", "景点", "攻略", "门票", "酒店", "机票", "美食", "游记", "自驾游",
    "百科", "百度百科", "搜狗百科", "快懂百科", "行政区划", "山东省简介", "山东简介",
    "山东旅游", "山东地图", "地图", "在线地图", "导航", "高德地图", "百度地图",
    "outlook", "office 365", "microsoft 365", "电子邮件", "邮箱", "login", "oauth",
    "csdn", "博客", "教程", "软件下载", "app下载", "招聘", "人才", "考试", "天气",
    "房产", "二手房", "小说", "视频", "图片", "景区", "排行榜", "必去", "好玩",
    "知乎", "百家号", "字典", "词典", "成语", "机构概况", "联系我们", "网站地图",
]

IGNORE_TITLE_KEYWORDS = [
    "登录", "注册", "网站地图", "404", "403", "无标题", "error", "验证码", "用户中心", "redirect", "continue"
]

NOISY_DOMAINS = {
    "baike.baidu.com", "baike.sogou.com", "m.baike.com", "www.baike.com",
    "you.ctrip.com", "tripadvisor.cn", "www.tripadvisor.cn", "map.tianditu.gov.cn",
    "ditu.amap.com", "map.baidu.com", "outlook.office365.com", "outlook.live.com",
    "office.com", "microsoft.com", "zhuanlan.zhihu.com", "zhihu.com", "blog.csdn.net",
    "zhidao.baidu.com", "jingyan.baidu.com", "baijiahao.baidu.com", "tieba.baidu.com",
}

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "shandong_pv_data.json"
DEFAULT_META_OUTPUT = PROJECT_ROOT / "data" / "meta.json"
DEFAULT_LOG = PROJECT_ROOT / "logs" / "crawler.log"
RAW_LOG = PROJECT_ROOT / "logs" / "raw_results.jsonl"
REJECT_LOG = PROJECT_ROOT / "logs" / "reject_results.jsonl"
BLOCK_DIR = PROJECT_ROOT / "logs" / "blocked_pages"


# =========================================================
# 2. 数据结构
# =========================================================

@dataclass
class SearchPlan:
    query: str
    source: str
    site_domain: str
    category: str = "policy"
    strict_site: bool = True
    keyword: str = SEARCH_KEYWORD


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str = ""
    publish_date: str = ""
    source: str = ""
    query_category: str = ""
    query: str = ""
    site_domain: str = ""
    rank: int = 0
    provider: str = "drission_baidu_v36"


@dataclass
class CardItem:
    id: str
    title: str
    content: str
    category: str
    category_label: str
    source: str
    publish_date: str
    homepage_url: str
    baidu_search_url: str
    original_url: str
    collected_at: str
    relevance_score: int = 0
    matched_query: str = ""
    time_hint: str = ""


class SearchBlockedError(RuntimeError):
    """搜索引擎出现验证 / 风控页面时抛出。"""


# =========================================================
# 3. 日志
# =========================================================

def setup_logging(debug: bool = False, no_file_log: bool = False) -> None:
    DEFAULT_LOG.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [logging.StreamHandler(sys.stdout)]
    if not no_file_log:
        handlers.append(
            RotatingFileHandler(
                DEFAULT_LOG,
                maxBytes=2 * 1024 * 1024,
                backupCount=2,
                encoding="utf-8",
                delay=True,
            )
        )
    logging.basicConfig(level=level, format=fmt, handlers=handlers)


def append_jsonl(path: Path, row: dict, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        logging.warning("写入日志失败：%s err=%s", path, e)


def reset_jsonl(path: Path, enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        path.touch()
    except OSError as e:
        logging.warning("初始化日志失败：%s err=%s", path, e)


# =========================================================
# 4. 通用工具
# =========================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def now_compact() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def today_local() -> date:
    return datetime.now().date()


def cutoff_date_for_years(years: int = 2) -> date:
    return today_local() - timedelta(days=365 * max(1, years) + 1)


def clean_text(text: str, max_len: Optional[int] = None) -> str:
    if not text:
        return ""
    text = html.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\u00a0\u3000\t\r\n]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = text.strip(" -_丨|·\t\r\n ")
    if max_len and len(text) > max_len:
        text = text[:max_len].rstrip() + "..."
    return text


def normalize_url(url: str) -> str:
    if not url:
        return ""
    url = html.unescape(str(url)).strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            return url
        query_parts = []
        for part in parsed.query.split("&"):
            if not part:
                continue
            key = part.split("=", 1)[0].lower()
            if key.startswith("utm_") or key in {"spm", "from", "source", "isappinstalled", "fr", "srcid"}:
                continue
            query_parts.append(part)
        parsed = parsed._replace(query="&".join(query_parts), fragment="")
        return urlunparse(parsed)
    except Exception:
        return url


def host_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
        return host.lower().lstrip("www.")
    except Exception:
        return ""


def path_of(url: str) -> str:
    try:
        return urlparse(url).path.lower()
    except Exception:
        return ""


def same_or_subdomain(host: str, domain: str) -> bool:
    host = (host or "").lower().lstrip("www.")
    domain = (domain or "").lower().lstrip("www.")
    return bool(host and domain and (host == domain or host.endswith("." + domain)))


def contains_any(text: str, words: Iterable[str]) -> bool:
    text = (text or "").lower()
    return any(w.lower() in text for w in words)


def count_hits(text: str, words: Iterable[str]) -> int:
    text = (text or "").lower()
    return sum(1 for w in words if w.lower() in text)


def title_fingerprint(title: str) -> str:
    title = clean_text(title)
    title = re.sub(r"\.\.\.|……", "", title)
    title = re.sub(r"[\s\-—–_·，。、《》【】\[\]（）()：:；;,.!?！？'\"“”‘’]+", "", title)
    return title.lower()


def item_id(title: str, url: str) -> str:
    raw = f"{title_fingerprint(title)}|{normalize_url(url)}"
    return hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def baidu_url(title: str) -> str:
    return "https://www.baidu.com/s?wd=" + quote_plus(title)


# =========================================================
# 5. 数据源和日期处理
# =========================================================

def configured_domains() -> Dict[str, str]:
    domains: Dict[str, str] = {}
    for name, homepage in PV_SEARCH_CONFIG["official_websites"].items():
        h = host_of(homepage)
        if h:
            domains[h] = name
    return domains


def iter_configured_domains_longest_first() -> List[Tuple[str, str]]:
    return sorted(configured_domains().items(), key=lambda kv: len(kv[0]), reverse=True)


def is_configured_domain(url: str) -> bool:
    h = host_of(url)
    if not h:
        return False
    return any(same_or_subdomain(h, domain) for domain, _ in iter_configured_domains_longest_first())


def source_name_for_url(url: str) -> str:
    h = host_of(url)
    if not h:
        return "未知来源"
    for domain, name in iter_configured_domains_longest_first():
        if same_or_subdomain(h, domain):
            return name
    return h


def homepage_for_source(source: str, url: str = "") -> str:
    if source in PV_SEARCH_CONFIG["official_websites"]:
        return PV_SEARCH_CONFIG["official_websites"][source]
    parsed = urlparse(url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "https://www.baidu.com/s?wd=" + quote_plus(source or "山东 光伏")


def parse_any_date(text: str) -> str:
    """从标题、摘要、URL 中提取日期。不会用 query 里的年份兜底。"""
    if not text:
        return ""
    s = html.unescape(str(text))

    patterns = [
        r"(?<!\d)(20\d{2})[-/.年/](0?[1-9]|1[0-2])[-/.月/](0?[1-9]|[12]\d|3[01])(?:日)?(?!\d)",
        r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?!\d)",
        r"t(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])[_-]",
    ]

    for pattern in patterns:
        for m in re.finditer(pattern, s):
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                continue

    today = datetime.now().date()
    if "今天" in s:
        return today.isoformat()
    if "昨天" in s:
        return (today - timedelta(days=1)).isoformat()

    m = re.search(r"(?<!\d)(\d{1,2})\s*天前", s)
    if m:
        days = int(m.group(1))
        return (today - timedelta(days=days)).isoformat()

    m = re.search(r"(?<!\d)(\d{1,2})\s*小时前", s)
    if m:
        return today.isoformat()

    m = re.search(r"(?<!\d)(\d{1,2})\s*分钟前", s)
    if m:
        return today.isoformat()

    return ""


def date_allowed_by_publish_date(publish_date: str, years: int, allow_undated: bool) -> Tuple[bool, str]:
    cutoff = cutoff_date_for_years(years)
    today = today_local() + timedelta(days=3)

    if not publish_date:
        if allow_undated:
            return True, "undated_allowed"
        return False, "缺少发布日期/日期线索"

    try:
        d = datetime.strptime(publish_date, "%Y-%m-%d").date()
    except ValueError:
        return False, f"发布日期格式无效：{publish_date}"

    if cutoff <= d <= today:
        return True, f"publish_date:{publish_date}"

    return False, f"发布日期不在近{years}年范围：{publish_date}，范围起点：{cutoff.isoformat()}"


# =========================================================
# 6. 相关性过滤
# =========================================================

def data_text_of(title: str, snippet: str, url: str) -> str:
    return f"{clean_text(title)} {clean_text(snippet)} {normalize_url(url)}"


def has_pv_topic(text: str) -> bool:
    return contains_any(text, PV_TERMS)


def has_shandong_signal(text: str, url: str, source: str) -> bool:
    if contains_any(text, SHANDONG_TERMS):
        return True
    if source in LOCAL_SHANDONG_SOURCE_NAMES:
        return True
    h = host_of(url)
    return any(x in h for x in ["shandong", "jinan", "qingdao", "sdb.nea", "nyj.shandong", "ccgp-shandong"])


def is_noisy_domain(url: str) -> bool:
    h = host_of(url)
    return any(same_or_subdomain(h, d) for d in NOISY_DOMAINS)


def is_obvious_home_or_invalid_page(title: str, url: str) -> bool:
    p = path_of(url)
    fp = title_fingerprint(title)
    if p in {"", "/", "/index.html", "/index.htm", "/index.shtml"}:
        return True
    if fp in {
        title_fingerprint("山东省能源局"),
        title_fingerprint("山东能源监管办"),
        title_fingerprint("国家能源局"),
        title_fingerprint("国家电网"),
        title_fingerprint("山东政府采购网"),
        title_fingerprint("中国招标投标公共服务平台"),
        title_fingerprint("北极星太阳能光伏网"),
        title_fingerprint("首页"),
    }:
        return True
    low_url = normalize_url(url).lower()
    if contains_any(low_url, ["/login", "/logout", "/register", "/user", "/member", "/sitemap"]):
        return True
    return False


def infer_category_from_text(text: str, default: str = "policy") -> str:
    low = (text or "").lower()
    scores = {"policy": 0, "market": 0, "project": 0}
    for cat, words in CATEGORY_KEYWORDS.items():
        scores[cat] += count_hits(low, words)
    if re.search(r"招标|中标|成交|采购|epc|候选人|备案|公示|项目|标段|合同|公告|磋商|询价|名单|清单", low, re.I):
        scores["project"] += 5
    if re.search(r"市场|分析|报告|装机|发电量|电价|数据|趋势|消纳|产业|统计|观察|研究|机制电价|竞价|收益|调研|现状|前景", low):
        scores["market"] += 5
    if re.search(r"政策|通知|办法|细则|规则|监管|补贴|规划|方案|解读|印发|改革|申报|用地|配储", low):
        scores["policy"] += 5
    best, score = max(scores.items(), key=lambda kv: kv[1])
    return best if score > 0 else default


def relevance_score(title: str, snippet: str, url: str, query: str, plan: SearchPlan) -> int:
    text = data_text_of(title, snippet, url) + " " + query
    score = 0
    if is_configured_domain(url):
        score += 8
    if has_pv_topic(text):
        score += 6
    elif plan.strict_site and same_or_subdomain(host_of(url), plan.site_domain) and SEARCH_KEYWORD in query:
        score += 3
    if has_shandong_signal(text, url, source_name_for_url(url)):
        score += 4
    for words in CATEGORY_KEYWORDS.values():
        score += min(count_hits(text, words), 3)
    return score


def reject_reason(title: str, snippet: str, url: str, query: str, plan: SearchPlan, args: argparse.Namespace) -> str:
    title = clean_text(title)
    snippet = clean_text(snippet)
    url = normalize_url(url)
    text = data_text_of(title, snippet, url)
    source = source_name_for_url(url)

    if len(title) < 4:
        return "标题过短"
    if contains_any(title, IGNORE_TITLE_KEYWORDS):
        return "标题为登录/错误页等无效标题"
    if not args.allow_noisy_domains and is_noisy_domain(url):
        return f"噪声域名：{host_of(url)}"
    if contains_any(f"{title} {url}".lower(), NEGATIVE_TITLE_KEYWORDS):
        return "标题/URL 命中旅游/百科/地图/招聘等负面关键词"
    if not is_configured_domain(url):
        return f"非指定的 11 个数据源域名：{host_of(url)}"
    if plan.strict_site and not same_or_subdomain(host_of(url), plan.site_domain):
        return f"严格 site 查询返回非目标域名：期望 {plan.site_domain}，实际 {host_of(url)}"
    if is_obvious_home_or_invalid_page(title, url):
        return "首页/登录页/无效页"
    if not has_shandong_signal(text, url, source):
        return "缺少山东地域信号"
    if not has_pv_topic(text):
        if not (plan.strict_site and same_or_subdomain(host_of(url), plan.site_domain) and SEARCH_KEYWORD in query):
            return "缺少光伏/新能源有效上下文"
    return ""


def accept_result(title: str, snippet: str, url: str, query: str, plan: SearchPlan, args: argparse.Namespace) -> Tuple[bool, str, int]:
    reason = reject_reason(title, snippet, url, query, plan, args)
    score = relevance_score(title, snippet, url, query, plan)
    if reason:
        return False, reason, score
    threshold = min(args.min_score, 4) if args.loose else args.min_score
    if score < threshold:
        return False, f"相关性分数过低：{score} < {threshold}", score
    return True, "", score


def make_summary(snippet: str) -> str:
    snippet = clean_text(snippet, max_len=360)
    return snippet or "已通过百度检索到相关结果，点击详情地址可自行查看原网页内容。"


# =========================================================
# 7. DrissionPage 浏览器配置
# =========================================================

STEALTH_JS = r"""
(() => {
  try {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  } catch(e) {}

  try {
    window.chrome = window.chrome || {
      app: { isInstalled: false },
      runtime: {},
      webstore: {}
    };
  } catch(e) {}

  try {
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
  } catch(e) {}

  try {
    Object.defineProperty(navigator, 'plugins', {
      get: () => [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
        { name: 'Native Client', filename: 'internal-nacl-plugin' }
      ]
    });
  } catch(e) {}

  try {
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
  } catch(e) {}

  try {
    window.matchMedia = window.matchMedia || function() {
      return { matches: false, addListener: () => {}, removeListener: () => {} };
    };
  } catch(e) {}
})();
"""


def safe_call(obj, method_name: str, *args, **kwargs) -> bool:
    fn = getattr(obj, method_name, None)
    if not callable(fn):
        return False
    try:
        fn(*args, **kwargs)
        return True
    except TypeError:
        return False
    except Exception:
        return False


def add_chrome_arg(co: ChromiumOptions, arg: str) -> None:
    if safe_call(co, "set_argument", arg):
        return
    if safe_call(co, "set_arg", arg):
        return
    safe_call(co, "add_argument", arg)


def set_browser_path(co: ChromiumOptions, chrome_path: str) -> None:
    if not chrome_path:
        return
    if safe_call(co, "set_browser_path", chrome_path):
        return
    safe_call(co, "set_paths", browser_path=chrome_path)


def set_user_data_path(co: ChromiumOptions, user_data_dir: Path) -> None:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    if safe_call(co, "set_user_data_path", str(user_data_dir)):
        return
    safe_call(co, "set_paths", user_data_path=str(user_data_dir))


def find_chrome_path() -> str:
    env_path = os.environ.get("CHROME_PATH") or os.environ.get("GOOGLE_CHROME_BIN")
    if env_path and Path(env_path).exists():
        return env_path
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return str(p)
    return ""


def build_drission_page(args: argparse.Namespace) -> ChromiumPage:
    chrome_path = args.chrome_path or find_chrome_path()
    co = ChromiumOptions()

    if chrome_path:
        logging.info("使用 Chrome/Edge 路径：%s", chrome_path)
        set_browser_path(co, chrome_path)
    else:
        logging.warning("未找到 Chrome/Edge 路径，将由 DrissionPage 自动查找浏览器")

    if os.environ.get("GITHUB_ACTIONS") == "true" and not args.headless and not os.environ.get("DISPLAY"):
        logging.warning("当前是 GitHub Actions 且没有 DISPLAY。请使用 xvfb-run 运行，否则非 headless 浏览器可能启动失败。")

    if args.headless:
        add_chrome_arg(co, "--headless=new")
        logging.warning("已启用 --headless。百度更容易触发验证，GitHub Actions 中建议用 xvfb-run 且不要加 --headless。")

    # 稳定性参数
    add_chrome_arg(co, "--no-sandbox")
    add_chrome_arg(co, "--disable-dev-shm-usage")
    add_chrome_arg(co, "--disable-gpu")
    add_chrome_arg(co, "--disable-blink-features=AutomationControlled")
    add_chrome_arg(co, "--disable-infobars")
    add_chrome_arg(co, "--window-size=1366,900")
    add_chrome_arg(co, "--lang=zh-CN")
    add_chrome_arg(co, "--remote-debugging-port=0")
    add_chrome_arg(co, "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

    # 独立用户目录，保留少量 Cookie，比每次全新环境更稳定。
    user_data_dir = PROJECT_ROOT / ".chrome_user_data_baidu"
    if args.fresh_profile and user_data_dir.exists():
        shutil.rmtree(user_data_dir, ignore_errors=True)
    set_user_data_path(co, user_data_dir)

    try:
        page = ChromiumPage(addr_or_opts=co)
    except TypeError:
        page = ChromiumPage(co)

    safe_call(page, "add_init_js", STEALTH_JS)
    try:
        page.run_js(STEALTH_JS)
    except Exception:
        pass

    return page


def get_page_html(page: ChromiumPage) -> str:
    try:
        html_value = getattr(page, "html", "")
        if callable(html_value):
            return html_value()
        if isinstance(html_value, str):
            return html_value
    except Exception:
        pass
    try:
        return page.run_js("return document.documentElement.outerHTML;") or ""
    except Exception:
        return ""


def current_page_url(page: ChromiumPage) -> str:
    for attr in ("url", "current_url"):
        try:
            value = getattr(page, attr, "")
            if callable(value):
                value = value()
            if value:
                return str(value)
        except Exception:
            pass
    try:
        return str(page.run_js("return location.href;") or "")
    except Exception:
        return ""


def save_blocked_artifacts(page: ChromiumPage, html_text: str, reason: str = "blocked") -> None:
    BLOCK_DIR.mkdir(parents=True, exist_ok=True)
    prefix = f"{now_compact()}_{reason}"
    html_path = BLOCK_DIR / f"{prefix}.html"
    png_path = BLOCK_DIR / f"{prefix}.png"

    try:
        html_path.write_text(html_text or "", encoding="utf-8")
        logging.warning("已保存验证页面 HTML：%s", html_path)
    except Exception as e:
        logging.warning("保存验证页面 HTML 失败：%s", e)

    try:
        if safe_call(page, "get_screenshot", path=str(png_path), full_page=True):
            logging.warning("已保存验证页面截图：%s", png_path)
        elif safe_call(page, "get_screenshot", str(png_path)):
            logging.warning("已保存验证页面截图：%s", png_path)
    except Exception as e:
        logging.warning("保存验证页面截图失败：%s", e)


def is_baidu_blocked_html(html_text: str, url: str = "") -> bool:
    low = (html_text or "").lower()
    url_low = (url or "").lower()
    signals = [
        "百度安全验证",
        "请输入验证码",
        "验证码",
        "访问异常",
        "网络不给力",
        "请完成下方验证",
        "verify you are human",
        "captcha",
        "wappass.baidu.com",
        "/sorry/",
        "百度账号安全验证",
        "sec.baidu.com",
        "人机验证",
    ]
    return any(s.lower() in low or s.lower() in url_low for s in signals)


# =========================================================
# 8. 百度 URL 构造、跳转解析、结果解析
# =========================================================

def build_baidu_search_url(query: str, count: int, page_no: int) -> str:
    rn = max(10, min(int(count or 10), 50))
    pn = max(0, (page_no - 1) * rn)
    return (
        "https://www.baidu.com/s?"
        f"wd={quote_plus(query)}"
        f"&rn={rn}"
        f"&pn={pn}"
        "&ie=utf-8"
        "&tn=baiduhome_pg"
        "&f=8"
        "&oq=" + quote_plus(query)
    )


def maybe_decode_baidu_redirect_from_query(url: str) -> str:
    """处理少量带 url / u / wd 参数的百度跳转链接。百度 link?url= 通常不能直接从参数还原真实 URL。"""
    if not url:
        return ""
    url = html.unescape(str(url)).strip()
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if "baidu.com" not in host:
        return normalize_url(url)

    qs = parse_qs(parsed.query)
    for key in ["url", "u", "wd", "q"]:
        values = qs.get(key)
        if values:
            candidate = unquote(values[0])
            if candidate.startswith("http") and "baidu.com" not in host_of(candidate):
                return normalize_url(candidate)
    return normalize_url(url)


def resolve_baidu_redirect(url: str, timeout: int = 8) -> str:
    """解析百度搜索结果跳转 URL。优先用结果节点 mu 属性；没有 mu 时才尝试 requests 跟随 302。"""
    url = maybe_decode_baidu_redirect_from_query(url)
    if not url:
        return ""

    h = host_of(url)
    if "baidu.com" not in h:
        return normalize_url(url)

    if requests is None:
        return normalize_url(url)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.baidu.com/",
        }
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True, verify=False)
        final_url = normalize_url(resp.url or "")
        if final_url and "baidu.com" not in host_of(final_url):
            return final_url
    except Exception as e:
        logging.debug("解析百度跳转失败：%s err=%s", url, e)

    return normalize_url(url)


def get_attr_json_url(value: str) -> str:
    if not value:
        return ""
    try:
        data = json.loads(html.unescape(value))
        if isinstance(data, dict):
            for key in ["url", "mu", "href", "linkUrl"]:
                v = data.get(key)
                if isinstance(v, str) and v.startswith("http"):
                    return normalize_url(v)
    except Exception:
        return ""
    return ""


def extract_url_from_baidu_container(container, title_a) -> str:
    candidates: List[str] = []

    # 百度结果容器常见真实地址：mu 属性
    for attr in ["mu", "data-mu", "data-url", "url"]:
        v = container.get(attr) if container else ""
        if v:
            candidates.append(v)

    # data-tools 可能包含真实 url
    if container:
        for attr in ["data-tools", "data-log"]:
            v = get_attr_json_url(container.get(attr) or "")
            if v:
                candidates.append(v)

    if title_a:
        href = title_a.get("href") or ""
        if href:
            candidates.append(href)

    for c in candidates:
        c = html.unescape(str(c)).strip()
        if c.startswith("//"):
            c = "https:" + c
        if c.startswith("http") and "baidu.com" not in host_of(c):
            return normalize_url(c)

    # 没有真实地址时，最后尝试解析百度跳转
    for c in candidates:
        c = html.unescape(str(c)).strip()
        if c.startswith("http") or c.startswith("//"):
            return resolve_baidu_redirect(c)

    return ""


def extract_baidu_results(html_text: str, query: str, page_no: int, count: int, resolve_redirects: bool = False) -> List[SearchHit]:
    soup = BeautifulSoup(html_text or "", "html.parser")
    hits: List[SearchHit] = []
    seen: Set[str] = set()
    rank_base = (page_no - 1) * max(10, min(int(count or 10), 50))

    # 百度普通结果：result c-container；也兼容 result-op。
    containers = []
    for selector in [
        "div.result.c-container",
        "div.result-op.c-container",
        "div.c-container[id]",
        "div#content_left > div[id]",
    ]:
        for node in soup.select(selector):
            if node not in containers:
                containers.append(node)

    for container in containers:
        title_node = container.select_one("h3") or container.select_one("h3.t") or container.select_one("a")
        if not title_node:
            continue

        title_a = title_node.find("a") if getattr(title_node, "name", "") != "a" else title_node
        if not title_a:
            title_a = container.select_one("h3 a, a[href]")

        title = clean_text(title_node.get_text(" "), max_len=180)
        if not title:
            continue

        url = extract_url_from_baidu_container(container, title_a)
        if resolve_redirects and url and "baidu.com" in host_of(url):
            url = resolve_baidu_redirect(url)
        url = normalize_url(url)
        if not url or not url.startswith("http"):
            continue

        key = url or title_fingerprint(title)
        if key in seen:
            continue
        seen.add(key)

        # 摘要节点优先；找不到就用整个容器文本去掉标题。
        snippet_parts = []
        for selector in [
            ".c-abstract",
            ".c-span-last",
            ".content-right_8Zs40",
            ".c-gap-top-small",
            "span.c-color-text",
            "div[class*=content]",
        ]:
            for sn in container.select(selector):
                txt = clean_text(sn.get_text(" "), max_len=600)
                if txt and txt not in snippet_parts:
                    snippet_parts.append(txt)

        if snippet_parts:
            snippet = clean_text(" ".join(snippet_parts), max_len=700)
        else:
            all_text = clean_text(container.get_text(" "), max_len=900)
            snippet = clean_text(all_text.replace(title, " ", 1), max_len=700)

        # 清理百度快照、广告等噪声
        snippet = re.sub(r"百度快照|查看快照|翻译此页|cached", " ", snippet, flags=re.I)
        snippet = clean_text(snippet, max_len=700)

        publish_date = parse_any_date(f"{title} {snippet} {url}")
        hits.append(
            SearchHit(
                title=title,
                url=url,
                snippet=snippet,
                publish_date=publish_date,
                query=query,
                rank=rank_base + len(hits) + 1,
                provider="drission_baidu_v36",
            )
        )

    return hits


def open_baidu_search_page(page: ChromiumPage, query: str, count: int, timeout: int, page_no: int, after_load_sleep: float) -> bool:
    url = build_baidu_search_url(query=query, count=count, page_no=page_no)
    logging.debug("DrissionPage Baidu URL: %s", url)
    try:
        page.get(url, timeout=timeout)
        time.sleep(after_load_sleep)
        try:
            page.run_js(STEALTH_JS)
        except Exception:
            pass

        html_text = get_page_html(page)
        cur_url = current_page_url(page)
        if is_baidu_blocked_html(html_text, cur_url):
            save_blocked_artifacts(page, html_text, reason="baidu_verify")
            raise SearchBlockedError(f"百度触发验证/风控页面：{cur_url}")
        return True
    except SearchBlockedError:
        raise
    except Exception as e:
        logging.warning("打开百度搜索页失败 query=%s page=%s err=%s", query, page_no, e)
        return False


def search_with_drission_baidu(
        page: ChromiumPage,
        query: str,
        count: int,
        timeout: int,
        max_pages: int,
        page_sleep_min: float,
        page_sleep_max: float,
        after_load_sleep: float,
        resolve_redirects: bool = False,
) -> List[SearchHit]:
    """
    百度多页搜索：
    1. 不点击下一页，直接使用 pn 参数，GitHub Actions 更稳定；
    2. 每页检测验证页；
    3. 默认依赖百度结果容器 mu 属性获取真实 URL；必要时可加 --resolve-baidu-redirects。
    """
    hits: List[SearchHit] = []
    max_pages = max(1, int(max_pages or 1))
    count = max(10, min(int(count or 10), 50))

    for page_no in range(1, max_pages + 1):
        if page_no > 1:
            sleep_seconds = random.uniform(max(0.0, page_sleep_min), max(page_sleep_min, page_sleep_max))
            logging.debug("同一 query 翻页前暂停 %.2f 秒", sleep_seconds)
            time.sleep(sleep_seconds)

        ok = open_baidu_search_page(
            page=page,
            query=query,
            count=count,
            timeout=timeout,
            page_no=page_no,
            after_load_sleep=after_load_sleep,
        )
        if not ok:
            break

        html_text = get_page_html(page)
        page_hits = extract_baidu_results(
            html_text,
            query=query,
            page_no=page_no,
            count=count,
            resolve_redirects=resolve_redirects,
        )
        logging.debug("百度第 %s 页解析到 %s 条：%s", page_no, len(page_hits), query)

        if not page_hits:
            if page_no == 1:
                logging.info("百度当前 query 没有解析到结果：%s", query)
            break

        hits.extend(page_hits)

    # 结果去重
    seen: Set[str] = set()
    unique: List[SearchHit] = []
    for hit in hits:
        key = normalize_url(hit.url) or title_fingerprint(hit.title)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


# =========================================================
# 9. JSON 读写和实时保存
# =========================================================

def safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def card_from_dict(row: dict) -> Optional[CardItem]:
    if not isinstance(row, dict):
        return None
    title = clean_text(row.get("title") or "")
    url = normalize_url(row.get("original_url") or row.get("url") or "")
    content = clean_text(row.get("content") or row.get("snippet") or "", max_len=500)
    if not title or not url:
        return None
    category = row.get("category") or "policy"
    source = row.get("source") or source_name_for_url(url)
    return CardItem(
        id=row.get("id") or item_id(title, url),
        title=title,
        content=content,
        category=category,
        category_label=row.get("category_label") or CATEGORY_LABEL.get(category, category),
        source=source,
        publish_date=row.get("publish_date") or "",
        homepage_url=row.get("homepage_url") or homepage_for_source(source, url),
        baidu_search_url=row.get("baidu_search_url") or baidu_url(title),
        original_url=url,
        collected_at=row.get("collected_at") or now_iso(),
        relevance_score=safe_int(row.get("relevance_score"), 0),
        matched_query=row.get("matched_query") or "",
        time_hint=row.get("time_hint") or "",
    )


def load_existing_items(output: Path) -> List[CardItem]:
    if not output.exists():
        return []
    try:
        with output.open("r", encoding="utf-8") as f:
            data = json.load(f)
        raw_items = data.get("items", []) if isinstance(data, dict) else data
        items: List[CardItem] = []
        if isinstance(raw_items, list):
            for row in raw_items:
                item = card_from_dict(row)
                if item:
                    items.append(item)
        logging.info("已读取旧数据：%s 条", len(items))
        return items
    except Exception as e:
        logging.warning("读取旧数据失败，将按空数据处理：%s err=%s", output, e)
        return []


def item_quality_score(item: CardItem) -> float:
    score = float(item.relevance_score or 0)
    if item.publish_date:
        score += 2
    if item.content:
        score += min(len(item.content), 360) / 120
    if item.source and item.source != "未知来源":
        score += 1
    return score


def sort_items(items: List[CardItem]) -> List[CardItem]:
    return sorted(
        items,
        key=lambda item: (item.publish_date or "0000-00-00", item.relevance_score, item.collected_at),
        reverse=True,
    )


def should_replace_item(old: CardItem, new: CardItem) -> bool:
    old_score = item_quality_score(old)
    new_score = item_quality_score(new)
    if new.publish_date and old.publish_date:
        if new.publish_date > old.publish_date:
            new_score += 1
        elif new.publish_date < old.publish_date:
            old_score += 1
    return new_score > old_score


def upsert_item(items: List[CardItem], new_item: CardItem) -> Tuple[List[CardItem], bool, str]:
    new_url = normalize_url(new_item.original_url)
    new_title_key = title_fingerprint(new_item.title)
    for idx, old in enumerate(items):
        old_url = normalize_url(old.original_url)
        old_title_key = title_fingerprint(old.title)
        same_url = bool(new_url and old_url and new_url == old_url)
        same_title = bool(new_title_key and old_title_key and new_title_key == old_title_key)
        if same_url or same_title:
            if should_replace_item(old, new_item):
                items[idx] = new_item
                return sort_items(items), True, "replaced"
            return items, False, "skipped"
    items.append(new_item)
    return sort_items(items), True, "inserted"


def build_output_data(items: List[CardItem], years: int = 2) -> dict:
    return {
        "generated_at": now_iso(),
        "total": len(items),
        "date_rule": {
            "mode": "publish_date_only",
            "years": years,
            "cutoff_date": cutoff_date_for_years(years).isoformat(),
            "allow_undated_default": False,
        },
        "query_scope": {
            "mode": "baidu_fixed_site_keyword_plus_global_shandong",
            "site_keywords": [kw for kw, _ in SITE_QUERY_KEYWORDS],
            "global_queries": [q for q, _ in GLOBAL_QUERIES],
            "query_example": "site:sdb.nea.gov.cn 光伏政策",
            "websites": PV_SEARCH_CONFIG["official_websites"],
            "query_count": len(PV_SEARCH_CONFIG["official_websites"]) * len(SITE_QUERY_KEYWORDS) + len(GLOBAL_QUERIES),
            "provider": "baidu",
            "path_positive_rules_removed": True,
        },
        "category_stats": {
            "policy": sum(1 for item in items if item.category == "policy"),
            "market": sum(1 for item in items if item.category == "market"),
            "project": sum(1 for item in items if item.category == "project"),
        },
        "items": [asdict(item) for item in items],
    }


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
    tmp_path.replace(path)


def save_items_snapshot(items: List[CardItem], output: Path, meta_output: Path, max_total_items: int = 0,
                        years: int = 2) -> List[CardItem]:
    items = sort_items(items)
    if max_total_items and max_total_items > 0:
        items = items[:max_total_items]
    data = build_output_data(items, years=years)
    atomic_write_json(output, data)
    atomic_write_json(
        meta_output,
        {
            "generated_at": data["generated_at"],
            "total": data["total"],
            "date_rule": data["date_rule"],
            "query_scope": data["query_scope"],
            "category_stats": data["category_stats"],
        },
    )
    return items


# =========================================================
# 10. 查询计划
# =========================================================

def build_queries(args: argparse.Namespace) -> List[SearchPlan]:
    plans: List[SearchPlan] = []

    for source, homepage in PV_SEARCH_CONFIG["official_websites"].items():
        domain = host_of(homepage)
        if not domain:
            continue

        for keyword, category in SITE_QUERY_KEYWORDS:
            plans.append(
                SearchPlan(
                    query=f"site:{domain} {keyword}",
                    source=source,
                    site_domain=domain,
                    category=category,
                    strict_site=True,
                    keyword=keyword,
                )
            )

    for query, category in GLOBAL_QUERIES:
        plans.append(
            SearchPlan(
                query=query,
                source="全局山东省检索",
                site_domain="",
                category=category,
                strict_site=False,
                keyword=query,
            )
        )

    seen = set()
    unique: List[SearchPlan] = []
    for plan in plans:
        key = (plan.query, plan.source, plan.site_domain, plan.category, plan.strict_site)
        if key in seen:
            continue
        seen.add(key)
        unique.append(plan)

    if args.shuffle_queries:
        random.shuffle(unique)

    logging.info(
        "生成查询计划总数：%d；百度查询格式：site:域名 光伏政策 / site:域名 光伏市场分析 / site:域名 光伏项目；全局查询：%s",
        len(unique),
        "；".join(q for q, _ in GLOBAL_QUERIES),
    )
    return unique


# =========================================================
# 11. 主流程
# =========================================================

def log_reject(hit: SearchHit, reason: str, score: int = 0, detail_log_enabled: bool = True) -> None:
    append_jsonl(
        REJECT_LOG,
        {
            "reason": reason,
            "score": score,
            "title": hit.title,
            "url": hit.url,
            "snippet": hit.snippet,
            "source": hit.source,
            "publish_date": hit.publish_date,
            "query": hit.query,
            "query_category": hit.query_category,
            "site_domain": hit.site_domain,
            "rank": hit.rank,
            "provider": hit.provider,
            "time": now_iso(),
        },
        enabled=detail_log_enabled,
    )


def close_drission_page(page: Optional[ChromiumPage]) -> None:
    """安全关闭浏览器，避免 GitHub Actions 上残留 Chrome 进程。"""
    if page is None:
        return
    try:
        page.quit()
        return
    except Exception:
        pass
    try:
        page.close()
    except Exception:
        pass


def sleep_with_log(seconds: float, reason: str) -> None:
    seconds = max(0.0, float(seconds or 0))
    if seconds <= 0:
        return
    logging.info("%s，暂停 %.2f 秒", reason, seconds)
    time.sleep(seconds)


def collect_items(args: argparse.Namespace) -> List[CardItem]:
    output_path = Path(args.out)
    meta_output_path = Path(args.meta_out)

    if args.overwrite:
        live_items: List[CardItem] = []
        live_items = save_items_snapshot(live_items, output_path, meta_output_path, args.max_total_items, years=args.years)
        logging.info("覆盖模式：已初始化 ./data/shandong_pv_data.json，后续符合条件的数据会实时写入")
    else:
        live_items = load_existing_items(output_path)
        live_items = save_items_snapshot(live_items, output_path, meta_output_path, args.max_total_items, years=args.years)
        logging.info("追加模式：旧数据已载入，后续符合条件的数据会实时追加写入")

    plans = build_queries(args)
    if args.limit_queries and args.limit_queries > 0:
        plans = plans[: args.limit_queries]

    cutoff = cutoff_date_for_years(args.years)
    logging.info(
        "准备执行百度搜索 query 数：%s；近%s年发布日期范围起点：%s；每个 query 最多翻页：%s",
        len(plans),
        args.years,
        cutoff.isoformat(),
        args.max_pages_per_query,
    )

    seen_raw_urls: Set[str] = set()
    searched_count = 0
    raw_unique_count = 0
    raw_dedup_count = 0
    accepted_count = 0
    inserted_count = 0
    replaced_count = 0
    skipped_count = 0
    rejected_count = 0

    page: Optional[ChromiumPage] = None
    queries_since_browser_restart = 0
    restart_every = max(0, int(args.restart_browser_every or 0))
    restart_sleep = max(0.0, float(args.restart_sleep or 0))

    try:
        for index, plan in enumerate(plans, start=1):
            if page is None:
                logging.info("启动浏览器：准备执行第 %s/%s 个 query", index, len(plans))
                page = build_drission_page(args)
                queries_since_browser_restart = 0

            logging.info("[%s/%s] DrissionPage 百度检索：%s", index, len(plans), plan.query)

            try:
                hits = search_with_drission_baidu(
                    page=page,
                    query=plan.query,
                    count=args.max_results_per_query,
                    timeout=args.timeout,
                    max_pages=args.max_pages_per_query,
                    page_sleep_min=args.page_sleep_min,
                    page_sleep_max=args.page_sleep_max,
                    after_load_sleep=args.after_load_sleep,
                    resolve_redirects=args.resolve_baidu_redirects,
                )
            except SearchBlockedError as e:
                logging.error("检测到百度验证，停止任务：%s", e)
                if args.stop_on_verify:
                    break
                close_drission_page(page)
                page = None
                sleep_with_log(restart_sleep, "检测到百度验证，已关闭浏览器，准备更换浏览器会话后继续")
                continue

            queries_since_browser_restart += 1
            searched_count += len(hits)

            for hit in hits:
                hit.query_category = plan.category
                hit.query = plan.query
                hit.site_domain = plan.site_domain
                hit.source = plan.source

                url = normalize_url(hit.url)
                raw_key = url or title_fingerprint(hit.title)
                if not raw_key:
                    continue
                if raw_key in seen_raw_urls:
                    raw_dedup_count += 1
                    continue
                seen_raw_urls.add(raw_key)
                raw_unique_count += 1

                append_jsonl(RAW_LOG, asdict(hit), enabled=not args.no_detail_logs)

                title = clean_text(hit.title, max_len=180)
                snippet = clean_text(hit.snippet, max_len=700)
                publish_date = hit.publish_date or parse_any_date(f"{title} {snippet} {url}")

                ok, reason, score = accept_result(
                    title=title,
                    snippet=snippet,
                    url=url,
                    query=plan.query,
                    plan=plan,
                    args=args,
                )
                if not ok:
                    rejected_count += 1
                    log_reject(hit, reason, score, detail_log_enabled=not args.no_detail_logs)
                    continue

                date_ok, date_reason = date_allowed_by_publish_date(
                    publish_date=publish_date,
                    years=args.years,
                    allow_undated=args.allow_undated,
                )
                if not date_ok:
                    rejected_count += 1
                    log_reject(hit, date_reason, score, detail_log_enabled=not args.no_detail_logs)
                    continue

                category = infer_category_from_text(f"{title} {snippet}", default=plan.category)
                if args.category and category != args.category:
                    rejected_count += 1
                    log_reject(hit, f"分类不匹配：{category} != {args.category}", score,
                               detail_log_enabled=not args.no_detail_logs)
                    continue

                source = source_name_for_url(url)
                card = CardItem(
                    id=item_id(title, url),
                    title=title,
                    content=make_summary(snippet),
                    category=category,
                    category_label=CATEGORY_LABEL.get(category, category),
                    source=source,
                    publish_date=publish_date or "",
                    homepage_url=homepage_for_source(source, url),
                    baidu_search_url=baidu_url(title),
                    original_url=url,
                    collected_at=now_iso(),
                    relevance_score=score,
                    matched_query=plan.query,
                    time_hint=date_reason,
                )

                accepted_count += 1
                live_items, changed, action = upsert_item(live_items, card)
                if action == "inserted":
                    inserted_count += 1
                elif action == "replaced":
                    replaced_count += 1
                else:
                    skipped_count += 1

                if changed:
                    live_items = save_items_snapshot(live_items, output_path, meta_output_path, args.max_total_items,
                                                     years=args.years)
                    logging.info(
                        "实时写入 ./data/shandong_pv_data.json：%s | 当前总数 %s | %s | %s | 来源=%s | 日期=%s",
                        "新增" if action == "inserted" else "更新",
                        len(live_items),
                        category,
                        title,
                        source,
                        publish_date,
                    )
                else:
                    logging.debug("重复跳过，不写入：%s", title)

            has_next_query = index < len(plans)
            if has_next_query and restart_every > 0 and queries_since_browser_restart >= restart_every:
                close_drission_page(page)
                page = None
                logging.info(
                    "已连续查询 %s 个 query，关闭浏览器并准备重启，降低百度验证概率",
                    queries_since_browser_restart,
                )
                sleep_with_log(restart_sleep, "浏览器已退出")
                continue

            sleep_min = max(0.0, float(args.query_sleep_min))
            sleep_max = max(sleep_min, float(args.query_sleep_max))
            if sleep_max > 0 and has_next_query:
                sleep_seconds = random.uniform(sleep_min, sleep_max)
                logging.info("本次 query 完成，暂停 %.2f 秒后继续", sleep_seconds)
                time.sleep(sleep_seconds)

    finally:
        close_drission_page(page)

    live_items = save_items_snapshot(live_items, output_path, meta_output_path, args.max_total_items, years=args.years)
    logging.info(
        "采集结束：搜索返回 %s 条，RAW唯一 %s 条，RAW去重 %s 条，符合条件 %s 条，新增 %s 条，更新 %s 条，重复跳过 %s 条，拒绝 %s 条，最终总数 %s 条",
        searched_count,
        raw_unique_count,
        raw_dedup_count,
        accepted_count,
        inserted_count,
        replaced_count,
        skipped_count,
        rejected_count,
        len(live_items),
    )
    return live_items


# =========================================================
# 12. 命令行参数
# =========================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="山东光伏情报站 - DrissionPage + 百度检索版 V36")

    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="输出 JSON 文件路径")
    parser.add_argument("--meta-out", default=str(DEFAULT_META_OUTPUT), help="输出 meta JSON 文件路径")
    parser.add_argument("--years", type=int, default=2, help="仅保留近 N 年发布日期数据，默认 2 年")
    parser.add_argument("--max-total-items", type=int, default=0, help="最多保留多少条，0 表示不限制")
    parser.add_argument("--overwrite", action="store_true", help="覆盖模式：清空旧数据后重跑")

    parser.add_argument("--limit-queries", type=int, default=0, help="限制 query 数量，0 表示不限制")
    parser.add_argument("--max-results-per-query", type=int, default=10, help="每页读取结果数，建议 10")
    parser.add_argument("--max-pages-per-query", type=int, default=1, help="每个 query 最多翻页数；百度建议 1")
    parser.add_argument("--timeout", type=int, default=35, help="页面加载超时时间秒")
    parser.add_argument("--query-sleep-min", type=float, default=20.0, help="每次 query 后最小停顿秒数")
    parser.add_argument("--query-sleep-max", type=float, default=35.0, help="每次 query 后最大停顿秒数")
    parser.add_argument("--page-sleep-min", type=float, default=3.0, help="同一 query 翻页最小停顿秒数")
    parser.add_argument("--page-sleep-max", type=float, default=8.0, help="同一 query 翻页最大停顿秒数")
    parser.add_argument("--after-load-sleep", type=float, default=3.0, help="页面加载后等待秒数")
    parser.add_argument("--restart-browser-every", type=int, default=3, help="每查询 N 个 query 后重启浏览器；0 表示不重启，默认 3")
    parser.add_argument("--restart-sleep", type=float, default=60.0, help="重启浏览器后暂停秒数，默认 60")

    parser.add_argument("--allow-undated", action="store_true", help="允许没有发布日期/日期线索的结果入库；默认不允许")
    parser.add_argument("--allow-noisy-domains", action="store_true", help="允许噪声域名入候选，不建议开启")
    parser.add_argument("--loose", action="store_true", help="放宽相关性分数阈值")
    parser.add_argument("--min-score", type=int, default=6, help="最小相关性分数，默认 6；结果少可调到 4")
    parser.add_argument("--category", choices=["policy", "market", "project"], default="", help="只保留某个分类")

    parser.add_argument("--headless", action="store_true", help="无界面运行；百度容易触发验证，GitHub Actions 不建议开启")
    parser.add_argument("--chrome-path", default=os.environ.get("CHROME_PATH", ""), help="Chrome/Edge 可执行文件路径")
    parser.add_argument("--fresh-profile", action="store_true", help="运行前清空浏览器用户目录")
    parser.add_argument("--shuffle-queries", action="store_true", help="随机打乱查询顺序，降低固定模式")
    parser.add_argument("--resolve-baidu-redirects", action="store_true", help="当百度结果没有 mu 真实地址时，尝试 requests 解析跳转")
    parser.add_argument("--stop-on-verify", action="store_true", default=True, help="检测到百度验证时停止任务，默认开启")
    parser.add_argument("--no-stop-on-verify", dest="stop_on_verify", action="store_false", help="检测到百度验证后跳过当前 query 继续，不推荐")

    parser.add_argument("--no-detail-logs", action="store_true", help="不写 raw_results.jsonl 和 reject_results.jsonl")
    parser.add_argument("--no-file-log", action="store_true", help="不写 logs/crawler.log")
    parser.add_argument("--debug", action="store_true", help="输出 debug 日志")

    return parser.parse_args()


# =========================================================
# 13. 入口
# =========================================================

def main() -> int:
    args = parse_args()
    setup_logging(debug=args.debug, no_file_log=args.no_file_log)

    logging.info("运行模式：%s", "headless 无头模式" if args.headless else "窗口模式 / Xvfb 虚拟窗口模式")
    if not args.headless:
        logging.info("当前未启用 --headless；GitHub Actions 请使用 xvfb-run 包裹运行。")
    if args.restart_browser_every and args.restart_browser_every > 0:
        logging.info(
            "浏览器重启策略：每查询 %s 个 query 关闭浏览器，暂停 %.2f 秒后继续",
            args.restart_browser_every,
            args.restart_sleep,
        )
    else:
        logging.info("浏览器重启策略：已关闭")

    try:
        reset_jsonl(RAW_LOG, enabled=not args.no_detail_logs)
        reset_jsonl(REJECT_LOG, enabled=not args.no_detail_logs)
        collect_items(args)
        return 0
    except KeyboardInterrupt:
        logging.warning("用户中断，已采集到的合规数据已实时写入结果文件")
        return 130
    except SearchBlockedError as e:
        logging.error("搜索被验证中断：%s", e)
        return 2
    except Exception:
        logging.error("运行失败：\n%s", traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
