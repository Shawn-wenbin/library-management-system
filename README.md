# 异步图书管理系统

当前仅交付 Phase 1：基础工程、用户认证与管理。需求以 [PROJECT_SPEC](docs/PROJECT_SPEC.md) 为准，开发约定见 [AGENTS.md](AGENTS.md)。图书、作者、分类、馆藏、借阅留待后续阶段；不包含预约、报表或 AI 功能。

## 本地启动

需要 uv、Python 3.12+、Docker Desktop / Docker Compose。仓库通过 `.python-version` 固定本地开发为 Python 3.12，通过 `uv.lock` 固定依赖。下面的命令均在仓库根目录运行。

```bash
uv sync --locked
cp .env.example .env
```

编辑 `.env`：填写 `MYSQL_PASSWORD`、`MYSQL_ROOT_PASSWORD` 和相应的 `DATABASE_URL`；生成随机的 `JWT_SECRET`，至少 32 字节。不要直接使用 `CHANGE_ME`。URL 中密码包含特殊字符时须百分号编码；随机口令可以使用 URL 安全字符避免手工编码。

```bash
uv run python -c 'import secrets; print(secrets.token_urlsafe(48))'
docker compose up -d --wait db
uv run alembic upgrade head
uv run python -m scripts.init_admin
uv run uvicorn app.main:app --reload
```

初始化前在 `.env` 中填写 `ADMIN_USERNAME`、`ADMIN_EMAIL`；`ADMIN_PASSWORD` 可以留空，由脚本在终端隐藏输入并二次确认。非交互模式必须设置密码环境变量。脚本只创建新管理员；同名、同邮箱且启用的管理员已存在时成功退出，不重设密码。同名读者、禁用账号或不匹配邮箱会报冲突，不会擅自提权或恢复账号。请只在受保护的本地终端执行。

数据库容器仅绑定本机回环地址。开发数据保存在 `mysql_data` 卷；修改 `.env` 中的账号密码不会自动修改已有卷中的 MySQL 账号。应用启动不自动建表或迁移。

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/ready
```

Swagger 文档：<http://127.0.0.1:8000/docs>。先调用注册/登录接口，复制登录结果中的 `access_token` 到 Authorize 的 Bearer 输入框，再调用受保护接口。`/health` 无需数据库可用；`/health/ready` 执行数据库探测，连接故障返回 503。这不是迁移版本检查。

## Phase 1 接口

以下接口使用 `/api/v1` 前缀，请求体都是 JSON：

- `POST /auth/register`：`username`、`email`、`password`，可选 `full_name`；成功 201，仅创建读者。
- `POST /auth/login`：`username`、`password`；返回 `access_token` 和 `token_type: bearer`。
- `GET /users/me`：本人资料。
- `PATCH /users/me`：仅修改 `email`、`full_name`；只有姓名可以置空。
- `PATCH /users/me/password`：`old_password`、`new_password`；成功 204。
- `GET /users`：管理员分页查询，支持 `page`、`page_size`、`role`、`is_active`。
- `GET /users/{id}`：管理员查询用户详情。
- `PATCH /users/{id}/status`：管理员提交严格布尔值 `is_active`。
- `PATCH /users/{id}/role`：管理员提交 `role: READER | ADMIN`。

用户名规范化为小写，限制 3–50 位 ASCII 字母、数字、下划线；邮箱校验后整体转小写。密码 8–128 个字符且不裁剪空白。请求多余字段返回 422。列表按 ID 升序，默认每页 20、最大 100，返回 `items/total/page/page_size`。所有用户响应显式使用白名单 Schema，不包含密码哈希。

错误格式统一为 `{"code": "...", "message": "...", "details": null}`。参数错误的 `details` 只包含字段位置和错误类型，不回显原始输入。未登录/无效凭证 401，权限不足 403，不存在 404，唯一性或最后管理员等业务冲突 409，参数错误 422。

## 认证与事务设计

调用链为 `api → services → repositories → models/MySQL`。对 Java 开发者而言，Service 对应业务/事务层，Repository 对应 DAO；区别在于数据库操作必须显式 `await`，同一 Session 不能在并发任务间共享。

- 应用 lifespan 创建 Engine 和 Session 工厂，退出时 `dispose()`；依赖通过 `async with` 为每个请求创建/关闭独立 Session。引擎使用 `mysql+asyncmy`、连接存活检查、`READ COMMITTED` 隔离级别。
- 鉴权读取触发 SQLAlchemy autobegin 后，写 Service 接续同一事务；成功一次 `commit()`，异常（包括取消）回滚。Repository 只查询/flush，不提交、回滚或关闭 Session；只读请求关闭 Session 时结束事务。
- `expire_on_commit=False` 和显式响应 Schema 避免提交后隐式 IO。Phase 1 没有 ORM 关系；后续阶段需要在 Repository 中显式加载关系，不能依赖异步 lazy load。
- 用户名/邮箱唯一性由数据库约束最终裁决，并发重复写入映射为 409。所有角色/状态变更先按主键锁定管理员集合，再锁定目标；重新检查操作者及有效管理员数量，防止同时移除全部管理员。此低频管理操作会串行执行，未引入全局锁表或重试框架。
- Argon2 哈希和校验在线程池执行。改密先完成 CPU 运算再锁用户行，并比较原哈希，防止两个改密请求覆盖彼此。
- Python 时间使用带 UTC 时区的 datetime，MySQL 连接时区固定 UTC；类型适配器负责 DATETIME(6) 时区转换。创建时间使用数据库默认值，更新时间由应用以 UTC 写入；直接手工 SQL 更新不自动维护更新时间。
- JWT 固定 HS256，校验签名、过期时间、签发时间、签发者、受众、用户 ID。受保护请求每次从数据库获取最新状态/角色，角色变更对后续请求即时生效。
- 只有 Access Token，无 Refresh Token、撤销表或退出接口；默认有效期 30 分钟，可通过 `JWT_ACCESS_TOKEN_MINUTES` 调整。改密或客户端删除 Token **不会立即吊销已签发 Token**。禁用期间旧 Token 被拒绝，重新启用后未过期 Token 可继续使用。
- 日志记录应用启停和脱敏错误类型；SQL 参数隐藏，不记录请求体、密码或 Token。更完整的请求 ID/访问日志留在 Phase 4。

## 自动化测试

无需 MySQL 的检查：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/unit
```

完整测试必须使用专用 MySQL 库，不能用 SQLite 替代。编辑 `.env` 中 `MYSQL_TEST_PASSWORD`、`MYSQL_TEST_ROOT_PASSWORD` 和 `TEST_DATABASE_URL`，后者默认对应本机 3307 的 `library_test` 数据库。

```bash
docker compose --profile test up -d --wait db-test
TEST_DATABASE_RESET=1 uv run python -m scripts.test_mysql
```

运行器先打印不含口令的目标主机/端口/数据库，验证驱动、`_test` 后缀以及与开发库的隔离，再执行 Alembic upgrade head，最后运行全部 pytest。`TEST_DATABASE_RESET=1` 明确允许测试前后清空该测试库的 `users` 表；不允许对日常使用的数据库设置此变量。测试还检查实际 `SELECT DATABASE()` 和迁移版本。测试服务使用 tmpfs，与开发数据库的数据卷分开；停止/重建测试容器后数据可能丢失。不支持多个 pytest 进程共享同一个测试库。

单独执行 `uv run pytest` 且没有 `TEST_DATABASE_URL` 时，MySQL 集成用例会明确 skip，不能据此宣称完整验收通过。运行器会从 `.env` 读取测试配置；直接运行 pytest 时应使用 `uv run --env-file .env pytest` 或导出环境变量。

测试覆盖注册/登录、输入校验、敏感信息隔离、角色/资源访问入口、禁用旧 Token、密码/资料修改、初始化幂等、唯一冲突、事务失败回滚、并发重复注册和最后管理员保护。并发用例通过独立 HTTP 客户端和独立请求 Session 运行，并查询最终数据库状态。

## 目录与后续阶段

`app/api/` 负责 HTTP 和依赖；`app/services/` 实现事务与规则；`app/repositories/` 查询写库；`app/models/` 表映射；`app/schemas/` 请求/响应；`app/core/` 配置/认证/错误；`app/db/` 数据库资源与 UTC 类型。`alembic/` 只有 users 初始迁移，`scripts/` 提供管理员初始化与安全测试入口，`tests/` 区分单元和集成测试。

规范审查、实施范围和本次运行记录见 [Phase 1 记录](docs/PHASE1.md)。下一阶段建议按既定规范实施目录与实体馆藏，不提前添加借阅、预约或统计功能。
