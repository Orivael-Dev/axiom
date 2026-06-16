/**
 * Rocksi → AXIOM CPI Bridge
 *
 * Paste this entire script into the Rocksi browser console (F12 → Console),
 * then press Enter. It reads the live robot and scene state, samples the TCP
 * position 5 times over 400 ms, and copies a JSON blob to your clipboard.
 *
 * Paste that JSON as the --state argument to axiom_cpi_local_agent.py:
 *
 *   python3 axiom_cpi_local_agent.py --state "$(xclip -o)" \
 *     --scene "Red cube on the table, roughly 8x8x8 cm"
 *
 * Or save to a file first:
 *
 *   # In the browser: after running this script, open the Console and
 *   # right-click the logged JSON → "Copy object" → paste into state.json
 *   python3 axiom_cpi_local_agent.py --state-file tools/rocksi_state.json
 *
 * Tested with Rocksi at rocksi.net (ndahn/Rocksi master branch).
 * Global functions used: getRobot(), getScene() — exported by scene.js.
 */

(function () {
  "use strict";

  // ── 1. Robot joints ───────────────────────────────────────────────────
  const robot = getRobot();

  if (!robot) {
    console.error("[Rocksi→CPI] getRobot() returned null — is a robot loaded?");
    return;
  }

  const arm_joints = (robot.arm?.movable || []).map((j, i) => ({
    name: j.name || `j${i + 1}`,
    angle_rad: +j.angle.toFixed(5),
    limit_lower: +(j.limit?.lower ?? 0).toFixed(5),
    limit_upper: +(j.limit?.upper ?? 0).toFixed(5),
  }));

  const gripper_fingers = (robot.hand?.movable || []).map((f, i) => ({
    name: f.name || `finger${i + 1}`,
    angle_rad: +f.angle.toFixed(5),
    open: +(f.states?.opened ?? 0).toFixed(5),
    closed: +(f.states?.closed ?? 0).toFixed(5),
  }));

  // ── 2. TCP pose via matrixWorld (no THREE import needed) ──────────────
  // THREE.js Matrix4.elements is column-major; translation is at [12,13,14].
  function snapTCP() {
    const el = robot.tcp.object.matrixWorld.elements;
    return {
      x: +el[12].toFixed(5),
      y: +el[13].toFixed(5),
      z: +el[14].toFixed(5),
    };
  }

  // ── 3. Scene SimObjects ───────────────────────────────────────────────
  // SimObject stores shape/size/mass as direct properties (extends Object3D).
  const scene = getScene();
  const sim_objects = [];
  const SHAPES = new Set(["box", "cube", "sphere", "cylinder"]);

  scene.traverse((obj) => {
    // Direct properties (SimObject class)
    const shape = obj.shape || obj.userData?.shape;
    if (!shape || !SHAPES.has(shape)) return;

    const size = obj.size || obj.userData?.size;
    const mass = obj.mass ?? obj.userData?.mass ?? null;
    const color = obj.color || obj.userData?.color || null;

    sim_objects.push({
      shape: shape,
      size: size
        ? {
            x: +size.x.toFixed(4),
            y: +size.y.toFixed(4),
            z: +size.z.toFixed(4),
          }
        : null,
      mass: mass !== null ? +Number(mass).toFixed(4) : null,
      color: color,
      position: {
        x: +obj.position.x.toFixed(4),
        y: +obj.position.y.toFixed(4),
        z: +obj.position.z.toFixed(4),
      },
    });
  });

  // ── 4. Sample TCP × 5 over 400 ms (approach → grip → lift) ──────────
  const N = 5;
  const INTERVAL_MS = 100;
  const tcp_samples = [];

  console.log(
    `[Rocksi→CPI] Sampling TCP ${N}× every ${INTERVAL_MS} ms …` +
      (sim_objects.length
        ? ` (${sim_objects.length} sim object(s) found)`
        : " (no sim objects found — describe the object via --scene)")
  );

  let tick = 0;

  const timer = setInterval(() => {
    tcp_samples.push({ t: Date.now(), tcp: snapTCP() });
    tick++;

    if (tick >= N) {
      clearInterval(timer);

      const state = {
        robot_name: robot.xacro || robot.info?.EN?.name || "franka_panda",
        arm_joints,
        gripper_fingers,
        tcp_samples,
        sim_objects,
        timestamp: Date.now(),
      };

      const json = JSON.stringify(state, null, 2);

      // Pretty-print a summary
      console.group("[Rocksi→CPI] State snapshot ready");
      console.log(
        "arm joints:",
        arm_joints.map((j) => `${j.name}=${j.angle_rad.toFixed(3)}`).join("  ")
      );
      console.log(
        "TCP positions:",
        tcp_samples
          .map(
            (s) =>
              `(${s.tcp.x.toFixed(3)}, ${s.tcp.y.toFixed(3)}, ${s.tcp.z.toFixed(3)})`
          )
          .join(" → ")
      );
      if (sim_objects.length) {
        sim_objects.forEach((o) =>
          console.log(
            `object: shape=${o.shape}  size=${JSON.stringify(o.size)}  mass=${o.mass}  color=${o.color}`
          )
        );
      }
      console.groupEnd();

      // Copy to clipboard (Chrome DevTools helper)
      if (typeof copy === "function") {
        copy(json);
        console.log(
          "[Rocksi→CPI] JSON copied to clipboard ✓  " +
            "Paste as --state '...' or save to tools/rocksi_state.json"
        );
      } else {
        // Firefox fallback
        console.log("[Rocksi→CPI] copy() not available — log output below:");
        console.log(json);
      }
    }
  }, INTERVAL_MS);
})();
