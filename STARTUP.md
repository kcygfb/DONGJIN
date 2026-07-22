# 项目启动说明

本文档只说明环境准备、首次安装、启动顺序和运行验证。项目结构与接口说明请查看 `PROJECT_GUIDE.md`。

## 1. 环境要求

- JDK 17 或更高版本
- Apache Maven 3.9+
- Node.js 20+ 与 npm
- Python 3.10+
- Neo4j 5.x

本项目默认端口：

| 服务 | 端口 |
|---|---:|
| Neo4j Bolt | 7687 |
| Python 训练服务 | 8001 |
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

启动 Neo4j 后，保持数据库运行。首次进入页面时可以点击第一板块的“生成标准电网”，程序会创建约 193 个设备和 217 条连接。

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
.\.venv\Scripts\python.exe -c "import fastapi, numpy, sklearn, joblib, uvicorn; print('Python GNN 环境正常')"
```

## 4. 首次安装前端依赖

```powershell
cd E:\Java\DONGJIN\frontend
npm install
```

## 5. 完整启动顺序

### 第一步：启动 Neo4j

通过 Neo4j Desktop、Neo4j Console 或系统服务启动数据库。

### 第二步：启动 Python 训练服务

打开一个 PowerShell：

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

1. 在第一板块生成标准电网。
2. 在第二板块生成故障样本并完成模型训练；默认生成 500 条，通常划分为 400 张训练图和 100 张测试图，模型为两层 GCN。
3. 在第三板块点击“生成隐藏故障并开始盲判”。页面会先书面显示只保存在浏览器中的真实故障，再把包含邻接传播信号的全拓扑无标签观测交给 Java/Python；GCN 自动定位和分类，最后由前端比较真实答案与研判结果。

研判成功后，第三板块会列出位置、类型、Top 5 候选和上下游设备，第一板块会同时高亮模型定位的故障源与溯源路径。训练时使用的拓扑可以与当前研判拓扑不同。

升级到纯 GNN 后必须先点击“重置训练”，再重新生成 500 条样本并训练一次。旧模型文件不包含完整的 GCN 权重和图特征缩放器，调用批量定位接口时会明确提示重新训练。

详细字段含义、约束和接口 JSON 请查看 `PROJECT_GUIDE.md` 的“错误研判模块”。

## 8. 常见问题

### 前端提示后端不可访问

确认 Spring Boot 已经运行在 `8080`，并检查 `frontend/vite.config.js` 中的代理地址。

### 训练任务连接 Python 失败

确认 Python 服务运行在 `127.0.0.1:8001`。Java 默认使用：

```text
app.training-service.base-url=http://127.0.0.1:8001
```

### Python 依赖标红

IntelliJ 的 Python 模块应选择：

```text
E:\Java\DONGJIN\python-training-service\.venv\Scripts\python.exe
```

### Neo4j 连接失败

检查 Neo4j 是否启动、密码是否正确，以及 `NEO4J_URI` 是否使用了正确端口。
