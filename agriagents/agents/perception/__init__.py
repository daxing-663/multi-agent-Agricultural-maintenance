"""感知组与诊断组的 Agent 工厂。"""

from agriagents.agents.perception.anomaly_screen import create_anomaly_screen
from agriagents.agents.perception.perception_agent import create_perception_agent

__all__ = ["create_perception_agent", "create_anomaly_screen"]
