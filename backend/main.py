

import os
import random
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


def _parse_cors_origins() -> list[str]:
    raw = os.getenv("MARKETMIRROR_CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500")
    return [item.strip() for item in raw.split(",") if item.strip()]


HOST = os.getenv("MARKETMIRROR_HOST", "0.0.0.0")
PORT = int(os.getenv("MARKETMIRROR_PORT", "8000"))
CORS_ORIGINS = _parse_cors_origins()

app = FastAPI(
    title="MarketMirror API",
    description="Virtual Financial Market Environment Simulator API",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response Models ──────────────────────────────────────────

class SimulationRequest(BaseModel):
    event_description: str = Field(..., min_length=8, max_length=500)
    target_industry: str = Field(..., min_length=2, max_length=50)
    investment_amount: float = Field(0.0, ge=0)


class AgentAction(BaseModel):
    agent_type: str
    action_type: str
    confidence: float
    reasoning: str


class SimulationResponse(BaseModel):
    policy_parsed: str
    market_impact_score: float
    scenario_tag: str
    agent_actions: list[AgentAction]
    timestamp: float


# ── Mock simulation helpers ────────────────────────────────────────────

def _build_scenario_tag(event_description: str, target_industry: str) -> str:
    if "疫情" in event_description:
        return f"pandemic-shock:{target_industry}"
    if "杠杆" in event_description or "流动性" in event_description:
        return f"deleveraging:{target_industry}"
    if "监管" in event_description or "合规" in event_description:
        return f"regulation-shock:{target_industry}"
    return f"general-stress:{target_industry}"


def _parse_policy(event_description: str, target_industry: str) -> str:
    return (
        f"Parsed Policy: {event_description}. "
        f"Interpreted as a major shock to {target_industry}."
    )


def _estimate_impact(event_description: str) -> float:
    if any(keyword in event_description for keyword in ["疫情", "危机", "暴跌", "流动性收紧"]):
        return round(random.uniform(-0.9, 0.35), 2)
    if any(keyword in event_description for keyword in ["监管", "合规", "去杠杆"]):
        return round(random.uniform(-0.65, 0.4), 2)
    return round(random.uniform(-0.6, 0.7), 2)


def _build_agent_actions(impact: float) -> list[AgentAction]:
    return [
        AgentAction(
            agent_type="Aggressive (激进型)",
            action_type="Short Volatility / 做空波动" if impact < -0.55
            else "Buy on Dip / 抄底" if impact < 0
            else "Leverage Long / 加杠杆做多",
            confidence=round(random.uniform(0.75, 0.95), 2),
            reasoning="High risk tolerance, seeking alpha in volatility.",
        ),
        AgentAction(
            agent_type="Conservative (保守型)",
            action_type="Panic Sell / 恐慌性抛售" if impact < -0.5
            else "Wait and See / 观望",
            confidence=round(random.uniform(0.7, 0.99), 2),
            reasoning="Capital preservation is the priority.",
        ),
        AgentAction(
            agent_type="Institutional (机构型)",
            action_type="Hedge / 对冲风险" if impact < 0
            else "Sector Rotation / 板块轮动",
            confidence=round(random.uniform(0.8, 0.9), 2),
            reasoning="Following strict risk management models and stress testing guidelines.",
        ),
    ]


# ── Routes ─────────────────────────────────────────────────────────────

@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "marketmirror-api", "version": "1.1.0"}


@app.post("/api/simulate")
def run_simulation(request: SimulationRequest) -> SimulationResponse:
    parsed_policy = _parse_policy(request.event_description, request.target_industry)
    impact = _estimate_impact(request.event_description)
    actions = _build_agent_actions(impact)
    scenario_tag = _build_scenario_tag(request.event_description, request.target_industry)

    return SimulationResponse(
        policy_parsed=parsed_policy,
        market_impact_score=impact,
        scenario_tag=scenario_tag,
        agent_actions=actions,
        timestamp=time.time(),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
