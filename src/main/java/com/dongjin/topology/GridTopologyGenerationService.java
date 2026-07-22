package com.dongjin.topology;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Random;
import org.springframework.stereotype.Service;

@Service
public class GridTopologyGenerationService {

    static final String GENERATOR_NAME = "dongjin-layered-grid-v1";
    private static final int DEFAULT_REGIONS = 3;
    private static final int DEFAULT_SUBSTATIONS_PER_REGION = 3;
    private static final int DEFAULT_FEEDERS_PER_SUBSTATION = 4;
    private static final int DEFAULT_LOADS_PER_FEEDER = 3;
    private static final int MAX_GENERATED_NODES = 5_000;

    private final TopologyRepository topologyRepository;

    public GridTopologyGenerationService(TopologyRepository topologyRepository) {
        this.topologyRepository = topologyRepository;
    }

    public TopologyGenerationResult generate(TopologyGenerationRequest request) {
        int regions = valueOrDefault(request == null ? null : request.regions(), DEFAULT_REGIONS);
        int substationsPerRegion = valueOrDefault(
                request == null ? null : request.substationsPerRegion(),
                DEFAULT_SUBSTATIONS_PER_REGION
        );
        int feedersPerSubstation = valueOrDefault(
                request == null ? null : request.feedersPerSubstation(),
                DEFAULT_FEEDERS_PER_SUBSTATION
        );
        int loadsPerFeeder = valueOrDefault(
                request == null ? null : request.loadsPerFeeder(),
                DEFAULT_LOADS_PER_FEEDER
        );
        long seed = request == null || request.seed() == null ? 20_260_717L : request.seed();
        boolean replaceGenerated = request == null
                || request.replaceGenerated() == null
                || request.replaceGenerated();

        validateRange("区域数量", regions, 1, 8);
        validateRange("每区域变电站数量", substationsPerRegion, 1, 10);
        validateRange("每变电站馈线数量", feedersPerSubstation, 1, 12);
        validateRange("每馈线负荷数量", loadsPerFeeder, 1, 20);

        int substationCount = regions * substationsPerRegion;
        int transformerCount = substationCount * 2;
        int feederCount = substationCount * feedersPerSubstation;
        int loadCount = feederCount * loadsPerFeeder;
        int expectedNodeCount = 1 + regions + substationCount + transformerCount + transformerCount
                + feederCount + loadCount;
        if (expectedNodeCount > MAX_GENERATED_NODES) {
            throw new IllegalArgumentException("生成规模过大，设备总数不能超过 " + MAX_GENERATED_NODES);
        }

        Random random = new Random(seed);
        List<Map<String, Object>> nodes = new ArrayList<>(expectedNodeCount);
        List<Map<String, Object>> edges = new ArrayList<>();
        addNode(nodes, "GRID-SOURCE-001", "华东500kV枢纽站", "substation", "500kV", "central", 3000);

        List<String> regionIds = new ArrayList<>();
        List<List<String>> regionSubstationIds = new ArrayList<>();
        List<String> allBusIds = new ArrayList<>();

        for (int regionIndex = 1; regionIndex <= regions; regionIndex++) {
            String regionCode = twoDigits(regionIndex);
            String regionId = "GRID-R" + regionCode + "-220";
            regionIds.add(regionId);
            addNode(
                    nodes, regionId, "区域" + regionIndex + "号220kV变电站", "substation", "220kV",
                    "region-" + regionCode, 1200 + random.nextInt(401)
            );
            addEdge(
                    edges, "LINK-SOURCE-R" + regionCode, "GRID-SOURCE-001", regionId,
                    "500kV主干线" + regionIndex, "transmission", "500kV", 0.01 + random.nextDouble() * 0.01
            );

            List<String> substationIds = new ArrayList<>();
            regionSubstationIds.add(substationIds);
            for (int substationIndex = 1; substationIndex <= substationsPerRegion; substationIndex++) {
                String subCode = regionCode + "-S" + twoDigits(substationIndex);
                String substationId = "GRID-" + subCode + "-110";
                substationIds.add(substationId);
                addNode(
                        nodes, substationId,
                        "区域" + regionIndex + "-" + substationIndex + "号110kV站",
                        "substation", "110kV", "region-" + regionCode, 500 + random.nextInt(301)
                );
                addEdge(
                        edges, "LINK-R" + regionCode + "-S" + twoDigits(substationIndex), regionId, substationId,
                        "220/110kV线路", "transmission", "220kV", 0.015 + random.nextDouble() * 0.02
                );

                List<String> substationBusIds = new ArrayList<>();
                for (int transformerIndex = 1; transformerIndex <= 2; transformerIndex++) {
                    String deviceCode = subCode + "-T" + transformerIndex;
                    String transformerId = "GRID-" + deviceCode;
                    String busId = "GRID-" + subCode + "-B" + transformerIndex;
                    substationBusIds.add(busId);
                    allBusIds.add(busId);
                    addNode(
                            nodes, transformerId, subCode + "主变" + transformerIndex, "transformer", "110/10kV",
                            "region-" + regionCode, 50 + random.nextInt(51)
                    );
                    addNode(
                            nodes, busId, subCode + "-10kV母线" + transformerIndex, "bus", "10kV",
                            "region-" + regionCode, 0
                    );
                    addEdge(
                            edges, "LINK-" + deviceCode + "-IN", substationId, transformerId,
                            "主变进线", "transformer", "110kV", 0.008 + random.nextDouble() * 0.01
                    );
                    addEdge(
                            edges, "LINK-" + deviceCode + "-OUT", transformerId, busId,
                            "主变低压侧", "transformer", "10kV", 0.005 + random.nextDouble() * 0.008
                    );
                }
                addEdge(
                        edges, "TIE-" + subCode + "-BUS", substationBusIds.get(0), substationBusIds.get(1),
                        "母联开关", "bus-tie", "10kV", 0.001
                );

                for (int feederIndex = 1; feederIndex <= feedersPerSubstation; feederIndex++) {
                    String feederCode = subCode + "-F" + twoDigits(feederIndex);
                    String switchId = "GRID-" + feederCode + "-SW";
                    String busId = substationBusIds.get((feederIndex - 1) % substationBusIds.size());
                    addNode(
                            nodes, switchId, subCode + "馈线" + feederIndex + "开关", "switch", "10kV",
                            "region-" + regionCode, 0
                    );
                    addEdge(
                            edges, "LINK-" + feederCode + "-SW", busId, switchId,
                            "10kV馈线" + feederIndex, "feeder", "10kV", 0.01 + random.nextDouble() * 0.025
                    );

                    String previousId = switchId;
                    for (int loadIndex = 1; loadIndex <= loadsPerFeeder; loadIndex++) {
                        String loadId = "GRID-" + feederCode + "-L" + twoDigits(loadIndex);
                        addNode(
                                nodes, loadId, subCode + "馈线" + feederIndex + "负荷" + loadIndex,
                                "load", "10kV", "region-" + regionCode, 5 + random.nextInt(26)
                        );
                        addEdge(
                                edges, "LINK-" + feederCode + "-L" + twoDigits(loadIndex), previousId, loadId,
                                "配电线路" + loadIndex, "distribution", "10kV",
                                0.02 + random.nextDouble() * 0.04
                        );
                        previousId = loadId;
                    }
                }
            }
        }

        addRingEdges(edges, regionIds, "220kV区域环网", "220kV", random);
        for (int regionIndex = 0; regionIndex < regionSubstationIds.size(); regionIndex++) {
            addRingEdges(
                    edges,
                    regionSubstationIds.get(regionIndex),
                    "110kV区域联络线" + (regionIndex + 1),
                    "110kV",
                    random
            );
        }
        for (int index = 0; index + 2 < allBusIds.size(); index += 4) {
            addEdge(
                    edges, "TIE-BUS-" + twoDigits(index + 1), allBusIds.get(index), allBusIds.get(index + 2),
                    "10kV备用联络线", "backup-tie", "10kV", 0.015 + random.nextDouble() * 0.015
            );
        }

        topologyRepository.saveGeneratedTopology(GENERATOR_NAME, nodes, edges, replaceGenerated);
        return new TopologyGenerationResult(
                GENERATOR_NAME, seed, nodes.size(), edges.size(), regions, substationCount,
                transformerCount, feederCount, loadCount, replaceGenerated
        );
    }

    private void addRingEdges(
            List<Map<String, Object>> edges,
            List<String> nodeIds,
            String name,
            String voltageLevel,
            Random random
    ) {
        if (nodeIds.size() < 2) {
            return;
        }
        for (int index = 0; index < nodeIds.size(); index++) {
            int nextIndex = (index + 1) % nodeIds.size();
            if (nodeIds.size() == 2 && index == 1) {
                break;
            }
            addEdge(
                    edges, "RING-" + voltageLevel.replace("kV", "") + "-" + nodeIds.get(index),
                    nodeIds.get(index), nodeIds.get(nextIndex), name, "ring-tie", voltageLevel,
                    0.01 + random.nextDouble() * 0.02
            );
        }
    }

    private void addNode(
            List<Map<String, Object>> nodes,
            String id,
            String name,
            String type,
            String voltageLevel,
            String zone,
            int capacityMva
    ) {
        Map<String, Object> node = new LinkedHashMap<>();
        node.put("id", id);
        node.put("name", name);
        node.put("type", type);
        node.put("status", "normal");
        node.put("voltageLevel", voltageLevel);
        node.put("zone", zone);
        node.put("capacityMva", capacityMva);
        nodes.add(node);
    }

    private void addEdge(
            List<Map<String, Object>> edges,
            String id,
            String source,
            String target,
            String name,
            String linkType,
            String voltageLevel,
            double impedance
    ) {
        Map<String, Object> edge = new LinkedHashMap<>();
        edge.put("id", id);
        edge.put("source", source);
        edge.put("target", target);
        edge.put("name", name);
        edge.put("status", "normal");
        edge.put("linkType", linkType);
        edge.put("voltageLevel", voltageLevel);
        edge.put("impedance", Math.round(impedance * 100_000.0) / 100_000.0);
        edges.add(edge);
    }

    private int valueOrDefault(Integer value, int defaultValue) {
        return value == null ? defaultValue : value;
    }

    private void validateRange(String name, int value, int minimum, int maximum) {
        if (value < minimum || value > maximum) {
            throw new IllegalArgumentException(name + "必须在 " + minimum + " 到 " + maximum + " 之间");
        }
    }

    private String twoDigits(int value) {
        return String.format("%02d", value);
    }
}
