package com.dongjin.training;

import com.dongjin.topology.GnnTopology;
import com.dongjin.topology.TopologyData;
import com.dongjin.topology.TopologyRepository;
import com.fasterxml.jackson.databind.JsonNode;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import org.springframework.stereotype.Service;

@Service
public class TrainingJobService {

    private final FaultSampleStore sampleStore;
    private final PythonTrainingGateway trainingGateway;
    private final TopologyRepository topologyRepository;
    private final Map<String, JobState> jobs = new ConcurrentHashMap<>();
    private final ExecutorService executor = Executors.newSingleThreadExecutor(task -> {
        Thread thread = new Thread(task, "python-training-worker");
        thread.setDaemon(true);
        return thread;
    });

    public TrainingJobService(
            FaultSampleStore sampleStore,
            PythonTrainingGateway trainingGateway,
            TopologyRepository topologyRepository
    ) {
        this.sampleStore = sampleStore;
        this.trainingGateway = trainingGateway;
        this.topologyRepository = topologyRepository;
    }

    public TrainingJobView start(StartTrainingRequest request) {
        List<FaultSample> samples = sampleStore.findByIds(request == null ? null : request.sampleIds());
        validateSamples(samples);
        List<TopologyData.Node> nodes = topologyRepository.findNodes();
        List<TopologyData.Edge> edges = topologyRepository.findEdges();
        if (nodes.isEmpty()) {
            throw new IllegalStateException("当前拓扑为空，无法训练 GNN");
        }
        GnnTopology topology = GnnTopology.from(nodes, edges);

        String datasetName = request == null || request.datasetName() == null || request.datasetName().isBlank()
                ? "grid-fault-dataset-" + Instant.now().toString().replace(':', '-')
                : request.datasetName().trim();
        JobState job = new JobState("training-" + UUID.randomUUID(), datasetName, samples.size());
        jobs.put(job.id, job);
        CompletableFuture.runAsync(() -> execute(job, samples, topology), executor);
        return job.view();
    }

    public TrainingJobView find(String id) {
        JobState job = jobs.get(id);
        if (job == null) {
            throw new IllegalArgumentException("训练任务不存在：" + id);
        }
        return job.view();
    }

    public boolean hasActiveJobs() {
        return jobs.values().stream().anyMatch(job -> "QUEUED".equals(job.status) || "RUNNING".equals(job.status));
    }

    public void clearFinishedJobs() {
        if (hasActiveJobs()) {
            throw new IllegalStateException("训练任务正在运行，完成后才能重置训练");
        }
        jobs.clear();
    }

    private void execute(JobState job, List<FaultSample> samples, GnnTopology topology) {
        job.status = "RUNNING";
        job.progress = 10;
        job.startedAt = Instant.now();
        try {
            job.progress = 35;
            job.result = trainingGateway.train(job.datasetName, samples, topology);
            job.progress = 100;
            job.status = "SUCCEEDED";
        } catch (RuntimeException exception) {
            job.status = "FAILED";
            job.error = exception.getMessage();
        } finally {
            job.finishedAt = Instant.now();
        }
    }

    private void validateSamples(List<FaultSample> samples) {
        if (samples.size() < 8) {
            throw new IllegalArgumentException("至少需要 8 条故障样本才能开始训练");
        }
        long labelCount = samples.stream().map(FaultSample::faultType).distinct().count();
        if (labelCount < 2) {
            throw new IllegalArgumentException("至少需要两种不同故障类型才能开始训练");
        }
    }

    private static final class JobState {
        private final String id;
        private final String datasetName;
        private final int sampleCount;
        private final Instant createdAt = Instant.now();
        private volatile String status = "QUEUED";
        private volatile int progress;
        private volatile Instant startedAt;
        private volatile Instant finishedAt;
        private volatile JsonNode result;
        private volatile String error;

        private JobState(String id, String datasetName, int sampleCount) {
            this.id = id;
            this.datasetName = datasetName;
            this.sampleCount = sampleCount;
        }

        private TrainingJobView view() {
            return new TrainingJobView(
                    id, datasetName, sampleCount, status, progress, createdAt, startedAt, finishedAt, result, error
            );
        }
    }
}
