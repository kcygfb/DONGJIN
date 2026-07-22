package com.dongjin.diagnosis;

import java.util.List;
import java.util.Map;

public record BlindDiagnosisRequest(
        List<Observation> observations,
        Integer topK,
        Integer traceDepth
) {
    public record Observation(
            String targetKind,
            String targetId,
            Map<String, Double> features
    ) {
    }
}
