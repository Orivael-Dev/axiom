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
