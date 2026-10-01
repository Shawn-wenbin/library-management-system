# Phase 4：工程化收尾交付记录

## 开工检查与复用范围

已阅读 AGENTS.md、PROJECT_SPEC.md、Phase 1–3 的相关代码、迁移、初始化脚本和测试。开工 Git 工作区干净；Python 3.12 环境、uv.lock 和已有依赖可复用。开发 MySQL 容器健康，只读 `alembic current` 确认为 `0003_loans (head)`。单元测试基线 55 passed，Ruff lint 通过；全仓格式基线有 7 个既有文件不通过。

复用请求独立 AsyncSession、应用级 Engine、UTC 类型、错误响应、认证与权限、Service 事务、Repository 行锁及 Phase 1–3 的真实 MySQL 并发测试。已有管理员初始化、目录模拟数据脚本满足初始化需求，本阶段补充它们的验收测试，不新增默认用户、密码、借阅样例或业务模块。不增加依赖，不修改七张表设计，不创建空迁移，不修改开发库数据。

## 实际修改文件

新增：

- `app/core/request_logging.py`：原生 ASGI 请求日志、request ID、脱敏异常处理。
- `tests/unit/test_request_logging.py`：响应与日志关联、合法性、重复头、脱敏、并发上下文隔离和取消传播。
- `tests/unit/test_seed_dev.py`：模拟数据目标库保护。
- `tests/integration/test_initialization.py`：真实 MySQL 初始化幂等、借还闭环、冲突回滚、空库保护和表结构检查。
- `docs/PHASE4.md`：本记录。

修改：

- `app/main.py`：注册请求日志中间件。
- `app/core/errors.py`：保留业务/HTTP/校验错误处理，将原未知异常兜底并入请求日志中间件，保持原 500 响应体。
- `scripts/test_mysql.py`：新增 `--verify-empty`，核验实际空库后升级并执行 Alembic check；加强测试库隔离保护。
- `tests/unit/test_test_database_guard.py`：非法 URL、开发库主机别名保护用例。
- `docs/PROJECT_SPEC.md`：6.4 补充 Phase 4 请求头和日志细则，未改变业务规则或响应体。
- `README.md`：从空库启动、迁移核验、请求日志、测试、排错、架构取舍及 v1 局限。

本次还在忽略的 `.local/` 中生成独立测试环境配置、真实 HTTP 验证脚本和脱敏日志；这些不是交付代码，不提交任何环境口令。完整验收的可复用入口是 `scripts.test_mysql`。

## 关键设计与假设

- 请求 ID 支持单个 1–64 位 ASCII 字母、数字、点、下划线、连字符；缺失、重复或非法时生成 UUID4 十六进制字符串。只用于追踪，不作为授权或幂等凭据；客户端自行提供的值不保证唯一，不应放入敏感信息。
- 使用原生 ASGI 中间件，每次调用的上下文为局部变量及当前 scope.state，不跨请求共享 Session 或追踪状态。响应发送时统一写入 X-Request-ID，业务、校验、HTTP 与未知异常均关联同一条请求摘要。
- 不改变已有错误 JSON 和 WWW-Authenticate。未知异常在响应开始前转成原有脱敏 500；响应已开始时不重复发送头，只向服务器传播脱敏错误。取消继续传播，不伪装成功。
- 日志摘要包含 UTC 时间、路由模板、状态、耗时和完成情况；不记录原始 URL、路径参数、查询串、请求体、认证头、Cookie、异常消息或堆栈。路由模板采用框架匹配结果，当前版本对于 include_router 可能不带统一前缀。日志消息为 JSON，沿用已有 Python logging 输出配置，运行环境负责收集与轮转。
- 5xx 和未处理异常用 ERROR，正常与 4xx 用 INFO；提高 LOG_LEVEL 会过滤正常请求摘要。耗时使用单调时钟，时间戳使用 UTC。
- 初始化采用最小复用：既有脚本仅创建安全输入的管理员和可选虚构目录；不自动制造借阅历史。验证重复初始化保持密码、人工修改和已借副本不变，并且 ISBN/条码冲突整批回滚。
- 空库验收必须指向独立 `_test` 库并显式设置 TEST_DATABASE_RESET=1；先核验真实库名且无表/视图，再执行既有三次迁移、模型核对和测试。非空库直接拒绝，不自动清表或 downgrade。
- 测试库名称必须与开发库不同，即使端口或主机不同也拒绝同名，采用保守判断防止 localhost/IP/代理别名绕过保护。测试服务必须独占，不支持多个进程同时运行。

## 实际验证命令与结果

本次专用目标：`127.0.0.1:33318/library_test`，Compose 项目 `library-phase4-test`，使用 tmpfs，与开发库及其数据卷隔离。

- `git status --short`、代码/依赖/迁移/测试检查：工作区初始干净。
- `docker compose ps`：开发库健康；沙箱初次拒绝 Docker socket，授权后完成。
- `.venv/bin/alembic current`：沙箱初次拒绝本机连接，授权后只读确认开发库为 `0003_loans (head)`。
- `UV_CACHE_DIR=/tmp/library-uv-cache uv run --locked pytest tests/unit -q`：基线 **55 passed**。
- 初版日志单测：**73 passed / 1 failed**；新测试未启动 lifespan，导致注册依赖缺少 Session 工厂。修正测试生命周期后 `.venv/bin/pytest tests/unit -q` 为 **80 passed**。
- `UV_CACHE_DIR=/tmp/library-uv-cache uv sync --locked`：成功，47 个解析依赖、45 个已安装包检查完成，无依赖变更。
- `docker compose --env-file .local/phase4-test.env -p library-phase4-test --profile test up -d --wait db-test`：成功启动新的空测试实例。
- 首轮 `uv run --locked --env-file .local/phase4-test.env python -m scripts.test_mysql --verify-empty -q --tb=short`：完整迁移及 Alembic check 成功；**185 passed / 1 failed**。新增表检查受 MySQL 列标签大小写影响，改为显式列别名。
- `docker compose --env-file .local/phase4-test.env -p library-phase4-test --profile test up -d --wait --force-recreate db-test`：仅重建本次专用 tmpfs 实例，再次从空库验收。
- 最终 `uv run --locked --env-file .local/phase4-test.env python -m scripts.test_mysql --verify-empty -q --tb=short`：**186 passed，0 failed，0 skipped**（80 单元、106 集成），23.97 秒；三次迁移从空库完成，Alembic check 输出 `No new upgrade operations detected.`。包含原有借还并发、权限、事务回滚测试。
- `uv run --locked --env-file .local/phase4-test.env python -c 'import runpy; runpy.run_path(".local/verify_phase4_smoke.py", run_name="__main__")'`：成功。脚本仅在已确认的本次测试库执行两次管理员 CLI（首次创建、再次不变）、模拟数据 CLI（5 分类、10 作者、24 书目、69 副本），启动真实 Uvicorn，验证数据库就绪、管理员登录、查书、借书、个人列表、归还、重复归还 409、未登录 401、request ID 和日志脱敏；API 进程已退出。
- 再次对已有表执行 `uv run --locked --env-file .local/phase4-test.env python -m scripts.test_mysql --verify-empty -q`：按预期返回 1，明确拒绝非空库，未运行迁移或测试。
- `.venv/bin/ruff check .`：通过。
- `.venv/bin/ruff format --check app/core/request_logging.py app/core/errors.py app/main.py scripts/test_mysql.py tests/unit/test_request_logging.py tests/unit/test_test_database_guard.py tests/unit/test_seed_dev.py tests/integration/test_initialization.py`：本阶段 8 个 Python 文件全部通过。
- `.venv/bin/ruff format --check . --output-format concise`：54 个通过，7 个既有文件不通过，与基线一致：`app/api/deps.py`、`app/api/health.py`、`app/core/security.py`、`app/db/session.py`、`app/repositories/catalog.py`、`app/repositories/users.py`、`app/services/catalog.py`。遵照不改无关代码的要求未批量格式化。
- `git diff --check`：通过；审查修改范围、新增文件及敏感信息，无依赖/业务表变动。
- `docker compose --env-file .local/phase4-test.env -p library-phase4-test --profile test stop db-test`：已停止；随后 `ps -a` 确认为 Exited (0)，开发库仍健康。11 个交付文件检查未发现本机环境口令或密钥。

## 启动、复验与剩余事项

新环境按 README 填写 `.env` 后，在仓库根目录运行：

```bash
uv sync --locked
docker compose up -d --wait db
uv run alembic upgrade head
uv run alembic current
uv run python -m scripts.init_admin
uv run python -m scripts.seed_dev --database library
uv run uvicorn app.main:app --reload
```

模拟数据为可选步骤；`--database` 与实际配置库名一致。访问 `/docs` 登录，或按 README 调用图书和借阅接口。普通独立测试入口：

```bash
docker compose --profile test up -d --wait db-test
TEST_DATABASE_RESET=1 uv run python -m scripts.test_mysql -q
```

首次空库验收增加 `--verify-empty`；已迁移的测试库使用普通入口。本次没有对开发库执行迁移或初始化，没有 git commit/push。

功能与验收无剩余阻塞。唯一已知检查遗留为上述 7 个既有格式问题。v1 的 Token 撤销、分页快照、普通子串搜索、日志诊断粒度及部署局限已在 README 记录。Phase 4 为当前规范最后阶段；下一步建议用户复验，后续增强需单独定义需求，不自动开始新功能。
