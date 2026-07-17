package com.dongjin.training;

import java.time.Instant;
import java.util.List;
import java.util.Map;

public record FaultSample(
        String id,
        String batchId,
        String faultType,
        String faultName,
        String targetId,
        String targetName,
        String targetKind,
        double severity,
        Instant generatedAt,
        long seed,
        Map<String, Double> features,
        List<String> affectedDeviceIds
) {
}
