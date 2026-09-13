# 《喵星破晓》（Dawn for Meow）

> 一款**单机本地运行**的放置类经营 / 星际 4X 挂机游戏：从地下避难所的纸箱窝出发，带领二代智械拾荒猫，对抗星系托管中枢「欧米伽」，一路造到火箭、星区与终局决战。
> 零美术资产（纯矢量图标 + CSS 动效），无公网部署，自己玩自己的喵。

**当前阶段：Milestone 1 模块 A ~ O 全部落地，可完整通关** 🚧

后端：`backend/` 工程骨架（FastAPI + SQLAlchemy 2.0 async）、`app/core/balance.py` 数值唯一出处、
**16 张表** ORM 模型与建表脚本、确定性离线结算引擎、新游戏初始化流程，共 **68 个接口**——离线结算与对账、
冷启动手点废墟、工位调度、设施建造、19 节点科技树、军备与远征、猫草水培基因实验室、智械匿名深网、
星球小游戏（密电译码 / 矿脉扫描 / 熔炉配比）、发射井与星图、欧米茄终局决战、生涯成就、多槽位存档导出导入。
冷启动闭环已端到端跑通（真实浏览器实测）：**手点废墟 → 第一座纸箱窝引来第一只猫 → 农夫上工 → 第 2 只猫 → 拾荒猫接管**。

前端：Vue 3 + Tailwind + Pinia 的极客终端界面、100ms 平滑插值 + 15s 静默快照双时钟、
HUD 双条（资源 / 警戒与电力）、中央终端日志流、公频电台底栏，左栏七个面板（设施建造 / 科技树 /
猫草实验室 / 智械深网 / 小游戏 / 生涯成就 / 存档槽位）+ 右栏（工位调度 / 战备机库）。

**星区推演导演**（LLM）已接入 5 个场景：新行星生态标签、外星球特化科技卡（一次批量铺满 12 节点）、
公频电台语料批处理、论坛发帖裁判、通关碑文；
默认便宜小模型 + 每日预算熔断 + 三道防火墙，断网时全部走本地模板兜底（不阻塞玩法）。
尚未实现的 1 个场景（异星植物学图鉴论文）目前在对应操作处诚实报错，不做假数据。

---

## 📚 文档导航

| 文档 | 管什么 | 什么时候看 |
| :--- | :--- | :--- |
| [挂机游戏详细设计与开发需求文档.md](挂机游戏详细设计与开发需求文档.md)（GDD） | 玩法口径与世界观叙事：章节演进、科技树、资源人口、战斗、安防、两大子系统、终局 | 想确认"这个玩法到底是什么" |
| [需求细分与功能模块拆解.md](需求细分与功能模块拆解.md)（WBS） | 15 个模块（A ~ O）的细分任务、前后端职责、验收标准与优先级 | 想知道"先做哪一步、怎样算做完" |
| [数值平衡表.md](数值平衡表.md) | **全部数值的唯一真值来源**：产出、造价、科技成本、电力、警戒度、战斗、股市、政令、小游戏 | **改任何数值之前必看** |
| [数据库设计定稿.md](数据库设计定稿.md) | 数据模型：16 张表 DDL、JSON 种子、时间基准、存档与对账协议 | 动表结构、排查存档问题时 |
| [代码结构与核心工程详细设计规范.md](代码结构与核心工程详细设计规范.md) | 前后端目录树、Pinia 分仓、API 定稿（68 个接口）、环境变量与 LLM 策略 | 写代码之前 |
| [backend/static/templates_default.json](backend/static/templates_default.json) | 公频电台上台兜底语料（0 Token 也能开台） | 调电台文案时 |
| [术语表.md](术语表.md) | 易混词规范（Tier / 算力 / 警戒度…）与 ID 命名规范 | 拿不准该怎么叫的时候 |
| [验收测试清单.md](验收测试清单.md) | 按模块 A ~ O 组织的测试用例与边界用例 | 提测自测之前 |
| [挂机游戏开发需求对齐.md](挂机游戏开发需求对齐.md) | ⚠️ **过程稿**（原始需求对齐聊天记录），含大量已被推翻的方案 | 只做考古，**不作为任何依据** |
| [归档/](归档/) | 历史稿（早期技术架构参考等），只读 | 一般不用看 |

**冲突裁决顺序**：数值 → 《数值平衡表》｜数据模型 → 《数据库设计定稿》｜接口与目录 → 《代码结构与核心工程详细设计规范》｜玩法口径 → GDD｜模块与优先级 → WBS。

---

## 🛠 技术栈

| 层 | 选型 |
| :--- | :--- |
| 前端 | Vue 3 + Vite + TypeScript + Tailwind CSS + Pinia + Lucide 矢量图标 |
| 后端 | FastAPI + SQLAlchemy + Pydantic v2 + Uvicorn |
| 数据库 | MySQL 8（本机，16 张紧凑表，零分布式缓存依赖） |
| AI | 第三方 LLM（DeepSeek 等）仅作"星区推演导演"：低频、高影响、三道防火墙兜底 |
| 运行 | 纯本机 Localhost 单机自娱 |

---

## 🗂 目录结构（后端已落地）

```text
dawn-for-meow/
├── backend/            # ✅ FastAPI 后端（app/core · app/models · app/schemas · app/services · app/api）
│   ├── main.py         #    启动入口（uvicorn main:app --reload）
│   ├── static/         #    JSON 种子（jobs / facilities / minigames / techs_planet0）
│   ├── scripts/        #    init_db.py：建库 + 建 16 张表 + 新游戏初始化
│   └── tests/          #    pytest（默认 SQLite；DWM_TEST_MYSQL_URL 跑真机 MySQL）
├── frontend/           # ✅ Vue 3 + Vite + Tailwind + Pinia（已可跑通冷启动与挂机）
└── *.md                # 设计文档（见上方导航）
```

完整的逐文件目录树见 [代码结构与核心工程详细设计规范.md](代码结构与核心工程详细设计规范.md) 第 1 章。

---

## 🚀 本地启动（后端已可用）

```bash
# ① 建库（MySQL 8，库不存在会自动创建）+ 建 16 张表 + 初始化 1 号槽位
cd backend
pip install -r requirements.txt
cp .env.example .env          # 填数据库账号；LLM key 只写本地 .env
python scripts/init_db.py --new-game 1

# ② 启动后端
python -m uvicorn main:app --reload          # 默认 8000；被占用时加 --port 8010
# 打开 http://127.0.0.1:8000/docs 看接口文档

# ③ 启动前端（另开一个终端）
cd frontend
npm install
npm run dev                                   # http://localhost:5173
# 后端不在 8000 时： $env:VITE_BACKEND_TARGET="http://127.0.0.1:8010"; npm run dev

# ④ 冒烟：读档（首次访问会自动建新档）
curl "http://127.0.0.1:8000/api/v1/colony/state"
curl -X POST "http://127.0.0.1:8000/api/v1/colony/snapshot" -H "Content-Type: application/json" -d "{\"slot\":1}"

# 冷启动四步（也可直接在 /docs 里点）
curl -X POST "http://127.0.0.1:8000/api/v1/colony/scavenge"      # 手点废墟 ×5 → 5 废铁
curl -X POST "http://127.0.0.1:8000/api/v1/facilities/build" -H "Content-Type: application/json" -d "{\"facility_id\":\"housing_box\"}"
curl -X POST "http://127.0.0.1:8000/api/v1/facilities/build" -H "Content-Type: application/json" -d "{\"facility_id\":\"farm_plot\"}"
curl -X POST "http://127.0.0.1:8000/api/v1/colony/dispatch" -H "Content-Type: application/json" -d "{\"role\":\"farmer\",\"delta\":1}"

# ⑤ 测试（Windows 下建议带 -p no:cacheprovider）
python -m pytest -p no:cacheprovider

# 前端类型检查与构建
cd frontend && npm run build
```

> 连不上 MySQL 时可用 `python scripts/init_db.py --url sqlite+aiosqlite:///./dev.db --new-game 1` 退回 SQLite 自测。
> 环境变量清单与 LLM 调用策略见 [代码结构与核心工程详细设计规范.md](代码结构与核心工程详细设计规范.md) 第 6 章。

---

## 📖 推荐阅读顺序

1. 本文（README）→ 建立全貌
2. GDD §1~§3 → 世界观、设计哲学与开局 15 分钟心流
3. WBS → 模块划分与优先级（P0 = MVP 先做什么）
4. 数值平衡表 → 所有数值口径
5. 数据库设计定稿 + 代码结构规范 → 准备动手写代码

---

## 📝 修订记录

## 📦 存档（git）

仓库就在本目录（首个提交见 `git log`）。**在 Codex 里要这样跑 git**：

> 新版 Codex（Windows，app 26.903.x）把命令放进一个**独立受限用户**（`CodexSandboxOnline`）里执行，
> 而工作区属主是你的 `os`。若在沙箱内 `git init`，`.git` 会属于沙箱用户 → git 报 dubious ownership，
> 更糟的是**沙箱 helper 的启动自检会卡死**，导致该会话所有命令失效（表现为 `helper_unknown_error: setup refresh had errors`）。

因此：

* **在 Codex 里让鱼鱼执行 git 时，走一次"沙箱外授权"**（`require_escalated`）——`.git` 属主就是 `os`，一切正常；
* 或**你自己在普通终端里跑 git**（同样是 `os` 身份），行为与从前一致；
* 或在 app 设置 / `~/.codex/config.toml` 把 `sandbox_mode` 改为 `danger-full-access`（命令不再走受限用户，恢复旧行为；代价是没有沙箱保护）；
* **千万不要**让沙箱内的命令去 `git init`（会连累整个会话）。

`git status` 之前记得带上 `-c safe.directory=E:/tmp/dawn-for-meow`（历史上目录属主与 git 进程身份不一致时会用到）。

---

| 版本 | 日期 | 内容 |
| :--- | :--- | :--- |
| v1.0 | 2026-09-12 | 建立项目说明、文档导航、技术栈与（规划中的）启动方式；明确声明当前处于设计阶段 |
| v1.1 | 2026-09-12 | 阶段 1 落地：声明后端数据底座已完成（16 张表 / 数值唯一出处 / 离线引擎 / 两个接口），补真实的建表与启动、测试命令，前端标注为阶段 2 |
| v1.2 | 2026-09-12 | 阶段 3（服务端）落地：补冷启动手点废墟 / 工位调度 / 设施建造三个接口与冷启动四步命令，接口总数 33 → 34，声明冷启动闭环已端到端跑通 |
| v1.3 | 2026-09-12 | 阶段 2 前端落地：补前端目录、前端启动与构建命令、`VITE_BACKEND_TARGET` 端口覆盖说明；声明 UI 已能跑通冷启动五步与挂机产出（真实浏览器实测） |
| v1.4 | 2026-09-13 | 模块 A ~ O 全量落地：README 首页改为按"后端 68 个接口 / 前端七面板 + 右栏"概述已实现能力，补 LLM 场景真实进度（4 个已接入、2 个诚实报错），文档导航的接口数 34 → 68，移除过期的分阶段措辞 |
| v1.5 | 2026-09-13 | LLM 场景 2（外星球特化科技卡）落地：场景进度改为 5 个已接入 / 1 个诚实报错（异星植物学图鉴论文）；接口总数不变（68） |
