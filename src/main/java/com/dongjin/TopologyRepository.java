package com.dongjin;

import java.util.List;
import org.neo4j.driver.Driver;
import org.neo4j.driver.Record;
import org.neo4j.driver.SessionConfig;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Repository;

@Repository
public class TopologyRepository {

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
