package com.dongjin.topology;

public record TopologyGenerationResult(
        String generator,
        long seed,
        int nodeCount,
        int edgeCount,
        int regions,
        int substations,
        int transformers,
        int feeders,
        int loads,
        boolean replacedPreviousGeneration
) {
}
