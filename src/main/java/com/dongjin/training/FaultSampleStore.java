package com.dongjin.training;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;

@Component
public class FaultSampleStore {

    private final Map<String, FaultSample> samples = new LinkedHashMap<>();

    public synchronized void addAll(Collection<FaultSample> newSamples) {
        newSamples.forEach(sample -> samples.put(sample.id(), sample));
    }

    public synchronized List<FaultSample> findAll() {
        return List.copyOf(samples.values());
    }

    public synchronized List<FaultSample> findByIds(List<String> ids) {
        if (ids == null || ids.isEmpty()) {
            return findAll();
        }

        var selected = new ArrayList<FaultSample>();
        ids.forEach(id -> {
            var sample = samples.get(id);
            if (sample == null) {
                throw new IllegalArgumentException("训练样本不存在：" + id);
            }
            selected.add(sample);
        });
        return List.copyOf(selected);
    }

    public synchronized void clear() {
        samples.clear();
    }
}
