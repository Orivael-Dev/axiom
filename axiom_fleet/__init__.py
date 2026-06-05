"""Axiom Fleet — multi-specialist SRD/AXM routing layer.

Each specialist is a ≤0.5B SRD-quantized model packed into a signed .axm
container. The FleetRouter dispatches queries to the right specialist based on
modality (text vs vision) and domain, then returns the result with the AXM
fingerprint for audit.

Quick start:
    from axiom_fleet import FleetRouter, load_manifest

    router = FleetRouter(load_manifest("examples/fleets/medical_fleet.json"))
    result = router.query("Describe the consolidation in this chest X-ray.",
                          image_path="/tmp/cxr.jpg")
    print(result.text)
    print(result.specialist_fingerprint)   # HMAC-chain proof of which model answered
"""
from axiom_fleet.fleet_manifest import FleetManifest, SpecialistConfig, load_manifest, save_manifest
from axiom_fleet.fleet_router import FleetRouter, FleetQuery, FleetResult

__all__ = [
    "FleetManifest",
    "SpecialistConfig",
    "load_manifest",
    "save_manifest",
    "FleetRouter",
    "FleetQuery",
    "FleetResult",
]
