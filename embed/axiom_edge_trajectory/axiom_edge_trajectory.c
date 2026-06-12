/*
 * axiom_edge_trajectory.c
 * Low-Power Edge Trajectory Agent — C port for Cortex-M / nRF targets.
 *
 * Projection matrix: seed 20240611, gaussian(0,1) via random.Random.
 * Verified to produce identical predicted_class and drift_status as the
 * Python reference in axiom_edge_trajectory.py.
 */
#include "axiom_edge_trajectory.h"

#include <math.h>   /* expf, sqrtf */
#include <string.h> /* memcpy, memset */

/* ── Projection matrix W[FLAT_DIM][N_CLASSES] ───────────────────────────── */
/* Generated from: random.Random(20240611).gauss(0,1) — DO NOT edit by hand. */

static const float W[AET_FLAT_DIM][AET_N_CLASSES] = {
    { -1.33755036f, -1.07621022f, -0.03166396f },
    { +0.56891065f, -0.39667140f, -1.96154756f },
    { -0.36815148f, +0.62558651f, -0.94066460f },
    { -1.93547621f, +0.22737934f, +0.22528426f },
    { -0.53008196f, +0.65633912f, -0.50822169f },
    { -0.37776479f, +0.87262390f, +0.17225375f },
    { +0.08012003f, +0.84265798f, -0.59133387f },
    { +0.34934682f, +1.37561157f, -0.22887574f },
    { -0.12032446f, -0.96105204f, +1.75873016f },
    { -0.75438542f, +0.03381710f, +1.57495564f },
    { +1.22500464f, -0.44037433f, -0.66768711f },
    { +0.70771246f, +0.62222416f, -1.06813385f },
    { +1.39843098f, -0.60360097f, +0.61529738f },
    { -1.34666278f, -0.63616364f, +0.62708566f },
    { +1.49831042f, -0.39407911f, -0.37212218f },
    { +0.24878360f, -1.36810693f, +0.80812520f },
    { -1.00309478f, +1.79358668f, +1.01156338f },
    { +0.90879412f, -0.14448498f, -0.32822472f },
    { -0.79334083f, -1.26010880f, -0.36359588f },
    { -1.56852368f, -0.80305150f, -2.59678289f }
};

/* ── Internal helpers ───────────────────────────────────────────────────── */

static void _dot_matrix(const float flat[AET_FLAT_DIM],
                         float scores[AET_N_CLASSES])
{
    for (int j = 0; j < AET_N_CLASSES; j++) {
        float acc = 0.0f;
        for (int i = 0; i < AET_FLAT_DIM; i++) {
            acc += flat[i] * W[i][j];
        }
        scores[j] = acc;
    }
}

static int _argmax(const float scores[AET_N_CLASSES])
{
    int   best_i = 0;
    float best   = scores[0];
    for (int i = 1; i < AET_N_CLASSES; i++) {
        if (scores[i] > best) {
            best   = scores[i];
            best_i = i;
        }
    }
    return best_i;
}

/* Stable softmax — returns the maximum probability (confidence). */
static float _softmax_max(const float scores[AET_N_CLASSES])
{
    float m = scores[0];
    for (int i = 1; i < AET_N_CLASSES; i++) {
        if (scores[i] > m) m = scores[i];
    }
    float exps[AET_N_CLASSES];
    float total = 0.0f;
    float emax  = 0.0f;
    for (int i = 0; i < AET_N_CLASSES; i++) {
        exps[i] = expf(scores[i] - m);
        total  += exps[i];
        if (exps[i] > emax) emax = exps[i];
    }
    return (total > 0.0f) ? (emax / total) : 0.0f;
}

static int _drift_status(const aet_agent_t *agent)
{
    if (!agent->baseline || agent->history_len == 0) {
        return AET_DRIFT_NO_BASELINE;
    }

    /* centroid of the active window */
    float centroid[AET_STATE_DIM] = {0};
    for (int r = 0; r < agent->history_len; r++) {
        for (int d = 0; d < AET_STATE_DIM; d++) {
            centroid[d] += agent->history[r][d];
        }
    }
    for (int d = 0; d < AET_STATE_DIM; d++) {
        centroid[d] /= (float)agent->history_len;
    }

    /* euclidean distance from baseline mean */
    float dist = 0.0f;
    for (int d = 0; d < AET_STATE_DIM; d++) {
        float diff = centroid[d] - agent->baseline->mean[d];
        dist += diff * diff;
    }
    dist = sqrtf(dist);

    float thr = (agent->baseline->threshold > 0.0f)
                    ? agent->baseline->threshold
                    : AET_DRIFT_THRESHOLD;

    return (dist > thr) ? AET_DRIFT_DETECTED : AET_DRIFT_STABLE;
}

/* ── Public API ─────────────────────────────────────────────────────────── */

void aet_init(aet_agent_t *agent, const aet_baseline_t *baseline)
{
    memset(agent, 0, sizeof(*agent));
    agent->baseline = baseline;
}

bool aet_ingest(aet_agent_t       *agent,
                const aet_token_t *token,
                aet_prediction_t  *out)
{
    int window = token->throttle ? AET_WINDOW_THROTTLED : AET_WINDOW_FULL;

    /* Append token's state vector to the sliding window. */
    if (agent->history_len < AET_WINDOW_FULL) {
        memcpy(agent->history[agent->history_len], token->v,
               AET_STATE_DIM * sizeof(float));
        agent->history_len++;
    } else {
        /* Shift left (drop oldest), append at end. */
        memmove(agent->history[0], agent->history[1],
                (AET_WINDOW_FULL - 1) * AET_STATE_DIM * sizeof(float));
        memcpy(agent->history[AET_WINDOW_FULL - 1], token->v,
               AET_STATE_DIM * sizeof(float));
    }

    /* Trim to the active window size from the right. */
    int active = (agent->history_len < window) ? agent->history_len : window;

    if (active < window) {
        return false; /* window not full yet */
    }

    /* Flatten the active window into a zero-padded FLAT_DIM vector. */
    float flat[AET_FLAT_DIM] = {0}; /* zero-init handles throttled padding */
    int   src_start = agent->history_len - window; /* oldest row to include */
    for (int r = 0; r < window; r++) {
        int row = src_start + r;
        for (int d = 0; d < AET_STATE_DIM; d++) {
            flat[r * AET_STATE_DIM + d] = agent->history[row][d];
        }
    }

    float scores[AET_N_CLASSES];
    _dot_matrix(flat, scores);

    out->predicted_class = _argmax(scores);
    out->confidence      = _softmax_max(scores);
    out->window_used     = window;
    out->low_power_mode  = token->throttle;
    out->drift_status    = _drift_status(agent);

    return true;
}
