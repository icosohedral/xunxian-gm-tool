# 寻仙 GM 工作台

一个本地运行的寻仙游戏管理工具，通过网页界面连接游戏服务端，方便管理账号和角色、发放物品、发送邮件、充值仙玉、维护商城商品和游戏配置。

## 工作台截图

### 服务概览

![工作台服务概览](doc/screenshots/dashboard.jpg)

### 物品管理

![物品管理与发放清单](doc/screenshots/items.jpg)

### 运维管理

![服务端控制](doc/screenshots/operations.jpg)

## 功能一览

- **服务概览**：查看工作台连接状态和常用功能入口。
- **账号管理**：查询账号与角色，查看详情并执行角色管理操作。
- **物品管理**：查询并发放物品，新增自定义武器，管理商城商品、仙玉和邮件。新增武器时会检查物品 ID、写入客户端与服务端包、备份并校验服务器包，生成客户端包供替换，并重启服务端加载定义。
- **技能配置**：设置跨职业技能、职业技能冷却与距离，以及自定义召唤物。
- **宠物配置**：调整骑宠和道具坐骑的移动速度、使用场景、飞行和乘骑施法设置。
- **游戏配置**：设置经验与掉钱倍率，编辑基础和动态定义表，调整等级锁定与副本进入次数。
- **运维管理**：重启或关闭服务端、查看操作日志、管理 IP 白名单和连接配置。

部分配置需要在「运维管理 → 连接配置」设置并保存客户端 `mb.dpk` 路径。生成的客户端包需按提示替换游戏目录中的 `mb.dpk`；服务器包修改后需重启服务端。

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
