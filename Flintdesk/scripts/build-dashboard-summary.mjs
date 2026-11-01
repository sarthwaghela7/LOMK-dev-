import { rolldown } from "rolldown";
const bundle = await rolldown({ input: "scripts/dashboard-summary-entry.js", platform: "node" });
await bundle.write({ file: "../Backend/services/dashboard_calculator.mjs", format: "esm" });
await bundle.close();
