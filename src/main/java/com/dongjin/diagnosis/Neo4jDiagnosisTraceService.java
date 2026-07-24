package com.dongjin.diagnosis;

import com.dongjin.topology.TopologyData;
import com.dongjin.topology.TopologyRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Service;

@Service
public class Neo4jDiagnosisTraceService {

    private final TopologyRepository topologyRepository;
    private final ObjectMapper objectMapper;

    public Neo4jDiagnosisTraceService(
            TopologyRepository topologyRepository,
            ObjectMapper objectMapper
    ) {
        this.topologyRepository = topologyRepository;
        this.objectMapper = objectMapper;
    }

    public JsonNode enrich(JsonNode diagnosis) {
        String targetId = diagnosis.path("targetBusinessId").asText("");
        if (targetId.isBlank()) {
            return diagnosis;
        }
        List<TopologyData.Node> nodes = topologyRepository.findNodes();
        List<TopologyData.Edge> edges = topologyRepository.findEdges();
        Map<String, TopologyData.Node> nodeById = new HashMap<>();
        nodes.forEach(node -> nodeById.put(node.id(), node));
        if (!nodeById.containsKey(targetId)) {
            throw new IllegalStateException("模型定位对象不属于Neo4j当前活动P1拓扑：" + targetId);
        }
        ObjectNode result = diagnosis.deepCopy();
        ObjectNode trace = objectMapper.createObjectNode();
        trace.put("source", "Neo4j active P1 topology");
        trace.put("targetBusinessId", targetId);
        trace.set("upstream", traverse(targetId, nodeById, edges, true));
        trace.set("downstream", traverse(targetId, nodeById, edges, false));
        result.set("neo4jTrace", trace);
        result.put("neo4jTopologyVerified", true);
        return result;
    }

    private ArrayNode traverse(
            String targetId,
            Map<String, TopologyData.Node> nodeById,
            List<TopologyData.Edge> edges,
            boolean reverse
    ) {
        Map<String, List<TopologyData.Edge>> adjacency = new HashMap<>();
        for (TopologyData.Edge edge : edges) {
            String key = reverse ? edge.target() : edge.source();
            adjacency.computeIfAbsent(key, ignored -> new ArrayList<>()).add(edge);
        }
        ArrayNode output = objectMapper.createArrayNode();
        ArrayDeque<Step> queue = new ArrayDeque<>();
        queue.add(new Step(targetId, 0));
        Set<String> visited = new HashSet<>();
        visited.add(targetId);
        while (!queue.isEmpty()) {
            Step current = queue.removeFirst();
            if (current.depth() >= 4) {
                continue;
            }
            for (TopologyData.Edge edge : adjacency.getOrDefault(current.nodeId(), List.of())) {
                String nextId = reverse ? edge.source() : edge.target();
                if (!visited.add(nextId)) {
                    continue;
                }
                TopologyData.Node node = nodeById.get(nextId);
                if (node == null) {
                    continue;
                }
                ObjectNode item = objectMapper.createObjectNode();
                item.put("businessId", node.id());
                item.put("name", node.name());
                item.put("elementType", node.type());
                item.put("depth", current.depth() + 1);
                item.put("viaRelationshipId", edge.id());
                item.put("viaRelationshipType", edge.relationType());
                output.add(item);
                queue.addLast(new Step(nextId, current.depth() + 1));
            }
        }
        return output;
    }

    private record Step(String nodeId, int depth) {
    }
}
