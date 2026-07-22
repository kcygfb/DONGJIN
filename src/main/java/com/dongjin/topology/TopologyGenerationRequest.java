package com.dongjin.topology;

public record TopologyGenerationRequest(
        Integer regions,
        Integer substationsPerRegion,
        Integer feedersPerSubstation,
        Integer loadsPerFeeder,
        Long seed,
        Boolean replaceGenerated
) {
}
