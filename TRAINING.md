# 故障生成与模型训练

第二板块由三部分组成：

- `FaultGenerationService`：读取 Neo4j 拓扑并生成带标签、可复现的故障样本。
- `PythonTrainingGateway`：将选定样本转交给 Python，并把训练/推理结果返回给 Java。
- `python-training-service`：训练并保存模型，自动激活最新模型，并提供统一推理接口。

## 启动顺序

1. 启动 Neo4j，并确认已有 `Device` 节点及设备关系。
2. 按照 `python-training-service/README.md` 启动 Python 服务（默认端口 `8001`）。
3. 启动 Spring Boot（默认端口 `8080`）。
4. 在 `frontend` 目录执行 `npm run dev`。

Java 默认连接 `http://127.0.0.1:8001`。需要修改时可设置 Spring 属性：

```text
app.training-service.base-url=http://127.0.0.1:8001
```

## Java 接口

- `POST /api/training/errors/generate`：生成故障样本。
- `GET /api/training/errors`：读取当前进程中的全部样本。
- `DELETE /api/training/errors`：清空当前样本。
- `POST /api/training/jobs`：创建异步训练任务。
- `GET /api/training/jobs/{id}`：查询任务进度和指标。
- `POST /api/training/predict`：转发到当前 Python 模型，用于后续错误研判。
- `GET /api/training/models/active`：查询当前激活模型。

生成请求示例：

```json
{
  "count": 120,
  "seed": 20260715,
  "faultTypes": [
    "DEVICE_OFFLINE",
    "VOLTAGE_ANOMALY",
    "LINE_OVERLOAD",
    "LINE_DISCONNECTED"
  ]
}
```

当前样本仓库有意保持为进程内存实现，方便先跑通第二板块且不引入新的数据库。模型文件由 Python 服务持久化；下一阶段如果需要跨 Java 重启保留训练集，可直接替换 `FaultSampleStore`，其余接口无需变化。
