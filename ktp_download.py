"""
课堂派资料下载工具 v3.0
自动探测并下载任意课程的所有内容（资料、互动课件、作业等）。

功能：
- 自动探测课程的所有内容类型
- 同时从 getCourseContent 和 getListsByFileType 获取文件
- 并发下载、断点续传、自动重试
- 进度条显示
- 文件类型筛选
- 下载日志
"""

import os
import sys
import re
import json
import hashlib
import logging
import argparse
import time
from datetime import datetime
from urllib.parse import unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
except ImportError:
    print("请先安装 requests: pip install requests")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

API_BASE = "https://openapiv5.ketangpai.com"

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
DEFAULT_OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")

CONTENT_TYPES = {
    "1": "互动课件",
    "2": "资料",
    "3": "案例学习",
    "4": "作业",
    "5": "话题",
    "6": "测试",
    "7": "公告",
    "8": "资料",
    "22": "腾讯会议",
    "25": "案例学习(AI)",
}


def setup_logging(verbose=False):
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file = os.path.join(LOG_DIR, f"download_{datetime.now():%Y%m%d_%H%M%S}.log")
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout) if verbose else logging.NullHandler(),
    ])
    return logging.getLogger("ktp")


def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


class ProgressBar:
    def __init__(self, total, desc=""):
        self.total = total
        self.desc = desc
        self.downloaded = 0
        self.failed = 0
        self.skipped = 0
        self._lock = Lock()
        if tqdm:
            self._bar = tqdm(total=total, desc=desc, unit="file", ncols=80)
        else:
            self._bar = None
            print(f"\n{desc} 共 {total} 个文件")

    def update(self, status="ok"):
        with self._lock:
            if status == "ok":
                self.downloaded += 1
            elif status == "fail":
                self.failed += 1
            elif status == "skip":
                self.skipped += 1
            if self._bar:
                self._bar.update(1)
            elif not self._bar:
                done = self.downloaded + self.failed + self.skipped
                print(f"\r  进度: {done}/{self.total} (成功:{self.downloaded} 跳过:{self.skipped} 失败:{self.failed})", end="", flush=True)

    def close(self):
        if self._bar:
            self._bar.close()
        else:
            print()

    def summary(self):
        return f"成功:{self.downloaded} 跳过:{self.skipped} 失败:{self.failed}"


class KetangpaiDownloader:
    def __init__(self, token=None, account=None, password=None, max_workers=3,
                 max_retries=3, timeout=120, log=None):
        self.token = token
        self.account = account
        self.password = password
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.timeout = timeout
        self.log = log or logging.getLogger("ktp")
        self.session = self._make_session()

    def _make_session(self):
        session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if self.token:
            session.headers["token"] = self.token
        return session

    def login(self, account=None, password=None, code=""):
        account = account or self.account
        password = password or self.password
        if not account or not password:
            return False, "未提供账号或密码"
        try:
            from Crypto.Cipher import AES
            from Crypto.Util.Padding import pad
            import base64
            import time as _time

            key = b"ktp4567890123456"
            iv = b"ktp4567890123456"
            cipher = AES.new(key, AES.MODE_CBC, iv=iv)
            encrypted = base64.b64encode(cipher.encrypt(pad(password.encode("utf-8"), AES.block_size))).decode("utf-8")

            reqtimestamp = int(_time.time() * 1000)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            }
            payload = json.dumps({
                "email": account,
                "password": encrypted,
                "remember": "0",
                "code": "",
                "mobile": "",
                "type": "login",
                "encryption": 1,
                "reqtimestamp": reqtimestamp
            }, separators=(",", ":"))
            resp = self.session.post(f"{API_BASE}/UserApi/login", data=payload, headers=headers, timeout=self.timeout)
            data = resp.json()
            if data.get("code") != 10000:
                return False, data.get("msg", "登录失败")
            token = data.get("data", {}).get("token")
            if not token:
                return False, "登录响应中未找到 token"
            self.token = token
            self.session.headers["token"] = self.token
            self.log.info("登录成功")
            return True, "登录成功"
        except requests.RequestException as e:
            return False, f"网络错误: {e}"

    def check_token(self):
        if not self.token:
            return False
        try:
            resp = self.session.post(f"{API_BASE}/UserApi/getUserBasinInfo", json={}, timeout=self.timeout)
            data = resp.json()
            return data.get("status") == 1
        except Exception:
            return False

    def ensure_login(self):
        if self.check_token():
            return True
        self.log.warning("Token 已过期，正在自动登录...")
        if not self.account or not self.password:
            self.log.error("未配置账号密码，无法自动登录")
            return False
        success, msg = self.login()
        if success:
            return True
        self.log.error(f"自动登录失败: {msg}")
        return False

    def get_course_list(self):
        if not self.ensure_login():
            return []
        try:
            resp = self.session.post(f"{API_BASE}/FutureV2/CourseMeans/getCourseList", json={}, timeout=self.timeout)
            data = resp.json()
            if data.get("status") != 1:
                self.log.error(f"获取课程列表失败: {data.get('message')}")
                return []
            return data.get("data", [])
        except requests.RequestException as e:
            self.log.error(f"网络错误: {e}")
            return []

    def _fetch_course_content(self, courseid, contenttype=None):
        """通过 getCourseContent API 获取内容"""
        all_items = []
        page = 1
        limit = 50
        while True:
            body = {"courseid": courseid, "page": page, "limit": limit}
            if contenttype is not None:
                body["contenttype"] = contenttype
            try:
                resp = self.session.post(f"{API_BASE}/FutureV2/CourseMeans/getCourseContent", json=body, timeout=self.timeout)
                data = resp.json()
                if data.get("status") != 1:
                    break
                raw = data.get("data", {})
                if isinstance(raw, list):
                    break
                total = raw.get("total", 0)
                lst = raw.get("list", [])
                for item in lst:
                    atts = item.get("attachment", [])
                    if atts:
                        all_items.append(item)
                    else:
                        for child in item.get("children", []):
                            if child.get("attachment"):
                                all_items.append(child)
                if len(all_items) >= total or not lst:
                    break
                page += 1
            except Exception as e:
                self.log.debug(f"getCourseContent(contenttype={contenttype}) 失败: {e}")
                break
        return all_items

    def _fetch_courseware_api(self, courseid):
        """通过 getListsByFileType API 获取资料"""
        all_items = []
        page = 1
        limit = 50
        while True:
            try:
                resp = self.session.post(f"{API_BASE}/CoursewareApi/getListsByFileType", json={
                    "courseid": courseid, "fileType": "", "page": page, "limit": limit
                }, timeout=self.timeout)
                data = resp.json()
                if data.get("status") != 1:
                    break
                result = data.get("data", {})
                items = result.get("list", [])
                total = result.get("total", 0)
                all_items.extend(items)
                if len(all_items) >= total or not items:
                    break
                page += 1
            except requests.RequestException as e:
                self.log.debug(f"getListsByFileType 第{page}页失败: {e}")
                break
        return all_items

    def get_course_template(self, courseid):
        """获取课程模板，确定有哪些内容类型"""
        try:
            resp = self.session.post(f"{API_BASE}/Futurev2/CourseTemplate/getSetting", json={"courseid": courseid}, timeout=self.timeout)
            data = resp.json()
            if data.get("status") == 1:
                return data.get("data", {}).get("navigation", [])
        except Exception:
            pass
        return []

    def get_all_course_files(self, courseid):
        """获取课程所有可下载文件（自动探测所有来源）"""
        seen = set()
        all_items = []

        def dedup_add(items):
            for item in items:
                atts = item.get("attachment", [])
                for att in atts:
                    url = att.get("url", "")
                    if url and url not in seen:
                        seen.add(url)
                        all_items.append(item)
                        break

        nav = self.get_course_template(courseid)
        contenttypes_found = set()
        for n in nav:
            ct = n.get("contenttype", "")
            if isinstance(ct, list):
                contenttypes_found.update(ct)
            elif ct and ct != "0":
                contenttypes_found.add(ct)

        for ct in sorted(contenttypes_found):
            items = self._fetch_course_content(courseid, contenttype=ct)
            if items:
                ct_name = CONTENT_TYPES.get(ct, f"类型{ct}")
                self.log.info(f"  [{ct_name}] 获取到 {len(items)} 个文件")
                dedup_add(items)

        if not all_items:
            items = self._fetch_course_content(courseid)
            if items:
                self.log.info(f"  [通用] 获取到 {len(items)} 个文件")
                dedup_add(items)

        if not all_items:
            items = self._fetch_courseware_api(courseid)
            if items:
                self.log.info(f"  [资料API] 获取到 {len(items)} 个文件")
                dedup_add(items)

        return all_items

    def sanitize_filename(self, name):
        name = unquote(name)
        name = re.sub(r'[\\/:*?"<>|]', '_', name)
        name = name.strip('. ')
        return name[:200] if len(name) > 200 else name

    def _download_with_retry(self, url, save_path, file_size=None):
        for attempt in range(1, self.max_retries + 1):
            try:
                existing_size = 0
                if os.path.exists(save_path):
                    existing_size = os.path.getsize(save_path)

                dl_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "*/*",
                    "Accept-Encoding": "identity",
                    "Connection": "keep-alive",
                }
                if existing_size > 0 and file_size and existing_size < file_size:
                    dl_headers["Range"] = f"bytes={existing_size}-"
                    mode = "ab"
                elif existing_size > 0 and (not file_size or existing_size >= file_size):
                    return True, "exists"
                else:
                    mode = "wb"

                resp = requests.get(url, stream=True, timeout=self.timeout, headers=dl_headers)
                if resp.status_code == 416:
                    return True, "exists"
                resp.raise_for_status()

                total = int(resp.headers.get("content-length", 0)) + existing_size if mode == "ab" else int(resp.headers.get("content-length", 0))

                if tqdm and total > 0:
                    bar = tqdm(total=total, unit="B", unit_scale=True, leave=False, ncols=80)
                    if existing_size > 0:
                        bar.update(existing_size)
                else:
                    bar = None

                with open(save_path, mode) as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            if bar:
                                bar.update(len(chunk))

                if bar:
                    bar.close()
                return True, "ok"

            except requests.RequestException as e:
                self.log.debug(f"下载尝试 {attempt}/{self.max_retries} 失败: {e}")
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 10))
                else:
                    return False, str(e)
        return False, "max retries exceeded"

    def download_file(self, url, save_path, file_size=None):
        ok, status = self._download_with_retry(url, save_path, file_size)
        return ok

    def download_course(self, courseid, course_name, output_dir=None,
                        file_filter=None, skip_existing=True):
        if output_dir is None:
            output_dir = DEFAULT_OUTPUT
        course_dir = os.path.join(output_dir, self.sanitize_filename(course_name))
        os.makedirs(course_dir, exist_ok=True)

        self.log.info(f"正在获取 [{course_name}] 的文件列表...")
        items = self.get_all_course_files(courseid)
        if not items:
            self.log.info(f"  [{course_name}] 没有找到任何文件")
            return 0, 0, 0

        if file_filter:
            ext_set = {e.lower().strip(".") for e in file_filter}
            filtered = []
            for item in items:
                atts = item.get("attachment", [])
                for att in atts:
                    name = att.get("name", "")
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext in ext_set:
                        filtered.append(item)
                        break
            items = filtered
            self.log.info(f"  筛选后剩余 {len(items)} 个文件")

        tasks = []
        for item in items:
            title = item.get("title", "未知文件")
            attachments = item.get("attachment", [])
            if not attachments:
                continue
            att = attachments[0]
            download_url = att.get("url", "")
            file_name = att.get("name", title)
            file_size_str = att.get("size", "")
            if not download_url:
                continue
            safe_name = self.sanitize_filename(file_name)
            save_path = os.path.join(course_dir, safe_name)
            if skip_existing and os.path.exists(save_path):
                continue
            tasks.append({
                "url": download_url,
                "save_path": save_path,
                "name": safe_name,
                "size": file_size_str,
            })

        if not tasks:
            self.log.info(f"  [{course_name}] 所有文件已下载，无需更新")
            return 0, 0, 0

        bar = ProgressBar(len(tasks), desc=course_name)
        results = {"ok": 0, "skip": 0, "fail": 0}

        def do_download(task):
            try:
                ok = self.download_file(task["url"], task["save_path"])
                if ok:
                    return "ok", task["name"]
                return "fail", task["name"]
            except Exception as e:
                return "fail", f"{task['name']}: {e}"

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(do_download, t): t for t in tasks}
            for future in as_completed(futures):
                status, name = future.result()
                bar.update(status)
                results[status] += 1
                if status == "fail":
                    self.log.warning(f"  下载失败: {name}")

        bar.close()
        self.log.info(f"[{course_name}] {bar.summary()}")
        self.log.info(f"  保存路径: {course_dir}")
        return results["ok"], results["skip"], results["fail"]


def main():
    parser = argparse.ArgumentParser(description="课堂派资料下载工具 v3.0")
    parser.add_argument("-u", "--account", help="手机号/邮箱")
    parser.add_argument("-p", "--password", help="密码")
    parser.add_argument("-c", "--course", type=int, help="课程编号 (0=全部)")
    parser.add_argument("-o", "--output", help="输出目录")
    parser.add_argument("-w", "--workers", type=int, default=3, help="并发下载数 (默认3)")
    parser.add_argument("-r", "--retries", type=int, default=3, help="最大重试次数 (默认3)")
    parser.add_argument("-f", "--filter", nargs="+", help="文件扩展名筛选，如: ppt pdf zip")
    parser.add_argument("--no-skip", action="store_true", help="不跳过已存在的文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    log = setup_logging(args.verbose)
    log.info("=" * 50)
    log.info("       课堂派资料下载工具 v3.0")
    log.info("=" * 50)

    config = load_config()
    account = args.account or config.get("account")
    password = args.password or config.get("password")
    token = config.get("token")

    if not account:
        account = input("请输入课堂派手机号/邮箱: ").strip()
    if not password:
        import getpass
        password = getpass.getpass("请输入密码: ").strip()

    if not account or not password:
        log.error("错误: 未提供账号或密码")
        sys.exit(1)

    config["account"] = account
    config["password"] = password
    save_config(config)

    downloader = KetangpaiDownloader(
        token=token, account=account, password=password,
        max_workers=args.workers, max_retries=args.retries,
        log=log,
    )

    log.info("正在获取课程列表...")
    courses = downloader.get_course_list()
    if not courses:
        log.error("未找到任何课程，请检查账号密码。")
        sys.exit(1)

    log.info(f"共找到 {len(courses)} 门课程:\n")
    for i, course in enumerate(courses, 1):
        name = course.get("coursename", "未知课程")
        semester = course.get("semester", "")
        term = course.get("term", "")
        log.info(f"  [{i:2d}] {name} ({semester} 第{term}学期)")
    log.info(f"  [ 0] 下载全部课程")

    if args.course is not None:
        choice = args.course
    else:
        while True:
            try:
                choice = int(input("\n请输入要下载的课程编号 (0=全部): ").strip())
                if 0 <= choice <= len(courses):
                    break
                log.warning("无效编号，请重新输入。")
            except ValueError:
                log.warning("请输入数字。")

    output_dir = args.output or DEFAULT_OUTPUT
    os.makedirs(output_dir, exist_ok=True)

    total_ok = total_skip = total_fail = 0

    def download_one(course):
        cid = course.get("id")
        name = course.get("coursename", "未知课程")
        return downloader.download_course(
            cid, name, output_dir,
            file_filter=args.filter,
            skip_existing=not args.no_skip,
        )

    if choice == 0:
        for course in courses:
            ok, skip, fail = download_one(course)
            total_ok += ok
            total_skip += skip
            total_fail += fail
    else:
        ok, skip, fail = download_one(courses[choice - 1])
        total_ok += ok
        total_skip += skip
        total_fail += fail

    log.info(f"\n{'=' * 50}")
    log.info(f"全部完成！成功: {total_ok}, 跳过: {total_skip}, 失败: {total_fail}")
    log.info(f"日志文件: {LOG_DIR}")


if __name__ == "__main__":
    main()
