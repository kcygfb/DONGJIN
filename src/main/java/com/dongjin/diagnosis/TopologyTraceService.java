package com.dongjin.diagnosis;

import com.dongjin.diagnosis.DiagnosisResult.TargetView;
import com.dongjin.diagnosis.DiagnosisResult.TraceStep;
import com.dongjin.diagnosis.DiagnosisResult.TraceView;
import com.dongjin.topology.TopologyData;
import com.dongjin.topology.TopologyRepository;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Service;

@Service
public class TopologyTraceService {

    private final TopologyRepository topologyRepository;

    public TopologyTraceService(TopologyRepository topologyRepository) {
        this.topologyRepository = topologyRepository;
    }

    public TraceOutcome trace(String requestedKind, String targetId, int maxDepth) {
        String kind = normalizeKind(requestedKind);
        List<TopologyData.Node> nodes = topologyRepository.findNodes();
        List<TopologyData.Edge> edges = topologyRepository.findEdges();
        Map<String, TopologyData.Node> nodeById = new HashMap<>();
        nodes.forEach(node -> nodeById.put(node.id(), node));

        Map<String, List<TopologyData.Edge>> incoming = new HashMap<>();
        Map<String, List<TopologyData.Edge>> outgoing = new HashMap<>();
        nodes.forEach(node -> {
            incoming.put(node.id(), new ArrayList<>());
            outgoing.put(node.id(), new ArrayList<>());
        });
        edges.forEach(edge -> {
            incoming.computeIfAbsent(edge.target(), ignored -> new ArrayList<>()).add(edge);
            outgoing.computeIfAbsent(edge.source(), ignored -> new ArrayList<>()).add(edge);
        });

        List<TraceStep> upstream;
        List<TraceStep> downstream;
        TargetView target;
        Set<String> tracedNodeIds = new LinkedHashSet<>();
        Set<String> tracedEdgeIds = new LinkedHashSet<>();

        if ("NODE".equals(kind)) {
            TopologyData.Node node = requireNode(nodeById, targetId);
            target = new TargetView("NODE", node.id(), node.name(), node.type(), null, null);
            tracedNodeIds.add(node.id());
            upstream = walk(node.id(), maxDepth, true, nodeById, incoming, tracedNodeIds, tracedEdgeIds);
            downstream = walk(node.id(), maxDepth, false, nodeById, outgoing, tracedNodeIds, tracedEdgeIds);
        } else {
            TopologyData.Edge edge = edges.stream()
                    .filter(candidate -> candidate.id().equals(targetId))
                    .findFirst()
                    .orElseThrow(() -> new IllegalArgumentException("当前拓扑中不存在线路：" + targetId));
            TopologyData.Node source = requireNode(nodeById, edge.source());
            TopologyData.Node destination = requireNode(nodeById, edge.target());
            target = new TargetView(
                    "EDGE", edge.id(), edge.name(), edge.relationType(), edge.source(), edge.target()
            );
            tracedNodeIds.add(source.id());
            tracedNodeIds.add(destination.id());
            tracedEdgeIds.add(edge.id());

            upstream = new ArrayList<>();
            upstream.add(endpoint(source));
            upstream.addAll(walk(
                    source.id(), maxDepth, true, nodeById, incoming, tracedNodeIds, tracedEdgeIds
            ));
            downstream = new ArrayList<>();
            downstream.add(endpoint(destination));
            downstream.addAll(walk(
                    destination.id(), maxDepth, false, nodeById, outgoing, tracedNodeIds, tracedEdgeIds
            ));
        }

        return new TraceOutcome(
                target,
                new TraceView(
                        maxDepth,
                        List.copyOf(upstream),
                        List.copyOf(downstream),
                        List.copyOf(tracedNodeIds),
                        List.copyOf(tracedEdgeIds)
                )
        );
    }

    private List<TraceStep> walk(
            String startId,
            int maxDepth,
            boolean reverse,
            Map<String, TopologyData.Node> nodeById,
            Map<String, List<TopologyData.Edge>> adjacency,
            Set<String> tracedNodeIds,
            Set<String> tracedEdgeIds
    ) {
        List<TraceStep> result = new ArrayList<>();
        Set<String> visited = new LinkedHashSet<>();
        ArrayDeque<QueueEntry> queue = new ArrayDeque<>();
        visited.add(startId);
        queue.add(new QueueEntry(startId, 0));

        while (!queue.isEmpty()) {
            QueueEntry current = queue.removeFirst();
            if (current.depth() >= maxDepth) {
                continue;
            }

            for (TopologyData.Edge edge : adjacency.getOrDefault(current.nodeId(), List.of())) {
                String nextId = reverse ? edge.source() : edge.target();
                TopologyData.Node nextNode = nodeById.get(nextId);
                if (nextNode == null || !visited.add(nextId)) {
                    continue;
                }

                int depth = current.depth() + 1;
                result.add(new TraceStep(
                        nextNode.id(), nextNode.name(), nextNode.type(), depth,
                        current.nodeId(), edge.id(), edge.name()
                ));
                tracedNodeIds.add(nextNode.id());
                tracedEdgeIds.add(edge.id());
                queue.addLast(new QueueEntry(nextNode.id(), depth));
            }
        }
        return result;
    }

    private TraceStep endpoint(TopologyData.Node node) {
        return new TraceStep(node.id(), node.name(), node.type(), 0, null, null, null);
    }

    private TopologyData.Node requireNode(Map<String, TopologyData.Node> nodeById, String id) {
        TopologyData.Node node = nodeById.get(id);
        if (node == null) {
            throw new IllegalArgumentException("当前拓扑中不存在设备：" + id);
        }
        return node;
    }

    private String normalizeKind(String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("必须指定研判对象类型 NODE 或 EDGE");
        }
        String kind = value.trim().toUpperCase(Locale.ROOT);
        if (!"NODE".equals(kind) && !"EDGE".equals(kind)) {
            throw new IllegalArgumentException("研判对象类型只支持 NODE 或 EDGE");
        }
        return kind;
    }

    public record TraceOutcome(TargetView target, TraceView trace) {
    }

    private record QueueEntry(String nodeId, int depth) {
    }
}
