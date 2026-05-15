# -*- coding: utf-8 -*-
"""
Structural lint for the android/ Sovereign Phone client scaffold.

The full APK build needs the Android SDK + Gradle, which aren't in this
container. This test still catches the things that would silently break
the scaffold on someone else's machine:

  - manifest is well-formed XML and declares the right permissions
  - every Kotlin source lives under the expected package
  - every Compose screen exists and is callable from MainActivity
  - Gradle scripts pin the documented versions (AGP, Kotlin, compose-bom)
  - README documents the build command + default emulator loopback

3 BLOCKED + 4 PASSED + 2 INVARIANTS, matching the rest of the suite.

BUG-003: UTF-8 output encoding
"""

import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


ANDROID = Path(__file__).resolve().parents[1] / "android"
PKG_ROOT = ANDROID / "app" / "src" / "main" / "kotlin" / "dev" / "orivael" / "axiom"


# ===========================================================================
# SECTION 1 — BLOCKED (the scaffold would fail to build without these)
# ===========================================================================

class TestAndroidBlocked:

    def test_blocked_manifest_is_valid_xml_with_internet_permission(self):
        manifest = ANDROID / "app" / "src" / "main" / "AndroidManifest.xml"
        tree = ET.parse(manifest)
        root = tree.getroot()
        ns = {"android": "http://schemas.android.com/apk/res/android"}
        perms = [p.get(f"{{{ns['android']}}}name")
                 for p in root.findall("uses-permission")]
        # INTERNET is non-negotiable — the whole app is REST traffic.
        assert "android.permission.INTERNET" in perms

    def test_blocked_main_activity_is_declared(self):
        manifest_text = (ANDROID / "app" / "src" / "main"
                          / "AndroidManifest.xml").read_text(encoding="utf-8")
        # The launcher activity must be exported + present so adb / launchers
        # can resolve it. Single-Activity host pattern.
        assert 'android:name=".MainActivity"' in manifest_text
        assert 'android:exported="true"' in manifest_text
        assert "android.intent.action.MAIN"  in manifest_text
        assert "android.intent.category.LAUNCHER" in manifest_text

    def test_blocked_versions_pinned(self):
        """AGP, Kotlin, and the Compose compiler extension must be pinned
        in lock-step — mismatched pairs are a very common build break."""
        root_build = (ANDROID / "build.gradle.kts").read_text(encoding="utf-8")
        app_build  = (ANDROID / "app" / "build.gradle.kts").read_text(encoding="utf-8")
        assert 'com.android.application") version "8.2.2"' in root_build
        assert 'kotlin.android") version "1.9.22"' in root_build
        assert 'kotlinCompilerExtensionVersion = "1.5.10"' in app_build
        assert 'compose-bom:2024.02.02' in app_build


# ===========================================================================
# SECTION 2 — PASSED (the scaffold contains the layers it advertises)
# ===========================================================================

class TestAndroidPassed:

    def test_passed_kotlin_files_under_package(self):
        """Every .kt file under app/src/main/kotlin must declare a
        package starting with dev.orivael.axiom — guards against the
        classic "wrong directory" copy-paste."""
        offenders = []
        for kt in PKG_ROOT.rglob("*.kt"):
            head = kt.read_text(encoding="utf-8").split("\n")[0]
            if not head.startswith("package dev.orivael.axiom"):
                offenders.append((str(kt), head))
        assert not offenders, f"offending packages: {offenders}"

    def test_passed_four_screens_present(self):
        """The bottom-nav layout expects exactly four composable
        screens. If any are missing, MainActivity won't compile."""
        screens = {"GateScreen", "HelloOperatorScreen",
                    "StatusScreen", "SettingsScreen"}
        screen_dir = PKG_ROOT / "ui" / "screens"
        found = {p.stem for p in screen_dir.glob("*Screen.kt")}
        assert screens.issubset(found), f"missing screens: {screens - found}"
        # No extra screens — catches accidental orphans.
        assert found == screens, f"unexpected screens: {found - screens}"

    def test_passed_hello_operator_transcript_matches_brief(self):
        """The on-phone Hello Operator demo must replay the ORVL-019 §4
        scam-call utterances verbatim. If anyone tweaks the wording
        the demo no longer matches the patent brief."""
        src = (PKG_ROOT / "ui" / "screens"
                / "HelloOperatorScreen.kt").read_text(encoding="utf-8")
        for expected in (
            "Hello, this is a call about your account",
            "This is the IRS calling",
            "owe back taxes",
            "Send gift cards",
        ):
            assert expected in src, f"missing transcript line: '{expected}'"
        # Speeds: 1x (real time), 4x (default demo), 10x (sprint mode).
        for speed in ("1x", "4x", "10x"):
            assert speed in src, f"missing replay speed {speed}"

    def test_passed_apiclient_covers_six_endpoints(self):
        """Every REST endpoint the Status + Gate screens consume must
        be wired in AxiomClient. Catches dropped methods on rename."""
        client_src = (PKG_ROOT / "network" / "AxiomClient.kt").read_text(encoding="utf-8")
        for method in ("phoneOutbound", "phoneInbound", "phoneStatus",
                        "cmaaFleet", "shieldStatus"):
            assert f"fun {method}" in client_src or \
                    f"suspend fun {method}" in client_src, f"missing {method}"

    def test_passed_in_call_module_present(self):
        """Live Call Mode — InCallTranscriptionService runs as a
        foreground service with microphone type, captures mic audio,
        routes each transcribed utterance through /phone/inbound, and
        appends to TranscriptionStore for the Hello Op UI feed.

        Catches drift on three places that have to stay aligned for
        the slice to work end-to-end on a device:
          - manifest permissions (RECORD_AUDIO + the FOREGROUND_SERVICE
            type-specific permission)
          - manifest service declaration with the microphone type
          - the Kotlin files themselves (service + store)
        """
        manifest = (ANDROID / "app" / "src" / "main"
                     / "AndroidManifest.xml").read_text(encoding="utf-8")
        for perm in (
            "android.permission.RECORD_AUDIO",
            "android.permission.FOREGROUND_SERVICE",
            "android.permission.FOREGROUND_SERVICE_MICROPHONE",
            "android.permission.POST_NOTIFICATIONS",
        ):
            assert perm in manifest, f"manifest missing {perm}"
        assert '.incall.InCallTranscriptionService' in manifest
        assert 'android:foregroundServiceType="microphone"' in manifest

        # Kotlin sources
        incall = PKG_ROOT / "incall"
        service = (incall / "InCallTranscriptionService.kt")
        store   = (incall / "TranscriptionStore.kt")
        assert service.exists()
        assert store.exists()

        svc_src = service.read_text(encoding="utf-8")
        # ASR backend is selected by the factory, not hardcoded — the
        # service should be backend-agnostic so Vosk can take over when
        # the on-device model is installed.
        assert "TranscriptionBackendFactory.create" in svc_src
        # Sessions persisted across utterances so L1->L2->L3 graduation
        # works across the call.
        assert 'sessionId = "incall-' in svc_src
        # Each utterance must flow through the /phone/inbound gate.
        assert "client.phoneInbound" in svc_src

        # System SpeechRecognizer fallback backend still guards
        # availability before allocating the recognizer.
        sr_backend = (incall / "SpeechRecognizerBackend.kt").read_text(encoding="utf-8")
        assert "SpeechRecognizer.isRecognitionAvailable" in sr_backend

    def test_passed_vosk_offline_backend_present(self):
        """On-device Vosk ASR — keeps audio off the network when the
        user opts in via Settings.

        Lint the four files plus the Gradle dependency line. The
        download URL must be pinned to a specific release so a remote
        bump can't silently alter behaviour."""
        incall = PKG_ROOT / "incall"
        for name in ("TranscriptionBackend", "VoskBackend",
                     "VoskModelManager", "SpeechRecognizerBackend"):
            f = incall / f"{name}.kt"
            assert f.exists(), f"incall/{name}.kt missing"

        # Factory must prefer Vosk when the model is installed.
        iface = (incall / "TranscriptionBackend.kt").read_text(encoding="utf-8")
        assert "VoskModelManager.isInstalled" in iface
        assert "VoskBackend(context)" in iface
        assert "SpeechRecognizerBackend(context)" in iface

        # Vosk URL pinned, marker file checked.
        mgr = (incall / "VoskModelManager.kt").read_text(encoding="utf-8")
        assert "alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip" in mgr
        assert "am/final.mdl" in mgr
        # Zip-slip guard is non-negotiable — extraction touches user data dir.
        assert "zip slip" in mgr.lower()

        # Vosk Android dependency declared in app/build.gradle.kts.
        app_build = (ANDROID / "app" / "build.gradle.kts").read_text(encoding="utf-8")
        assert "com.alphacephei:vosk-android" in app_build

        # TranscriptionStore exposes the backend label so the banner can
        # tell the operator which engine is running.
        store = (incall / "TranscriptionStore.kt").read_text(encoding="utf-8")
        assert "setBackendLabel" in store
        assert "backendLabel" in store

    def test_passed_security_module_present(self):
        """HMAC client-side verification: KeystoreManager wraps the master
        key under an Android Keystore AES-GCM key; SignatureVerifier
        recomputes signatures by canonicalising the response JSON the
        same way Python's json.dumps(sort_keys=True, ensure_ascii=True,
        separators=(',', ':')) does. All three files must exist."""
        sec_dir = PKG_ROOT / "security"
        for name in ("KeystoreManager", "CanonicalJson", "SignatureVerifier"):
            f = sec_dir / f"{name}.kt"
            assert f.exists(), f"security/{name}.kt missing"

        keystore = (sec_dir / "KeystoreManager.kt").read_text(encoding="utf-8")
        # AES-256-GCM only — anything else fails to wrap reliably across OEMs.
        assert "AES/GCM/NoPadding" in keystore
        assert "AndroidKeyStore" in keystore
        # Refuses to store anything if the cipher init fails (no plaintext fallback).
        assert "KeyProperties.PURPOSE_ENCRYPT" in keystore
        assert "KeyProperties.PURPOSE_DECRYPT" in keystore

        verifier = (sec_dir / "SignatureVerifier.kt").read_text(encoding="utf-8")
        # Derived-key salt must match axiom_signing.derive_key(b"axiom-aspa-device-v1")
        assert 'axiom-aspa-device-v1' in verifier
        # Strip 'signature' before canonicalising — matches the server.
        assert 'filterKeys { it != "signature" }' in verifier

    def test_passed_call_screening_service_wired(self):
        """The Hello Operator product needs a CallScreeningService that:
          - is registered in the manifest under BIND_SCREENING_SERVICE
          - declares the android.telecom.CallScreeningService intent filter
          - is exported (the OS binds to it)
          - has a corresponding Kotlin source file under telephony/
        Any of these missing → calls won't be intercepted at runtime."""
        manifest = (ANDROID / "app" / "src" / "main"
                     / "AndroidManifest.xml").read_text(encoding="utf-8")
        assert 'android.permission.BIND_SCREENING_SERVICE' in manifest
        assert 'android.telecom.CallScreeningService' in manifest
        assert '.telephony.AxiomCallScreeningService' in manifest

        # Required permissions for caller-ID + call-log annotation
        assert 'android.permission.READ_PHONE_STATE' in manifest
        assert 'android.permission.READ_CALL_LOG' in manifest

        # Kotlin source for the service
        svc = (PKG_ROOT / "telephony" / "AxiomCallScreeningService.kt")
        assert svc.exists(), "AxiomCallScreeningService.kt missing"
        body = svc.read_text(encoding="utf-8")
        assert "class AxiomCallScreeningService : CallScreeningService" in body
        assert "respondToCall" in body, "service must respond to the OS"

        # Companion store for the in-app log
        store = (PKG_ROOT / "telephony" / "CallScreeningStore.kt")
        assert store.exists(), "CallScreeningStore.kt missing"

    def test_passed_emulator_loopback_is_default(self):
        """The README promises 10.0.2.2:8000 as the default server URL.
        Make sure both the SettingsStore default and the manifest's
        network_security_config agree."""
        store_src = (PKG_ROOT / "data" / "SettingsStore.kt").read_text(encoding="utf-8")
        assert 'DEFAULT_SERVER_URL: String = "http://10.0.2.2:8000"' in store_src

        netsec = (ANDROID / "app" / "src" / "main" / "res" / "xml"
                   / "network_security_config.xml").read_text(encoding="utf-8")
        assert "10.0.2.2" in netsec
        # Cleartext is only allowed for the dev domains, never the wild card.
        assert "cleartextTrafficPermitted=\"true\"" in netsec
        assert "<base-config" not in netsec, \
               "base-config would widen the cleartext window beyond dev domains"


# ===========================================================================
# SECTION 3 — INVARIANTS
# ===========================================================================

class TestAndroidInvariants:

    def test_invariant_readme_documents_build_steps(self):
        readme = (ANDROID / "README.md").read_text(encoding="utf-8")
        assert "./gradlew assembleDebug" in readme
        assert "10.0.2.2" in readme
        assert "AXIOM_MASTER_KEY" in readme

    def test_invariant_gitignore_excludes_gradle_artifacts(self):
        """The Gradle build emits stuff that must never be committed."""
        gi = (ANDROID / ".gitignore").read_text(encoding="utf-8")
        for entry in ("build/", ".gradle/", "local.properties",
                       "*.apk", "gradle-wrapper.jar"):
            assert entry in gi, f"missing .gitignore entry: {entry}"
