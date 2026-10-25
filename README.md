# 寻仙 GM 工作台

一个本地运行的寻仙游戏管理工具，通过网页界面连接游戏服务端，方便管理账号和角色、发放物品、发送邮件、充值仙玉、维护商城商品和游戏配置。

## 工作台截图

### 服务概览

![工作台服务概览](doc/screenshots/dashboard.jpg)

### 物品管理

![物品管理与发放清单](doc/screenshots/items.jpg)

### 运维管理

![服务端控制](doc/screenshots/operations.jpg)

## 运行环境

- Python 3.13 或更新版本
- 能连接游戏服务端的 SSH 和 MySQL 配置

## 安装与启动

首次安装时，在项目目录打开终端，创建虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

之后每次启动，在项目目录运行：

```bash
source .venv/bin/activate
python gm_server.py
```

也可以直接运行虚拟环境中的 Python：

```bash
./.venv/bin/python gm_server.py
```

Windows PowerShell 首次安装：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Windows PowerShell 日常启动：

```powershell
.\.venv\Scripts\Activate.ps1
python gm_server.py
```

启动后，在浏览器打开 <http://127.0.0.1:8899>。

## 首次连接

在工作台打开「运维管理」→「连接配置」，填写服务端 IP、SSH 登录信息、MySQL 账号和数据库名称，然后保存并测试连接。配置保存在项目目录的 `gm_config.json`，该文件包含密码，已加入 `.gitignore`。

工作台默认只监听本机地址 `127.0.0.1`。请勿将它暴露到公网。使用完毕后，在运行程序的终端按 `Ctrl+C` 退出。
