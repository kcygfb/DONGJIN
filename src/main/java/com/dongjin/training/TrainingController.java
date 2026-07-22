package com.dongjin.training;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.Instant;
import java.util.List;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/training")
public class TrainingController {

    private final FaultGenerationService faultGenerationService;
    private final FaultSampleStore sampleStore;
    private final TrainingJobService trainingJobService;
    private final PythonTrainingGateway trainingGateway;

    public TrainingController(
            FaultGenerationService faultGenerationService,
            FaultSampleStore sampleStore,
            TrainingJobService trainingJobService,
            PythonTrainingGateway trainingGateway
    ) {
        this.faultGenerationService = faultGenerationService;
        this.sampleStore = sampleStore;
        this.trainingJobService = trainingJobService;
        this.trainingGateway = trainingGateway;
    }

    @PostMapping("/errors/generate")
    public FaultGenerationResult generateErrors(@RequestBody(required = false) GenerateFaultRequest request) {
        return faultGenerationService.generate(request);
    }

    @GetMapping("/errors")
    public List<FaultSample> getErrors() {
        return sampleStore.findAll();
    }

    @DeleteMapping("/errors")
    public void clearErrors() {
        sampleStore.clear();
    }

    @PostMapping("/jobs")
    public TrainingJobView startTraining(@RequestBody(required = false) StartTrainingRequest request) {
        return trainingJobService.start(request);
    }

    @GetMapping("/jobs/{id}")
    public TrainingJobView getTrainingJob(@PathVariable String id) {
        return trainingJobService.find(id);
    }

    @GetMapping("/models/active")
    public JsonNode activeModel() {
        return trainingGateway.activeModel();
    }

    @PostMapping("/reset")
    public TrainingResetResult resetTraining() {
        if (trainingJobService.hasActiveJobs()) {
            throw new IllegalStateException("训练任务正在运行，完成后才能重置训练");
        }
        JsonNode pythonResult = trainingGateway.reset();
        sampleStore.clear();
        trainingJobService.clearFinishedJobs();
        return new TrainingResetResult("RESET", Instant.now(), pythonResult);
    }
}
