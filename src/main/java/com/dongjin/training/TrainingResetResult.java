package com.dongjin.training;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.Instant;

public record TrainingResetResult(
        String status,
        Instant resetAt,
        JsonNode pythonService
) {
}
