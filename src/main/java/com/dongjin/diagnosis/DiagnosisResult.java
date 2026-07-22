package com.dongjin.diagnosis;

import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;

public record DiagnosisResult(
        TargetView target,
        JsonNode prediction,
        TraceView trace
) {
    public record TargetView(
            String kind,
            String id,
            String name,
            String type,
            String sourceId,
            String targetId
    ) {
    }

    public record TraceView(
            int maxDepth,
            List<TraceStep> upstream,
            List<TraceStep> downstream,
            List<String> nodeIds,
            List<String> edgeIds
    ) {
    }

    public record TraceStep(
            String nodeId,
            String nodeName,
            String nodeType,
            int depth,
            String connectedToId,
            String viaEdgeId,
            String viaEdgeName
    ) {
    }
}
