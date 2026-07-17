package com.dongjin.training;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class PythonTrainingGateway {

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final String baseUrl;

    public PythonTrainingGateway(
            ObjectMapper objectMapper,
            @Value("${app.training-service.base-url:http://127.0.0.1:8001}") String baseUrl
    ) {
        this.objectMapper = objectMapper;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .build();
    }

    public JsonNode train(String datasetName, List<FaultSample> samples) {
        return post("/train", Map.of(
                "datasetName", datasetName,
                "samples", samples
        ), Duration.ofMinutes(15));
    }

    public JsonNode predict(JsonNode request) {
        return post("/predict", request, Duration.ofSeconds(30));
    }

    public JsonNode activeModel() {
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + "/models/active"))
                .timeout(Duration.ofSeconds(10))
                .GET()
                .build();
        return send(request);
    }

    private JsonNode post(String path, Object payload, Duration timeout) {
        String body;
        try {
            body = objectMapper.writeValueAsString(payload);
        } catch (JsonProcessingException exception) {
            throw new TrainingGatewayException("训练数据序列化失败", exception);
        }

        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        return send(request);
    }

    private JsonNode send(HttpRequest request) {
        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new TrainingGatewayException(
                        "Python 训练服务返回错误（" + response.statusCode() + "）：" + response.body()
                );
            }
            return objectMapper.readTree(response.body());
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new TrainingGatewayException("训练请求被中断", exception);
        } catch (IOException exception) {
            throw new TrainingGatewayException(
                    "无法连接 Python 训练服务，请确认它已在 " + baseUrl + " 启动",
                    exception
            );
        }
    }
}
