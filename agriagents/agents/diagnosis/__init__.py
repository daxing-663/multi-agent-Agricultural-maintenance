"""诊断组的 Agent 工厂。"""

from agriagents.agents.diagnosis.diagnosis_agent import create_diagnosis_agent
from agriagents.agents.diagnosis.severity_assess import create_severity_assess

__all__ = ["create_diagnosis_agent", "create_severity_assess"]
