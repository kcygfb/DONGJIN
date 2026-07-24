package com.dongjin.topology;

public record TopologyGenerationRequest(
        String simbenchCode,
        String topologyVersion,
        Boolean force
) {
    public static TopologyGenerationRequest defaults() {
        return new TopologyGenerationRequest(null, "v1", false);
    }

    public TopologyGenerationRequest normalized() {
        return new TopologyGenerationRequest(
                simbenchCode,
                topologyVersion == null || topologyVersion.isBlank()
                        ? "v1"
                        : topologyVersion,
                Boolean.TRUE.equals(force)
        );
    }
}
