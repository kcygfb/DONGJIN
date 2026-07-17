package com.dongjin.training;

public class TrainingGatewayException extends RuntimeException {

    public TrainingGatewayException(String message) {
        super(message);
    }

    public TrainingGatewayException(String message, Throwable cause) {
        super(message, cause);
    }
}
