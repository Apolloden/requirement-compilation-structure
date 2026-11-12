"""Human-readable names shared by single-stage and pipeline progress displays."""

STAGES = {
    "00": "Initialize project",
    "01": "Read requirements and analyze reference images",
    "02": "Design database schema",
    "03": "Infer database read/write obligations",
    "04": "Implement database",
    "05": "Design backend APIs including P/Q",
    "06": "Implement backend with test-first acceptance",
    "07": "Design UI and frontend data",
    "08": "Implement UI and run existence tests",
    "09": "Design frontend triggers",
    "10": "Implement triggers and run end-to-end tests",
}


# Stages that share a number with a classic stage but do something else.
NAMED = {"02_plan_design": "Plan the whole design"}


def stage_label(script: str) -> str:
    """Show the pipeline stage number and its purpose."""
    number = script[:2]
    if script in NAMED:
        return f"Stage {number} | {NAMED[script]}"
    return f"Stage {number} | {STAGES[number]}" if number in STAGES else script
