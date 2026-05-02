"""
SOVEREIGN ENDPOINTS — paste into examples/guard_api.py
=======================================================
Add these imports at the top (after existing imports):

  import sys, os
  sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
  try:
      from sovereign.sovereign import Sovereign
      _sovereign = Sovereign()
      SOVEREIGN_AVAILABLE = True
  except ImportError:
      _sovereign = None
      SOVEREIGN_AVAILABLE = False

Then paste these routes before if __name__ == "__main__":
"""

SOVEREIGN_ROUTES = '''
# ══════════════════════════════════════════════════════════════
# SOVEREIGN ENDPOINTS
# ══════════════════════════════════════════════════════════════

@app.get("/sovereign/status")
async def sovereign_status():
    """Fleet status — all agents + kill switch + manifest."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available")
    return _sovereign.fleet_status()

@app.post("/sovereign/register")
async def sovereign_register(name: str, trust_level: int = 2):
    """Register a new agent in the fleet."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available")
    agent = _sovereign.register_agent(name, trust_level)
    return agent.to_dict()

@app.post("/sovereign/message")
async def sovereign_message(
    from_agent: str,
    to_agent:   str,
    content:    str
):
    """Process an inter-agent message through Sovereign."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available")
    result = _sovereign.process_message(from_agent, to_agent, content)
    return result

@app.post("/sovereign/escalate")
async def sovereign_escalate(agent_id: str, reason: str):
    """Escalate an agent to Level 3 Suspend."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available")
    return _sovereign.escalate_to_level3(agent_id, reason)

@app.get("/sovereign/agents")
async def sovereign_agents():
    """List all registered agents."""
    if not SOVEREIGN_AVAILABLE or not _sovereign:
        raise HTTPException(503, "Sovereign not available")
    return _sovereign.registry.fleet_manifest()
'''

print("Paste the above routes into examples/guard_api.py")
print("Then add the imports shown at the top of this file")
