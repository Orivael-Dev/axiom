package dev.orivael.axiom.security

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Canonical JSON encoder matching Python's
 *   json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(',', ':'))
 *
 * This is the exact encoding the AXIOM Python stack uses before HMACing
 * a payload (see axiom_signing._canonical and axiom_sovereign_phone
 * _canonical). For client-side signature verification to succeed, the
 * Kotlin side must produce byte-identical output.
 *
 *  - Object keys sorted alphabetically (lexicographic on UTF-16 codepoints,
 *    which matches Python's `sorted()` for ASCII-only keys, which is what
 *    the AXIOM stack uses).
 *  - No whitespace between tokens (`,` and `:` separators only).
 *  - Non-ASCII characters in strings escaped to `\uXXXX` so the output
 *    is pure ASCII — matches Python's `ensure_ascii=True`.
 *  - Booleans: `true` / `false`. Null: `null`.
 *  - Numbers: integers as plain digits; floats via [java.lang.Double.toString]
 *    which matches Python's `repr` for the ranges we care about
 *    (round(x, N) outputs for N ≤ 4).
 *
 * Edge cases this encoder DELIBERATELY does not cover:
 *  - Floats outside the [1e-3, 1e7] range. Python's repr switches to
 *    scientific notation at different thresholds than Java; AXIOM
 *    payloads stay well inside the comfortable range.
 *  - Surrogate pairs that aren't paired up. AXIOM payloads are
 *    ASCII-only in practice (signatures hex, timestamps ISO-8601,
 *    text fields UTF-8 but the canonical encoder escapes them).
 */
object CanonicalJson {

    fun encode(element: JsonElement): String = buildString { write(element, this) }

    private fun write(element: JsonElement, out: StringBuilder) {
        when (element) {
            is JsonNull      -> out.append("null")
            is JsonObject    -> writeObject(element, out)
            is JsonArray     -> writeArray(element, out)
            is JsonPrimitive -> writePrimitive(element, out)
        }
    }

    private fun writeObject(obj: JsonObject, out: StringBuilder) {
        out.append('{')
        val sorted = obj.keys.sorted()
        sorted.forEachIndexed { i, k ->
            if (i > 0) out.append(',')
            writeString(k, out)
            out.append(':')
            write(obj.getValue(k), out)
        }
        out.append('}')
    }

    private fun writeArray(arr: JsonArray, out: StringBuilder) {
        out.append('[')
        arr.forEachIndexed { i, e ->
            if (i > 0) out.append(',')
            write(e, out)
        }
        out.append(']')
    }

    private fun writePrimitive(p: JsonPrimitive, out: StringBuilder) {
        when {
            p.isString -> writeString(p.content, out)
            else -> {
                // booleans + numbers + null are all literal — kotlinx.serialization
                // already stores them as the raw text token.
                out.append(p.content)
            }
        }
    }

    private fun writeString(s: String, out: StringBuilder) {
        out.append('"')
        for (ch in s) {
            when (ch) {
                '\\' -> out.append("\\\\")
                '"'  -> out.append("\\\"")
                '\b' -> out.append("\\b")
                '' -> out.append("\\f")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                else -> {
                    val code = ch.code
                    if (code < 0x20 || code > 0x7E) {
                        out.append("\\u")
                        out.append(code.toString(16).padStart(4, '0'))
                    } else {
                        out.append(ch)
                    }
                }
            }
        }
        out.append('"')
    }
}
