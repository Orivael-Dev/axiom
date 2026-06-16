"""Axiom Latent Agent Fabric — MiniSRDAgent + AgentRouter + FabricCoordinator.

Implements the dormant-capsule micro-agent architecture from the
Axiom Latent Agent Fabric concept document:

  50+ specialist agents stored as signed SRD capsules (MiniSRDAgent).
  Only tiny VRAMAgentTokens stay hot in memory.
  AgentRouter scores all dormant agents against the current MET event,
  wakes the top 2-5, collects compact AgentResults, and FabricCoordinator
  merges them through Coordinator.compose() into a signed EventToken chain.

The SRD/MET connection:
  - A packed .axm file IS the dormant capsule; its fingerprint IS the
    agent's identity signature.
  - MET hydration slots (embedding pinned F16, transformer chunks cold)
    ARE the dormancy mechanism.
  - The EventToken produced by a woken agent IS the AgentResult.
"""
from axiom_agent_fabric.capsule import MiniSRDAgent, VRAMAgentToken
from axiom_agent_fabric.result import AgentResult
from axiom_agent_fabric.router import AgentRouter, WakeScore
from axiom_agent_fabric.coordinator import FabricCoordinator, FabricResult
from axiom_agent_fabric.power_conditioner import (
    PowerConditionerAgent,
    PowerState,
    PowerProfile,
    InferenceConfig,
    PowerSensor,
    InputConditioner,
)

__all__ = [
    "MiniSRDAgent",
    "VRAMAgentToken",
    "AgentResult",
    "AgentRouter",
    "WakeScore",
    "FabricCoordinator",
    "FabricResult",
    "PowerConditionerAgent",
    "PowerState",
    "PowerProfile",
    "InferenceConfig",
    "PowerSensor",
    "InputConditioner",
]
