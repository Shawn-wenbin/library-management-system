# Phase 2：图书目录与实体馆藏交付记录

## 开工检查与范围

已阅读 AGENTS.md、PROJECT_SPEC.md、Phase 1 实现及测试。开工时 Git 工作区干净，依赖和 `.venv` 已存在，迁移只有 `0001_users`。Phase 1 的 Engine/请求级 AsyncSession、READ COMMITTED、UTC 时间适配器、当前用户及管理员依赖、统一错误响应、MySQL 测试隔离机制均可复用，无需新增依赖或修改用户模块。

基线单元测试 23 项通过，完整独立 MySQL 测试 53 项通过；Ruff check 通过。格式检查在开工时已有 5 个不符合格式的文件：`app/api/deps.py`、`app/api/health.py`、`app/core/security.py`、`app/db/session.py`、`app/repositories/users.py`。本阶段未修改这些文件。

开发数据库服务位于本机 3308 且正在运行。本次仅启动已有独立 Compose 项目 `library-phase1-test` 的 db-test，核验目标为 `127.0.0.1:33317/library_test`，未迁移或清理开发库。测试凭据沿用被忽略的 `.local/phase1-test.env`，未输出或提交。

实施范围为五张目录/馆藏表和规范 6.2 的全部接口；没有 loans 表、借还接口或其他后续阶段占位。

## 实施计划与实际文件

先建立五张表及迁移，再实现 Schema、Repository、Service、API，最后补齐真实 MySQL 测试、文档和迁移验证。

新增文件：

- `alembic/versions/0002_catalog.py`：categories、authors、books、book_authors、book_copies 迁移。
- `app/models/catalog.py`：模型、状态枚举、显式 ORM 关系和时间字段。
- `app/schemas/catalog.py`：输入校验、响应白名单和分页结构。
- `app/repositories/catalog.py`：查询、关系加载、聚合库存、行锁和持久化。
- `app/services/catalog.py`：引用校验、事务、冲突映射、上下架与状态规则。
- `app/api/catalog.py`：公开查询及管理员管理入口。
- `tests/unit/test_catalog_schemas.py`、`tests/integration/test_catalog.py`：本阶段测试。
- `docs/PHASE2.md`：本记录。

修改文件：

- `app/main.py`：注册目录路由。
- `alembic/env.py`：注册目录 ORM 元数据。
- `tests/integration/conftest.py`：要求迁移到 0002，按外键依赖顺序清理独立测试库。
- `scripts/test_mysql.py`：更新测试库清理范围提示。
- `README.md`：接口、操作示例、数据规则、验证与测试说明。
- `docs/PROJECT_SPEC.md`：6.2 补充本阶段实现细则。

## 关键设计与明确的假设

- 沿用 api → services → repositories → models/MySQL。Service 接续鉴权触发的 autobegin，在顶层提交或回滚；Repository 不提交、回滚或关闭 Session。
- 表使用 InnoDB、utf8mb4、unsigned BIGINT、DATETIME(6)。分类名、ISBN、条码有唯一约束；作者姓名不唯一。全部关联采用 RESTRICT，关联表有联合主键及作者反查索引。
- 每个公开响应使用 Schema 白名单。关系采用 `lazy="raise"`，Repository 用 selectinload 显式加载分类和作者。当前页图书的库存通过一次分组查询计算，无库存计数字段，不公开条码和位置。
- 作者关键词和作者 ID 使用关系 EXISTS 筛选，避免多作者 JOIN 导致重复书目和错误总数。关键词参数绑定且转义 LIKE 通配符；排序列用显式白名单，同值按 ID 稳定排序。
- 写入图书与替换作者关系在同一事务；更新书目先锁定书目行，分类/作者删除先锁定目标行，再检查关联。外键约束兜底并发引用变化。唯一冲突映射为 409，外键冲突映射为 REFERENCE_CONFLICT，不暴露 SQL。
- 副本状态检查与更新在同一行锁事务内，锁定读取使用 populate_existing，按最新状态判断。元信息与状态分开，条码和 book_id 不允许修改，创建固定 AVAILABLE。
- 状态图原先未列出全部边。本阶段采用最小规则：四种非借出状态互相可转换，同状态更新幂等，允许撤销注销；BORROWED 既不能作为管理状态变更来源，也不能作为目标。
- 默认公开查询仅显示上架书目。管理员显式 `is_active=false` 查询下架列表或详情，该入口复用数据库当前用户状态/角色校验。总副本数包含全部状态；下架书目可借数为 0。
- DELETE 图书只下架，不解除作者或馆藏关联；已关联下架图书的分类/作者仍禁止物理删除。不提供副本物理删除接口。
- SPEC 6.2 明确了参数命名、排序、分页、ISBN 规范化、PATCH 空值、字段长度、封面协议、状态规则与响应码。这些为原规范未限定部分的实现选择，未调整角色、数据库表范围或阶段边界。

## 实际执行与结果

- `git status --short`、文件/依赖/迁移/测试配置检查：开工时无已有未提交修改。
- `.venv/bin/pytest tests/unit -q`：基线 23 passed；新增 Schema 测试后 41 passed。
- `docker compose ps -a`：确认开发容器运行；初次沙箱 Docker 访问受限，授权后成功。
- `docker compose --env-file .local/phase1-test.env -p library-phase1-test --profile test up -d --wait db-test`：独立测试容器健康。
- `uv run --env-file .local/phase1-test.env python -m scripts.test_mysql`：基线 53 passed；第一轮 Phase 2 测试 97 passed；补齐锁竞争和冲突回滚用例后最终 **107 passed，0 failed，0 skipped**，其中 66 项集成测试、41 项单元测试。初次 uv 缓存访问受沙箱限制，授权后运行成功。
- `uv run --env-file .local/phase1-test.env python .local/verify_phase2_migrations.py`：临时验证脚本先检查 `_test` 隔离和实际数据库，再运行 `alembic current/check/downgrade 0001_users/upgrade head/check`。版本为 `0002_catalog (head)`，两次 check 均为 `No new upgrade operations detected.`；往返后 Phase 1 测试用户保留，表集合符合本阶段范围。临时脚本只用于本次明确核验的测试库，不是项目日常运行入口。
- `.venv/bin/ruff check .`：通过。
- `.venv/bin/ruff format --check` 后接本次 12 个新增/修改 Python 文件：全部通过。
- `.venv/bin/ruff format --check .`：仍仅原有 5 个无关文件不通过，41 个文件格式通过；未扩大修改范围。
- `git diff --check`：通过。检查了新增与修改文件、敏感信息及阶段范围。

真实 MySQL 覆盖多作者、多副本、ISBN/条码及分类唯一性、搜索/过滤/分页/排序、下架关联保留、全部管理员入口的匿名/读者拒绝、旧管理员令牌的最新权限校验、参数错误和不存在资源。并发唯一写入采用独立 HTTP 客户端/请求 Session，校验最终数据库行数。状态竞争测试由独立事务持锁变为 BORROWED，再验证管理请求等待后拒绝修改，最终状态保持 BORROWED。故障注入在 flush 后触发异常，验证书目/作者关联/副本状态回滚；测试未实现或调用借还业务。

## 启动与验证、剩余事项

使用现有 `.env` 配置，按 README 执行：

```bash
uv sync --locked
docker compose up -d --wait db
uv run alembic upgrade head
uv run python -m scripts.init_admin
uv run uvicorn app.main:app --reload
```

访问 `/docs`，管理员登录后依次创建分类、作者、书目、两本副本；检查库存，再修改一册为维修并下架图书。README 提供请求体及预期结果。

标准测试入口仍为独立测试库配置配合 `TEST_DATABASE_RESET=1 uv run python -m scripts.test_mysql`。本次复用临时配置的完整命令见上文。不要对开发库设置测试清理授权。

本阶段功能、真实数据库测试和迁移验证无剩余阻塞。全仓格式检查存在开工前的 5 个文件问题，已保留。READ COMMITTED 下列表总数、条目及库存是多条查询，可能反映并发变更的不同瞬间；业务写入事务和状态检查不依赖公开列表快照。

“下架后不可新借”的 Phase 2 部分为隐藏公开记录、可借数归零及保留关联；真正的借阅入口校验和历史借阅测试须在 Phase 3 建立 loans 后完成。本阶段不会宣称借还流程已经验收。

验证后已执行 `docker compose --env-file .local/phase1-test.env -p library-phase1-test --profile test stop db-test`，停止本次测试容器；开发数据库保持原状。

未执行 git commit/push。下一阶段建议按规范实现借还事务及统一锁顺序、最后一本并发争抢与用户五本额度边界测试；停止在 Phase 2，等待用户指令。
