# SPDX-License-Identifier: Apache-2.0

"""Service boundary that records deterministic policy decisions before returning."""

from __future__ import annotations

from eis.policy import (
    DecisionSink,
    PolicyDecision,
    PolicyEngine,
    PolicyRequest,
    record_decision,
)


class PolicyService:
    """Evaluate with the pure engine, then record before exposing the result.

    A sink failure propagates, so callers never receive a decision that was not
    audited. The engine itself remains deterministic and side-effect free.
    """

    def __init__(self, engine: PolicyEngine, sink: DecisionSink) -> None:
        self._engine = engine
        self._sink = sink

    @property
    def policy_version(self) -> str:
        return self._engine.policy_version

    @property
    def policy_hash(self) -> str:
        return self._engine.policy_hash

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        decision = self._engine.evaluate(request)
        record_decision(decision, self._sink)
        return decision

    def evaluate_payload(self, payload: object) -> PolicyDecision:
        """Validate an untrusted mapping, evaluate it and record the decision."""
        return self.evaluate(PolicyRequest.from_mapping(payload))
