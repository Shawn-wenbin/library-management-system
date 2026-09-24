# AGENTS.md — 图书管理系统的 Codex 开发约定

> 本文件只规定 **Codex 如何在本仓库开发**。项目范围、业务规则、数据模型、API 和阶段验收，以 [`docs/PROJECT_SPEC.md`](docs/PROJECT_SPEC.md) 为准。不要在本文件中重复业务需求。

## 1. 开工前必须做的事

1. 先阅读本文件、`docs/PROJECT_SPEC.md` 和当前任务相关的已有代码，再提出实施计划。
2. 先检查 Git 状态、已有依赖、目录、迁移和测试配置；不要假设仓库是空的或 MySQL 已启动。
3. 一次只开发用户明确指定的阶段/功能；不要提前实现后续模块，不要为已排除的模块创建空接口或空表。
4. 需求存在歧义时，优先选择与现有设计一致的最小实现；在最终总结中写明假设。若会改变业务契约，先更新 `PROJECT_SPEC.md` 并指出变动。
5. 尽量提交可检查、可测试的小改动；不要顺手重构无关代码或覆盖用户已有修改。

 ## 2. 架构边界

采用模块化单体：`api/` → `services/` → `repositories/` → `models/` / MySQL；`schemas/`、`core/`、`db/` 为支撑模块。

- **Router / `api/`：** HTTP 参数、Pydantic 请求/响应、依赖注入、身份与权限入口、HTTP 状态码；不直接编写 SQL 或承载完整业务流程。
- **Service / `services/`：** 业务规则、跨 Repository 编排、事务边界、业务异常；不依赖 HTTP Request 对象。
- **Repository / `repositories/`：** 基于注入的 `AsyncSession` 查询和写库；不处理 HTTP 异常、不决定权限、不调用 `commit()`。
- **ORM / `models/`：** 表映射与关系；**Schema / `schemas/`：** 对外请求和响应契约。不能直接把 ORM 对象的全部字段返回给客户端。
- 保持适度分层，不预先设计通用 BaseRepository、复杂 Unit of Work 框架、微服务、消息队列或无实际用途的抽象层。

## 3. Python 异步与数据库约定

- 使用 `PROJECT_SPEC.md` 指定的 Python / FastAPI / SQLAlchemy 2.x / `asyncmy` / MySQL 技术栈；公开函数和方法应有明确类型注解。
- Router → Service → Repository → 数据库 I/O 采用 `async def` / `await`；禁止在异步接口内使用同步数据库驱动、同步 Session 或阻塞式 `requests`。
- 应用级 `AsyncEngine` 和 Session 工厂可以复用；**一个 `AsyncSession` 不可跨并发 asyncio Task 共享**。每个请求或独立运行的任务应使用自己的 Session。
- 使用 FastAPI 依赖及上下文管理器保证 Session 关闭。Repository 允许 `add()`、`execute()`、`flush()`，但禁止提交、回滚或关闭外部注入的 Session。
- 一个顶层写业务由 Service 统一掌握提交/回滚；嵌套调用的其他 Service 不得擅自提交。失败时回滚并重新抛出异常，禁止把部分成功伪装为成功。
- 注意 SQLAlchemy `autobegin`：Session 已开启事务后不要重复调用 `begin()`。需加锁的读取与更新必须位于**同一个事务**，持锁期间不得等待外部 HTTP / 模型 API。
- 显式加载响应所需的 ORM 关系（如 `selectinload`）；避免异步隐式 lazy load 与 Session 关闭后的关系属性访问。
- 领域时间统一为 UTC。MySQL `DATETIME` 不携带时区，存取边界应统一规范化，禁止混用有时区和无时区的 Python `datetime` 比较。
- 查询必须使用 SQLAlchemy 绑定参数；排序字段使用允许列表，禁止拼接用户提供的 SQL 片段。

## 4. 安全与错误处理

- 数据库密码、JWT 密钥、管理员初始口令只能从环境变量/安全输入读取；提交 `.env.example`，不提交 `.env`、口令、密钥或真实令牌。
- 密码采用 Argon2 等安全单向哈希；不得存储或返回明文密码、密码哈希。
- 服务端必须检查身份、角色及资源归属；不能依赖前端隐藏按钮，也不能只相信 JWT 中过期的角色声明。
- 使用 Pydantic 做输入校验，使用明确业务异常和一致的 JSON 错误结构；不向 API 客户端暴露 SQL、堆栈或机密数据。
- 对可能影响历史数据的删除操作保持外键完整性；日志不得记录密码、Token 或完整敏感请求体。

## 5. 数据库迁移与测试环境

- 数据库结构统一由 Alembic 管理，不在应用启动时调用 `Base.metadata.create_all()` 自动建立业务表。
- 审查 Alembic 自动生成结果，核对非空、唯一、外键、索引、删除行为、字段类型及已有数据兼容性。
- 集成测试、并发测试必须使用**独立的 MySQL 测试库**；SQLite 不能替代 MySQL 行锁/事务语义验证。
- 测试不得误删、清空或迁移开发者日常使用的数据库。执行危险 SQL 前必须确认当前数据库目标。

## 6. 测试与完成标准

- 每项已实现行为覆盖正常、权限、参数错误、业务冲突和必要的事务回滚场景。
- 使用 `pytest`、`pytest-asyncio`、HTTPX 异步客户端测试 API；集成测试优先走真实服务和可隔离的 MySQL 测试库。
- 并发测试必须通过**独立请求、独立 Session** 触发竞争，并检查最终数据库状态，不能只看 HTTP 响应。
- 每阶段完成后运行相关测试、Ruff 检查；真实记录运行的命令、通过/失败/受阻情况，没运行不能声称通过。
- 接口行为、配置或数据库结构发生变化时，同步更新测试、迁移、README 和相关规范文档。

## 7. Codex 工作流程与仓库安全

1. 阅读指定阶段的规范、检查现有代码和迁移状态，先给出简短实施计划及预计改动文件。
2. 只修改当前任务范围；以可运行、可测试的纵向功能切片交付，不为填满目录而生成空脚手架。
3. 完成后检查 `git diff`、敏感信息、无关改动，并说明是否变更了产品规范。
4. 未经用户明确要求，不执行未知目标上的破坏性数据库操作，不覆盖用户修改，不自动 `git commit` / `git push`，不创建远程仓库。
5. 不假设 Docker、MySQL、依赖下载、账号密码或网络一定可用；遇到阻塞先完成能验证的部分，清楚列出阻塞点和复现命令。
6. 最终必须汇报：修改文件、重要设计取舍、实际运行的命令与结果、启动和验证方式、剩余问题、下一阶段建议；**不得自行开始下一阶段**。
7. 所有注释使用中文

## 8. 常用命令（在项目初始化后）

以下是计划采用的命令。实际命令以生成后的 `pyproject.toml` 和 Compose 服务名为准，未执行前不能声称可用。

```bash
uv sync
# 按 .env.example 创建本地 .env；不要提交 .env
docker compose up -d db
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
uv run ruff check .
uv run ruff format --check .
uv run pytest
```
