package com.dongjin.training;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.Instant;

public record TrainingJobView(
        String id,
        String datasetName,
        int sampleCount,
        String status,
        int progress,
        Instant createdAt,
        Instant startedAt,
        Instant finishedAt,
        JsonNode result,
        String error
) {
}
