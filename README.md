# 异步图书管理系统

当前已交付 Phase 1–4：基础工程、用户认证、图书目录、实体馆藏、借阅归还及工程化收尾。需求以 [PROJECT_SPEC](docs/PROJECT_SPEC.md) 为准，开发约定见 [AGENTS.md](AGENTS.md)。包含真实 MySQL 事务与并发控制；不包含预约、报表或 AI 功能。

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
uv run alembic current
uv run python -m scripts.init_admin
uv run uvicorn app.main:app --reload
```

初始化前在 `.env` 中填写 `ADMIN_USERNAME`、`ADMIN_EMAIL`；`ADMIN_PASSWORD` 可以留空，由脚本在终端隐藏输入并二次确认。非交互模式必须设置密码环境变量。脚本只创建新管理员；同名、同邮箱且启用的管理员已存在时成功退出，不重设密码。同名读者、禁用账号或不匹配邮箱会报冲突，不会擅自提权或恢复账号。请只在受保护的本地终端执行。

空库迁移依次建立 `0001_users → 0002_catalog → 0003_loans`，`alembic current` 应显示 `0003_loans (head)`；已有库执行 upgrade head 会保留数据并仅应用缺少的迁移。可运行 `uv run alembic check` 核对模型与迁移是否一致。Phase 4 没有表结构变更，不新增迁移版本。

数据库容器仅绑定本机回环地址。开发数据保存在 `mysql_data` 卷；修改 `.env` 中的账号密码不会自动修改已有卷中的 MySQL 账号。应用启动不自动建表或迁移。

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/ready
```

Swagger 文档：<http://127.0.0.1:8000/docs>。先调用注册/登录接口，复制登录结果中的 `access_token` 到 Authorize 的 Bearer 输入框，再调用受保护接口。`/health` 无需数据库可用；`/health/ready` 执行数据库探测，连接故障返回 503。这不是迁移版本检查。

## 开发模拟数据

在本机开发库完成 `alembic upgrade head` 后运行，库名按 `.env` 的 `DATABASE_URL` 填写。脚本兼容 `0002_catalog` 和 `0003_loans`：

```bash
uv run python -m scripts.seed_dev --database library
```

脚本追加 5 个示例分类、10 位虚构作者、24 本虚构图书、69 册馆藏，涵盖多作者、分页、无库存、无可借副本、下架及 AVAILABLE/MAINTENANCE/LOST/RETIRED 状态。ISBN 为校验位合法的模拟号码，不代表真实出版物；馆藏条码使用 `MOCK-` 前缀。已有用户不变，不生成借阅记录或 BORROWED 副本。

整批写入使用一个事务，失败则回滚；按分类名、作者简介标识和 ISBN 识别样例，重复运行跳过已有记录，不重置编辑后的字段，也不补回已有书目被删除的副本。ISBN 被非样例书目占用或条码冲突会终止并回滚。仅允许连接本机地址，且实际数据库名必须与 `--database` 一致；不自动建表、迁移或清空数据。

启动 API 后，可访问 `/api/v1/books?page_size=5`、`/api/v1/books?keyword=Python` 和 `/api/v1/books?available_only=true` 验证；下架图书及馆藏明细使用管理员身份查询。

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

## Phase 2 接口与验证

路径均带 `/api/v1` 前缀。公开可访问图书、作者、分类；写入和实体副本明细需要管理员。

- `GET/POST /categories`，`PATCH/DELETE /categories/{id}`：分类列表、新增、修改和无关联删除。
- `GET/POST /authors`，`GET/PATCH/DELETE /authors/{id}`：作者分页和管理；允许重名，关联图书时拒绝删除。
- `GET/POST /books`，`GET/PATCH/DELETE /books/{id}`：图书分页、详情和管理。DELETE 只下架，PATCH `is_active=true` 可重新上架。
- `GET/POST /books/{id}/copies`：管理员分页查询/新增实体副本；列表可传 `status`。
- `GET/PATCH /copies/{id}`：管理员读取副本及修改 `location/acquired_at`。
- `PATCH /copies/{id}/status`：管理员提交 `status`；借出状态的流转返回 409。

图书搜索参数：`keyword`（书名/ISBN/作者姓名，百分号和下划线按字面搜索）、`category_id`、`author_id`、`available_only`。排序使用 `sort_by=id|title|publication_date|created_at`、`sort_order=asc|desc`，默认 ID 升序；同值按 ID 同方向排序。图书、作者及副本列表返回 `items/total/page/page_size`，默认 1/20、最大每页 100；分类返回数组。

图书默认只查上架记录，管理员也需显式传 `is_active=false` 才能查看下架列表/详情。该参数为 false 时会检查当前账号状态和管理员权限。公开响应含分类、作者、`total_copies/available_copies`，不包含馆藏条码和位置。总数包含所有状态副本，可借数仅包含 AVAILABLE，下架图书可借数为 0。

通过 Swagger 按以下顺序可验证完整流程：

1. 用管理员登录，创建分类 `{"name":"计算机"}` 和作者 `{"name":"示例作者"}`，保存返回 ID。
2. 创建图书 `{"title":"示例图书","category_id":1,"author_ids":[1],"isbn":"978-0-306-40615-7"}`，将示例 ID 替换为实际值。
3. 对图书新增两个副本：`{"barcode":"BOOK-001","location":"A1"}`、`{"barcode":"BOOK-002"}`；详情应显示总数 2、可借数 2。
4. 将其中一册状态改为 MAINTENANCE，详情可借数应为 1；公开查询不会返回条码。
5. DELETE 下架图书，公开详情返回 404；管理员加 `?is_active=false` 可看到记录及两册馆藏，关联作者/分类仍不能删除。

创建副本只接受 `barcode/location/acquired_at`，状态固定 AVAILABLE。条码唯一且不可修改；AVAILABLE、MAINTENANCE、LOST、RETIRED 之间允许变更和同状态幂等操作，允许撤销注销。馆藏管理接口拒绝以 BORROWED 为来源或目标；借出状态只经下方借还流程变更，不提供副本删除接口。

图书 `author_ids` 默认为空、不能重复、最多 100 个；PATCH 空数组解除全部作者关系。ISBN 允许 null，提供时须为有效 ISBN-13，保存前去除空白与连字符。PATCH 省略字段保持原值、可选字段可置空，必填字段不能为 null；空对象及未知字段返回 422。姓名、标题、条码去除首尾空白，封面地址只允许 HTTP(S)；作者简介和图书描述最多 16000 字符。

## 借阅与归还

1. 登录后 `POST /api/v1/loans`，请求体为 `{"book_id":1}`（替换为实际书目 ID）。服务端选择可借副本，成功返回 201 和借阅详情；应还时间固定为借出时间加 14 天。
2. `GET /api/v1/loans/me` 查询本人历史；可加 `status=BORROWED` 或 `status=RETURNED` 及 `book_id`。`GET /api/v1/loans/{id}` 查询本人记录详情。
3. `POST /api/v1/loans/{id}/return` 无需请求体，成功返回 200 和归还后的详情。再次归还返回 409；图书下架仍允许归还。
4. 管理员可以在借阅请求增加 `user_id` 为目标读者借书，或调用归还接口代还。普通读者传任何 `user_id` 均为 403。管理员同样受目标读者启用、最多五本、同种书只借一本及库存规则限制；允许管理员为已禁用读者归还历史借阅。
5. 管理员 `GET /api/v1/loans` 支持 `user_id/book_id/status` 筛选；`GET /api/v1/loans/overdue` 返回当前逾期未归还记录，支持 `user_id/book_id`。三个列表均默认 `page=1&page_size=20`、最大每页 100，按 ID 升序。

详情仅返回借阅 ID、用户 ID、书目 ID、副本 ID、借还时间、状态、创建/更新时间及 `is_overdue`；不暴露条码、位置或用户敏感资料。时间统一 UTC；逾期是查询时计算的 `status=BORROWED` 且当前时间严格晚于 `due_at`，数据库不存 OVERDUE 状态。

借还加锁顺序统一为 **目标用户 → 书目 → 副本 → 已有借阅记录**。用户锁内检查五本上限及同种书重复借阅；书目共享锁阻止并发下架并兼容馆藏更新的隐式外键共享锁，用户及副本使用排他锁；副本的 `SELECT ... FOR UPDATE` 协调库存与管理状态变化。归还前的普通读取只用于定位不可变外键，加锁后重新读取状态；等待用户锁后刷新操作者权限。只锁一个目标用户，不额外锁代办管理员，避免与既有管理员集合锁顺序形成环。

借阅记录与副本状态同事务提交，异常统一回滚。没有自动重试；同一用户的借还串行化，不同用户可借同一书目的不同副本。借阅列表沿用 READ COMMITTED，计数与分页条目可能反映并发修改的不同时刻，不保证列表快照一致性。

## 认证与事务设计

调用链为 `api → services → repositories → models/MySQL`。对 Java 开发者而言，Service 对应业务/事务层，Repository 对应 DAO；区别在于数据库操作必须显式 `await`，同一 Session 不能在并发任务间共享。

- 应用 lifespan 创建 Engine 和 Session 工厂，退出时 `dispose()`；依赖通过 `async with` 为每个请求创建/关闭独立 Session。引擎使用 `mysql+asyncmy`、连接存活检查、`READ COMMITTED` 隔离级别。
- 鉴权读取触发 SQLAlchemy autobegin 后，写 Service 接续同一事务；成功一次 `commit()`，异常（包括取消）回滚。Repository 只查询/flush，不提交、回滚或关闭 Session；只读请求关闭 Session 时结束事务。
- `expire_on_commit=False` 和显式响应 Schema 避免提交后隐式 IO。目录关系配置 `lazy="raise"`，Repository 用 `selectinload` 显式加载分类与作者，库存按当前页书目批量聚合，避免逐本查询副本。
- 用户名/邮箱唯一性由数据库约束最终裁决，并发重复写入映射为 409。所有角色/状态变更先按主键锁定管理员集合，再锁定目标；重新检查操作者及有效管理员数量，防止同时移除全部管理员。此低频管理操作会串行执行，未引入全局锁表或重试框架。
- Argon2 哈希和校验在线程池执行。改密先完成 CPU 运算再锁用户行，并比较原哈希，防止两个改密请求覆盖彼此。
- Python 时间使用带 UTC 时区的 datetime，MySQL 连接时区固定 UTC；类型适配器负责 DATETIME(6) 时区转换。创建时间使用数据库默认值，更新时间由应用以 UTC 写入；直接手工 SQL 更新不自动维护更新时间。
- JWT 固定 HS256，校验签名、过期时间、签发时间、签发者、受众、用户 ID。受保护请求每次从数据库获取最新状态/角色，角色变更对后续请求即时生效。
- 只有 Access Token，无 Refresh Token、撤销表或退出接口；默认有效期 30 分钟，可通过 `JWT_ACCESS_TOKEN_MINUTES` 调整。改密或客户端删除 Token **不会立即吊销已签发 Token**。禁用期间旧 Token 被拒绝，重新启用后未过期 Token 可继续使用。
- 日志记录应用启停及下方请求摘要；SQL 参数隐藏，关闭 Uvicorn 原始访问日志，避免原始 URL 和查询参数进入日志。

## 请求日志与定位

应用处理的每个 HTTP 响应包含 `X-Request-ID`，包括 401/403/404/409/422/500/503。客户端可传单个 1–64 位 ASCII 字母、数字、点、下划线、连字符组成的 ID；缺失、不合法或重复传头时生成新的 UUID4 十六进制 ID。它不是认证凭据；不要把密码或个人信息放进该头。

```bash
curl -i -H 'X-Request-ID: local-health-001' http://127.0.0.1:8000/health
```

`app.requests` 的日志消息为 JSON，包含 `timestamp`（UTC）、`request_id`、`method`、`route`（框架匹配的路由模板）、`status`、`duration_ms`、`outcome`。路由模板不包含实际路径参数，未匹配路径记为 `<unmatched>`；框架可能返回不含 include_router 前缀的模板，例如 `/auth/register`。耗时包含响应发送与请求内清理时间。正常完成为 `complete`，未处理异常为 `error` 并增加 `error_type`，取消为 `interrupted`；尚未发送响应时状态为 null。请求取消不被转换为成功。

正常及 4xx 摘要为 INFO，5xx/未处理异常为 ERROR。默认 `LOG_LEVEL=INFO`；设为 WARNING/ERROR 后不输出正常请求摘要。异常消息、堆栈、原始 URL、查询参数、请求体、Cookie 和 Authorization 不写入请求日志，错误响应体保持原有格式。请求 ID 仅保存在当前调用及 `request.state`，不会跨并发请求共享。日志输出到标准错误，由运行环境负责收集和轮转；v1 未集成分布式追踪或日志平台。

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

运行器先打印不含口令的目标主机/端口/数据库，验证驱动、`_test` 后缀以及测试库名称与开发库不同（即使主机或端口不同也拒绝同名，防止主机别名绕过保护），再执行 Alembic upgrade head，最后运行全部 pytest。`TEST_DATABASE_RESET=1` 明确允许测试前后按外键顺序清空该测试库的 `loans/book_copies/book_authors/books/authors/categories/users` 表；不允许对日常使用的数据库设置此变量。测试还检查实际 `SELECT DATABASE()` 和迁移版本。测试服务使用 tmpfs，与开发数据库的数据卷分开；停止/重建测试容器后数据可能丢失。不支持多个 pytest 进程共享同一个测试库。

单独执行 `uv run pytest` 且没有 `TEST_DATABASE_URL` 时，MySQL 集成用例会明确 skip，不能据此宣称完整验收通过。运行器会从 `.env` 读取测试配置；直接运行 pytest 时应使用 `uv run --env-file .env pytest` 或导出环境变量。

测试覆盖注册/登录、输入校验、敏感信息隔离、角色/资源访问入口、禁用旧 Token、密码/资料修改、初始化幂等、唯一冲突、事务失败回滚、并发重复注册和最后管理员保护。并发用例通过独立 HTTP 客户端和独立请求 Session 运行，并查询最终数据库状态。Phase 2 追加多作者/多副本、搜索分页、上下架、关联删除保护、ISBN/条码校验、全部管理员接口权限、并发唯一冲突、持锁状态更新和事务回滚测试。

Phase 3 追加借阅、归还、管理员代办、资源归属、14 天借期、上限/重复/库存冲突、逾期边界与分页、历史外键与状态约束测试。并发用例在独立请求和 Session 到达首次加锁点时同步放行，验证最后一册争抢、第五本额度、同种书重复、重复归还和归还/再借竞争，并检查最终数据库；还验证锁等待后的禁用/降权/下架/维修状态及 flush/commit 失败回滚。

Phase 4 新增请求日志字段和脱敏、请求 ID 生成/验证/并发隔离、错误响应及取消、初始化目标保护、管理员与目录初始化幂等、保留人工修改和已借馆藏、ISBN/条码冲突整批回滚及七张 InnoDB 表检查。初始化集成测试实际执行初始化函数，再经 HTTPX 登录、借阅、归还。

### 从空库验收

对刚创建且尚未迁移的独立测试服务运行：

```bash
docker compose --profile test up -d --wait db-test
TEST_DATABASE_RESET=1 uv run python -m scripts.test_mysql --verify-empty -q
```

此模式先连接并核对实际数据库名，确认没有任何表或视图，再运行完整 Alembic upgrade head、alembic check 及全部测试。若已有表会拒绝执行，不删除现有表，不自动 downgrade。复用已有测试库时去掉 `--verify-empty`。需要再次验证空库时，可为测试服务使用独立 Compose 项目与未占用端口，同时将 TEST_DATABASE_URL 端口改为相同值；不要使用开发数据卷做空库验收。所有测试库均需独占，不支持并行测试进程共用。

### 常见本地问题与 v1 局限

- 数据库连接失败：检查 `docker compose ps`、端口与 DATABASE_URL，服务健康后再迁移。端口冲突时同时修改 MYSQL_PORT 和 DATABASE_URL；测试服务对应 MYSQL_TEST_PORT 和 TEST_DATABASE_URL。
- 容器已有数据卷时修改 `.env` 密码不会重建 MySQL 账号，需使用原账号或按 MySQL 管理流程改密；不要为了修复连接删除开发数据卷。
- `/health` 成功但 `/health/ready` 503：应用进程存在但数据库不可用；ready 为 200 也不代表迁移到 head，另查 `alembic current`。
- 初始化冲突时核对已有用户名/邮箱/状态；脚本不会自动提权、启用或重设密码。模拟数据为可选开发数据，适合串行执行，不是生产导入工具。
- 401 时检查 Token 过期、签发配置和账号是否禁用；403 时检查当前角色和借阅归属。用响应头中的 request ID 关联日志，不发送密码或 Token 给排查者。
- v1 为单馆 API，没有前端、预约、续借、罚款、通知、报表或 AI 模块。关键词查询是普通子串搜索；计数与分页不保证同一快照；事务无自动死锁重试。没有 Refresh Token、即时 Token 撤销、登录限流或 TLS 终止；公开部署需另行配置 HTTPS 和访问控制。日志脱敏以降低诊断细节为代价。
- 全仓格式检查存在 7 个 Phase 4 开始前即存在的格式问题（详见交付记录）；本阶段变更文件检查通过，未批量格式化无关代码。

## 目录与交付记录

`app/api/` 负责 HTTP 和依赖；`app/services/` 实现事务与规则；`app/repositories/` 查询写库；`app/models/` 表映射；`app/schemas/` 请求/响应；`app/core/` 配置/认证/错误；`app/db/` 数据库资源与 UTC 类型。`alembic/` 包含 users 初始迁移及目录/馆藏的 `0002_catalog` 及借阅表 `0003_loans` 迁移，`scripts/` 提供管理员初始化、可选模拟目录数据与安全测试/空库验收入口，`tests/` 区分单元和集成测试。

实施及验证记录见 [Phase 1](docs/PHASE1.md)、[Phase 2](docs/PHASE2.md)、[Phase 3](docs/PHASE3.md) 和 [Phase 4](docs/PHASE4.md)。v1 分阶段工作已完成；后续功能需另行确定范围。
