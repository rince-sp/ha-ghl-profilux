/*
 * ProfiLux dashboard strategy.
 *
 * Auto-generates a dashboard from whatever ProfiLux entities exist — no
 * hard-coded entity IDs. Registered as a frontend resource by the integration,
 * so a dashboard needs only:
 *
 *   strategy:
 *     type: custom:profilux
 *
 * Layout (a "sections" view, two columns wide):
 *   1. Controller alarm            — full width, at the very top
 *   2. Sensors (left) | Power (right)
 *   3. Switching channels          — socket cards (name + state colour + power)
 *   4. Level control loops         — loop cards (name + state colour)
 *   5. Dosing pumps                — reservoir fill level
 *
 * Socket and loop cards behave like area cards: the face shows the name, a
 * state-coloured icon and (for sockets) the current power draw; tapping opens
 * the more-info dialog with the toggle and the detailed attributes.
 */

// Gauge ranges keyed by unit of measurement.
const RANGES = {
  "°C": { min: 18, max: 32, severity: { green: 24, yellow: 27, red: 29 } },
  pH: { min: 6, max: 9 },
  mV: { min: 0, max: 500 },
  "mS/cm": { min: 0, max: 60, severity: { green: 45, yellow: 56, red: 58 } },
  "µS/cm": { min: 0, max: 2000 },
  "%": { min: 0, max: 100 },
  "mg/L": { min: 0, max: 20 },
};

class ProfiluxDashboardStrategy {
  static async generate(config, hass) {
    const entities = Object.values(hass.entities || {}).filter(
      (e) => e.platform === "profilux"
    );
    const ids = entities.map((e) => e.entity_id).sort((a, b) => a.localeCompare(b));
    const stateOf = (id) => hass.states[id];

    const isSensor = (id) => id.startsWith("sensor.");
    const gauges = ids.filter(
      (id) => isSensor(id) && !/_(current|power|status)$/.test(id) && !/_fill_level$/.test(id)
    );
    const dosing = ids.filter((id) => isSensor(id) && /_fill_level$/.test(id));
    const totalPower = ids.find((id) => id.endsWith("_total_power"));
    const totalCurrent = ids.find((id) => id.endsWith("_total_current"));
    const status = ids.filter((id) => isSensor(id) && id.endsWith("_status"));
    const alarms = ids.filter(
      (id) => id.startsWith("binary_sensor.") && id.endsWith("_alarm")
    );
    const socketSensors = ids.filter(
      (id) =>
        id.startsWith("binary_sensor.") &&
        !id.endsWith("_alarm") &&
        !/_(min|max)_float$/.test(id)
    );

    // Match a switch and its status binary_sensor by the socket/loop name
    // (everything after the domain + "…profilux_"), because the two can carry
    // different entity-id prefixes (e.g. one created before the device gained an
    // area name).
    const socketName = (id) => id.replace(/^.*?profilux_/, "");
    // The controller-wide alarm's name is just "alarm"; per-loop alarms are
    // "<loop>_alarm". Pull the controller alarm out to pin it at the top.
    const controllerAlarm = alarms.find((id) => socketName(id) === "alarm");
    const levelAlarms = alarms.filter((id) => id !== controllerAlarm);
    const switches = ids.filter((id) => id.startsWith("switch."));

    // Prefer the controllable switch (tap-to-toggle) over the read-only status
    // sensor when a switch exists for the same socket.
    const sockets = socketSensors.map((bs) => {
      const name = socketName(bs);
      return switches.find((s) => socketName(s) === name) || bs;
    });

    // --- card builders --------------------------------------------------
    const heading = (h, icon) => ({ type: "heading", heading: h, icon });

    const gaugeCard = (id) => {
      const unit = stateOf(id) ? stateOf(id).attributes.unit_of_measurement : undefined;
      const r = RANGES[unit] || {};
      const card = { type: "gauge", entity: id, needle: true, grid_options: { columns: 6 } };
      if (r.min !== undefined) card.min = r.min;
      if (r.max !== undefined) card.max = r.max;
      if (r.severity) card.severity = r.severity;
      if (unit) card.unit = unit;
      return card;
    };

    // A socket card: name + state-coloured icon + current draw on the face; the
    // toggle and per-outlet power live in the tap-to-open more-info dialog.
    const socketCard = (id) => ({
      type: "tile",
      entity: id,
      icon: "mdi:power-socket-de",
      color: "amber",
      state_content: id.startsWith("switch.") ? ["state", "power_w"] : ["state", "current_a"],
      grid_options: { columns: 4 },
    });

    const tile = (id, columns, extra) => ({
      type: "tile",
      entity: id,
      grid_options: { columns },
      ...(extra || {}),
    });

    const sections = [];

    // 1. Controller alarm — full width, at the very top.
    if (controllerAlarm) {
      sections.push({
        type: "grid",
        column_span: 2,
        cards: [
          {
            type: "tile",
            entity: controllerAlarm,
            name: "Controller Alarm",
            icon: "mdi:alert",
            grid_options: { columns: "full" },
          },
        ],
      });
    }

    // 2a. Sensors — gauges, in the left column.
    if (gauges.length) {
      sections.push({
        type: "grid",
        column_span: 1,
        cards: [heading("Sensoren", "mdi:gauge"), ...gauges.map(gaugeCard)],
      });
    }

    // 2b. Power & current — in the right column, with a 24 h trend.
    if (totalPower || totalCurrent) {
      const cards = [heading("Stromverbrauch", "mdi:flash")];
      if (totalPower) cards.push(tile(totalPower, 6, { color: "orange", icon: "mdi:flash" }));
      if (totalCurrent) cards.push(tile(totalCurrent, 6, { color: "orange", icon: "mdi:current-ac" }));
      if (totalPower) {
        cards.push({
          type: "history-graph",
          hours_to_show: 24,
          entities: [{ entity: totalPower }],
          grid_options: { columns: "full", rows: 4 },
        });
      }
      sections.push({ type: "grid", column_span: 1, cards });
    }

    // 3. Switching channels — socket cards, full width.
    if (sockets.length) {
      sections.push({
        type: "grid",
        column_span: 2,
        cards: [heading("Schaltkanäle", "mdi:power-socket-de"), ...sockets.map(socketCard)],
      });
    }

    // 4. Level control loops — one card per loop, coloured by its alarm state
    // (red = fault, e.g. a dry float; green/neutral = OK). Tapping opens the
    // loop's more-info dialog, which lists the sensors assigned to the loop.
    const loopKey = (id) =>
      socketName(id).replace(/_(status|alarm|min_float|max_float)$/, "");
    const loopNames = [...new Set([...levelAlarms, ...status].map(loopKey))];
    if (loopNames.length) {
      const loopCards = loopNames.map((nm) => {
        const alarm = levelAlarms.find((id) => loopKey(id) === nm);
        const stat = status.find((id) => loopKey(id) === nm);
        const primary = alarm || stat;
        const friendly = stateOf(primary)?.attributes.friendly_name || nm;
        const name = friendly.replace(/\s+(alarm|status)$/i, "");
        return {
          type: "tile",
          entity: primary,
          name,
          icon: "mdi:waves",
          grid_options: { columns: 4 },
        };
      });
      sections.push({
        type: "grid",
        column_span: 2,
        cards: [heading("Niveau-Regelkreise", "mdi:water-percent"), ...loopCards],
      });
    }

    // 5. Dosing pumps — reservoir fill level, full width.
    if (dosing.length) {
      sections.push({
        type: "grid",
        column_span: 2,
        cards: [
          heading("Dosierpumpen", "mdi:cup-water"),
          ...dosing.map((id) =>
            tile(id, 6, {
              color: "light-blue",
              icon: "mdi:cup-water",
              state_content: ["state", "percent"],
            })
          ),
        ],
      });
    }

    return {
      title: config.title || "ProfiLux",
      views: [
        {
          title: "Aquarium",
          path: "aquarium",
          type: "sections",
          icon: "mdi:fishbowl",
          max_columns: 2,
          sections,
        },
      ],
    };
  }
}

// Guard so loading this module twice (e.g. as a frontend extra *and* a Lovelace
// resource) doesn't throw "already defined".
if (!customElements.get("ll-strategy-dashboard-profilux")) {
  customElements.define("ll-strategy-dashboard-profilux", ProfiluxDashboardStrategy);
}
