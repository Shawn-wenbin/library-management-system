# Phase 3：借阅归还与并发交付记录

## 开工检查与范围

已阅读 AGENTS.md、PROJECT_SPEC.md、Phase 1/2 的代码、迁移与测试配置。开工 Git 工作区干净，`.venv` 和依赖可复用；现有迁移为 `0001_users → 0002_catalog`，开发 MySQL 容器健康。开发库经只读 `alembic current` 确认为 `0002_catalog`，本次没有迁移或修改开发库数据。

复用现有请求独立 AsyncSession、READ COMMITTED、UTCDateTime、JWT 当前用户与管理员依赖、统一错误响应、Catalog/User Repository 行锁、分页模型以及独立测试库安全校验。不新增依赖、不重构 Phase 1/2 的业务模块，不实施 Phase 4。

## 实际修改文件

新增：

- `app/models/loan.py`：借阅映射、状态枚举、索引、状态/时间 CHECK 和 RESTRICT 外键。
- `app/schemas/loan.py`：严格借阅输入及明确字段的借阅响应。
- `app/repositories/loans.py`：资格、库存、借阅查询及行锁，显式加载副本关系。
- `app/services/loans.py`：借还业务、事务、归属及资格校验、逾期计算。
- `app/api/loans.py`：六个借阅接口，静态路径优先注册。
- `alembic/versions/0003_loans.py`：仅新增 loans 表及其索引、约束。
- `tests/unit/test_loan_schemas.py`：14 项输入契约用例。
- `tests/integration/test_loans.py`：35 项真实 MySQL 接口、约束、回滚及并发用例。
- `docs/PHASE3.md`：本交付记录。

修改：

- `app/main.py`、`alembic/env.py`：注册借阅路由及迁移元数据。
- `tests/integration/conftest.py`：要求迁移到 `0003_loans`，清理时先删除 loans。
- `scripts/test_mysql.py`：测试清理范围提示包含借阅表。
- `scripts/seed_dev.py`：原有目录模拟数据脚本兼容 `0003_loans`；仅调整版本白名单，不新增借阅模拟数据功能。
- `docs/PROJECT_SPEC.md`：6.3 补充原规范未明确的输入、字段、分页、错误码及锁规则。
- `README.md`：本阶段接口验证方式、锁顺序、测试覆盖、迁移及下一阶段说明。

另在忽略目录创建 `.local/verify_phase3_migrations.py` 作为本次迁移验证脚本；沿用已有 `.local/phase1-test.env`，未改写或输出其口令。该脚本不是项目日常运行入口。

## 关键设计与明确的假设

- 借期固定 14 天、最多五条未归还记录、同种图书只允许一册；普通用户 ID 由服务端确定，普通用户显式提交 `user_id` 返回 403，即使为本人 ID。未知字段及显式空 user_id 返回 422。
- 管理员省略 user_id 时为自己借阅，代办时仍受目标读者所有借阅资格约束。禁用用户本人受认证拒绝，但允许管理员代还其历史借阅。
- 写事务接续鉴权引起的 autobegin。顶层 Service 只提交一次，业务异常、约束冲突及其他异常均回滚；Repository 不提交或回滚。先在事务内构造响应，再提交，避免关系隐式读取。
- 统一加锁顺序为 **目标用户排他锁 → 书目共享锁 → 副本排他锁 → 已有借阅排他锁**。借阅数量与同种书检查在用户锁内执行；归还先普通读取不可变归属定位，再按顺序加锁并刷新状态。等待用户锁后重新读取操作者当前角色/启用状态。
- 采用书目共享锁阻止并发下架。实际 MySQL 测试发现副本状态更新可能隐式取得书目外键共享锁，使用书目排他锁会引入相反等待风险；共享锁与该行为兼容。副本依然通过 SELECT FOR UPDATE 保护，不使用 SKIP LOCKED，也不加入重试。不同用户可同时借阅同种书的不同副本。
- 不额外锁代办管理员行，避免与既有管理员集合锁的顺序冲突。管理员权限按请求读取并在等待目标用户锁后刷新；此后并发撤权不会追溯取消已经开始处理的操作。
- `loans` 保留用户与副本外键，删除为 RESTRICT；借阅状态与 returned_at 组合受 CHECK 约束，due_at 必须晚于 borrowed_at。没有新增存储的 OVERDUE、冗余库存或额外表。
- 时间沿用带 UTC 时区的 Python datetime 与 MySQL DATETIME(6) 边界适配。每次列表查询统一取一个当前时间，严格晚于 due_at 且未归还才为逾期。列表计数和条目仍沿用 READ COMMITTED 多次查询，不保证跨查询快照一致。
- 响应不包含条码、位置、密码或完整用户信息。借阅 POST 返回 201、归还 POST 返回 200；三个列表沿用 1/20/100 分页限制和 ID 升序。规范仅补充未明确细则，未改变第 5 节业务规则或阶段范围。

## 实际命令与结果

- `git status --short`、文件/依赖/代码/迁移配置检查：开工无未提交修改。
- `.venv/bin/pytest tests/unit -q`：基线 **41 passed**。
- `docker compose ps -a`：开发 MySQL 健康；初次 Docker socket 受沙箱限制，获准后完成。
- `docker compose --env-file .local/phase1-test.env -p library-phase1-test --profile test up -d --wait db-test`：独立测试容器健康。
- `uv run --env-file .local/phase1-test.env python -m scripts.test_mysql -q`：Phase 1/2 基线 **107 passed**。初次 uv 缓存受沙箱限制，获准后运行。
- 首轮新增测试 **146 passed / 1 failed**：CHECK 违例实际为 asyncmy OperationalError 3819，修正测试异常类型与错误码断言。
- 补充锁等待测试后 **154 passed / 1 failed**：副本状态事务使借阅在书目锁处等待，定位并改为书目共享锁，补充同种书双副本并发成功用例。
- `uv run --env-file .local/phase1-test.env python -m scripts.test_mysql tests/integration/test_loans.py -q --tb=short`：**35 passed**。
- `uv run --env-file .local/phase1-test.env python .local/verify_phase3_migrations.py`：核验实际独立测试库后执行 current/check/downgrade 0002_catalog/upgrade head；往返成功，三次 Alembic check 均无模型差异。升级前已有用户及 5 分类、10 作者、24 书目、69 副本和作者关联逐字段保持不变；模拟数据脚本重复执行零新增，已借副本未被覆盖；迁移后借还成功。验证结束清理测试数据。
- `uv run --env-file .local/phase1-test.env python -m scripts.test_mysql -q --tb=short`：最终 **156 passed，0 failed，0 skipped**，包含 55 项单元测试和 101 项集成测试。
- `.venv/bin/ruff check .`：通过。
- `.venv/bin/ruff format --check` 后接本次 13 个新增/修改 Python 文件：通过。
- `.venv/bin/ruff format --check . --output-format concise`：49 个文件通过；7 个既有文件不通过，与开工基线相同，未修改无关格式。文件为 `app/api/deps.py`、`app/api/health.py`、`app/core/security.py`、`app/db/session.py`、`app/repositories/catalog.py`、`app/repositories/users.py`、`app/services/catalog.py`。
- `.venv/bin/alembic current`：沙箱初次禁止本机连接，获准后只读确认开发库为 **0002_catalog**。
- `git diff --check`：通过；审查了修改、新增文件及敏感信息与阶段范围。

并发测试使用独立 HTTP 客户端、独立请求 Session，在首次加锁点通过屏障同步放行，并检查数据库借阅总数、活跃借阅、副本状态及归还时间。覆盖最后一本争抢、第五本额度（本人及管理员代办同时请求）、同种图书重复、重复归还、归还与再借、两册同时借出；锁等待场景覆盖用户禁用、目标读者禁用、管理员降权、图书下架、副本维修。故障注入覆盖借出/归还 flush 后失败、提交前失败及约束冲突，核验回滚和后续业务可继续执行。

## 启动、验证及剩余事项

开发库本次保持在 `0002_catalog`。使用已有 `.env`，启动新接口前执行：

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

通过 `/docs` 登录后，对有 AVAILABLE 副本的上架书目提交 `POST /api/v1/loans`，查看本人列表及借阅详情，再归还。管理员可验证代办及 `/loans/overdue`；具体请求体与预期结果见 README。

从标准独立测试配置复验：

```bash
docker compose --profile test up -d --wait db-test
TEST_DATABASE_RESET=1 uv run python -m scripts.test_mysql
```

本次测试目标是 `127.0.0.1:33317/library_test`，由原有临时测试配置提供。测试环境与开发库隔离，不支持多个 pytest 进程共享同一个测试库。未运行的命令不会被视为已验证；本次 API 验证使用 HTTPX ASGI 客户端和真实 MySQL，没有启动新的常驻 API 进程。

Phase 3 功能、迁移和测试无剩余阻塞。已知剩余项是上述 7 个原有格式问题；开发库尚需由启动流程升级。已执行 `docker compose --env-file .local/phase1-test.env -p library-phase1-test --profile test stop db-test` 并确认测试容器停止，开发数据库保持原状。

未执行 git commit/push。下一阶段建议按规范开展 Phase 4 工程化收尾；本次停止在 Phase 3，等待指令。
