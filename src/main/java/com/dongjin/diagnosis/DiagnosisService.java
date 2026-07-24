package com.dongjin.diagnosis;

import com.dongjin.diagnosis.BlindDiagnosisRequest.Observation;
import com.dongjin.diagnosis.BlindDiagnosisResult.LocationCandidate;
import com.dongjin.topology.TopologyData;
import com.dongjin.topology.GnnTopology;
import com.dongjin.topology.TopologyRepository;
import com.dongjin.training.PythonComputeGateway;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Service;

@Service
public class DiagnosisService {

    private static final Set<String> REQUIRED_FEATURES = Set.of(
            "voltagePu",
            "currentPu",
            "activePowerPu",
            "reactivePowerPu",
            "temperatureC",
            "connectivityRatio",
            "alarmCount",
            "topologyDegree"
    );

    private final PythonComputeGateway trainingGateway;
    private final TopologyTraceService traceService;
    private final TopologyRepository topologyRepository;

    public DiagnosisService(
            PythonComputeGateway trainingGateway,
            TopologyTraceService traceService,
            TopologyRepository topologyRepository
    ) {
        this.trainingGateway = trainingGateway;
        this.traceService = traceService;
        this.topologyRepository = topologyRepository;
    }

    public BlindDiagnosisResult locate(BlindDiagnosisRequest request) {
        if (request == null || request.observations() == null || request.observations().isEmpty()) {
            throw new IllegalArgumentException("全拓扑观测数据不能为空");
        }
        int topK = valueInRange("候选数量", request.topK(), 4, 1, 4);
        int traceDepth = valueInRange("溯源深度", request.traceDepth(), 4, 1, 8);

        List<TopologyData.Node> nodes = topologyRepository.findNodes();
        List<TopologyData.Edge> edges = topologyRepository.findEdges();
        Map<String, String> targetNames = new HashMap<>();
        Set<String> expectedTargets = new LinkedHashSet<>();
        nodes.forEach(node -> {
            String key = targetKey("NODE", node.id());
            expectedTargets.add(key);
            targetNames.put(key, node.name());
        });
        edges.forEach(edge -> {
            String key = targetKey("EDGE", edge.id());
            expectedTargets.add(key);
            targetNames.put(key, edge.name());
        });

        Set<String> receivedTargets = new LinkedHashSet<>();
        List<Map<String, Object>> observationsForModel = new ArrayList<>(request.observations().size());
        for (Observation observation : request.observations()) {
            if (observation == null) {
                throw new IllegalArgumentException("观测记录不能为空");
            }
            String kind = normalizeKind(observation.targetKind());
            String key = targetKey(kind, observation.targetId());
            if (!expectedTargets.contains(key)) {
                throw new IllegalArgumentException("观测对象不属于当前拓扑：" + key);
            }
            if (!receivedTargets.add(key)) {
                throw new IllegalArgumentException("观测对象重复：" + key);
            }
            validateFeatures(observation.features());
            Map<String, Double> modelFeatures = new LinkedHashMap<>(observation.features());
            modelFeatures.put("targetIsEdge", "EDGE".equals(kind) ? 1.0 : 0.0);
            observationsForModel.add(Map.of(
                    "targetKind", kind,
                    "targetId", observation.targetId(),
                    "features", modelFeatures
            ));
        }

        Set<String> missingTargets = new LinkedHashSet<>(expectedTargets);
        missingTargets.removeAll(receivedTargets);
        if (!missingTargets.isEmpty()) {
            throw new IllegalArgumentException(
                    "必须提交当前拓扑的完整观测数据，缺少 " + missingTargets.size() + " 个对象"
            );
        }

        JsonNode batchResult = trainingGateway.predictBatch(Map.of(
                "observations", observationsForModel,
                "topology", GnnTopology.from(nodes, edges),
                "topK", topK
        ));
        List<ScoredPrediction> scored = new ArrayList<>();
        for (JsonNode predictionNode : batchResult.path("predictions")) {
            String kind = normalizeKind(predictionNode.path("targetKind").asText());
            String id = predictionNode.path("targetId").asText();
            JsonNode compatibleCandidate = compatibleCandidate(kind, predictionNode.path("candidates"));
            ObjectNode adjustedPrediction = predictionNode.deepCopy();
            adjustedPrediction.put("modelVersion", batchResult.path("modelVersion").asText());
            adjustedPrediction.put("modelType", batchResult.path("modelType").asText("GNN_GCN"));
            adjustedPrediction.put("predictedFaultType", compatibleCandidate.path("faultType").asText());
            adjustedPrediction.put("confidence", compatibleCandidate.path("confidence").asDouble());
            scored.add(new ScoredPrediction(
                    kind,
                    id,
                    adjustedPrediction,
                    predictionNode.path("anomalyScore").asDouble(),
                    compatibleCandidate.path("confidence").asDouble()
            ));
        }
        if (scored.isEmpty()) {
            throw new IllegalStateException("模型没有返回任何定位候选");
        }
        scored.sort(Comparator
                .comparingDouble(ScoredPrediction::anomalyScore).reversed()
                .thenComparing(Comparator.comparingDouble(ScoredPrediction::confidence).reversed()));

        ScoredPrediction winner = scored.get(0);
        TopologyTraceService.TraceOutcome trace = traceService.trace(
                winner.targetKind(), winner.targetId(), traceDepth
        );
        List<LocationCandidate> candidates = scored.stream().limit(5).map(candidate -> {
            String key = targetKey(candidate.targetKind(), candidate.targetId());
            return new LocationCandidate(
                    candidate.targetKind(),
                    candidate.targetId(),
                    targetNames.getOrDefault(key, candidate.targetId()),
                    candidate.prediction().path("predictedFaultType").asText(),
                    candidate.anomalyScore(),
                    candidate.confidence()
            );
        }).toList();
        return new BlindDiagnosisResult(
                trace.target(),
                winner.prediction(),
                trace.trace(),
                request.observations().size(),
                candidates
        );
    }

    private JsonNode compatibleCandidate(String kind, JsonNode candidates) {
        Set<String> allowed = "EDGE".equals(kind)
                ? Set.of("LINE_OVERLOAD", "LINE_DISCONNECTED")
                : Set.of("DEVICE_OFFLINE", "VOLTAGE_ANOMALY");
        for (JsonNode candidate : candidates) {
            if (allowed.contains(candidate.path("faultType").asText())) {
                return candidate;
            }
        }
        throw new IllegalStateException("模型没有返回与拓扑对象类型匹配的故障候选");
    }

    private String normalizeKind(String value) {
        if (value == null) {
            throw new IllegalArgumentException("观测对象类型不能为空");
        }
        String kind = value.trim().toUpperCase(Locale.ROOT);
        if (!"NODE".equals(kind) && !"EDGE".equals(kind)) {
            throw new IllegalArgumentException("观测对象类型只支持 NODE 或 EDGE");
        }
        return kind;
    }

    private String targetKey(String kind, String id) {
        if (id == null || id.isBlank()) {
            throw new IllegalArgumentException("观测对象 ID 不能为空");
        }
        return kind + ":" + id;
    }

    private void validateFeatures(Map<String, Double> features) {
        if (features == null) {
            throw new IllegalArgumentException("错误特征不能为空");
        }
        Set<String> missing = new LinkedHashSet<>(REQUIRED_FEATURES);
        missing.removeAll(features.keySet());
        if (!missing.isEmpty()) {
            throw new IllegalArgumentException("缺少错误特征：" + String.join("、", missing));
        }
        Set<String> unknown = new LinkedHashSet<>(features.keySet());
        unknown.removeAll(REQUIRED_FEATURES);
        if (!unknown.isEmpty()) {
            throw new IllegalArgumentException("包含未知错误特征：" + String.join("、", unknown));
        }
        features.forEach((key, value) -> {
            if (value == null || !Double.isFinite(value)) {
                throw new IllegalArgumentException("错误特征必须是有效数值：" + key);
            }
        });
        double connectivityRatio = features.get("connectivityRatio");
        if (connectivityRatio < 0 || connectivityRatio > 1) {
            throw new IllegalArgumentException("connectivityRatio 必须在 0 到 1 之间");
        }
        requireNonNegativeInteger("alarmCount", features.get("alarmCount"));
        requireNonNegativeInteger("topologyDegree", features.get("topologyDegree"));
    }

    private void requireNonNegativeInteger(String name, double value) {
        if (value < 0 || value != Math.rint(value)) {
            throw new IllegalArgumentException(name + " 必须是大于或等于 0 的整数");
        }
    }

    private int valueInRange(String name, Integer value, int defaultValue, int minimum, int maximum) {
        int resolved = value == null ? defaultValue : value;
        if (resolved < minimum || resolved > maximum) {
            throw new IllegalArgumentException(name + "必须在 " + minimum + " 到 " + maximum + " 之间");
        }
        return resolved;
    }

    private record ScoredPrediction(
            String targetKind,
            String targetId,
            ObjectNode prediction,
            double anomalyScore,
            double confidence
    ) {
    }
}
