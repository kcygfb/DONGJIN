package com.dongjin.diagnosis;

import com.dongjin.training.PythonComputeGateway;
import com.fasterxml.jackson.databind.JsonNode;
import java.util.Map;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class OnlineDiagnosisController {

    private final PythonComputeGateway pythonComputeGateway;
    private final Neo4jDiagnosisTraceService traceService;

    public OnlineDiagnosisController(
            PythonComputeGateway pythonComputeGateway,
            Neo4jDiagnosisTraceService traceService
    ) {
        this.pythonComputeGateway = pythonComputeGateway;
        this.traceService = traceService;
    }

    @GetMapping("/inference/models")
    public JsonNode inferenceModels() {
        return pythonComputeGateway.inferenceModels();
    }

    @GetMapping("/inference/model")
    public JsonNode inferenceModel() {
        return pythonComputeGateway.inferenceModel();
    }

    @GetMapping("/inference/model/history")
    public JsonNode inferenceModelHistory() {
        return pythonComputeGateway.inferenceModelHistory();
    }

    @PostMapping("/inference/model/check/{modelId}")
    public JsonNode checkInferenceModel(@PathVariable String modelId) {
        return pythonComputeGateway.checkInferenceModel(modelId);
    }

    @PostMapping("/inference/model/select")
    public JsonNode selectInferenceModel(@RequestBody Map<String, Object> request) {
        return pythonComputeGateway.selectInferenceModel(request);
    }

    @PostMapping("/inference/model/reload")
    public JsonNode reloadInferenceModel() {
        return pythonComputeGateway.reloadInferenceModel();
    }

    @PostMapping("/inference/model/rollback")
    public JsonNode rollbackInferenceModel() {
        return pythonComputeGateway.rollbackInferenceModel();
    }

    @PostMapping("/diagnosis/current")
    public JsonNode diagnoseCurrent() {
        return traceService.enrich(pythonComputeGateway.diagnoseCurrentSnapshot());
    }

    @PostMapping("/diagnosis/snapshots/{snapshotId}")
    public JsonNode diagnoseSnapshot(@PathVariable String snapshotId) {
        return traceService.enrich(pythonComputeGateway.diagnoseSnapshot(snapshotId));
    }

    @GetMapping("/diagnosis/{diagnosisId}")
    public JsonNode diagnosisResult(@PathVariable String diagnosisId) {
        return pythonComputeGateway.diagnosisResult(diagnosisId);
    }

    @PostMapping("/diagnosis/monitor/start")
    public JsonNode startDiagnosisMonitor(
            @RequestBody(required = false) Map<String, Object> request
    ) {
        return pythonComputeGateway.startDiagnosisMonitor(
                request == null ? Map.of() : request
        );
    }

    @GetMapping("/diagnosis/monitor/status")
    public JsonNode diagnosisMonitorStatus() {
        return pythonComputeGateway.diagnosisMonitorStatus();
    }

    @PostMapping("/diagnosis/monitor/stop")
    public JsonNode stopDiagnosisMonitor() {
        return pythonComputeGateway.stopDiagnosisMonitor();
    }

    @GetMapping("/shadow-sessions")
    public JsonNode shadowSessions() {
        return pythonComputeGateway.shadowSessions();
    }

    @PostMapping("/shadow-sessions")
    public JsonNode createShadowSession(@RequestBody Map<String, Object> request) {
        return pythonComputeGateway.createShadowSession(request);
    }

    @GetMapping("/shadow-sessions/{sessionId}")
    public JsonNode shadowSession(@PathVariable String sessionId) {
        return pythonComputeGateway.shadowSession(sessionId);
    }

    @PostMapping("/shadow-sessions/{sessionId}/diagnose")
    public JsonNode diagnoseShadowSession(@PathVariable String sessionId) {
        return traceService.enrich(pythonComputeGateway.diagnoseShadowSession(sessionId));
    }

    @PostMapping("/shadow-sessions/{sessionId}/reveal")
    public JsonNode revealShadowSession(@PathVariable String sessionId) {
        return pythonComputeGateway.revealShadowSession(sessionId);
    }

    @DeleteMapping("/shadow-sessions/{sessionId}")
    public JsonNode closeShadowSession(@PathVariable String sessionId) {
        return pythonComputeGateway.closeShadowSession(sessionId);
    }

    @PostMapping("/short-circuit-analyses")
    public JsonNode shortCircuitAnalysis(@RequestBody Map<String, Object> request) {
        return pythonComputeGateway.runShortCircuitAnalysis(request);
    }

    @GetMapping("/short-circuit-analyses/{analysisId}")
    public JsonNode shortCircuitAnalysis(@PathVariable String analysisId) {
        return pythonComputeGateway.shortCircuitAnalysis(analysisId);
    }
}
