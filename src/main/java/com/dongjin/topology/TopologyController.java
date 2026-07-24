package com.dongjin.topology;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/topology")
public class TopologyController {

    private final TopologyRepository topologyRepository;
    private final GridTopologyGenerationService generationService;

    public TopologyController(
            TopologyRepository topologyRepository,
            GridTopologyGenerationService generationService
    ) {
        this.topologyRepository = topologyRepository;
        this.generationService = generationService;
    }

    @GetMapping
    public TopologyData getTopology() {
        return new TopologyData(
                topologyRepository.findNodes(),
                topologyRepository.findEdges()
        );
    }

    @PostMapping("/generate")
    public TopologyGenerationResult generateTopology(
            @RequestBody(required = false) TopologyGenerationRequest request
    ) {
        return generationService.generate(request);
    }

    @GetMapping("/source")
    public TopologyGenerationResult getActiveSource() {
        return generationService.activeSource();
    }
}
