"""
课堂派资料下载工具 GUI v3.1
支持账号密码登录、课程选择、文件选择下载。
"""

import os
import sys
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ktp_download import KetangpaiDownloader, LOG_DIR, CONFIG_PATH, DEFAULT_OUTPUT

import logging


def bind_mousewheel(widget):
    def _on_enter(e):
        widget.bind_all("<MouseWheel>", lambda e: widget.yview_scroll(-e.delta // 120, "units"))
    def _on_leave(e):
        widget.unbind_all("<MouseWheel>")
    widget.bind("<Enter>", _on_enter)
    widget.bind("<Leave>", _on_leave)


class DownloadGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("课堂派资料下载工具 v3.1")
        self.root.geometry("800x720")
        self.root.minsize(700, 640)

        self.config = self._load_config()
        self.downloader = None
        self.courses = []
        self.course_vars = []
        self.select_all_var = tk.BooleanVar()
        self.is_downloading = False
        self.stop_event = threading.Event()
        self.log_queue = queue.Queue()
        self.all_course_files = {}

        self._build_ui()
        self._poll_log_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.config.get("token"):
            self._try_token_login()

    def _load_config(self):
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_config(self):
        self.config["account"] = self.account_var.get()
        self.config["password"] = self.password_var.get()
        self.config["output_dir"] = self.output_var.get()
        self.config["workers"] = self.workers_var.get()
        self.config["retries"] = self.retries_var.get()
        self.config["skip_existing"] = self.skip_var.get()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="课堂派资料下载工具", font=("Microsoft YaHei", 13, "bold")).pack(anchor=tk.W)

        # --- Login ---
        login_frame = ttk.LabelFrame(main, text="登录", padding=8)
        login_frame.pack(fill=tk.X, pady=(8, 4))

        row1 = ttk.Frame(login_frame)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="账号:").pack(side=tk.LEFT)
        self.account_var = tk.StringVar(value=self.config.get("account", ""))
        e1 = ttk.Entry(row1, textvariable=self.account_var, width=20)
        e1.pack(side=tk.LEFT, padx=(4, 12))
        e1.bind("<Control-a>", lambda e: e1.select_range(0, tk.END))
        ttk.Label(row1, text="密码:").pack(side=tk.LEFT)
        self.password_var = tk.StringVar(value=self.config.get("password", ""))
        e2 = ttk.Entry(row1, textvariable=self.password_var, width=20, show="*")
        e2.pack(side=tk.LEFT, padx=(4, 12))
        e2.bind("<Control-a>", lambda e: e2.select_range(0, tk.END))
        self.login_btn = ttk.Button(row1, text="登录", command=self._do_login)
        self.login_btn.pack(side=tk.LEFT)

        self.login_status = ttk.Label(login_frame, text="未登录", foreground="gray")
        self.login_status.pack(anchor=tk.W, pady=(4, 0))

        # --- Course + File List (side by side) ---
        list_frame = ttk.Frame(main)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 4))

        # Course list (left)
        course_lf = ttk.LabelFrame(list_frame, text="课程列表", padding=6)
        course_lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))

        course_top = ttk.Frame(course_lf)
        course_top.pack(fill=tk.X)
        ttk.Button(course_top, text="刷新", command=self._load_courses).pack(side=tk.LEFT)
        ttk.Checkbutton(course_top, text="全选", variable=self.select_all_var, command=self._toggle_select_all).pack(side=tk.LEFT, padx=(8, 0))

        self.course_canvas = tk.Canvas(course_lf, highlightthickness=0)
        csb = ttk.Scrollbar(course_lf, orient=tk.VERTICAL, command=self.course_canvas.yview)
        self.course_inner = ttk.Frame(self.course_canvas)
        self.course_inner.bind("<Configure>", lambda e: self.course_canvas.configure(scrollregion=self.course_canvas.bbox("all")))
        self.course_canvas.create_window((0, 0), window=self.course_inner, anchor=tk.NW)
        self.course_canvas.configure(yscrollcommand=csb.set)
        csb.pack(side=tk.RIGHT, fill=tk.Y)
        self.course_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bind_mousewheel(self.course_canvas)

        # File list (right)
        file_lf = ttk.LabelFrame(list_frame, text='课程文件 (选中课程后点击"加载文件")', padding=6)
        file_lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 0))

        file_top = ttk.Frame(file_lf)
        file_top.pack(fill=tk.X)
        ttk.Button(file_top, text="加载文件", command=self._load_files).pack(side=tk.LEFT)
        self.file_select_all_var = tk.BooleanVar()
        ttk.Checkbutton(file_top, text="全选", variable=self.file_select_all_var, command=self._toggle_file_select_all).pack(side=tk.LEFT, padx=(8, 0))

        self.file_canvas = tk.Canvas(file_lf, highlightthickness=0)
        fsb = ttk.Scrollbar(file_lf, orient=tk.VERTICAL, command=self.file_canvas.yview)
        self.file_inner = ttk.Frame(self.file_canvas)
        self.file_inner.bind("<Configure>", lambda e: self.file_canvas.configure(scrollregion=self.file_canvas.bbox("all")))
        self.file_canvas.create_window((0, 0), window=self.file_inner, anchor=tk.NW)
        self.file_canvas.configure(yscrollcommand=fsb.set)
        fsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bind_mousewheel(self.file_canvas)

        self.file_vars = []
        self.current_course_files = []

        # --- Filter & Settings ---
        settings_frame = ttk.LabelFrame(main, text="筛选与设置", padding=8)
        settings_frame.pack(fill=tk.X, pady=(4, 4))

        filter_row = ttk.Frame(settings_frame)
        filter_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(filter_row, text="文件筛选:").pack(side=tk.LEFT)
        self.filter_vars = {}
        for ext in ["ppt", "pptx", "pdf", "zip", "rar", "doc", "docx", "xls", "xlsx", "mp4"]:
            var = tk.BooleanVar(value=True)
            self.filter_vars[ext] = var
            ttk.Checkbutton(filter_row, text=ext, variable=var).pack(side=tk.LEFT, padx=(4, 0))
        ttk.Label(filter_row, text="自定义:").pack(side=tk.LEFT, padx=(8, 0))
        self.custom_filter_var = tk.StringVar()
        e3 = ttk.Entry(filter_row, textvariable=self.custom_filter_var, width=12)
        e3.pack(side=tk.LEFT, padx=(4, 0))
        e3.bind("<Control-a>", lambda e: e3.select_range(0, tk.END))

        dir_row = ttk.Frame(settings_frame)
        dir_row.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(dir_row, text="下载目录:").pack(side=tk.LEFT)
        self.output_var = tk.StringVar(value=self.config.get("output_dir", DEFAULT_OUTPUT))
        e4 = ttk.Entry(dir_row, textvariable=self.output_var, width=40)
        e4.pack(side=tk.LEFT, padx=(4, 4), fill=tk.X, expand=True)
        e4.bind("<Control-a>", lambda e: e4.select_range(0, tk.END))
        ttk.Button(dir_row, text="浏览", command=self._browse_dir).pack(side=tk.LEFT)

        opt_row = ttk.Frame(settings_frame)
        opt_row.pack(fill=tk.X)
        ttk.Label(opt_row, text="并发数:").pack(side=tk.LEFT)
        self.workers_var = tk.IntVar(value=self.config.get("workers", 3))
        ttk.Spinbox(opt_row, from_=1, to=10, textvariable=self.workers_var, width=4).pack(side=tk.LEFT, padx=(4, 8))
        ttk.Label(opt_row, text="重试:").pack(side=tk.LEFT)
        self.retries_var = tk.IntVar(value=self.config.get("retries", 3))
        ttk.Spinbox(opt_row, from_=1, to=10, textvariable=self.retries_var, width=4).pack(side=tk.LEFT, padx=(4, 8))
        self.skip_var = tk.BooleanVar(value=self.config.get("skip_existing", True))
        ttk.Checkbutton(opt_row, text="跳过已下载", variable=self.skip_var).pack(side=tk.LEFT)

        # --- Download Control ---
        ctrl_frame = ttk.Frame(main)
        ctrl_frame.pack(fill=tk.X, pady=(4, 4))

        self.dl_btn = ttk.Button(ctrl_frame, text="开始下载", command=self._start_download)
        self.dl_btn.pack(side=tk.LEFT)
        self.stop_btn = ttk.Button(ctrl_frame, text="停止", command=self._stop_download, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(8, 0))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(ctrl_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 8))
        self.progress_label = ttk.Label(ctrl_frame, text="0/0")
        self.progress_label.pack(side=tk.LEFT)

        # --- Log ---
        log_frame = ttk.LabelFrame(main, text="日志", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        self.log_text = scrolledtext.ScrolledText(log_frame, height=5, font=("Consolas", 9), state=tk.DISABLED, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.bind("<Control-a>", lambda e: self.log_text.tag_add("sel", "1.0", "end"))
        self.log_text.bind("<Control-c>", lambda e: self.root.clipboard_clear() or self.root.clipboard_append(self.log_text.get("sel.first", "sel.last")))

    def _setup_gui_logging(self):
        logger = logging.getLogger("ktp")
        for h in logger.handlers[:]:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                logger.removeHandler(h)
                h.close()

        class LogStream:
            def __init__(self, q):
                self.q = q
            def write(self, msg):
                if msg.strip():
                    self.q.put(msg)
            def flush(self):
                pass

        handler = logging.StreamHandler(LogStream(self.log_queue))
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(handler)

    def _poll_log_queue(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state=tk.NORMAL)
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state=tk.DISABLED)
            except queue.Empty:
                break
        self.root.after(100, self._poll_log_queue)

    def _try_token_login(self):
        token = self.config.get("token", "")
        if not token:
            return
        self.login_status.config(text="正在验证Token...", foreground="gray")

        def task():
            try:
                dl = KetangpaiDownloader(token=token)
                ok = dl.check_token()
                self.root.after(0, lambda: self._on_token_check(ok, dl))
            except Exception:
                self.root.after(0, lambda: self._on_token_check(False, None))

        threading.Thread(target=task, daemon=True).start()

    def _on_token_check(self, ok, dl):
        if ok and dl:
            self.downloader = dl
            self.login_status.config(text="已登录 (Token有效)", foreground="green")
            self._load_courses()
        else:
            self.login_status.config(text="Token已过期，请重新登录", foreground="red")

    def _do_login(self):
        account = self.account_var.get().strip()
        password = self.password_var.get().strip()
        if not account or not password:
            messagebox.showwarning("提示", "请输入账号和密码")
            return

        self.login_btn.config(state=tk.DISABLED)
        self.login_status.config(text="登录中...", foreground="gray")
        self._save_config()

        def task():
            try:
                dl = KetangpaiDownloader(account=account, password=password)
                ok, msg = dl.login(account, password)
                self.root.after(0, lambda: self._on_login_result(ok, msg, dl))
            except Exception as e:
                self.root.after(0, lambda: self._on_login_result(False, str(e), None))

        threading.Thread(target=task, daemon=True).start()

    def _on_login_result(self, ok, msg, dl):
        self.login_btn.config(state=tk.NORMAL)
        if ok and dl:
            self.downloader = dl
            self.login_status.config(text="已登录", foreground="green")
            self._save_config()
            self._load_courses()
        else:
            self.login_status.config(text=f"登录失败: {msg}", foreground="red")

    def _paste_token(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("获取Token")
        dialog.geometry("520x180")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="在浏览器 F12 → Console 输入:", font=("Microsoft YaHei", 9)).pack(padx=10, pady=(10, 0), anchor=tk.W)
        cmd = 'document.cookie.split(";").find(c=>c.trim().startsWith("token="))?.split("=")[1]'
        e = ttk.Entry(dialog, width=60, font=("Consolas", 9))
        e.insert(0, cmd)
        e.config(state="readonly")
        e.pack(padx=10, pady=4, fill=tk.X)

        ttk.Label(dialog, text="复制输出的 token 粘贴到下方:").pack(padx=10, anchor=tk.W)
        token_var = tk.StringVar()
        te = ttk.Entry(dialog, textvariable=token_var, width=60)
        te.pack(padx=10, pady=4, fill=tk.X)
        te.focus_set()

        def submit():
            t = token_var.get().strip().strip("'\"")
            if t:
                self.config["token"] = t
                self._save_config()
                dialog.destroy()
                self._try_token_login()

        ttk.Button(dialog, text="确定", command=submit).pack(pady=4)

    def _load_courses(self):
        if not self.downloader:
            messagebox.showwarning("提示", "请先登录")
            return
        self.login_status.config(text="正在获取课程列表...", foreground="gray")

        def task():
            try:
                courses = self.downloader.get_course_list()
                self.root.after(0, lambda: self._on_courses_loaded(courses))
            except Exception:
                self.root.after(0, lambda: self._on_courses_loaded([]))

        threading.Thread(target=task, daemon=True).start()

    def _on_courses_loaded(self, courses):
        self.courses = courses
        self.course_vars = []
        self.login_status.config(text=f"已登录 | 共 {len(courses)} 门课程", foreground="green")

        for w in self.course_inner.winfo_children():
            w.destroy()

        for i, course in enumerate(courses):
            var = tk.BooleanVar()
            self.course_vars.append(var)
            name = course.get("coursename", "未知课程")
            semester = course.get("semester", "")
            term = course.get("term", "")
            text = f"[{i+1:2d}] {name} ({semester} 第{term}学期)"
            ttk.Checkbutton(self.course_inner, text=text, variable=var).pack(anchor=tk.W)

    def _toggle_select_all(self):
        val = self.select_all_var.get()
        for var in self.course_vars:
            var.set(val)

    def _load_files(self):
        selected = self._get_selected_courses()
        if not selected:
            messagebox.showwarning("提示", "请先选择至少一门课程")
            return
        if not self.downloader:
            messagebox.showwarning("提示", "请先登录")
            return

        for w in self.file_inner.winfo_children():
            w.destroy()
        self.file_vars = []
        self.file_category_vars = []
        self.current_course_files = []

        ttk.Label(self.file_inner, text="正在加载文件...", foreground="gray").pack(anchor=tk.W)

        def task():
            try:
                categorized = {}
                TYPE_NAMES = {"1": "互动课件", "4": "作业", "6": "测试", "7": "公告"}

                for course in selected:
                    cid = course.get("id")
                    cname = course.get("coursename", "")

                    items = self.downloader.get_all_course_files(cid)
                    for item in items:
                        atts = item.get("attachment", [])
                        if not atts:
                            continue
                        file_type = item.get("type", "0")
                        cat_name = TYPE_NAMES.get(file_type, "资料")
                        file_name = atts[0].get("name", item.get("title", ""))
                        folder_info = item.get("folder")
                        if isinstance(folder_info, dict) and folder_info.get("title"):
                            folder = folder_info["title"]
                        else:
                            folder = ""
                        if cat_name not in categorized:
                            categorized[cat_name] = {}
                        if folder not in categorized[cat_name]:
                            categorized[cat_name][folder] = []
                        categorized[cat_name][folder].append({
                            "course": cname,
                            "title": item.get("title", ""),
                            "name": file_name,
                            "url": atts[0].get("url", ""),
                            "size": atts[0].get("size", ""),
                        })

                self.root.after(0, lambda: self._on_files_loaded(categorized))
            except Exception as e:
                self.root.after(0, lambda: self._on_files_loaded({}))

        threading.Thread(target=task, daemon=True).start()

    def _on_files_loaded(self, categorized):
        for w in self.file_inner.winfo_children():
            w.destroy()
        self.file_vars = []
        self.file_category_vars = []
        self.current_course_files = []

        if not categorized:
            ttk.Label(self.file_inner, text="未找到文件", foreground="gray").pack(anchor=tk.W)
            return

        for cat_name, folders in categorized.items():
            total_files = sum(len(files) for files in folders.values())
            cat_var = tk.BooleanVar(value=True)
            self.file_category_vars.append((cat_var, total_files))

            cat_frame = ttk.Frame(self.file_inner)
            cat_frame.pack(fill=tk.X, pady=(6, 0))
            ttk.Checkbutton(cat_frame, text=f"【{cat_name}】({total_files}个)", variable=cat_var,
                           command=lambda cv=cat_var: self._toggle_category(cv)).pack(side=tk.LEFT)

            unnamed_files = folders.get("", [])
            named_folders = [(k, v) for k, v in folders.items() if k]

            for f in unnamed_files:
                var = tk.BooleanVar(value=True)
                self.file_vars.append(var)
                self.current_course_files.append(f)
                name = f["name"]
                size = f.get("size", "")
                text = f"  {name} ({size})"
                ttk.Checkbutton(self.file_inner, text=text, variable=var).pack(anchor=tk.W, padx=(16, 0))

            for folder_name, files in named_folders:
                folder_var = tk.BooleanVar(value=True)
                self.file_category_vars.append((folder_var, len(files)))

                folder_frame = ttk.Frame(self.file_inner)
                folder_frame.pack(fill=tk.X, padx=(16, 0))
                ttk.Checkbutton(folder_frame, text=f"  {folder_name} ({len(files)}个)", variable=folder_var,
                               command=lambda cv=folder_var: self._toggle_category(cv)).pack(side=tk.LEFT)

                for f in files:
                    var = tk.BooleanVar(value=True)
                    self.file_vars.append(var)
                    self.current_course_files.append(f)
                    name = f["name"]
                    size = f.get("size", "")
                    text = f"    {name} ({size})"
                    ttk.Checkbutton(self.file_inner, text=text, variable=var).pack(anchor=tk.W, padx=(32, 0))

    def _toggle_category(self, cat_var):
        val = cat_var.get()
        for cv, count in self.file_category_vars:
            if cv == cat_var:
                start = sum(c for _, c in self.file_category_vars[:self.file_category_vars.index((cv, count))])
                for i in range(start, start + count):
                    if i < len(self.file_vars):
                        self.file_vars[i].set(val)
                break

    def _toggle_file_select_all(self):
        val = self.file_select_all_var.get()
        for var in self.file_vars:
            var.set(val)
        for cat_var, _ in self.file_category_vars:
            cat_var.set(val)

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=self.output_var.get())
        if d:
            self.output_var.set(d)

    def _get_selected_courses(self):
        return [self.courses[i] for i, var in enumerate(self.course_vars) if var.get()]

    def _get_selected_files(self):
        return [self.current_course_files[i] for i, var in enumerate(self.file_vars) if var.get()]

    def _get_filter(self):
        custom = self.custom_filter_var.get().strip()
        if custom:
            return [f.strip() for f in custom.split(",") if f.strip()]
        return [ext for ext, var in self.filter_vars.items() if var.get()] or None

    def _start_download(self):
        selected_files = self._get_selected_files()
        if not selected_files:
            courses = self._get_selected_courses()
            if not courses:
                messagebox.showwarning("提示", "请先选择课程并加载文件，或直接选择课程下载全部文件")
                return
            self._start_download_courses(courses)
            return

        if not self.downloader:
            messagebox.showwarning("提示", "请先登录")
            return

        self.is_downloading = True
        self.stop_event.clear()
        self.dl_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.progress_label.config(text="准备中...")
        self._save_config()
        self._setup_gui_logging()

        output_dir = self.output_var.get()
        file_filter = self._get_filter()
        workers = self.workers_var.get()
        retries = self.retries_var.get()
        skip = self.skip_var.get()
        token = self.downloader.token

        if file_filter:
            ext_set = {e.lower().strip(".") for e in file_filter}
            filtered = []
            for f in selected_files:
                name = f.get("name", "")
                ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                if ext in ext_set:
                    filtered.append(f)
            selected_files = filtered

        def task():
            total = len(selected_files)
            ok = skip_cnt = fail = 0

            import re as _re
            from urllib.parse import unquote as _unquote
            import requests as _req

            for idx, f in enumerate(selected_files):
                if self.stop_event.is_set():
                    break
                name = f.get("name", "unknown")
                safe_name = _unquote(name)
                safe_name = _re.sub(r'[\\/:*?"<>|]', '_', safe_name).strip('. ')
                safe_name = safe_name[:200]

                course_name = f.get("course", "未知课程")
                safe_course = _re.sub(r'[\\/:*?"<>|]', '_', course_name).strip('. ')
                course_dir = os.path.join(output_dir, safe_course)
                os.makedirs(course_dir, exist_ok=True)
                save_path = os.path.join(course_dir, safe_name)

                self.root.after(0, lambda n=safe_name, i=idx, t=total:
                    self.progress_label.config(text=f"[{i+1}/{t}] {n[:25]}..."))

                if skip and os.path.exists(save_path):
                    skip_cnt += 1
                    progress = (idx + 1) / total * 100
                    self.root.after(0, lambda p=progress, o=ok, s=skip_cnt, fv=fail:
                        self._update_progress(p, o, s, fv))
                    continue

                url = f.get("url", "")
                if not url:
                    fail += 1
                    progress = (idx + 1) / total * 100
                    self.root.after(0, lambda p=progress, o=ok, s=skip_cnt, fv=fail:
                        self._update_progress(p, o, s, fv))
                    continue

                downloaded = False
                for attempt in range(retries):
                    if self.stop_event.is_set():
                        break
                    try:
                        dl_headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            "Accept": "*/*",
                        }
                        resp = _req.get(url, stream=True, timeout=120, headers=dl_headers)
                        if resp.status_code == 200:
                            with open(save_path, "wb") as fout:
                                for chunk in resp.iter_content(chunk_size=8192):
                                    if self.stop_event.is_set():
                                        break
                                    if chunk:
                                        fout.write(chunk)
                            if not self.stop_event.is_set():
                                downloaded = True
                                break
                        resp.close()
                    except Exception:
                        pass
                    import time as _time
                    _time.sleep(1)

                if downloaded:
                    ok += 1
                elif not self.stop_event.is_set():
                    fail += 1

                progress = (idx + 1) / total * 100
                self.root.after(0, lambda p=progress, o=ok, s=skip_cnt, fv=fail:
                    self._update_progress(p, o, s, fv))

            self.root.after(0, lambda: self._on_download_done(ok, skip_cnt, fail))

        threading.Thread(target=task, daemon=True).start()

    def _start_download_courses(self, courses):
        if not self.downloader:
            messagebox.showwarning("提示", "请先登录")
            return

        self.is_downloading = True
        self.stop_event.clear()
        self.dl_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.progress_label.config(text="准备中...")
        self._save_config()
        self._setup_gui_logging()

        output_dir = self.output_var.get()
        file_filter = self._get_filter()
        workers = self.workers_var.get()
        retries = self.retries_var.get()
        skip = self.skip_var.get()
        token = self.downloader.token

        def task():
            dl = KetangpaiDownloader(token=token, max_workers=workers, max_retries=retries)
            total_ok = total_skip = total_fail = 0
            total_courses = len(courses)

            for idx, course in enumerate(courses):
                if self.stop_event.is_set():
                    break
                cid = course.get("id")
                name = course.get("coursename", "未知课程")
                ok, skip_cnt, fail = dl.download_course(cid, name, output_dir, file_filter=file_filter, skip_existing=skip)
                total_ok += ok
                total_skip += skip_cnt
                total_fail += fail
                progress = (idx + 1) / total_courses * 100
                self.root.after(0, lambda p=progress, o=total_ok, s=total_skip, f=total_fail:
                    self._update_progress(p, o, s, f))

            self.root.after(0, lambda: self._on_download_done(total_ok, total_skip, total_fail))

        threading.Thread(target=task, daemon=True).start()

    def _update_progress(self, pct, ok, skip, fail):
        self.progress_var.set(pct)
        self.progress_label.config(text=f"成功:{ok} 跳过:{skip} 失败:{fail}")

    def _on_download_done(self, ok, skip, fail):
        self.is_downloading = False
        self.dl_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_label.config(text=f"完成! 成功:{ok} 跳过:{skip} 失败:{fail}")
        messagebox.showinfo("完成", f"下载完成!\n成功: {ok}\n跳过: {skip}\n失败: {fail}")

    def _stop_download(self):
        self.stop_event.set()
        self.stop_btn.config(state=tk.DISABLED)
        self.progress_label.config(text="正在停止...")

    def _on_close(self):
        if self.is_downloading:
            if not messagebox.askyesno("确认", "正在下载中，确定退出吗？"):
                return
            self.stop_event.set()
        self._save_config()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = DownloadGUI()
    app.run()
