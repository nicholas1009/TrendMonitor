"""Risk-engine input assembly boundary; no risk rules live here."""

from .assembler import RiskInputAssembler
from .market_bundle import MARKET_INDEXES, build_market_risk_group, market_coverage_status
from .preflight import PreflightGate, PreflightResult
from .service import RiskInputService
from .serialization import risk_input_from_dict
from .snapshot import RiskInputSnapshotStore

__all__ = [
    "PreflightGate",
    "PreflightResult",
    "RiskInputAssembler",
    "RiskInputService",
    "RiskInputSnapshotStore",
    "MARKET_INDEXES",
    "build_market_risk_group",
    "market_coverage_status",
    "risk_input_from_dict",
]
