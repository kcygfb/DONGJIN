package com.dongjin.topology;

import java.util.List;

public record TopologyData(
        List<Node> nodes,
        List<Edge> edges
) {
    public record Node(
            String id,
            String name,
            String type,
            String status,
            String voltageLevel
    ) {
    }

    public record Edge(
            String id,
            String source,
            String target,
            String name,
            String status,
            String relationType
    ) {
    }
}
