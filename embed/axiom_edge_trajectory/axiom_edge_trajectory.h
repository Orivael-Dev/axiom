/*
 * axiom_edge_trajectory.h
 * Low-Power Edge Trajectory Agent — C port for Cortex-M / nRF targets.
 *
 * Mirrors axiom_edge_trajectory.py exactly:
 *   - Sliding window shrinks 5→3 when throttle flag is set
 *   - Throttled window is zero-padded to FLAT_DIM before the matrix pass
 *   - Projection matrix is identical (same seed, same gaussian values)
 *   - Drift detection via centroid distance vs cached baseline
 *
 * No heap allocation. All state lives in aet_agent_t (caller-owned).
 * No dependencies beyond <math.h> (expf, sqrtf).
 *
 * Typical nRF52840 @ 64 MHz: ~25 µs per ingest call (vs ~4 ms CPython).
 *
 * Usage:
 *   aet_agent_t agent;
 *   aet_baseline_t baseline = { {70.0f, 54.0f, 1.2f, 1.0f}, 5.0f };
 *   aet_init(&agent, &baseline);
 *
 *   aet_token_t tok = { {72.0f, 52.0f, 1.4f, 1.0f}, false };
 *   aet_prediction_t pred;
 *   if (aet_ingest(&agent, &tok, &pred)) {
 *       // pred.predicted_class, pred.confidence, pred.drift_status
 *   }
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── Constants ──────────────────────────────────────────────────────────── */

#define AET_STATE_DIM        4      /* [HeartRate, HRV_ms, METs, MotionID]  */
#define AET_WINDOW_FULL      5      /* sliding window at full power          */
#define AET_WINDOW_THROTTLED 3      /* shrunken window when throttled        */
#define AET_N_CLASSES        3      /* prediction target classes             */
#define AET_FLAT_DIM        20      /* WINDOW_FULL * STATE_DIM               */
#define AET_DRIFT_THRESHOLD  0.10f  /* matches Python DRIFT_THRESHOLD        */

/* drift_status codes */
#define AET_DRIFT_NO_BASELINE  0
#define AET_DRIFT_STABLE       1
#define AET_DRIFT_DETECTED     2

/* ── Data types ─────────────────────────────────────────────────────────── */

typedef struct {
    float v[AET_STATE_DIM]; /* state vector: HR, HRV_ms, METs, MotionID */
    bool  throttle;         /* Power_Agent_Throttle_Flag                 */
} aet_token_t;

typedef struct {
    int   predicted_class; /* 0 .. AET_N_CLASSES-1                */
    int   window_used;     /* actual window length (3 or 5)        */
    bool  low_power_mode;  /* true when throttled path was taken   */
    int   drift_status;    /* AET_DRIFT_NO_BASELINE / _STABLE / _DETECTED */
    float confidence;      /* softmax max, 0.0 .. 1.0              */
} aet_prediction_t;

typedef struct {
    float mean[AET_STATE_DIM]; /* cached baseline centroid vector */
    float threshold;           /* drift distance threshold         */
} aet_baseline_t;

typedef struct {
    /* sliding window — oldest entry at index 0 */
    float history[AET_WINDOW_FULL][AET_STATE_DIM];
    int   history_len;              /* 0 .. AET_WINDOW_FULL            */
    const aet_baseline_t *baseline; /* NULL → drift returns NO_BASELINE */
} aet_agent_t;

/* ── Public API ─────────────────────────────────────────────────────────── */

/*
 * aet_init — zero the agent state.
 *   baseline: pointer to a caller-owned aet_baseline_t, or NULL.
 *             The pointer is stored; caller must keep it alive.
 */
void aet_init(aet_agent_t *agent, const aet_baseline_t *baseline);

/*
 * aet_ingest — push one token into the sliding window.
 *   Returns true and fills *out when the active window is full.
 *   Returns false while the window is still filling (out untouched).
 */
bool aet_ingest(aet_agent_t       *agent,
                const aet_token_t *token,
                aet_prediction_t  *out);

#ifdef __cplusplus
}
#endif
