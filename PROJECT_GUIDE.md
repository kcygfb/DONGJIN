# 项目代码与接口说明

## 1. 项目目标

该项目是一个电网拓扑、故障样本生成、模型训练和错误研判工作台。目前包含：

1. Neo4j 电网拓扑读取、生成和展示。
2. 基于拓扑的可复现故障样本生成。
3. Java 到 Python 的异步训练任务传递。
4. GCN 图神经网络训练、评估、保存和激活。
5. 对全拓扑观测执行 GNN 故障定位、分类和上下游溯源。

## 2. 总体结构

```text
DONGJIN
├─ src/main/java/com/dongjin
│  ├─ DongjinApplication.java
│  ├─ topology
│  │  ├─ TopologyController.java
│  │  ├─ TopologyRepository.java
│  │  └─ GridTopologyGenerationService.java
│  └─ training
│     ├─ FaultGenerationService.java
│     ├─ FaultSampleStore.java
│     ├─ TrainingJobService.java
│     ├─ PythonTrainingGateway.java
│     └─ TrainingController.java
├─ frontend
│  └─ src
│     ├─ components
│     └─ services
├─ python-training-service
│  ├─ app/main.py
│  └─ requirements.txt
├─ STARTUP.md
└─ PROJECT_GUIDE.md
```

## 3. 服务调用关系

```text
Vue 前端
  ↓ /api
Spring Boot
  ├─→ Neo4j：拓扑读写
  └─→ Python 8001：模型训练、预测、重置
        ↓
      artifacts：模型文件和元数据
```

前端只调用 Spring Boot，不直接调用 Python。训练和盲判时 Java 都会把当前 Neo4j 拓扑结构发送给 Python；Python 使用 GNN 完成整图定位与分类，Java 再完成上下游溯源。

## 4. 电网拓扑模块

### 主要代码

- `TopologyController`：提供拓扑查询和生成接口。
- `TopologyRepository`：执行 Neo4j 查询和批量写入。
- `GridTopologyGenerationService`：创建分层电网数据。
- `TopologySection.vue`：展示拓扑、刷新数据和触发标准电网生成。

### 标准电网结构

默认生成：

- 1 座 500kV 枢纽站
- 3 座 220kV 区域站
- 9 座 110kV 变电站
- 18 台主变压器
- 18 组 10kV 母线
- 36 个馈线开关
- 108 个负荷
- 约 193 个设备、217 条连接

同时包含区域环网、110kV联络线、母联开关和10kV备用联络线。

生成器只替换带有以下标记的数据：

```text
generatedBy=dongjin-layered-grid-v1
```

手工创建的 Neo4j 节点不会被删除。

### 拓扑接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/topology` | 查询全部 Device 节点和连接 |
| POST | `/api/topology/generate` | 生成或替换程序创建的标准电网 |

生成规模可以通过以下字段调整：

```json
{
  "regions": 3,
  "substationsPerRegion": 3,
  "feedersPerSubstation": 4,
  "loadsPerFeeder": 3,
  "seed": 20260717,
  "replaceGenerated": true
}
```

设备总数上限为 5000。

## 5. 故障生成模块

`FaultGenerationService` 从当前 Neo4j 拓扑中选择合法目标，生成带标签和特征的故障样本。

当前支持：

- `DEVICE_OFFLINE`：设备离线
- `VOLTAGE_ANOMALY`：电压异常
- `LINE_OVERLOAD`：线路过载
- `LINE_DISCONNECTED`：线路断开

样本包含目标设备、严重程度、随机种子、受影响设备和数值特征。相同拓扑、参数和随机种子可以复现相同的数据分布。

页面和接口的默认生成量为 500 条。样本满足分层留出条件时，默认按照约 80%/20% 划分训练集和测试集，即通常为 400/100。

训练时，每条带标签样本会被扩展成一个完整图快照：目标对象注入故障特征，一跳和二跳邻接对象加入逐级衰减的传播信号，其余对象使用正常观测。目标 ID 用于把故障放到图中的正确位置，但不会作为数值特征学习。因此 GNN 可以在一张拓扑上训练，再对节点数量不同的当前拓扑进行归纳式研判。模型使用 `targetIsEdge` 区分设备与线路，该字段由系统生成。

样本目前保存在 `FaultSampleStore` 的进程内存中，Java 重启后会清空。

## 6. 训练模块

### Java 侧

- `TrainingController`：暴露训练 REST API。
- `TrainingJobService`：异步执行训练任务并维护状态。
- `PythonTrainingGateway`：通过 HTTP 把样本传递给 Python。

### Python 侧

`python-training-service/app/main.py` 使用：

- `DictVectorizer`：将可扩展特征字典转换为模型输入。
- `StandardScaler`：按训练集缩放数值特征。
- NumPy 与 SciPy 稀疏矩阵：实现两层 GCN，使用归一化邻接矩阵执行两轮消息传播，包含 ReLU、Softmax、类别加权交叉熵和 Adam 反向传播，是主训练与主研判模型。
- `joblib`：保存模型。
- FastAPI：提供训练、预测、模型查询和重置接口。

图的建模方式为：每个设备是一个 GNN 顶点，每条线路也作为一个 GNN 顶点，线路顶点分别连接源设备和目标设备。这样设备故障、线路故障都能使用统一的顶点分类输出，并通过两层消息传播接收邻接对象的状态。

训练完成后会返回 GNN 指标并自动激活模型。页面中的“GNN最终准确率”表示测试图中故障位置和故障类型同时正确；另行显示 GNN 定位准确率，以及 Precision、Recall、F1和混淆矩阵元数据。

模型默认保存在：

```text
python-training-service/artifacts
```

可以通过 `DONGJIN_MODEL_DIR` 环境变量修改。

### Java 训练接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/training/errors/generate` | 生成故障样本 |
| GET | `/api/training/errors` | 查询当前样本 |
| DELETE | `/api/training/errors` | 清空当前样本 |
| POST | `/api/training/jobs` | 创建异步训练任务 |
| GET | `/api/training/jobs/{id}` | 查询训练进度和指标 |
| GET | `/api/training/models/active` | 查询当前激活模型 |
| POST | `/api/training/reset` | 重置模型、样本和训练记录 |

### Python 内部接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/train` | 训练并激活模型 |
| POST | `/predict/batch` | 使用 GNN 对完整拓扑执行主研判 |
| GET | `/models/active` | 查询当前模型 |
| DELETE | `/reset` | 删除当前及历史模型 |

## 7. 训练重置

第二板块的“重置训练”会：

1. 删除 Python 当前激活模型。
2. 删除 Python 历史模型文件和元数据。
3. 清空 Java 内存中的故障样本。
4. 清空已完成的训练任务和页面指标。

训练任务正在运行时不允许重置，避免后台任务重新生成模型。

## 8. 错误研判模块

第三板块采用“前端持有真值、后端与 Python 盲判”的测试流程。必须使用新的 GNN 模型，因此升级后需要重置旧模型、重新生成 500 条样本并训练一次。

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
