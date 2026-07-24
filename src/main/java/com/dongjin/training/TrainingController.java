package com.dongjin.training;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.Instant;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/training")
public class TrainingController {

    private final PythonComputeGateway trainingGateway;

    public TrainingController(PythonComputeGateway trainingGateway) {
        this.trainingGateway = trainingGateway;
    }

    @PostMapping("/offline/scenario-batches")
    public JsonNode generateOfflineScenarioBatch(@RequestBody JsonNode request) {
        return trainingGateway.generateOfflineScenarioBatch(request);
    }

    @GetMapping("/offline/scenario-batches")
    public JsonNode offlineScenarioBatches() {
        return trainingGateway.offlineScenarioBatches();
    }

    @GetMapping("/offline/scenario-batches/{batchId}")
    public JsonNode offlineScenarioBatch(@PathVariable String batchId) {
        return trainingGateway.offlineScenarioBatch(batchId);
    }

    @PostMapping("/offline/datasets")
    public JsonNode buildOfflineDataset(@RequestBody JsonNode request) {
        return trainingGateway.buildOfflineDataset(request);
    }

    @GetMapping("/offline/datasets")
    public JsonNode offlineDatasets() {
        return trainingGateway.offlineDatasets();
    }

    @GetMapping("/offline/datasets/{datasetId}")
    public JsonNode offlineDataset(@PathVariable String datasetId) {
        return trainingGateway.offlineDataset(datasetId);
    }

    @GetMapping("/offline/datasets/{datasetId}/preview")
    public JsonNode offlineDatasetPreview(
            @PathVariable String datasetId,
            @RequestParam(defaultValue = "100") int limit
    ) {
        if (limit < 1 || limit > 1000) {
            throw new IllegalArgumentException("limit必须在1到1000之间");
        }
        return trainingGateway.offlineDatasetPreview(datasetId, limit);
    }

    @PostMapping("/offline/models")
    public JsonNode runOfflineTraining(@RequestBody JsonNode request) {
        return trainingGateway.runOfflineTraining(request);
    }

    @GetMapping("/offline/models")
    public JsonNode offlineModels() {
        return trainingGateway.offlineModels();
    }

    @GetMapping("/offline/models/{modelId}")
    public JsonNode offlineModel(@PathVariable String modelId) {
        return trainingGateway.offlineModel(modelId);
    }

    @GetMapping("/models/active")
    public JsonNode activeModel() {
        return trainingGateway.activeModel();
    }

    @PostMapping("/reset")
    public TrainingResetResult resetTraining() {
        JsonNode pythonResult = trainingGateway.reset();
        return new TrainingResetResult("RESET", Instant.now(), pythonResult);
    }
}
