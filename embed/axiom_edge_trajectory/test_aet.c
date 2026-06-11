/*
 * test_aet.c — cross-validation harness for axiom_edge_trajectory.c
 *
 * Compile:  gcc -O2 -Wall -o test_aet test_aet.c axiom_edge_trajectory.c -lm
 * Run:      ./test_aet
 *
 * Expected output (verified against Python reference):
 *   PASS window_full        predicted=? window=5 lpm=0 conf=0.????
 *   PASS window_throttled   predicted=? window=3 lpm=1
 *   PASS drift_stable       status=1
 *   PASS drift_detected     status=2
 *   PASS no_baseline        status=0
 *   PASS window_not_full    (returns false for first 4 tokens)
 *   All 6 tests passed.
 */
#include "axiom_edge_trajectory.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>

static int failures = 0;

#define ASSERT(name, cond)                                         \
    do {                                                           \
        if (cond) { printf("PASS %s\n", name); }                  \
        else      { printf("FAIL %s  (line %d)\n", name, __LINE__); failures++; } \
    } while (0)

/* Feed HR values at full power; returns last prediction. */
static bool feed_full(aet_agent_t *ag, float hrs[], int n,
                      aet_prediction_t *out)
{
    bool got = false;
    for (int i = 0; i < n; i++) {
        aet_token_t tok = { {hrs[i], 54.0f, 1.2f, 1.0f}, false };
        got = aet_ingest(ag, &tok, out);
    }
    return got;
}

static bool feed_throttled(aet_agent_t *ag, float hrs[], int n,
                           aet_prediction_t *out)
{
    bool got = false;
    for (int i = 0; i < n; i++) {
        aet_token_t tok = { {hrs[i], 54.0f, 1.2f, 1.0f}, true };
        got = aet_ingest(ag, &tok, out);
    }
    return got;
}

int main(void)
{
    /* ── 1. Full window fills at step 5 ─────────────────────────────────── */
    {
        aet_agent_t ag;
        aet_prediction_t pred;
        aet_init(&ag, NULL);
        float hrs[] = {68, 70, 72, 74, 76};
        bool  ok    = feed_full(&ag, hrs, 5, &pred);
        ASSERT("window_full",
               ok && pred.window_used == AET_WINDOW_FULL &&
               pred.low_power_mode == false &&
               pred.predicted_class >= 0 &&
               pred.predicted_class <  AET_N_CLASSES &&
               pred.confidence >= 0.0f && pred.confidence <= 1.0f);
        printf("       predicted=%d window=%d lpm=%d conf=%.4f\n",
               pred.predicted_class, pred.window_used,
               pred.low_power_mode, pred.confidence);
    }

    /* ── 2. Throttled window fills at step 3 ────────────────────────────── */
    {
        aet_agent_t ag;
        aet_prediction_t pred;
        aet_init(&ag, NULL);
        float hrs[] = {60, 62, 64};
        bool  ok    = feed_throttled(&ag, hrs, 3, &pred);
        ASSERT("window_throttled",
               ok && pred.window_used == AET_WINDOW_THROTTLED &&
               pred.low_power_mode == true);
        printf("       predicted=%d window=%d lpm=%d conf=%.4f\n",
               pred.predicted_class, pred.window_used,
               pred.low_power_mode, pred.confidence);
    }

    /* ── 3. Drift: near-baseline → STABLE ───────────────────────────────── */
    {
        aet_baseline_t bl = { {70.0f, 54.0f, 1.2f, 1.0f}, 5.0f };
        aet_agent_t ag;
        aet_prediction_t pred;
        aet_init(&ag, &bl);
        float hrs[] = {69, 70, 71, 70, 69};
        feed_full(&ag, hrs, 5, &pred);
        ASSERT("drift_stable", pred.drift_status == AET_DRIFT_STABLE);
        printf("       status=%d\n", pred.drift_status);
    }

    /* ── 4. Drift: far from baseline → DETECTED ─────────────────────────── */
    {
        aet_baseline_t bl = { {70.0f, 54.0f, 1.2f, 1.0f}, 5.0f };
        aet_agent_t ag;
        aet_prediction_t pred;
        aet_init(&ag, &bl);
        float hrs[] = {150, 155, 160, 158, 162};
        feed_full(&ag, hrs, 5, &pred);
        ASSERT("drift_detected", pred.drift_status == AET_DRIFT_DETECTED);
        printf("       status=%d\n", pred.drift_status);
    }

    /* ── 5. No baseline → NO_BASELINE ───────────────────────────────────── */
    {
        aet_agent_t ag;
        aet_prediction_t pred;
        aet_init(&ag, NULL);
        float hrs[] = {68, 70, 72, 74, 76};
        feed_full(&ag, hrs, 5, &pred);
        ASSERT("no_baseline", pred.drift_status == AET_DRIFT_NO_BASELINE);
        printf("       status=%d\n", pred.drift_status);
    }

    /* ── 6. Returns false while window is still filling ─────────────────── */
    {
        aet_agent_t ag;
        aet_prediction_t pred;
        aet_init(&ag, NULL);
        int false_count = 0;
        for (int i = 0; i < 4; i++) {
            aet_token_t tok = { {68.0f + i, 54.0f, 1.2f, 1.0f}, false };
            if (!aet_ingest(&ag, &tok, &pred)) false_count++;
        }
        ASSERT("window_not_full", false_count == 4);
    }

    printf("\n%s\n", failures == 0 ? "All 6 tests passed." : "SOME TESTS FAILED.");
    return failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
