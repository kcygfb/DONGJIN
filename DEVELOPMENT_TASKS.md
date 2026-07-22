# DONGJIN 第一部分开发计划：标准电网与实时潮流数据底座

## 1. 阶段目标

本阶段只建设项目的数据源和数据存储底座，形成以下最小闭环：

```text
SimBench 标准电网与时间曲线
              ↓
     pandapower 加载、校验、潮流计算
              ├────────→ Neo4j：静态设备、参数和拓扑关系
              └────────→ Redis：当前完整动态潮流快照
```

完成后，系统应能通过配置加载一个确定的 SimBench 电网，生成可复现的权威电网包，将静态模型幂等写入 Neo4j，并按照仿真时钟持续计算潮流、向 Redis 原子发布同一时刻的完整结果。

本阶段不是随机生成一张“看起来像电网”的图。标准网络原始数据由 SimBench 提供，pandapower 负责把它加载为可计算模型并生成动态物理状态。

---

## 2. 本阶段边界

### 2.1 包含内容

- SimBench 标准电网加载与网络编号配置。
- pandapower 网络校验、基准潮流和连续准静态潮流计算。
- 权威电网包、稳定业务 ID 和统一版本体系。
- Neo4j 静态设备、铭牌参数和拓扑关系存储。
- Redis 当前完整潮流快照存储。
- 仿真启动、暂停、继续、停止和状态查询。
- 数据一致性、幂等性、失败保护和基础自动测试。

### 2.2 暂不包含

- GNN 训练与推理。
- 故障注入、故障标签和根因定位。
- 前端实时大屏和拓扑编辑。
- Java 业务接口的全面迁移。
- Kafka、消息队列和长期历史时序数据库。
- 短路、保护动作、电磁暂态和毫秒级录波。
- CIM/CGMES 或真实生产电网文件导入。

只有本计划全部验收后，才开始上述后续模块。

---

## 3. 已确定的技术决策

| 项目 | 第一部分决定 |
|---|---|
| 标准电网来源 | SimBench |
| 首个算例 | 默认 `1-MV-urban--0-sw`，必须配置化，不得写死在业务代码中 |
| 扩展算例 | 数据链路稳定后可验证 `1-MVLV-urban-all-0-sw`，不应修改存储协议 |
| 计算引擎 | pandapower |
| 数据生产服务 | 新建独立 Python 服务 `grid-data-service` |
| 静态数据库 | Neo4j |
| 动态实时存储 | Redis，第一部分只保存当前及短期快照 |
| 长期历史 | 暂不建设；后续按查询和保留需求评估 TimescaleDB、TDengine 或 Redis 时间序列 |
| 计算性质 | 按时间点重复执行的 AC 稳态潮流，即连续准静态仿真，不是电磁暂态仿真 |
| 权威数据源 | 版本化 pandapower 电网包；Neo4j 和 Redis 均为其运行时投影 |

### 3.1 关于仿真频率

SimBench 曲线具有自己的原始时间点。若系统每秒计算一次，需要在相邻原始时间点之间进行插值或保持值。每秒运行 pandapower 只会提高发布刷新频率，并不会把原始曲线变成真实的秒级采样数据。

第一版应同时区分：

- `simulationTime`：当前模拟的电网时间。
- `publishedAt`：快照实际写入 Redis 的系统时间。
- `profileSourceTime`：本次输入所依据的原始曲线时间位置。

---

## 4. 总体组件职责

### 4.1 SimBench

提供相互配套的标准网络数据：

- Bus、Line、Transformer、Switch、Load、SGen、ExternalGrid 等设备。
- 设备连接关系。
- 额定电压、阻抗、容量、线路长度等工程参数。
- 负荷、发电和储能时间曲线。

### 4.2 pandapower

- 加载 SimBench 网络为 `pandapowerNet`。
- 应用指定时刻的负荷和发电输入。
- 执行潮流计算。
- 产生 `res_bus`、`res_line`、`res_trafo` 等结果。
- 为 Neo4j 和 Redis 发布器提供同一份数据来源。

### 4.3 Neo4j

- 保存设备实体及其稳定业务 ID。
- 保存端点和连接关系。
- 保存额定值、阻抗和容量等静态参数。
- 保存电网包来源、版本和导入状态。
- 不保存持续变化的电压、电流和负载率。

### 4.4 Redis

- 保存当前有效快照指针。
- 保存完整潮流快照及其质量、版本和计算状态。
- 短期保留少量历史快照，防止内存无限增长。
- 不作为静态拓扑的权威存储，也不承担第一部分的长期历史分析。

### 4.5 `grid-data-service`

- 封装 SimBench 和 pandapower。
- 构建并保存权威电网包。
- 向 Neo4j 发布静态模型。
- 运行仿真时钟和潮流循环。
- 向 Redis 原子发布动态快照。
- 提供健康、初始化、仿真控制和状态接口。

---

## 5. 统一数据边界

| 对象 | Neo4j 静态部分 | Redis 动态部分 |
|---|---|---|
| Bus | ID、名称、额定电压、区域、地理坐标 | 电压幅值、相角、注入有功和无功 |
| Line | 两端 Bus、长度、R/X/C、额定电流、线路类型 | 两端有功/无功、电流、负载率、损耗 |
| Transformer | 高低压 Bus、容量、变比、短路阻抗、接线参数 | 两侧功率、电流、负载率、当前档位 |
| Switch | 连接对象、开关类型、所属设备 | 当前分合状态、是否投入运行 |
| Load | 所属 Bus、设备类型、基准或额定参数 | 当前 P/Q 和使用的曲线值 |
| SGen | 所属 Bus、发电类型、额定容量 | 当前 P/Q 和使用的曲线值 |
| ExternalGrid | 接入 Bus、基准参数 | 当前平衡功率和无功 |
| GridModel | SimBench 编号、模型版本、元素统计、校验和 | 当前活动版本引用 |

原则：设备身份、设备能力和连接关系属于静态数据；当前运行状态和计算结果属于动态数据。

---

## 6. 统一身份与版本

所有产物必须包含以下字段：

| 字段 | 含义 | 示例 |
|---|---|---|
| `gridId` | 电网逻辑身份 | `simbench-1-mv-urban-0-sw` |
| `topologyVersion` | 静态拓扑版本 | `v1` |
| `schemaVersion` | 数据协议版本 | `grid-schema-v1` |
| `snapshotId` | 单次动态断面身份 | `20260722T140002.000Z-000123` |
| `simulationTime` | 仿真时间 | ISO 8601 时间 |
| `publishedAt` | 实际发布时间 | ISO 8601 时间 |

### 6.1 稳定业务 ID

不得直接将 pandapower 的 DataFrame 数字索引作为跨系统 ID。应由以下稳定输入生成业务 ID：

```text
{gridId}:{elementType}:{sourceIndex}
```

例如：

```text
simbench-1-mv-urban-0-sw:bus:17
simbench-1-mv-urban-0-sw:line:42
```

要求：

- 同一电网重复初始化得到完全一致的 ID。
- Neo4j、Redis和权威电网包使用相同 ID。
- ID 生成规则写入 `manifest.json`。
- 未来改变规则时必须提升 `schemaVersion`。

---

## 7. 模块 P0：工程和运行环境

**目标**：建立独立数据生产服务和可重复启动的基础设施。

| 编号 | 任务 | 交付内容 | 验收标准 |
|---|---|---|---|
| P0.1 | 新建 Python 服务 | `grid-data-service/`，包含应用入口、配置、领域模型、存储适配器和测试目录 | 服务可独立启动，不依赖现有 GNN 服务 |
| P0.2 | 锁定依赖 | pandapower、simbench、neo4j Python Driver、redis-py、FastAPI、Pydantic 和测试依赖 | 新环境可根据锁定文件完成安装 |
| P0.3 | 基础设施编排 | Docker Compose 启动 Neo4j 和 Redis，配置健康检查及 Neo4j 持久化 | 一条命令启动；两个数据库健康可连接 |
| P0.4 | 外置配置 | SimBench 编号、数据库地址、快照 TTL、仿真周期、插值策略均通过配置或环境变量传入 | 修改算例和数据库地址不需要改代码 |
| P0.5 | 健康检查 | `GET /health` | 返回服务、pandapower、SimBench、Neo4j、Redis和活动电网状态 |

建议目录：

```text
grid-data-service/
├── app/
│   ├── api/
│   ├── domain/
│   ├── grid/
│   ├── publishers/
│   ├── simulation/
│   ├── settings.py
│   └── main.py
├── tests/
├── requirements.txt
└── README.md
```

---

## 8. 模块 P1：权威电网包

**目标**：将指定 SimBench 网络固化为可重载、可校验、可追溯的项目源数据。

### 8.1 加载与校验流程

1. 根据配置读取 SimBench 网络编号。
2. 调用 `simbench.get_simbench_net(code)`。
3. 检查必需设备表和时间曲线。
4. 生成稳定业务 ID 映射。
5. 运行正常基准潮流。
6. 只有全部校验通过后才写出正式电网包。

### 8.2 电网包结构

```text
artifacts/grids/{gridId}/{topologyVersion}/
├── network.json
├── manifest.json
├── id-mapping.json
├── topology.json
├── profile-metadata.json
└── baseline-results.json
```

| 编号 | 任务 | 验收标准 |
|---|---|---|
| P1.1 | 保存完整网络 | `network.json` 可被 pandapower 重新加载并得到等价设备表 |
| P1.2 | 生成清单 | 记录来源、SimBench 编号、依赖版本、元素数量、生成时间和文件校验和 |
| P1.3 | 保存 ID 映射 | 可在业务 ID、pandapower 表名和源索引之间双向查询 |
| P1.4 | 静态完整性校验 | 无重复业务 ID、无缺失线路端点、额定值合法、引用对象存在 |
| P1.5 | 曲线匹配校验 | 每个需要动态驱动的 Load、SGen、Storage 均有明确曲线或明确的回退规则 |
| P1.6 | 基准潮流 | 基准 `runpp()` 收敛，关键结果为有限数值；失败时禁止发布该版本 |
| P1.7 | 可复现验证 | 使用同一配置重复生成时，除生成时间外的核心内容和设备 ID 一致 |

`manifest.json`至少包含：

```json
{
  "gridId": "simbench-1-mv-urban-0-sw",
  "topologyVersion": "v1",
  "schemaVersion": "grid-schema-v1",
  "simbenchCode": "1-MV-urban--0-sw",
  "source": "SimBench",
  "elementCounts": {},
  "dependencyVersions": {},
  "idStrategy": "{gridId}:{elementType}:{sourceIndex}",
  "checksums": {}
}
```

---

## 9. 模块 P2：Neo4j 静态模型

**目标**：把权威电网包投影成可查询的设备图，不在 Neo4j 内随机造数。

### 9.1 图模型

节点类型：

- `GridModel`
- `Bus`
- `Line`
- `Transformer`
- `Switch`
- `Load`
- `SGen`
- `ExternalGrid`
- 网络存在时增加 `Storage`

推荐关系：

```text
(Line)-[:FROM_TERMINAL]->(Bus)
(Line)-[:TO_TERMINAL]->(Bus)
(Transformer)-[:HV_TERMINAL]->(Bus)
(Transformer)-[:LV_TERMINAL]->(Bus)
(Load)-[:CONNECTED_TO]->(Bus)
(SGen)-[:CONNECTED_TO]->(Bus)
(ExternalGrid)-[:CONNECTED_TO]->(Bus)
(Switch)-[:CONTROLS]->(Bus|Line|Transformer)
(GridModel)-[:CONTAINS]->(设备)
```

不得只把线路压缩成无身份的 `(Bus)-[:CONNECTED_TO]->(Bus)`，因为线路自身具有阻抗、容量和动态结果，必须保留独立业务 ID。

### 9.2 任务

| 编号 | 任务 | 验收标准 |
|---|---|---|
| P2.1 | 建立约束 | `businessId + topologyVersion` 唯一；活动版本和常用查询字段有索引 |
| P2.2 | 映射静态参数 | Bus、Line、Transformer、Switch、Load、SGen、ExternalGrid 字段映射清晰且有单位 |
| P2.3 | 幂等导入 | 同一版本重复导入不增加节点和关系数量 |
| P2.4 | 导入后核对 | 各类型数量、端点数量和关键参数与电网包完全一致 |
| P2.5 | 活动版本切换 | 新版本完整导入并验证后才标记为活动版本；失败时旧版本仍可查询 |
| P2.6 | 数据保护 | 只修改 `managedBy=grid-data-service` 的数据，不清理用户自建节点 |

### 9.3 静态发布接口

```text
POST /grids/initialize
POST /grids/{gridId}/publish/neo4j
GET  /grids/active
GET  /grids/{gridId}/validation
```

初始化和发布应当可重复调用；已存在的相同版本返回当前结果，不重复创建数据。

---

## 10. 模块 P3：SimBench 曲线驱动

**目标**：把 SimBench 原始曲线转换为每个仿真时刻的 Load、SGen 和 Storage 输入。

| 编号 | 任务 | 验收标准 |
|---|---|---|
| P3.1 | 提取曲线元数据 | 记录曲线类型、时间范围、原始时间间隔、关联设备和单位 | 可查询任一动态设备使用了哪条曲线 |
| P3.2 | 生成绝对值输入 | 将曲线和设备基准值转换为 pandapower 所需的当前 P/Q | 抽样时间点与 SimBench 原始数据一致 |
| P3.3 | 插值策略 | 第一版支持 `hold` 和 `linear`，默认值写入配置和快照 provenance | 相同网络、时间和策略产生相同输入 |
| P3.4 | 时间边界 | 明确定义开始时间、结束行为、跨日和曲线末尾行为 | 不会因越界隐式读取错误时间点 |
| P3.5 | 缺失曲线处理 | 初始化时失败，或使用被明确记录的固定基准值回退；不得静默补零 | 日志和清单可识别全部回退设备 |

第一部分不声称生成真实秒级SCADA数据。曲线插值结果属于仿真输入，必须保留来源和插值方式。

---

## 11. 模块 P4：连续潮流计算

**目标**：按仿真时钟持续更新输入并运行 pandapower，产出物理一致的完整断面。

### 11.1 单次计算流程

```text
取得 simulationTime
        ↓
读取/插值该时刻的负荷和发电输入
        ↓
更新 pandapowerNet
        ↓
执行 pandapower.runpp()
        ↓
校验收敛状态和结果完整性
        ↓
构建一个完整 Snapshot
        ↓
交给 Redis 发布器
```

### 11.2 任务

| 编号 | 任务 | 验收标准 |
|---|---|---|
| P4.1 | 仿真状态机 | 支持 `STOPPED`、`RUNNING`、`PAUSED`、`ERROR` | 状态转换受控，重复启动不会产生两个计算循环 |
| P4.2 | 仿真时钟 | 支持起始时间、计算周期、倍速、暂停和继续 | 状态接口准确返回步数和当前仿真时间 |
| P4.3 | 单线程计算 | 同一个 `pandapowerNet` 同时最多执行一个计算 | 不出现重叠修改和乱序发布 |
| P4.4 | 结果采集 | 采集 Bus、Line、Transformer、ExternalGrid 及设备当前输入 | 快照对象覆盖活动电网要求的全部设备 |
| P4.5 | 不收敛保护 | 不收敛、异常或结果缺失时不发布新活动快照 | Redis 活动指针始终指向最后一个成功快照 |
| P4.6 | 性能记录 | 记录输入准备、潮流计算、序列化和 Redis 发布耗时 | 能定位计算周期延迟来源 |

### 11.3 控制接口

```text
POST /simulation/start
POST /simulation/pause
POST /simulation/resume
POST /simulation/stop
GET  /simulation/status
```

第一版默认计算周期可设为1秒，但不得将“1秒发布一次”等同于“SimBench原始数据为1秒采样”。

---

## 12. 模块 P5：Redis 完整快照

**目标**：保证读取端每次拿到同一拓扑版本、同一仿真时刻的完整结果。

### 12.1 Key 设计

```text
dongjin:grid:active
dongjin:snapshot:{snapshotId}
dongjin:snapshot:active
dongjin:simulation:status
```

第一部分不按设备分别维护活动值，例如不使用每条线路各自独立更新的 `measurement:{deviceId}` 作为主要读取入口，因为这种方式容易让读取端得到混合时间断面。

### 12.2 快照结构

```json
{
  "snapshotId": "20260722T140002.000Z-000123",
  "gridId": "simbench-1-mv-urban-0-sw",
  "topologyVersion": "v1",
  "schemaVersion": "grid-snapshot-v1",
  "simulationTime": "2026-01-01T00:02:03Z",
  "profileSourceTime": "2026-01-01T00:00:00Z",
  "publishedAt": "2026-07-22T14:00:02Z",
  "converged": true,
  "calculationDurationMs": 85,
  "profileStrategy": "linear",
  "buses": {},
  "lines": {},
  "transformers": {},
  "switches": {},
  "loads": {},
  "generators": {},
  "externalGrids": {}
}
```

所有数值字段必须在Schema中注明单位，例如：

- 电压：`vmPu`、`vaDegree`。
- 有功：`pMw`。
- 无功：`qMvar`。
- 电流：`iKa`。
- 负载率：`loadingPercent`。
- 损耗：`plMw`、`qlMvar`。

### 12.3 原子发布

发布顺序必须固定：

1. 构建并校验完整快照。
2. 写入 `dongjin:snapshot:{snapshotId}`。
3. 设置该快照的 TTL。
4. 最后更新 `dongjin:snapshot:active`。
5. 更新失败时保持旧活动指针。

| 编号 | 任务 | 验收标准 |
|---|---|---|
| P5.1 | 定义 `grid-snapshot-v1` | 有JSON Schema或等价Pydantic模型，并覆盖全部字段和单位 |
| P5.2 | 完整性校验 | 发布前校验版本、对象覆盖、有限数值和收敛状态 | 半成品或包含非法数值的快照不会激活 |
| P5.3 | 原子切换 | 先写完整数据，再切换活动指针 | 并发读取不会看到混合时间断面 |
| P5.4 | 保留策略 | 快照TTL配置化，默认仅保留短期数据 | Redis内存不会随运行时间无限增长 |
| P5.5 | 当前快照接口 | `GET /snapshots/current` | 返回活动快照；没有有效快照时返回明确状态 |

---

## 13. 模块 P6：测试和第一部分验收

### 13.1 自动测试

| 编号 | 测试范围 | 必测内容 |
|---|---|---|
| P6.1 | ID与版本单元测试 | 重复生成ID一致；版本字段完整；不同设备类型不冲突 |
| P6.2 | SimBench加载测试 | 网络存在、设备表完整、曲线可匹配、错误编号明确失败 |
| P6.3 | 基准潮流测试 | 正常算例收敛，Bus/Line/Transformer关键结果为有限数值 |
| P6.4 | Neo4j集成测试 | 数量一致、端点正确、参数一致、重复导入幂等、活动版本切换安全 |
| P6.5 | Redis集成测试 | 完整快照、活动指针、TTL、失败快照不激活、服务重连 |
| P6.6 | 仿真循环测试 | 启停、暂停、继续、不重复计算、时间递增、不收敛保护 |
| P6.7 | 端到端测试 | SimBench → 电网包 → Neo4j → 曲线 → 潮流 → Redis |

### 13.2 最终验收标准

必须同时满足：

1. 指定 SimBench 编号能被成功加载并生成版本化电网包。
2. 保存的 `network.json` 能被重新加载并通过相同基准潮流校验。
3. 相同配置重复初始化时设备业务 ID 完全一致。
4. Neo4j 中各类设备数量和端点关系与权威电网包完全一致。
5. Neo4j 重复发布同一版本不会产生重复节点或关系。
6. 仿真运行后，Redis 持续出现完整且收敛的潮流快照。
7. 活动快照的 `topologyVersion` 与 Neo4j 活动模型一致。
8. 任意一条 Line 均可用相同业务 ID 在 Neo4j 查询静态参数，在 Redis 查询当前潮流结果。
9. 计算失败或不收敛时，Redis 仍指向上一份成功快照。
10. 连续运行30分钟无重叠计算、混合时刻快照和无限内存增长。

### 13.3 人工抽查场景

随机选择以下对象各至少一个：

- Bus
- Line
- Transformer
- Load
- SGen

对照检查：

```text
SimBench/pandapower源数据
        ↕
权威电网包
        ↕
Neo4j静态对象
        ↕
Redis动态对象
```

业务 ID、设备类型、连接关系、单位和版本必须一致；动态结果应与同一时刻的 pandapower 结果一致。

---

## 14. 实施顺序和里程碑

```text
P0 工程与环境
       ↓
P1 权威电网包
       ↓
P2 Neo4j静态模型
       ↓
P3 SimBench曲线驱动
       ↓
P4 连续潮流计算
       ↓
P5 Redis完整快照
       ↓
P6 测试与验收
```

### 里程碑 A：标准电网可复现

完成 P0、P1。可以通过配置生成、校验、保存并重新加载同一个标准电网。

### 里程碑 B：静态电网可查询

完成 P2。Neo4j完整表达设备及其端点关系，重复导入保持幂等。

### 里程碑 C：动态潮流可持续发布

完成 P3、P4、P5。SimBench曲线驱动pandapower持续计算，Redis只激活完整成功快照。

### 里程碑 D：第一部分正式完成

完成 P6。通过自动测试、30分钟稳定运行和跨存储人工抽查后，才允许进入故障场景、历史数据、GNN或前端开发。

---

## 15. 实施期间必须遵守的规则

1. 不在 Java、Python、Neo4j 或前端中另造一套随机拓扑。
2. 所有下游数据必须能追溯到同一个权威电网包。
3. 不将 pandapower 数字索引直接暴露为跨系统业务 ID。
4. 不把动态潮流结果写入 Neo4j 作为实时读取方案。
5. 不把静态拓扑只存入 Redis。
6. 不在潮流未收敛时发布活动快照。
7. 不逐设备更新“当前值”后让读取端自行拼接断面。
8. 不把插值得到的秒级值描述成真实SCADA秒级采样。
9. 不在第一部分顺便引入GNN、故障仿真和长期时序库。
10. 任何Schema变化必须提升版本并补充兼容性说明。

---

## 16. 第一部分完成后的后续入口

第一部分完成后，再单独制定下一部分计划。候选方向包括：

- 物理故障和运行场景引擎。
- 量测噪声、延迟、丢失和质量码。
- 长期历史时序数据库。
- Java统一查询接口和前端实时展示。
- 基于统一拓扑和快照的GNN数据集、训练与研判。
- CIM/CGMES或合法脱敏生产电网导入。

这些内容不属于当前开发范围，也不作为第一部分验收依赖。
