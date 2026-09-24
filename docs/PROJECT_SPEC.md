# 图书管理系统项目需求说明（PROJECT_SPEC）

**版本：** v1 范围基线  
**目标：** FastAPI + MySQL 异步 REST API 后端，单馆部署  
**文档分工：** 本文说明**做什么、验收什么**；Codex 的开发行为、代码约束和工作流程见 [`../AGENTS.md`](../AGENTS.md)。

## 1. 项目目标与边界

开发一个可复现、可测试、有真实业务事务的图书管理后端，用于快速恢复服务端工程能力，重点实践 Python 异步 Web、ORM、认证授权、业务分层、数据库事务和并发一致性，并为后续 AI Agent / AI 全栈服务开发积累可复用能力。

**包含：** 读者/管理员用户、JWT 身份认证、作者/分类/图书信息、实体馆藏副本、搜索与分页、借阅/归还、个人和管理员借阅记录、逾期判断、数据库迁移与自动化测试。

**不包含（v1）：** 图书预约与排队、统计报表/排行榜、罚款支付、续借、消息通知、多分馆、前端 UI、Redis、Celery、消息队列、微服务、RAG/LLM/Agent 功能。不得为被排除的功能生成空表、空路由或占位类。

## 2. 技术栈与整体架构

| 领域 | 方案 |
|---|---|
| 运行环境 | Python 3.12+、FastAPI、Uvicorn / ASGI |
| 数据库 | MySQL 8+、InnoDB、utf8mb4 |
| ORM / 驱动 | SQLAlchemy 2.x 异步 ORM、`AsyncSession`、`asyncmy` |
| 接口模型与配置 | Pydantic 2.x、pydantic-settings |
| 数据库迁移 | Alembic 异步环境 |
| 认证 | 签名 JWT Access Token、Argon2 密码哈希 |
| 依赖与代码质量 | uv、Ruff |
| 测试 | pytest、pytest-asyncio、HTTPX、独立 MySQL 测试库 |
| 本地环境 | Docker Compose 启动 MySQL，应用可在本地直接运行 |

架构采用模块化单体、Router → Service → Repository → ORM/MySQL，Pydantic Schema 独立于 ORM Model。Engine 为应用级资源，每个请求拥有独立 AsyncSession。更细的分层/异步约束见 `AGENTS.md`。

## 3. 角色与权限

- **匿名用户：** 注册普通读者、登录、浏览上架图书、作者、分类及公开的可借数量；不能查询任何用户/实体副本/借阅私有明细。
- **READER（读者）：** 修改个人资料与密码、借阅符合条件的图书、归还自己的借阅、查询自己的当前/历史借阅记录。
- **ADMIN（管理员）：** 拥有读者能力；另外管理图书、作者、分类、实体副本和用户账号；启用/禁用用户、调整角色；查看全部借阅与逾期记录；可为指定读者办理借阅/归还。
- 公开注册只能创建 `READER`，不能通过传参获取管理员权限。管理员通过仅在本地受保护执行的初始化脚本创建。
- 禁用用户不能登录，也不能凭旧 Token 继续访问受保护接口。受保护请求需从数据库获取用户最新状态/角色，不能只依赖 Token 中可能过期的角色信息。
- 普通读者只能访问自己的借阅详情及归还接口。管理员为其他人借书时提供 `user_id`；普通读者借书的用户 ID 一律由服务端身份确定。

## 4. 数据库设计：七张业务表

统一约定：MySQL InnoDB、`utf8mb4`；默认主键 `BIGINT UNSIGNED AUTO_INCREMENT`；外键类型与被引用主键保持完全一致；涉及的创建/更新时间统一使用 `DATETIME(6)`。领域时间以 UTC 为准；由于 MySQL DATETIME 本身不保存时区，代码必须约定存取时的时区规范化方式。时间字段的数据库默认值和应用更新策略应保持一致。

### 4.1 `users` 用户表

| 字段 | MySQL 类型 | 规则 |
|---|---|---|
| id | BIGINT UNSIGNED | 主键 |
| username | VARCHAR(50) | 非空、唯一 |
| email | VARCHAR(255) | 非空、唯一；入库前规范化 |
| password_hash | VARCHAR(255) | 非空；严禁输出到 API |
| full_name | VARCHAR(100) | 可空 |
| role | VARCHAR(20) | 非空，`READER` / `ADMIN`，默认 `READER` |
| is_active | BOOLEAN | 非空，默认 true |
| created_at, updated_at | DATETIME(6) | 非空 |

用户名、邮箱建立唯一约束；管理员列表筛选可按需增加 `(role, is_active)` 索引。注册请求不得允许客户端写入 `role` / `is_active` 等特权字段。

### 4.2 `categories` 图书分类表

`id` 主键；
`name VARCHAR(100) NOT NULL UNIQUE`；
`description VARCHAR(500) NULL`；
创建/更新时间。
v1 只有一级分类。存在关联图书时不得物理删除分类，必须先解除关联或调整图书分类。

### 4.3 `authors` 作者表

`id` 主键；
`name VARCHAR(150) NOT NULL`；
`biography TEXT NULL`；
创建/更新时间。
不同作者可能重名，姓名不设置全局唯一约束。仍关联图书的作者不能直接物理删除。

### 4.4 `books` 图书信息表

| 字段 | MySQL 类型 | 规则 |
|---|---|---|
| id | BIGINT UNSIGNED | 主键 |
| isbn | VARCHAR(13) | 唯一、可空；非空时规范化为数字形式 ISBN-13 |
| title | VARCHAR(255) | 非空 |
| subtitle | VARCHAR(255) | 可空 |
| category_id | BIGINT UNSIGNED | 非空，外键 → categories.id |
| publisher | VARCHAR(150) | 可空 |
| publication_date | DATE | 可空 |
| description | TEXT | 可空 |
| cover_url | VARCHAR(500) | 可空 |
| is_active | BOOLEAN | 非空，默认 true |
| created_at, updated_at | DATETIME(6) | 非空 |

一本图书属于一个分类，可由多位作者共同创作，也可对应多本实体副本。ISBN 用来区分有 ISBN 的书目/版本；不同版次可有不同 books 记录。提供 ISBN 时验证 ISBN-13 格式和校验位，未知 ISBN 存 NULL。索引 `(category_id, is_active)`。

公开图书查询：书名/ISBN/作者关键词、分类、作者和 `available_only` 筛选，以及分页/白名单排序。第一版只要求普通数据库查询，不承诺普通 B-tree 标题索引能加速任意子串的 `LIKE '%keyword%'` 搜索。

`DELETE /books/{id}` 在业务含义上是**下架**（将 `is_active=false`），不执行 SQL 物理删除。下架后不再产生新借阅，但历史借阅仍可归还。图书总副本数/可借数由 `book_copies` 查询得出，不在 books 表手动维护库存计数字段。

### 4.5 `book_authors` 图书作者关联表

`book_id BIGINT UNSIGNED NOT NULL` 外键 → books.id；`author_id BIGINT UNSIGNED NOT NULL` 外键 → authors.id；联合主键 `(book_id, author_id)`；索引 `(author_id, book_id)`。删除图书作者关联与删除作者/图书是不同操作，不得破坏历史借阅关联。

### 4.6 `book_copies` 实体馆藏表

| 字段 | MySQL 类型 | 规则 |
|---|---|---|
| id | BIGINT UNSIGNED | 主键 |
| book_id | BIGINT UNSIGNED | 非空，外键 → books.id |
| barcode | VARCHAR(64) | 非空、唯一；创建后不可修改 |
| status | VARCHAR(20) | 非空；见下方状态 |
| location | VARCHAR(100) | 可空 |
| acquired_at | DATE | 可空 |
| created_at, updated_at | DATETIME(6) | 非空 |

状态只有 `AVAILABLE`（可借）、`BORROWED`（借出）、`MAINTENANCE`（维修）、`LOST`（遗失）、`RETIRED`（注销）。索引 `(book_id, status)`。一条记录代表一本具体实体书；公共 API 可以暴露可借数量，但不能公开全部馆藏条码和管理信息。

借出状态的副本，管理员不能绕过借阅/归还流程直接修改为可借、维修、遗失或注销；维修/遗失/注销副本不能借出。实体副本已有借阅历史时不能物理删除。管理员的副本状态接口只允许针对未借出的副本执行合法流转。

### 4.7 `loans` 借阅记录表

| 字段 | MySQL 类型 | 规则 |
|---|---|---|
| id | BIGINT UNSIGNED | 主键 |
| user_id | BIGINT UNSIGNED | 非空，外键 → users.id |
| book_copy_id | BIGINT UNSIGNED | 非空，外键 → book_copies.id |
| borrowed_at | DATETIME(6) | 非空，UTC |
| due_at | DATETIME(6) | 非空，UTC |
| returned_at | DATETIME(6) | 可空，UTC |
| status | VARCHAR(20) | 非空，`BORROWED` / `RETURNED` |
| created_at, updated_at | DATETIME(6) | 非空 |

索引 `(user_id, status)`、`(book_copy_id, status)`、`(status, due_at)`。借阅中 `returned_at IS NULL`，已归还则不为空；通过 Service 校验和适当数据库约束保证状态一致。`OVERDUE` **不是**存储状态：当前 UTC 时间超过 due_at 且 status= BORROWED 时即为逾期。账号或图书下架不应删除借阅历史。

### 4.8 关系与删除规则

- categories 1:N books；books M:N authors（经 book_authors）。
- books 1:N book_copies；users 1:N loans；同一 book_copy 可在不同时间有多条 loans。
- 有历史借阅的用户、图书、副本禁止物理删除；使用禁用、下架或注销状态保留关联。
- v1 不建立 reservations、统计表或手动库存计数列。

## 5. 借阅与归还业务契约

1. 用户必须处于启用状态；图书必须上架且至少有一本 `AVAILABLE` 副本。
2. 每位读者同时最多有 **5** 条未归还借阅记录。
3. 同一读者同一时刻只能借阅同一种图书的 **一个副本**；已归还后允许再次借阅。
4. 借期为服务端决定的 **14 天**；客户端不能指定借阅时间、应还时间或副本状态。
5. 新建借阅记录与实体副本 `AVAILABLE → BORROWED` 在**同一事务**提交，失败时一起回滚。
6. 归还同时更新 `returned_at`、借阅状态 `BORROWED → RETURNED` 和副本状态 `BORROWED → AVAILABLE`，同一事务提交；重复归还返回业务冲突。图书已下架仍允许归还。
7. 普通用户只能还自己的书；管理员可代还、可为指定读者借书，但不能绕过目标读者的启用、数量、重复借阅和库存约束。
8. 已借出副本的状态只能经合法借还业务改变；事务失败后数据库必须保持一致。

### 5.1 并发一致性要求

- 使用真实 MySQL / InnoDB 事务及 `SELECT ... FOR UPDATE` 行锁保护可借实体副本选择，防止不同用户同时借走同一本实体书。
- 同一用户并发借书也需要串行化其资格检查（例如先锁用户行，再检查未归还数量和同种图书重复借阅），防止超出 5 本上限或重复借阅同种图书。
- 实现借阅与归还时制定并记录**统一锁获取顺序**，降低死锁风险；只有在确有必要并经过测试时才加入有界重试。
- 锁定读取与修改必须在同一事务中；事务持锁期间不调用外部 HTTP / 模型 API。并发请求使用独立 AsyncSession。
- 对“两个用户抢借最后一本实体副本”和“同一用户在第 5 本额度边界同时发起多次借阅”编写真实 MySQL 并发测试，除 HTTP 响应外还应检查最终数据库状态。

## 6. REST API 规范

统一前缀 `/api/v1`。请求/响应使用 Pydantic Schema，不返回 `password_hash` 等敏感信息。公开列表和详情仅显示上架图书；管理员可显式筛选查看已下架图书。

### 6.1 认证与用户

| 方法 | 路径 | 权限 | 功能 |
|---|---|---|---|
| POST | `/auth/register` | 公开 | 注册读者；用户名/邮箱重复时报冲突 |
| POST | `/auth/login` | 公开 | 校验用户名/密码，返回 access token 与 token_type |
| GET | `/users/me` | 已登录 | 查询本人资料 |
| PATCH | `/users/me` | 已登录 | 修改允许的资料字段，不接受角色/状态 |
| PATCH | `/users/me/password` | 已登录 | 验证旧密码后修改密码 |
| GET | `/users` | ADMIN | 分页查询用户 |
| GET | `/users/{id}` | ADMIN | 查询指定用户 |
| PATCH | `/users/{id}/status` | ADMIN | 启用/禁用；禁止误禁用最后一个管理员 |
| PATCH | `/users/{id}/role` | ADMIN | 修改角色；禁止降级最后一个有效管理员 |

v1 仅实现 JWT Access Token，不建立 Refresh Token/服务端撤销表。客户端删除 Token 即视为本地退出登录；不要求 `/logout` 接口。Token 有效期可配置，并在 README 中说明该方案不支持立即吊销已发放 Token，禁用用户的强制拒绝通过每次请求查询用户状态实现。

Phase 1 实现细则（明确原有契约，不扩展范围）：

- 注册/登录使用 JSON；用户名为 3–50 位 ASCII 字母、数字或下划线，去除首尾空白并转小写；邮箱校验后去除首尾空白并整体转小写。唯一性采用不区分大小写、区分重音的 MySQL 排序规则。
- 密码为 8–128 个字符，不裁剪空白；个人资料仅允许修改 `email`、`full_name`，其中只有 `full_name` 可显式置空。未知请求字段（包括角色、状态）返回 422。
- 用户列表沿用 `items/total/page/page_size` 分页格式，默认 1/20、最大每页 100，按 ID 升序；可按角色和启用状态筛选。
- “最后一个管理员”统一指最后一个 **启用的 ADMIN**；状态与角色变更必须在同一事务中串行锁定管理员集合、重新校验操作者权限和目标用户，再检查有效管理员数量，避免并发禁用/降级绕过限制。
- JWT 使用固定 HS256 算法及 `sub/iat/exp/iss/aud` 校验，不把角色作为授权依据。改密不撤销已发放 Token；用户禁用期间旧 Token 被拒绝，重新启用后未过期 Token 可以继续使用。
- Python 领域时间使用带 UTC 时区的 datetime；数据库连接时区固定 UTC，DATETIME(6) 写入时去掉 UTC 时区、读取时恢复，数据库默认时间和应用更新时间均为 UTC。
- `/health` 检查进程存活；`/health/ready` 查询数据库，失败返回不含内部细节的 503。

### 6.2 图书目录与实体馆藏

| 方法 | 路径 | 权限 | 功能 |
|---|---|---|---|
| GET | `/books` | 公开 | 上架图书列表、关键词/分类/作者/可借过滤、分页、排序 |
| GET | `/books/{id}` | 公开 | 上架图书详情及汇总库存 |
| POST | `/books` | ADMIN | 新增图书，校验分类和作者 ID |
| PATCH | `/books/{id}` | ADMIN | 修改图书信息和作者关联 |
| DELETE | `/books/{id}` | ADMIN | 下架图书，非物理删除 |
| GET | `/authors` | 公开 | 作者分页列表 |
| GET | `/authors/{id}` | 公开 | 作者详情 |
| POST | `/authors` | ADMIN | 新增作者 |
| PATCH | `/authors/{id}` | ADMIN | 修改作者 |
| DELETE | `/authors/{id}` | ADMIN | 仅删除无关联图书作者 |
| GET | `/categories` | 公开 | 分类列表 |
| POST | `/categories` | ADMIN | 新增分类 |
| PATCH | `/categories/{id}` | ADMIN | 修改分类 |
| DELETE | `/categories/{id}` | ADMIN | 仅删除无关联图书分类 |
| GET | `/books/{id}/copies` | ADMIN | 实体馆藏列表、状态和条码 |
| POST | `/books/{id}/copies` | ADMIN | 新增实体副本，要求唯一条码、初始为 AVAILABLE |
| GET | `/copies/{id}` | ADMIN | 馆藏副本详情 |
| PATCH | `/copies/{id}` | ADMIN | 修改书架位置等非流通状态信息 |
| PATCH | `/copies/{id}/status` | ADMIN | 维护未借出副本的合法状态流转 |

图书列表默认 `page=1`、`page_size=20`，最大每页 100 条；响应包含 `items`、`total`、`page`、`page_size`。排序字段严格采用白名单。

### 6.3 借阅与归还

| 方法 | 路径 | 权限 | 功能 |
|---|---|---|---|
| POST | `/loans` | READER/ADMIN | 提交 book_id 借书；ADMIN 可额外传 user_id；由服务端挑选副本 |
| POST | `/loans/{id}/return` | 借阅者/ADMIN | 归还未归还记录；重复归还返回冲突 |
| GET | `/loans/me` | 已登录 | 本人当前/历史借阅，分页 |
| GET | `/loans` | ADMIN | 全部借阅，按用户/图书/状态过滤 |
| GET | `/loans/overdue` | ADMIN | 查询当前逾期且未归还记录 |
| GET | `/loans/{id}` | 本人/ADMIN | 借阅详情 |

静态 `/loans/overdue` 必须在动态 `/loans/{id}` 之前注册，避免路由冲突。

### 6.4 响应与错误处理

业务错误响应示例：

```json
{
  "code": "BOOK_NOT_AVAILABLE",
  "message": "当前图书暂无可借副本",
  "details": null
}
```

`401`：未登录/Token 无效；`403`：角色不足或无权访问他人资源；`404`：资源不存在；`409`：重复、借阅上限、库存不足或非法状态冲突；`422`：请求参数校验失败。唯一约束并发冲突应映射为有意义的业务错误，不得返回底层 SQL 或数据库密码。

可提供 `/health` 作为进程存活检查；如果提供数据库就绪检查，须和存活检查区分，不把短暂数据库故障等同于进程已退出。

## 7. 分阶段实施与验收

### Phase 1 — 基础工程、异步数据库、认证

创建 uv 工程、FastAPI、Ruff、配置与 `.env.example`、Docker Compose MySQL、应用启动/关闭、日志及统一错误处理；建立 Engine / AsyncSession 工厂和依赖；配置 Alembic 异步迁移，创建 users 表；实现注册、登录、JWT 当前用户依赖、角色校验、个人资料/密码接口及最小管理员用户接口；提供安全且可重复执行的本地管理员初始化脚本。不要提前实现图书/借阅业务接口。

**验收：** 新环境可以安装、连接 MySQL、运行迁移及启动 API；未认证/越权访问被拒绝；禁用用户无法拿旧 Token 访问接口；密码安全哈希且不返回；认证测试使用独立测试库。

### Phase 2 — 图书目录与实体馆藏

建立 categories、authors、books、book_authors、book_copies 五张表及迁移；实现 ORM 关系、Schema、Repository、Service、API；完成图书/作者/分类管理、作者关联、搜索筛选分页、实体副本新增及合法状态变更，按副本计算总馆藏与可借数。

**验收：** 多作者和多副本场景、图书查询分页、状态变更均正确；下架图书不可新借；历史关联保留；测试通过。

### Phase 3 — 借阅归还与并发

建立 loans 表及迁移，实现借阅/归还、本人和管理员查询、逾期筛选；落实第 5 节全部业务约束，尤其事务原子性、行锁、同一用户借阅数量并发控制、资源归属与重复归还。

**验收：** 单副本双人并发抢借只能成功一次；同一用户并发请求不能突破上限或重复借同种图书；冲突/异常后数据一致；并发测试使用真实 MySQL 与独立 Session。

### Phase 4 — 工程化收尾

完善请求日志、request ID、各类测试、README、初始化数据、从空库完整迁移及本地运行文档；记录关键架构选择与 v1 局限，不引入范围外业务。

**验收：** 开发者从全新环境可启动 MySQL、迁移、初始化管理员、运行 API、调用接口并执行测试；文档中的命令可以复现。

## 8. v1 完成定义

仓库包含可运行异步后端、七张业务表和版本化迁移、正确的认证授权、完整的图书/馆藏/借阅/归还业务、真实 MySQL 并发测试及可复现的环境与文档。预约和统计模块不属于交付范围。
