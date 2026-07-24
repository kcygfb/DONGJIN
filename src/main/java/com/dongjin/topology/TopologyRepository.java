package com.dongjin.topology;

import java.util.List;
import org.neo4j.driver.Driver;
import org.neo4j.driver.Record;
import org.neo4j.driver.SessionConfig;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Repository;

@Repository
public class TopologyRepository {

    private static final String MANAGED_BY = "dongjin-python-service";

    private static final String NODE_QUERY = """
            MATCH (grid:GridModel {
                managedBy: $managedBy,
                active: true
            })-[:CONTAINS]->(node:Device)
            RETURN DISTINCT
              node.businessId AS id,
              coalesce(node.name, node.businessId) AS name,
              coalesce(node.elementType, "device") AS type,
              coalesce(node.status, "normal") AS status,
              coalesce(node.voltageLevel, "") AS voltageLevel
            ORDER BY id
            """;

    private static final String EDGE_QUERY = """
            MATCH (grid:GridModel {
                managedBy: $managedBy,
                active: true
            })-[:CONTAINS]->(source:Device)-[relation]->(target:Device)
                  <-[:CONTAINS]-(grid)
            WHERE relation.managedBy = $managedBy
            RETURN DISTINCT
              relation.relationshipId AS id,
              source.businessId AS source,
              target.businessId AS target,
              coalesce(relation.name, type(relation)) AS name,
              coalesce(relation.status, "normal") AS status,
              type(relation) AS relationType
            ORDER BY id
            """;

    private final Driver driver;
    private final String database;

    public TopologyRepository(
            Driver driver,
            @Value("${app.neo4j.database}") String database
    ) {
        this.driver = driver;
        this.database = database;
    }

    public List<TopologyData.Node> findNodes() {
        try (var session = driver.session(
                SessionConfig.builder().withDatabase(database).build()
        )) {
            return session.executeRead(transaction -> transaction
                    .run(NODE_QUERY, java.util.Map.of("managedBy", MANAGED_BY))
                    .list(this::toNode));
        }
    }

    public List<TopologyData.Edge> findEdges() {
        try (var session = driver.session(
                SessionConfig.builder().withDatabase(database).build()
        )) {
            return session.executeRead(transaction -> transaction
                    .run(EDGE_QUERY, java.util.Map.of("managedBy", MANAGED_BY))
                    .list(this::toEdge));
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
