package com.dongjin.training;

import java.util.Locale;

public enum FaultType {
    DEVICE_OFFLINE("设备离线", false),
    VOLTAGE_ANOMALY("电压异常", false),
    LINE_OVERLOAD("线路过载", true),
    LINE_DISCONNECTED("线路断开", true);

    private final String displayName;
    private final boolean edgeTarget;

    FaultType(String displayName, boolean edgeTarget) {
        this.displayName = displayName;
        this.edgeTarget = edgeTarget;
    }

    public String displayName() {
        return displayName;
    }

    public boolean edgeTarget() {
        return edgeTarget;
    }

    public static FaultType from(String value) {
        if (value == null || value.isBlank()) {
            throw new IllegalArgumentException("故障类型不能为空");
        }

        try {
            return valueOf(value.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException exception) {
            throw new IllegalArgumentException("不支持的故障类型：" + value);
        }
    }
}
