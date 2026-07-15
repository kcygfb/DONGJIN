package com.dongjin;

import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/topology")
public class TopologyController {

    private final TopologyRepository topologyRepository;

    public TopologyController(TopologyRepository topologyRepository) {
        this.topologyRepository = topologyRepository;
    }

    @GetMapping
    public TopologyData getTopology() {
        return new TopologyData(
                topologyRepository.findNodes(),
                topologyRepository.findEdges()
        );
    }
}
