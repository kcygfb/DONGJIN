package com.dongjin.training;

import java.util.List;
import java.util.Map;

public record FaultGenerationResult(
        String batchId,
        long seed,
        int count,
        Map<String, Long> distribution,
        List<FaultSample> samples
) {
}
