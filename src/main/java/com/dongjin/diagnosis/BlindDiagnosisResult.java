package com.dongjin.diagnosis;

import com.dongjin.diagnosis.DiagnosisResult.TargetView;
import com.dongjin.diagnosis.DiagnosisResult.TraceView;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.List;

public record BlindDiagnosisResult(
        TargetView target,
        JsonNode prediction,
        TraceView trace,
        int observationCount,
        List<LocationCandidate> locationCandidates
) {
    public record LocationCandidate(
            String targetKind,
            String targetId,
            String targetName,
            String predictedFaultType,
            double anomalyScore,
            double confidence
    ) {
    }
}
