package com.dongjin.training;

import java.util.List;

public record StartTrainingRequest(
        String datasetName,
        List<String> sampleIds
) {
}
