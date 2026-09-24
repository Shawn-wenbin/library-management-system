# Phase 1 审查、计划与交付记录

## 规范审查

初始仓库仅包含用户创建的 `AGENTS.md`、`docs/PROJECT_SPEC.md` 和 `.DS_Store`；没有提交、依赖、代码、迁移或测试配置。完整审阅两份文件后确认：AGENTS 规定开发约束，SPEC 规定业务及验收，职责一致；没有技术上无法实现或必须更改的业务需求。

范围保持认证/用户、图书目录/分类/作者、实体馆藏、借阅归还。本次仅执行 Phase 1；预约、报表及其他排除项未创建表、路由或占位类。

本次只在 SPEC 6.1 补充原契约的实现细则：JSON 登录、用户名/邮箱规范化、可编辑资料、密码长度、分页、最后有效管理员并发规则、JWT 生命周期、UTC 边界及健康检查。AGENTS 未修改。由于原规范未限定这些输入细节，采用 README 中记录的最小规则；没有改变角色体系、阶段划分或业务模块范围。

端到端链路核实为 FastAPI async 路由 → async Service → async Repository → SQLAlchemy 2.x AsyncSession → asyncmy → MySQL；同步 Argon2 运算卸载到线程池。Engine 生命周期与请求 Session 生命周期分离，写事务由 Service 负责，不重复 begin、不依赖旧 JWT 角色。

SQLAlchemy 的 autobegin 在首次数据库操作时建立事务，因此鉴权查询之后再无条件 `session.begin()` 会出错。本实现直接在顶层写业务提交/回滚。参考 [SQLAlchemy 事务文档](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html) 和 [Session 并发规则](https://docs.sqlalchemy.org/en/20/orm/session_basics.html)。JWT 与 Argon2 的库选择参考 [FastAPI 官方认证示例](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)。

异步关系加载约束合理，Phase 1 暂无关系，不生成未来关系占位。Alembic 采用异步连接和 `run_sync` 迁移桥接；启动应用不调用 create_all。初始 users 迁移已人工核对主键 unsigned、自增、非空、唯一键、角色约束、UTC 微秒时间、InnoDB 和 utf8mb4 排序规则，无后续阶段表或外键。

## 计划与验收

- 基础工程：uv/锁文件、Python 3.12、Ruff、配置示例、Compose、应用生命周期、统一错误、脱敏日志；安装及健康检查可运行。
- 数据库：应用级异步 Engine、每请求独立 Session、users 模型及 Alembic 初始迁移；独立 MySQL 空库可升级。
- 认证：注册读者、登录、JWT 当前用户依赖、Argon2；重复用户名/邮箱冲突、错误参数拒绝、响应不泄露哈希。
- 用户：本人资料/密码、管理员列表/详情/状态/角色；读者越权被拒绝，禁用用户旧 Token 被拒绝，最后有效管理员得到串行保护。
- 运维入口：本地安全、可重复执行的管理员初始化脚本；存活与数据库就绪分开。
- 测试：HTTPX 异步接口测试、独立 MySQL、唯一竞争和管理员并发请求、写入失败回滚及最终状态检查。

文件按 `app/api`、`services`、`repositories`、`models`、`schemas`、`core`、`db` 分层；增加 `alembic`、`scripts`、`tests`、README、Compose、环境变量示例与依赖配置，不建立 BaseRepository 或 Unit of Work 抽象框架。

风险控制：数据库唯一约束兜底并发注册；管理员更新按固定顺序持锁并读取新状态；READ COMMITTED 避免旧一致性快照参与计数；测试目标强制 `_test`、显式允许清空、校验实际数据库名；秘钥仅存在环境/被忽略本地文件；JWT 不提供服务端立即撤销。

## 实际验证记录

初始 Docker daemon 未运行，默认本地 MySQL socket 无法连接；已启动 Docker Desktop，使用独立 Compose 项目 `library-phase1-test`，仅创建测试服务，映射 `127.0.0.1:33317`。测试口令随机生成于被忽略的 `.local/phase1-test.env`，没有输出或加入版本管理。没有连接、迁移或清空日常开发库。

实际环境：Python 3.12.13、MySQL 8.4.11、SQLAlchemy 2.0.54、asyncmy 0.2.15，具体依赖见 `uv.lock`。

执行命令及结果：

- `uv init --bare --python 3.12 --no-workspace --name library-management-system`、`uv add ...`、`uv add --dev ...`、`uv python pin 3.12`：完成初始化、依赖安装和 Python 固定。uv 初次尝试访问默认缓存遭沙箱阻止，授权后成功。
- `docker info` / `mysqladmin ping`：初始环境连接失败；启动 Docker Desktop 后 Docker 可用。未使用本机既有 MySQL。
- `docker compose --env-file .local/phase1-test.env -p library-phase1-test --profile test up -d --wait db-test`：镜像拉取及独立测试容器健康检查成功。
- `uv run pytest tests/unit`：23 passed。
- `uv sync --locked`：成功，锁文件可复现安装。
- `uv run ruff check .`：通过。开发过程中的导入排序和长行问题已修复。
- `uv run ruff format --check .`：38 files already formatted。
- `uv run --env-file .local/phase1-test.env python -m scripts.test_mysql`：最终 **53 passed，0 skipped，0 failed**，包含 30 项真实 MySQL 集成测试和 23 项单元测试。
- 对明确核验的 `127.0.0.1:33317/library_test`，以测试 URL 作为子进程 `DATABASE_URL` 执行 `alembic current`、`downgrade base`、`upgrade head`、`check`：迁移往返成功，版本为 `0001_users (head)`，`No new upgrade operations detected.`。降级只操作本次临时测试库，日常运行脚本不会自动降级。
- 同一临时库执行两次 `python -m scripts.init_admin`：首次创建成功，第二次幂等退出，不修改密码。
- 实际启动 `uvicorn app.main:app --host 127.0.0.1 --port 18017`，通过 HTTPX 网络请求验证 `/health`、`/health/ready`、管理员登录、本人资料和 OpenAPI：全部成功，验证后退出 Uvicorn。
- `git status --short`、`git diff --check`、逐个新增文件的 diff 检查及随机密钥泄露扫描：未发现机密或范围外功能；原 SPEC 的两处 Markdown 行尾双空格为已有换行语法，保留。`.local/`、`.venv/`、`.DS_Store` 均被忽略。

自动化测试额外覆盖并发改密和“鉴权后管理员被降级”的竞争场景；各竞争使用独立请求/Session，并检查最终数据库状态。

未执行 git commit / push。没有应用启动或数据库测试的剩余阻塞。开发环境 `.env` 仍需使用者按示例填写，未创建可长期使用的开发管理员或预设口令。测试使用的临时 Uvicorn 已退出，独立数据库测试服务在验证后停止。

## 实际文件变更

- 修改 `docs/PROJECT_SPEC.md`（6.1 实现细则）；保留原 `AGENTS.md` 不变。
- 新增工程配置：`.gitignore`、`.python-version`、`.env.example`、`pyproject.toml`、`uv.lock`、`compose.yaml`、`alembic.ini`。
- 新增应用：`app/main.py`；`api/{auth,deps,health,users}.py`；`core/{config,errors,security}.py`；`db/{base,session}.py`；`models/user.py`；`repositories/users.py`；`schemas/user.py`；`services/{health,users}.py`；各实际 Python 包的 `__init__.py`。
- 新增迁移：`alembic/env.py`、`alembic/script.py.mako`、`alembic/versions/0001_users.py`。
- 新增脚本：`scripts/__init__.py`、`scripts/init_admin.py`、`scripts/test_mysql.py`。
- 新增测试：`tests/conftest.py`、`tests/integration/conftest.py`、`tests/integration/test_auth_users.py`、`tests/unit/{test_health_and_errors,test_security,test_test_database_guard}.py`。
- 新增文档：`README.md`、本文件。临时验证脚本、日志及随机配置仅保存在被忽略的 `.local/`，不属于提交内容。

## 剩余限制与下一步

Access Token 无服务端立即撤销能力；管理员集合锁适合当前单馆低频管理场景；用户分页的总数和 items 在 READ COMMITTED 下可能看到并发写入前后不同瞬间。这些不影响本阶段验收，没有引入额外抽象或模块。

下一步仅建议按 Phase 2 实现分类、作者、图书与实体馆藏，配套五张表迁移、显式关系加载和合法副本状态流转；须等待用户明确指令。本次没有开始 Phase 2–4。
