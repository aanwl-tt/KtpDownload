# 课堂派资料下载工具

用于下载课堂派 (ketangpai.com) 课程中的所有资料，包括标注为"不允许下载"的文件。

## 功能

- 账号密码直接登录（AES 加密，无需验证码）
- 支持下载互动课件、资料、作业等所有类型
- 文件按类别和文件夹分组显示
- 并发下载、断点续传、自动重试
- 进度条显示、下载日志
- 文件类型筛选
- GUI 图形界面

## 使用方法

### 命令行版本

```bash
pip install requests pycryptodome tqdm
python ktp_download.py
```

### GUI 版本

```bash
pip install requests pycryptodome tqdm
python ktp_download_gui.py
```

### 打包为 EXE

```bash
pip install pyinstaller
python -m PyInstaller --onefile --windowed --name "课堂派下载工具" --exclude-module torch --exclude-module scipy --exclude-module matplotlib --exclude-module pandas --exclude-module numpy --exclude-module PIL --exclude-module lxml --exclude-module jinja2 --exclude-module cryptography ktp_download_gui.py
```

或直接双击 `build.bat`。

## 依赖

- requests
- pycryptodome（登录加密）
- tqdm（进度条，可选）
