package com.dongjin.training;

import com.dongjin.topology.TopologyData;
import com.dongjin.topology.TopologyRepository;
import java.time.Instant;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.UUID;
import java.util.stream.Collectors;
import org.springframework.stereotype.Service;

@Service
public class FaultGenerationService {

    private static final String NORMAL_LABEL = "NORMAL";
    private static final int DEFAULT_COUNT = 500;
    private static final int MAX_COUNT = 5_000;
    private static final List<FaultType> DEFAULT_TYPES = List.of(
            FaultType.DEVICE_OFFLINE,
            FaultType.VOLTAGE_ANOMALY,
            FaultType.LINE_OVERLOAD,
            FaultType.LINE_DISCONNECTED
    );

    private final TopologyRepository topologyRepository;
    private final FaultSampleStore sampleStore;

    public FaultGenerationService(TopologyRepository topologyRepository, FaultSampleStore sampleStore) {
        this.topologyRepository = topologyRepository;
        this.sampleStore = sampleStore;
    }

    public FaultGenerationResult generate(GenerateFaultRequest request) {
        int count = request == null || request.count() == null ? DEFAULT_COUNT : request.count();
        if (count < 4 || count > MAX_COUNT) {
            throw new IllegalArgumentException("生成数量必须在 4 到 " + MAX_COUNT + " 之间");
        }

        long seed = request == null || request.seed() == null ? System.currentTimeMillis() : request.seed();
        List<FaultType> requestedTypes = resolveTypes(request == null ? null : request.faultTypes());
        List<TopologyData.Node> nodes = topologyRepository.findNodes();
        List<TopologyData.Edge> edges = topologyRepository.findEdges();
        if (nodes.isEmpty()) {
            throw new IllegalStateException("Neo4j 中没有可用于生成故障的 Device 节点");
        }

        List<FaultType> availableTypes = requestedTypes.stream()
                .filter(type -> !type.edgeTarget() || !edges.isEmpty())
                .toList();
        if (availableTypes.size() < 2) {
            throw new IllegalStateException("至少需要两类具有可用拓扑目标的故障才能训练模型");
        }

        String batchId = "batch-" + UUID.randomUUID();
        Instant generatedAt = Instant.now();
        Random random = new Random(seed);
        Map<String, Integer> degrees = calculateDegrees(nodes, edges);
        Map<String, List<String>> neighbours = calculateNeighbours(nodes, edges);
        List<FaultSample> samples = new ArrayList<>(count);

        int faultIndex = 0;
        for (int index = 0; index < count; index++) {
            long sampleSeed = seed + index;
            if (index % 5 == 0) {
                boolean edgeTarget = !edges.isEmpty() && (index / 5) % 2 == 1;
                samples.add(edgeTarget
                        ? createNormalEdgeSample(batchId, index, sampleSeed, generatedAt, edges, random)
                        : createNormalNodeSample(batchId, index, sampleSeed, generatedAt, nodes, degrees, random));
            } else {
                FaultType type = availableTypes.get(faultIndex++ % availableTypes.size());
                double severity = round(0.35 + random.nextDouble() * 0.65);
                samples.add(type.edgeTarget()
                        ? createEdgeSample(batchId, index, sampleSeed, generatedAt, type, severity, edges, random)
                        : createNodeSample(
                                batchId, index, sampleSeed, generatedAt, type, severity,
                                nodes, degrees, neighbours, random
                        ));
            }
        }

        sampleStore.addAll(samples);
        Map<String, Long> distribution = samples.stream().collect(Collectors.groupingBy(
                FaultSample::faultType,
                LinkedHashMap::new,
                Collectors.counting()
        ));
        return new FaultGenerationResult(batchId, seed, samples.size(), distribution, List.copyOf(samples));
    }

    private FaultSample createNodeSample(
            String batchId,
            int index,
            long sampleSeed,
            Instant generatedAt,
            FaultType type,
            double severity,
            List<TopologyData.Node> nodes,
            Map<String, Integer> degrees,
            Map<String, List<String>> neighbours,
            Random random
    ) {
        TopologyData.Node target = nodes.get(random.nextInt(nodes.size()));
        Map<String, Double> measuredFeatures = switch (type) {
            case DEVICE_OFFLINE -> deviceOfflineFeatures(severity, degrees.getOrDefault(target.id(), 0), random);
            case VOLTAGE_ANOMALY -> voltageAnomalyFeatures(severity, degrees.getOrDefault(target.id(), 0), random);
            default -> throw new IllegalArgumentException("故障类型不是设备故障：" + type);
        };
        Map<String, Double> features = withTargetKind(measuredFeatures, false);

        var affected = new LinkedHashSet<String>();
        affected.add(target.id());
        affected.addAll(neighbours.getOrDefault(target.id(), List.of()));
        return new FaultSample(
                sampleId(sampleSeed, index), batchId, type.name(), type.displayName(), target.id(), target.name(),
                "NODE", severity, generatedAt, sampleSeed, features, List.copyOf(affected)
        );
    }

    private FaultSample createEdgeSample(
            String batchId,
            int index,
            long sampleSeed,
            Instant generatedAt,
            FaultType type,
            double severity,
            List<TopologyData.Edge> edges,
            Random random
    ) {
        TopologyData.Edge target = edges.get(random.nextInt(edges.size()));
        Map<String, Double> measuredFeatures = switch (type) {
            case LINE_OVERLOAD -> lineOverloadFeatures(severity, random);
            case LINE_DISCONNECTED -> lineDisconnectedFeatures(severity, random);
            default -> throw new IllegalArgumentException("故障类型不是线路故障：" + type);
        };
        Map<String, Double> features = withTargetKind(measuredFeatures, true);

        return new FaultSample(
                sampleId(sampleSeed, index), batchId, type.name(), type.displayName(), target.id(), target.name(),
                "EDGE", severity, generatedAt, sampleSeed, features, List.of(target.source(), target.target())
        );
    }

    private FaultSample createNormalNodeSample(
            String batchId,
            int index,
            long sampleSeed,
            Instant generatedAt,
            List<TopologyData.Node> nodes,
            Map<String, Integer> degrees,
            Random random
    ) {
        TopologyData.Node target = nodes.get(random.nextInt(nodes.size()));
        Map<String, Double> features = withTargetKind(
                normalFeatures(degrees.getOrDefault(target.id(), 0), random),
                false
        );
        return new FaultSample(
                sampleId(sampleSeed, index), batchId, NORMAL_LABEL, "正常运行", target.id(), target.name(),
                "NODE", 0, generatedAt, sampleSeed, features, List.of(target.id())
        );
    }

    private FaultSample createNormalEdgeSample(
            String batchId,
            int index,
            long sampleSeed,
            Instant generatedAt,
            List<TopologyData.Edge> edges,
            Random random
    ) {
        TopologyData.Edge target = edges.get(random.nextInt(edges.size()));
        Map<String, Double> features = withTargetKind(normalFeatures(2, random), true);
        return new FaultSample(
                sampleId(sampleSeed, index), batchId, NORMAL_LABEL, "正常运行", target.id(), target.name(),
                "EDGE", 0, generatedAt, sampleSeed, features, List.of(target.source(), target.target())
        );
    }

    private Map<String, Double> normalFeatures(int degree, Random random) {
        return features(
                1.0 + jitter(random, 0.025),
                0.55 + jitter(random, 0.2),
                0.48 + jitter(random, 0.18),
                0.14 + jitter(random, 0.08),
                39 + jitter(random, 8),
                0.98 + jitter(random, 0.02),
                random.nextDouble() < 0.85 ? 0 : 1,
                degree
        );
    }

    private Map<String, Double> deviceOfflineFeatures(double severity, int degree, Random random) {
        return features(
                0.02 + jitter(random, 0.03),
                0.01 + jitter(random, 0.02),
                jitter(random, 0.02),
                jitter(random, 0.01),
                32 + jitter(random, 8),
                Math.max(0, 0.25 - severity * 0.2 + jitter(random, 0.05)),
                2 + Math.round(severity * 5),
                degree
        );
    }

    private Map<String, Double> voltageAnomalyFeatures(double severity, int degree, Random random) {
        boolean lowVoltage = random.nextBoolean();
        double voltage = lowVoltage ? 0.9 - severity * 0.3 : 1.1 + severity * 0.28;
        return features(
                voltage + jitter(random, 0.025),
                0.55 + severity * 0.35 + jitter(random, 0.08),
                0.48 + severity * 0.25 + jitter(random, 0.06),
                0.18 + severity * 0.2 + jitter(random, 0.04),
                45 + severity * 18 + jitter(random, 4),
                0.92 - severity * 0.08 + jitter(random, 0.03),
                1 + Math.round(severity * 3),
                degree
        );
    }

    private Map<String, Double> lineOverloadFeatures(double severity, Random random) {
        return features(
                0.98 - severity * 0.12 + jitter(random, 0.02),
                1.0 + severity * 0.75 + jitter(random, 0.08),
                0.9 + severity * 0.65 + jitter(random, 0.08),
                0.25 + severity * 0.28 + jitter(random, 0.05),
                55 + severity * 48 + jitter(random, 5),
                0.95 - severity * 0.08 + jitter(random, 0.02),
                1 + Math.round(severity * 4),
                2
        );
    }

    private Map<String, Double> lineDisconnectedFeatures(double severity, Random random) {
        return features(
                0.45 - severity * 0.3 + jitter(random, 0.04),
                jitter(random, 0.025),
                jitter(random, 0.02),
                jitter(random, 0.02),
                34 + jitter(random, 7),
                Math.max(0, 0.35 - severity * 0.28 + jitter(random, 0.04)),
                3 + Math.round(severity * 5),
                2
        );
    }

    private Map<String, Double> features(
            double voltagePu,
            double currentPu,
            double activePowerPu,
            double reactivePowerPu,
            double temperatureC,
            double connectivityRatio,
            double alarmCount,
            double topologyDegree
    ) {
        Map<String, Double> features = new LinkedHashMap<>();
        features.put("voltagePu", round(Math.max(0, voltagePu)));
        features.put("currentPu", round(Math.max(0, currentPu)));
        features.put("activePowerPu", round(Math.max(0, activePowerPu)));
        features.put("reactivePowerPu", round(Math.max(0, reactivePowerPu)));
        features.put("temperatureC", round(Math.max(0, temperatureC)));
        features.put("connectivityRatio", round(clamp(connectivityRatio, 0, 1)));
        features.put("alarmCount", Math.max(0, alarmCount));
        features.put("topologyDegree", Math.max(0, topologyDegree));
        return Map.copyOf(features);
    }

    private Map<String, Double> withTargetKind(Map<String, Double> measuredFeatures, boolean edgeTarget) {
        Map<String, Double> features = new LinkedHashMap<>(measuredFeatures);
        features.put("targetIsEdge", edgeTarget ? 1.0 : 0.0);
        return Map.copyOf(features);
    }

    private List<FaultType> resolveTypes(List<String> values) {
        if (values == null || values.isEmpty()) {
            return DEFAULT_TYPES;
        }
        return values.stream().map(FaultType::from).distinct().toList();
    }

    private Map<String, Integer> calculateDegrees(List<TopologyData.Node> nodes, List<TopologyData.Edge> edges) {
        Map<String, Integer> degrees = new HashMap<>();
        nodes.forEach(node -> degrees.put(node.id(), 0));
        edges.forEach(edge -> {
            degrees.computeIfPresent(edge.source(), (id, degree) -> degree + 1);
            degrees.computeIfPresent(edge.target(), (id, degree) -> degree + 1);
        });
        return degrees;
    }

    private Map<String, List<String>> calculateNeighbours(List<TopologyData.Node> nodes, List<TopologyData.Edge> edges) {
        Map<String, List<String>> neighbours = new HashMap<>();
        nodes.forEach(node -> neighbours.put(node.id(), new ArrayList<>()));
        edges.forEach(edge -> {
            neighbours.computeIfPresent(edge.source(), (id, values) -> append(values, edge.target()));
            neighbours.computeIfPresent(edge.target(), (id, values) -> append(values, edge.source()));
        });
        return neighbours;
    }

    private List<String> append(List<String> values, String value) {
        values.add(value);
        return values;
    }

    private String sampleId(long sampleSeed, int index) {
        return "fault-" + Long.toUnsignedString(sampleSeed) + "-" + String.format("%04d", index);
    }

    private double jitter(Random random, double amplitude) {
        return (random.nextDouble() * 2 - 1) * amplitude;
    }

    private double clamp(double value, double minimum, double maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }

    private double round(double value) {
        return Math.round(value * 10_000.0) / 10_000.0;
    }
}
