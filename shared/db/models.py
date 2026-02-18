from dataclasses import dataclass
from typing import Optional

@dataclass
class SurveyRow:
    survey_id: str
    status: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    total_runtime_seconds: Optional[float] = None
