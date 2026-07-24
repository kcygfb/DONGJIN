# 项目启动说明

本文档只说明环境准备、首次安装、启动顺序和运行验证。项目结构与接口说明请查看 `PROJECT_GUIDE.md`。

## 1. 环境要求

- JDK 17 或更高版本
- Apache Maven 3.9+
- Node.js 20+ 与 npm
- Python 3.10+
- Neo4j 5.x
- Redis 7.x 或兼容的本机服务（当前电脑已安装 Memurai Developer 4.1.7）

本项目默认端口：

| 服务 | 端口 |
|---|---:|
| Neo4j Bolt | 7687 |
| Redis | 6379 |
| Python计算服务（训练+标准电网） | 8001 |
| Spring Boot | 8080 |
| Vite 前端 | 5173 |

## 2. 配置 Neo4j

Spring Boot 从以下环境变量读取 Neo4j 配置：

```text
NEO4J_URI=neo4j://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=你的密码
NEO4J_DATABASE=neo4j
```

其中密码必须配置。也可以在 IntelliJ 的 Spring Boot 运行配置中添加这些环境变量。

启动Neo4j后保持数据库运行。页面“生成并发布SimBench拓扑”会在生成或复用P1电网包后，将该版本投影为Neo4j活动拓扑。

Redis默认配置：

```text
REDIS_URL=redis://localhost:6379/0
```

Redis用于连续潮流完整快照，必须在启动仿真前运行。

当前电脑使用 Redis 协议兼容的 Memurai。运行文件、配置、日志和本地数据均位于
`E:\Java\DONGJIN`，Windows 服务启动类型为`Manual`，不会随开机自动运行。

```powershell
# 在项目根目录手动启动、查看状态和停止
cd E:\Java\DONGJIN
.\scripts\start-memurai.ps1
.\scripts\status-memurai.ps1
.\scripts\stop-memurai.ps1
```

启动和停止服务时 Windows 会请求管理员确认。配置文件位于
`E:\Java\DONGJIN\config\memurai.conf`，运行文件和本地数据位于
`E:\Java\DONGJIN\runtime\memurai`。服务仅监听`127.0.0.1:6379`，
最大内存固定为`256MB`，淘汰策略为`volatile-ttl`。

动态快照默认保留600秒；达到内存上限时优先淘汰最早到期的旧快照。
`runtime`目录已被Git忽略，不会提交二进制、日志和RDB文件。Memurai Developer
仅用于本地开发和测试，连续运行满10天后需要停止再启动。

## 3. 首次安装 Python 依赖

只需要执行一次：

```powershell
cd E:\Java\DONGJIN\python-training-service

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

如果系统终端找不到 `python`，使用已安装解释器的完整路径创建虚拟环境：

```powershell
& "C:\Users\xzc12\AppData\Local\Programs\Python\Python312\python.exe" `
  -m venv "E:\Java\DONGJIN\python-training-service\.venv"
```

验证依赖：

```powershell
.\.venv\Scripts\python.exe -c "import fastapi, numpy, sklearn, pyarrow, pandapower, simbench, neo4j, redis; print('Python计算环境正常')"
```

## 4. 首次安装前端依赖

```powershell
cd E:\Java\DONGJIN\frontend
npm install
```

## 5. 完整启动顺序

### 第一步：启动 Neo4j

通过 Neo4j Desktop、Neo4j Console 或系统服务启动数据库。

### 第二步：启动Python计算服务

先在项目根目录执行`.\scripts\start-memurai.ps1`，然后打开一个 PowerShell：

```powershell
cd E:\Java\DONGJIN\python-training-service
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

看到 `Application startup complete` 后，访问：

```text
http://127.0.0.1:8001/docs
```

### 第三步：启动 Spring Boot

再打开一个 PowerShell：

```powershell
cd E:\Java\DONGJIN
mvn spring-boot:run
```

也可以在 IntelliJ 中直接运行：

```text
src/main/java/com/dongjin/DongjinApplication.java
```

### 第四步：启动前端

再打开一个 PowerShell：

```powershell
cd E:\Java\DONGJIN\frontend
npm run dev
```

访问终端显示的地址，通常为：

```text
http://localhost:5173
```

## 6. 运行验证

依次检查：

```text
Python 健康检查：http://127.0.0.1:8001/health
Python 接口文档：http://127.0.0.1:8001/docs
Java 拓扑接口：http://127.0.0.1:8080/api/topology
Java 活动电网包：http://127.0.0.1:8080/api/topology/source
Java 仿真状态：http://127.0.0.1:8080/api/simulation/status
Java 当前快照：http://127.0.0.1:8080/api/snapshots/current
前端页面：http://localhost:5173
```

前端生产构建验证：

```powershell
cd E:\Java\DONGJIN\frontend
npm run build
```

Java 构建验证：

```powershell
cd E:\Java\DONGJIN
mvn clean package
```

## 7. 页面使用顺序

1. 在第一板块点击“生成并发布SimBench拓扑”，生成或复用`1-MV-urban--0-sw/v1`权威包，并在核对成功后激活Neo4j投影。
2. 选择`线性插值`或`保持前值`，点击“启动”开始连续潮流。页面每2秒读取Redis活动快照，并在原拓扑节点旁显示实时电压、负载率或功率。
3. 可在同一区域暂停、继续或停止；计算失败时状态变为`ERROR`，Redis仍指向上一个成功快照。
4. 模型训练不再放在前端页面中，需要时通过Java训练接口或Python内部接口手动执行。
5. 已有兼容活动模型后，在研判板块点击“生成隐藏故障并开始盲判”。

研判成功后，研判板块会列出位置、类型、Top 5候选和上下游设备，拓扑板块会同时高亮模型定位的故障源与溯源路径。

离线生成训练数据和训练模型时，不需要启动Neo4j、Memurai、Java或前端；只要P1
权威电网包已经生成，就可以在单独PowerShell中执行：

```powershell
cd E:\Java\DONGJIN\python-training-service
.\.venv\Scripts\python.exe scripts\run_offline_training_pipeline.py `
  --samples-per-type 12 `
  --random-seed 20260723 `
  --maximum-epochs 120
```

训练数据不会藏在进程内存中。场景真值、模拟量测、完整CSV、Parquet、训练历史、
评估指标和逐场景预测都位于`python-training-service/artifacts`。详细说明见
`OFFLINE_TRAINING_GUIDE.md`。

需要分步执行时，手动调用`/api/training/offline/*`代理接口或Python
`/offline/*`接口；前端不提供造数、训练和故障注入入口。

详细字段含义、约束和接口 JSON 请查看 `PROJECT_GUIDE.md` 的“错误研判模块”。

## 8. 常见问题

### 前端提示后端不可访问

确认 Spring Boot 已经运行在 `8080`，并检查 `frontend/vite.config.js` 中的代理地址。

### Java连接Python服务失败

确认Python服务运行在`127.0.0.1:8001`。Java默认使用：

```text
app.python-service.base-url=http://127.0.0.1:8001
```

### Python 依赖标红

IntelliJ 的 Python 模块应选择：

```text
E:\Java\DONGJIN\python-training-service\.venv\Scripts\python.exe
```

### Neo4j 连接失败

检查 Neo4j 是否启动、密码是否正确，以及 `NEO4J_URI` 是否使用了正确端口。

### 仿真启动后进入ERROR

先查看页面显示的`lastError`，再检查Redis是否启动、`REDIS_URL`是否正确，以及Neo4j活动版本是否和当前权威电网包一致。系统不会在Neo4j未发布活动拓扑时启动连续潮流。
