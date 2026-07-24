package com.dongjin.topology;

import com.dongjin.training.PythonComputeGateway;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class GridRuntimeController {

    private final PythonComputeGateway pythonComputeGateway;

    public GridRuntimeController(PythonComputeGateway pythonComputeGateway) {
        this.pythonComputeGateway = pythonComputeGateway;
    }

    @PostMapping("/simulation/start")
    public JsonNode startSimulation(
            @RequestBody(required = false) Map<String, Object> request
    ) {
        return pythonComputeGateway.startSimulation(
                request == null ? Map.of() : request
        );
    }

    @PostMapping("/simulation/pause")
    public JsonNode pauseSimulation() {
        return pythonComputeGateway.pauseSimulation();
    }

    @PostMapping("/simulation/resume")
    public JsonNode resumeSimulation() {
        return pythonComputeGateway.resumeSimulation();
    }

    @PostMapping("/simulation/stop")
    public JsonNode stopSimulation() {
        return pythonComputeGateway.stopSimulation();
    }

    @GetMapping("/simulation/status")
    public JsonNode simulationStatus() {
        return pythonComputeGateway.simulationStatus();
    }

    @GetMapping("/simulation/profiles")
    public JsonNode simulationProfiles() {
        return pythonComputeGateway.simulationProfiles();
    }

    @GetMapping("/snapshots/current")
    public JsonNode currentSnapshot() {
        return pythonComputeGateway.currentSnapshot();
    }
}
