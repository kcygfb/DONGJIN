package com.dongjin.topology;

import com.dongjin.training.PythonComputeGateway;
import org.springframework.stereotype.Service;

@Service
public class GridTopologyGenerationService {

    private final PythonComputeGateway pythonComputeGateway;

    public GridTopologyGenerationService(PythonComputeGateway pythonComputeGateway) {
        this.pythonComputeGateway = pythonComputeGateway;
    }

    public TopologyGenerationResult generate(TopologyGenerationRequest request) {
        TopologyGenerationRequest normalizedRequest = (
                request == null ? TopologyGenerationRequest.defaults() : request.normalized()
        );
        TopologyGenerationResult initialized = pythonComputeGateway.initializeGrid(
                normalizedRequest
        );
        return initialized.withNeo4jProjection(
                pythonComputeGateway.publishGridToNeo4j(initialized.gridId())
        );
    }

    public TopologyGenerationResult activeSource() {
        return pythonComputeGateway.activeGrid();
    }
}
