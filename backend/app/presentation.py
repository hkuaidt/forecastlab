"""Public status semantics shared by lightweight history and exported reports."""
def report_failed(run) -> bool:
    return bool((run.status in {"failed", "partial", "interrupted"} and run.failed_stage == "forecast")
        or (run.forecast and not run.forecast.probabilities
            and any("校验未通过" in item for item in run.forecast.limitations)))
