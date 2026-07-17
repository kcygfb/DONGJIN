package com.dongjin.training;

import java.util.List;

public record GenerateFaultRequest(
        Integer count,
        Long seed,
        List<String> faultTypes
) {
}
