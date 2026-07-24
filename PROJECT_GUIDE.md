# 项目代码与接口说明

## 1. 项目目标

该项目是一个标准电网、动态潮流、离线训练和错误研判工作台。目前包含：

1. Neo4j 电网拓扑读取、生成和展示。
2. pandapower + SimBench 标准电网和连续潮流。
3. Redis/Memurai 当前动态快照。
4. 基于P1权威电网包的可见离线场景、数据集和GCN训练。
5. 后续将合格离线模型接入全拓扑故障定位、分类和上下游溯源。

## 2. 总体结构

```text
DONGJIN
├─ src/main/java/com/dongjin
│  ├─ DongjinApplication.java
│  ├─ topology
│  │  ├─ TopologyController.java
│  │  ├─ TopologyRepository.java
│  │  ├─ GridTopologyGenerationService.java
│  │  └─ GridRuntimeController.java
│  └─ training
│     ├─ PythonComputeGateway.java
│     └─ TrainingController.java
├─ frontend
│  └─ src
│     ├─ components
│     └─ services
├─ python-training-service
│  ├─ app/main.py
│  ├─ app/grid
│  │  ├─ api.py
│  │  ├─ artifact_service.py
│  │  ├─ publishers
│  │  │  ├─ neo4j.py
│  │  │  └─ redis_snapshot.py
│  │  ├─ simulation
│  │  │  ├─ api.py
│  │  │  ├─ engine.py
│  │  │  ├─ models.py
│  │  │  └─ profiles.py
│  │  ├─ scenarios
│  │  │  ├─ models.py
│  │  │  └─ service.py
│  │  ├─ datasets
│  │  │  └─ builder.py
│  │  ├─ training
│  │  │  └─ trainer.py
│  │  ├─ offline_api.py
│  │  ├─ settings.py
│  │  └─ health.py
│  ├─ scripts/run_offline_training_pipeline.py
│  └─ requirements.txt
├─ OFFLINE_TRAINING_GUIDE.md
├─ STARTUP.md
└─ PROJECT_GUIDE.md
```

## 3. 服务调用关系

```text
Vue 前端
  ↓ /api
Spring Boot
  ├─→ Neo4j：拓扑读写
  └─→ Python 8001：标准电网、在线潮流、离线造数与训练
        ├─→ SimBench + pandapower
        ├─→ Neo4j：活动静态拓扑投影
        ├─→ Redis：原子完整潮流快照
        └─→ artifacts：模型文件和电网包
```

前端只调用Spring Boot，不直接调用Python。项目只保留一个Python工程和8001端口。
离线训练读取P1权威包，不读取在线Neo4j/Redis；Java只提供手动代理接口，正常
前端没有造数和训练入口。合格离线模型接入在线错误研判属于下一部分工作。

## 4. 电网拓扑模块

### 主要代码

- `TopologyController`：提供拓扑查询和生成接口。
- `TopologyRepository`：只查询Python服务发布并核对成功的活动Neo4j拓扑。
- `GridTopologyGenerationService`：复用现有Java接口，依次完成权威包初始化和Neo4j幂等发布。
- `GridRuntimeController`：在现有Java `/api` 下代理仿真控制、曲线元数据和Redis当前快照。
- `PythonComputeGateway`：统一调用8001端口的电网、训练和研判接口。
- `app/grid/artifact_service.py`：加载SimBench、校验、运行基准潮流并原子生成权威电网包。
- `app/grid/publishers/neo4j.py`：把P1包投影为带专用标签、稳定ID、参数和标准端点关系的Neo4j活动模型。
- `app/grid/simulation/profiles.py`：将SimBench曲线转成确定性的P/Q输入，支持`hold`和`linear`。
- `app/grid/simulation/engine.py`：维护单线程连续潮流状态机并构建完整快照。
- `app/grid/publishers/redis_snapshot.py`：通过Redis事务发布不可变完整快照和活动指针。
- `TopologySection.vue`：展示活动SimBench拓扑，并在同一画布叠加当前潮流数据和仿真控制。

### SimBench权威电网包

默认算例为`1-MV-urban--0-sw`，实际包含：

- 144个Bus
- 147条Line
- 2台Transformer
- 305个Switch
- 139个Load
- 134个SGen
- 1个ExternalGrid
- 35136个时间点的负荷与新能源曲线

生成过程会运行基准潮流，保存全部静态表、标准类型、稳定ID、拓扑关系、曲线元数据、基准结果、依赖版本和SHA-256校验和。正式产物位于：

```text
python-training-service/artifacts/grids/simbench-1-mv-urban-0-sw/v1/
```

P1电网包是唯一权威源。Neo4j和Redis只是运行时投影：Neo4j保存活动版本的静态设备与关系，Redis保存同一版本当前时刻的动态潮流。发布器只管理`managedBy=dongjin-python-service`的数据，不删除用户自建节点。

### 拓扑接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/topology` | 查询活动GridModel包含的Device节点和标准连接 |
| POST | `/api/topology/generate` | 生成/复用权威包，幂等发布Neo4j并激活 |
| GET | `/api/topology/source` | 查询当前活动权威电网包 |

生成请求：

```json
{
  "simbenchCode": "1-MV-urban--0-sw",
  "topologyVersion": "v1",
  "force": false
}
```

相同版本已经存在且`force=false`时直接复用；`force=true`时在临时目录完成全部校验后原子替换该版本。

### 连续潮流与快照接口

| 方法 | Java路径 | 说明 |
|---|---|---|
| POST | `/api/simulation/start` | 启动唯一计算循环，可传`startTime`、`speedFactor`、`profileStrategy` |
| POST | `/api/simulation/pause` | 暂停，不推进仿真时间 |
| POST | `/api/simulation/resume` | 继续现有循环 |
| POST | `/api/simulation/stop` | 停止循环，保留最后成功快照 |
| GET | `/api/simulation/status` | 查询状态、步数、仿真时间、耗时和最后错误 |
| GET | `/api/simulation/profiles` | 查询SimBench曲线范围、间隔、字段和单位 |
| GET | `/api/snapshots/current` | 读取Redis活动指针指向的完整`grid-snapshot-v1` |

Redis键固定为`dongjin:grid:active`、`dongjin:snapshot:{snapshotId}`、`dongjin:snapshot:active`和`dongjin:simulation:status`。快照先完整写入并设置TTL，最后在同一事务中切换活动指针；潮流不收敛或快照非法时不发布。

## 5. 离线场景与训练数据

第二部分不再使用 Java 内存随机样本。`FaultGenerationService`、`FaultSampleStore`
和 `TrainingJobService` 等旧链路已经删除，正式训练数据统一来自：

```text
P1权威电网包
  → 独立pandapower场景副本
  → TruthSnapshot
  → MeasurementFrame
  → GNN Dataset
```

Python 侧主要代码：

- `app/grid/scenarios/models.py`：场景、事件和质量码契约。
- `app/grid/scenarios/service.py`：基线/事件潮流、量测变换、标签和场景档案。
- `app/grid/datasets/builder.py`：稳定ID图、48维特征、缺失掩码和场景级切分。
- `app/grid/training/trainer.py`：两层GCN、早停、阈值校准、评估和资格门禁。
- `app/grid/offline_api.py`：场景、数据集和模型的统一FastAPI入口。

正式可见数据位于：

```text
python-training-service/artifacts/
├── scenarios/
├── datasets/
└── models/
```

每一层都同时保存人工可读的 JSON、JSONL、CSV、README 和机器高效读取的
Parquet/joblib；Manifest记录P1版本、图签名、Schema、随机种子和SHA-256。
详细文件树、指标和使用命令见 `OFFLINE_TRAINING_GUIDE.md`。

## 6. GNN离线训练模块

训练必须由命令或API显式启动，场景生成和数据集构建完成后不会自动训练，
训练完成后也不会自动接入在线研判。

图中每个P1对象是一个顶点，关系由P1拓扑构建。模型读取MeasurementFrame派生
的48个显式特征，标签和物理核对来自TruthSnapshot与ScenarioDefinition。
训练、验证和测试按完整`scenarioRunId`切分，不按节点行随机切分。

正式数据集 `p1-v1-gnn-dataset-v1` 包含108个场景、94,176行、872个顶点、
1,182条图边；正式模型 `p1-v1-gcn-model-v3` 已通过提高后的严格配置门槛。
被拒绝的v1模型仍作为白箱失败记录保存。

### Python离线接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/offline/scenario-batches/generate` | 生成可见场景批次 |
| GET | `/offline/scenario-batches` | 列出场景批次 |
| GET | `/offline/scenario-batches/{batchId}` | 查看批次Manifest |
| POST | `/offline/datasets/build` | 构建可见GNN数据集 |
| GET | `/offline/datasets` | 列出数据集 |
| GET | `/offline/datasets/{datasetId}` | 查看数据集Manifest |
| GET | `/offline/datasets/{datasetId}/preview` | 浏览CSV数据预览 |
| POST | `/offline/training/run` | 显式训练一个版本 |
| GET | `/offline/models` | 列出模型 |
| GET | `/offline/models/{modelId}` | 查看模型Manifest与指标 |

### Java统一代理

`TrainingController`与现有`PythonComputeGateway`只做代理，不在Java内复制场景
或训练状态。路径位于：

```text
/api/training/offline/scenario-batches
/api/training/offline/datasets
/api/training/offline/models
```

各资源同时提供列表、单项详情和数据集预览接口。正常前端没有恢复训练模块。

## 7. 手动训练与白箱验收

完整离线命令：

```powershell
cd E:\Java\DONGJIN\python-training-service
.\.venv\Scripts\python.exe scripts\run_offline_training_pipeline.py `
  --samples-per-type 12 `
  --random-seed 20260723 `
  --maximum-epochs 120
```

P1包存在时，这条命令不要求Neo4j、Memurai、Java和前端处于运行状态。输出的
场景、数据集、训练历史、指标和逐场景预测均保存在E盘项目目录中。

## 8. 错误研判模块

第三板块当前保留“前端持有真值、后端与Python盲判”的演示流程，但尚未接入
第二部分的合格离线模型。不得再通过旧随机样本训练；下一部分需要建立
MeasurementFrame到离线模型48维特征的在线适配、模型注册和兼容性门禁。

### 测试流程与隔离边界

1. 用户点击“生成隐藏故障并开始盲判”。
2. Vue 前端在当前拓扑中随机选择一个设备或线路，并随机注入四类故障之一。
3. 前端为当前拓扑的全部设备和线路生成观测数据；根因对象为完整故障信号，一跳和二跳对象包含逐级衰减信号。页面立即把真实故障类型、真实位置和真实观测值书面展示给用户。
4. 真实答案只保存在浏览器内存中。发送到 `/api/diagnosis/locate` 的请求只包含对象 ID 和无标签观测数据，不包含真实故障类型、真实位置或答案字段。
5. Java 强制校验请求是否覆盖当前拓扑的全部对象，避免通过只上传异常对象向模型泄露位置。
6. Python 把所有观测组成特征矩阵，把设备与线路组成邻接矩阵，由 GCN 通过两轮消息传播计算每个对象的正常概率、异常分数和故障类别。
7. Java 选择异常分数最高的对象作为定位结果，并完成上下游溯源。
8. Vue 在本地比较模型结果与真实答案，分别显示位置和类型是否一致。

训练样本约 20% 为正常运行样本，其余样本在四类故障中均衡生成。正常类别使模型能够排除拓扑中大量无故障对象，而不是把每个对象强制归入某种故障。

### 溯源规则

Java 按拓扑连接的 `source → target` 方向执行最多 4 层广度优先追踪：

- 向上游溯源：逆着连接方向查找供电侧设备和线路。
- 向下游追踪：顺着连接方向查找负荷侧设备和线路。
- 环网和联络线使用已访问集合避免无限循环。
- 故障源、上游路径和下游路径会以不同颜色高亮在第一板块拓扑图上。

### 单条观测字段

| 字段 | 含义 | 输入要求 |
|---|---|---|
| `voltagePu` | 电压标幺值 | 数值 |
| `currentPu` | 电流标幺值 | 数值 |
| `activePowerPu` | 有功功率标幺值 | 数值 |
| `reactivePowerPu` | 无功功率标幺值 | 数值 |
| `temperatureC` | 温度，单位 ℃ | 数值 |
| `connectivityRatio` | 设备连接率 | 0～1 |
| `alarmCount` | 告警数量 | 大于或等于 0 的整数 |
| `topologyDegree` | 拓扑连接度 | 大于或等于 0 的整数 |

`targetIsEdge` 由 Java 根据观测对象自动添加，前端不发送该字段。测试真值包含同一组观测值，便于书面对照。

### 研判接口

第三板块使用：

```text
POST /api/diagnosis/locate
```

请求结构：

```jsonc
{
  "observations": [
    {
      "targetKind": "NODE",
      "targetId": "GRID-R01-S01-110",
      "features": {
        "voltagePu": 1.0,
        "currentPu": 0.55,
        "activePowerPu": 0.48,
        "reactivePowerPu": 0.14,
        "temperatureC": 39.0,
        "connectivityRatio": 0.98,
        "alarmCount": 0,
        "topologyDegree": 4
      }
    }
    // 必须继续包含当前拓扑中的所有其他节点和线路
  ],
  "topK": 4,
  "traceDepth": 4
}
```

接口返回模型定位目标、预测故障类型、异常分数、Top 5 定位候选以及上下游溯源路径。返回结果不包含真实答案；正确性比较只在 Vue 前端完成。

系统只保留完整拓扑 `/api/diagnosis/locate` 研判入口。单对象输入缺少邻接关系，不能执行 GNN 消息传播，因此旧的单对象调试接口已移除。
