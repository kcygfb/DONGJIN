package com.dongjin.topology;

import java.util.Map;

public record TopologyGenerationResult(
        String status,
        boolean reused,
        String gridId,
        String simbenchCode,
        String topologyVersion,
        String schemaVersion,
        String artifactPath,
        Map<String, Integer> elementCounts,
        Map<String, Object> profileSummary,
        Map<String, Object> baseline,
        Map<String, Object> validation,
        Map<String, String> checksums,
        Map<String, Object> neo4jProjection
) {
    public TopologyGenerationResult withNeo4jProjection(Map<String, Object> projection) {
        return new TopologyGenerationResult(
                status,
                reused,
                gridId,
                simbenchCode,
                topologyVersion,
                schemaVersion,
                artifactPath,
                elementCounts,
                profileSummary,
                baseline,
                validation,
                checksums,
                projection
        );
    }
}
