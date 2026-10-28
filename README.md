# 寻仙 GM 工作台

一个本地运行的寻仙游戏管理工具，通过网页界面连接游戏服务端，方便管理账号和角色、发放物品、发送邮件、充值仙玉、维护商城商品和游戏配置。

## 工作台截图

截图在页面数据载入完成后截取；账号标识和连接用户名已模糊处理。

### 服务概览

![工作台服务概览](doc/screenshots/dashboard.jpg)

### 账号管理

![账号管理](doc/screenshots/accounts.jpg)

### 物品管理

![物品管理与发放清单](doc/screenshots/items.jpg)

### 技能配置

![技能配置](doc/screenshots/skills.jpg)

### 宠物配置

![宠物配置](doc/screenshots/pets.jpg)

### 游戏配置

![游戏配置](doc/screenshots/game-config.jpg)

### 运维管理

![运维管理与服务控制](doc/screenshots/operations.jpg)

## 功能一览

- **服务概览**：查看工作台连接状态和常用功能入口。
- **账号管理**：管理账号与角色，充值仙玉和发送邮件。
- **物品管理**：发放物品、管理商城和背包商店商品、新建武器及删除自定义道具。
- **技能配置**：设置跨职业技能、职业技能冷却与距离，以及自定义召唤物。
- **宠物配置**：调整骑宠和道具坐骑的移动速度、使用场景、飞行和乘骑施法设置。
- **游戏配置**：设置经验与掉钱倍率，编辑基础和动态定义表，调整等级锁定与副本进入次数。
- **运维管理**：热重载配置、重启或关闭服务端、查看操作日志，以及管理 IP 白名单和连接配置。

部分配置需要在「运维管理 → 连接配置」设置客户端 `mb.dpk` 路径。生成客户端包后，需替换游戏目录中的 `mb.dpk` 并重新登录。技能、骑宠、商城、背包商店和新建武器支持热重载；技能定义重载会清理正在运行的技能效果。详见[技能热重载实现说明](doc/技能热重载实现说明.md)。

右上角修改队列会集中合并全部项目，每个有变化的服务端包只上传、备份和替换一次，客户端只下载最终的一份 `mb.dpk`。详见[修改队列批量写包说明](doc/修改队列批量写包说明.md)。

背包商店售价无法通过原价和折扣精确表示时，工具会修改物品基础购买价，可能影响其他商人出售同一物品的金币售价。

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
