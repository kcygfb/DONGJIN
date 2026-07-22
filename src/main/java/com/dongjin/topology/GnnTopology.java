package com.dongjin.topology;

import java.util.List;

public record GnnTopology(
        List<Node> nodes,
        List<Edge> edges
) {
    public static GnnTopology from(List<TopologyData.Node> nodes, List<TopologyData.Edge> edges) {
        return new GnnTopology(
                nodes.stream().map(node -> new Node(node.id())).toList(),
                edges.stream().map(edge -> new Edge(edge.id(), edge.source(), edge.target())).toList()
        );
    }

    public record Node(String id) {
    }

    public record Edge(String id, String source, String target) {
    }
}
