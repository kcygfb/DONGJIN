package com.dongjin.topology;

import java.util.List;
import java.util.Map;
import org.neo4j.driver.Driver;
import org.neo4j.driver.Record;
import org.neo4j.driver.SessionConfig;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Repository;

@Repository
public class TopologyRepository {

    private static final String DELETE_GENERATED_QUERY = """
            MATCH (n:Device {generatedBy: $generator})
            DETACH DELETE n
            """;

    private static final String SAVE_GENERATED_NODES_QUERY = """
            UNWIND $nodes AS item
            MERGE (n:Device {id: item.id})
            SET n.name = item.name,
                n.type = item.type,
                n.status = item.status,
                n.voltageLevel = item.voltageLevel,
                n.zone = item.zone,
                n.capacityMva = item.capacityMva,
                n.generatedBy = $generator
            """;

    private static final String SAVE_GENERATED_EDGES_QUERY = """
            UNWIND $edges AS item
            MATCH (source:Device {id: item.source})
            MATCH (target:Device {id: item.target})
            MERGE (source)-[r:GRID_LINK {id: item.id}]->(target)
            SET r.name = item.name,
                r.status = item.status,
                r.linkType = item.linkType,
                r.voltageLevel = item.voltageLevel,
                r.impedance = item.impedance,
                r.generatedBy = $generator
            """;

    private static final String NODE_QUERY = """
            MATCH (n:Device)
            RETURN
              coalesce(n.id, elementId(n)) AS id,
              coalesce(n.name, n.id, elementId(n)) AS name,
              coalesce(n.type, "device") AS type,
              coalesce(n.status, "normal") AS status,
              coalesce(n.voltageLevel, "") AS voltageLevel
            ORDER BY id
            """;

    private static final String EDGE_QUERY = """
            MATCH (source:Device)-[r]->(target:Device)
            RETURN
              coalesce(r.id, elementId(r)) AS id,
              coalesce(source.id, elementId(source)) AS source,
              coalesce(target.id, elementId(target)) AS target,
              coalesce(r.name, type(r)) AS name,
              coalesce(r.status, "normal") AS status,
              type(r) AS relationType
            ORDER BY id
            """;

    private final Driver driver;
    private final String database;

    public TopologyRepository(Driver driver, @Value("${app.neo4j.database}") String database) {
        this.driver = driver;
        this.database = database;
    }

    public List<TopologyData.Node> findNodes() {
        try (var session = driver.session(SessionConfig.builder().withDatabase(database).build())) {
            return session.executeRead(transaction -> transaction.run(NODE_QUERY).list(this::toNode));
        }
    }

    public List<TopologyData.Edge> findEdges() {
        try (var session = driver.session(SessionConfig.builder().withDatabase(database).build())) {
            return session.executeRead(transaction -> transaction.run(EDGE_QUERY).list(this::toEdge));
        }
    }

    public void saveGeneratedTopology(
            String generator,
            List<Map<String, Object>> nodes,
            List<Map<String, Object>> edges,
            boolean replaceGenerated
    ) {
        try (var session = driver.session(SessionConfig.builder().withDatabase(database).build())) {
            session.executeWrite(transaction -> {
                if (replaceGenerated) {
                    transaction.run(DELETE_GENERATED_QUERY, Map.of("generator", generator)).consume();
                }
                transaction.run(
                        SAVE_GENERATED_NODES_QUERY,
                        Map.of("generator", generator, "nodes", nodes)
                ).consume();
                transaction.run(
                        SAVE_GENERATED_EDGES_QUERY,
                        Map.of("generator", generator, "edges", edges)
                ).consume();
                return null;
            });
        }
    }

    private TopologyData.Node toNode(Record record) {
        return new TopologyData.Node(
                record.get("id").asString(),
                record.get("name").asString(),
                record.get("type").asString(),
                record.get("status").asString(),
                record.get("voltageLevel").asString()
        );
    }

    private TopologyData.Edge toEdge(Record record) {
        return new TopologyData.Edge(
                record.get("id").asString(),
                record.get("source").asString(),
                record.get("target").asString(),
                record.get("name").asString(),
                record.get("status").asString(),
                record.get("relationType").asString()
        );
    }
}
