package com.dongjin.training;

import com.dongjin.topology.TopologyGenerationRequest;
import com.dongjin.topology.TopologyGenerationResult;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class PythonComputeGateway {

    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;
    private final String baseUrl;

    public PythonComputeGateway(
            ObjectMapper objectMapper,
            @Value("${app.python-service.base-url:http://127.0.0.1:8001}") String baseUrl
    ) {
        this.objectMapper = objectMapper;
        this.baseUrl = baseUrl.replaceAll("/+$", "");
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(5))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    public TopologyGenerationResult initializeGrid(TopologyGenerationRequest request) {
        JsonNode response = post("/grids/initialize", request, Duration.ofMinutes(5));
        return convert(response, TopologyGenerationResult.class, "标准电网响应解析失败");
    }

    public TopologyGenerationResult activeGrid() {
        JsonNode response = get("/grids/active", Duration.ofSeconds(15));
        return convert(response, TopologyGenerationResult.class, "活动电网响应解析失败");
    }

    public Map<String, Object> publishGridToNeo4j(String gridId) {
        String encodedGridId = URLEncoder.encode(gridId, StandardCharsets.UTF_8);
        JsonNode response = post(
                "/grids/" + encodedGridId + "/publish/neo4j",
                Map.of(),
                Duration.ofMinutes(5)
        );
        return objectMapper.convertValue(
                response,
                new TypeReference<Map<String, Object>>() {
                }
        );
    }

    public JsonNode startSimulation(Object request) {
        return post("/simulation/start", request, Duration.ofSeconds(30));
    }

    public JsonNode pauseSimulation() {
        return post("/simulation/pause", Map.of(), Duration.ofSeconds(10));
    }

    public JsonNode resumeSimulation() {
        return post("/simulation/resume", Map.of(), Duration.ofSeconds(10));
    }

    public JsonNode stopSimulation() {
        return post("/simulation/stop", Map.of(), Duration.ofSeconds(10));
    }

    public JsonNode simulationStatus() {
        return get("/simulation/status", Duration.ofSeconds(10));
    }

    public JsonNode currentSnapshot() {
        return get("/snapshots/current", Duration.ofSeconds(10));
    }

    public JsonNode simulationProfiles() {
        return get("/simulation/profiles", Duration.ofSeconds(30));
    }

    public JsonNode generateOfflineScenarioBatch(Object request) {
        return post("/offline/scenario-batches/generate", request, Duration.ofMinutes(30));
    }

    public JsonNode offlineScenarioBatches() {
        return get("/offline/scenario-batches", Duration.ofSeconds(30));
    }

    public JsonNode offlineScenarioBatch(String batchId) {
        return get("/offline/scenario-batches/" + encode(batchId), Duration.ofSeconds(30));
    }

    public JsonNode buildOfflineDataset(Object request) {
        return post("/offline/datasets/build", request, Duration.ofMinutes(15));
    }

    public JsonNode offlineDatasets() {
        return get("/offline/datasets", Duration.ofSeconds(30));
    }

    public JsonNode offlineDataset(String datasetId) {
        return get("/offline/datasets/" + encode(datasetId), Duration.ofSeconds(30));
    }

    public JsonNode offlineDatasetPreview(String datasetId, int limit) {
        return get(
                "/offline/datasets/" + encode(datasetId) + "/preview?limit=" + limit,
                Duration.ofSeconds(30)
        );
    }

    public JsonNode runOfflineTraining(Object request) {
        return post("/offline/training/run", request, Duration.ofMinutes(30));
    }

    public JsonNode offlineModels() {
        return get("/offline/models", Duration.ofSeconds(30));
    }

    public JsonNode offlineModel(String modelId) {
        return get("/offline/models/" + encode(modelId), Duration.ofSeconds(30));
    }

    public JsonNode inferenceModels() {
        return get("/inference/models", Duration.ofSeconds(30));
    }

    public JsonNode inferenceModel() {
        return get("/inference/model", Duration.ofSeconds(10));
    }

    public JsonNode inferenceModelHistory() {
        return get("/inference/model/history", Duration.ofSeconds(30));
    }

    public JsonNode checkInferenceModel(String modelId) {
        return post(
                "/inference/model/check/" + encode(modelId),
                Map.of(),
                Duration.ofSeconds(30)
        );
    }

    public JsonNode selectInferenceModel(Object request) {
        return post("/inference/model/select", request, Duration.ofSeconds(30));
    }

    public JsonNode reloadInferenceModel() {
        return post("/inference/model/reload", Map.of(), Duration.ofSeconds(30));
    }

    public JsonNode rollbackInferenceModel() {
        return post("/inference/model/rollback", Map.of(), Duration.ofSeconds(30));
    }

    public JsonNode diagnoseCurrentSnapshot() {
        return post("/diagnosis/current", Map.of(), Duration.ofMinutes(2));
    }

    public JsonNode diagnoseSnapshot(String snapshotId) {
        return post(
                "/diagnosis/snapshots/" + encode(snapshotId),
                Map.of(),
                Duration.ofMinutes(2)
        );
    }

    public JsonNode diagnosisResult(String diagnosisId) {
        return get(
                "/diagnosis/" + encode(diagnosisId),
                Duration.ofSeconds(30)
        );
    }

    public JsonNode startDiagnosisMonitor(Object request) {
        return post("/diagnosis/monitor/start", request, Duration.ofSeconds(30));
    }

    public JsonNode diagnosisMonitorStatus() {
        return get("/diagnosis/monitor/status", Duration.ofSeconds(10));
    }

    public JsonNode stopDiagnosisMonitor() {
        return post("/diagnosis/monitor/stop", Map.of(), Duration.ofSeconds(30));
    }

    public JsonNode shadowSessions() {
        return get("/shadow-sessions", Duration.ofSeconds(30));
    }

    public JsonNode createShadowSession(Object request) {
        return post("/shadow-sessions", request, Duration.ofMinutes(5));
    }

    public JsonNode shadowSession(String sessionId) {
        return get("/shadow-sessions/" + encode(sessionId), Duration.ofSeconds(30));
    }

    public JsonNode diagnoseShadowSession(String sessionId) {
        return post(
                "/shadow-sessions/" + encode(sessionId) + "/diagnose",
                Map.of(),
                Duration.ofMinutes(2)
        );
    }

    public JsonNode revealShadowSession(String sessionId) {
        return post(
                "/shadow-sessions/" + encode(sessionId) + "/reveal",
                Map.of(),
                Duration.ofSeconds(30)
        );
    }

    public JsonNode closeShadowSession(String sessionId) {
        return delete(
                "/shadow-sessions/" + encode(sessionId),
                Duration.ofSeconds(30)
        );
    }

    public JsonNode runShortCircuitAnalysis(Object request) {
        return post(
                "/short-circuit-analyses",
                request,
                Duration.ofMinutes(2)
        );
    }

    public JsonNode shortCircuitAnalysis(String analysisId) {
        return get(
                "/short-circuit-analyses/" + encode(analysisId),
                Duration.ofSeconds(30)
        );
    }

    private JsonNode get(String path, Duration timeout) {
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .timeout(timeout)
                .GET()
                .build();
        return send(request);
    }

    private JsonNode post(String path, Object payload, Duration timeout) {
        String body;
        try {
            body = objectMapper.writeValueAsString(payload);
        } catch (JsonProcessingException exception) {
            throw new TrainingGatewayException("Python请求数据序列化失败", exception);
        }

        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .timeout(timeout)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(body))
                .build();
        return send(request);
    }

    private JsonNode delete(String path, Duration timeout) {
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .timeout(timeout)
                .DELETE()
                .build();
        return send(request);
    }

    private JsonNode send(HttpRequest request) {
        try {
            HttpResponse<String> response = httpClient.send(request, HttpResponse.BodyHandlers.ofString());
            if (response.statusCode() < 200 || response.statusCode() >= 300) {
                throw new TrainingGatewayException(
                        "Python计算服务返回错误（" + response.statusCode() + "）：" + response.body()
                );
            }
            return objectMapper.readTree(response.body());
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new TrainingGatewayException("Python请求被中断", exception);
        } catch (IOException exception) {
            throw new TrainingGatewayException(
                    "无法连接Python计算服务，请确认它已在" + baseUrl + "启动",
                    exception
            );
        }
    }

    private String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private <T> T convert(JsonNode value, Class<T> type, String message) {
        try {
            return objectMapper.treeToValue(value, type);
        } catch (JsonProcessingException exception) {
            throw new TrainingGatewayException(message, exception);
        }
    }
}
