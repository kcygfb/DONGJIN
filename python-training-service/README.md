# Python 故障训练服务

该服务接收 Spring Boot 传来的故障样本，训练随机森林基线模型，保存并自动激活模型，同时提供后续错误研判使用的统一预测接口。

## 启动

```powershell
cd E:\Java\DONGJIN\python-training-service
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8001
```

接口文档启动后位于 `http://127.0.0.1:8001/docs`。

## 主要接口

- `POST /train`：训练、保存并激活一个新模型。
- `POST /predict`：使用当前模型进行单条错误研判。
- `GET /models/active`：查询当前模型及评估指标。
- `GET /health`：健康检查。

模型默认保存在 `python-training-service/artifacts`。可通过环境变量 `DONGJIN_MODEL_DIR` 修改保存位置。

预测请求示例：

```json
{
  "features": {
    "voltagePu": 0.72,
    "currentPu": 0.91,
    "activePowerPu": 0.64,
    "reactivePowerPu": 0.31,
    "temperatureC": 58.0,
    "connectivityRatio": 0.87,
    "alarmCount": 3,
    "topologyDegree": 2
  },
  "topK": 3
}
```
