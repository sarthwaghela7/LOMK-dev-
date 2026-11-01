import { useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { extractHour as sharedExtractHour } from "../../utils/reportMetrics";

function CallVolumeChart({ data, viewMode }) {
  const chartData = useMemo(() => {
    if (!data || data.length === 0) return [];

    // ── Hourly view: group calls by hour-of-day (0–23) using the call's
    //    own time-of-day field, the same basis Report Analysis uses for its
    //    "Call Volume by Hour" table (see extractHour above). ─────────────
    if (viewMode === "hour") {
      const buckets = new Map();
      for (let h = 0; h < 24; h++) {
        buckets.set(h, { date: `${h}:00`, calls: 0, ts: h });
      }
      data.forEach((call) => {
        const h = sharedExtractHour(call);
        if (h === null) return;
        buckets.get(h).calls += 1;
      });
      return Array.from(buckets.values());
    }

    const counts = new Map();
    data.forEach((call) => {
      const rawDate =
        call.call_recording_date || call.call_date || call.date || call.created_at || call.timestamp || call.call_time;
      if (!rawDate) return;

      const d = new Date(rawDate);
      if (Number.isNaN(d.getTime())) return;

      const key = d.toISOString().slice(0, 10);
      const label = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
      const current = counts.get(key) || { date: label, calls: 0, ts: d.getTime() };
      current.calls += 1;
      current.ts = Math.min(current.ts, d.getTime());
      counts.set(key, current);
    });

    const sorted = Array.from(counts.values())
      .sort((a, b) => {
        return a.ts - b.ts;
      });

    // If too many days, keep last 14 for readability
    return sorted.length > 14 ? sorted.slice(-14) : sorted;
  }, [data, viewMode]);

  if (chartData.length === 0 || chartData.every((d) => d.calls === 0)) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 220, color: "var(--slate-500)", fontSize: 13 }}>
        No call volume data available
      </div>
    );
  }

  return (
    <div style={{ width: "100%", height: 260 }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: "#64748b", fontSize: 11 }}
            axisLine={{ stroke: "#334155" }}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "#64748b", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#0f172a",
              border: "1px solid #334155",
              borderRadius: 8,
              color: "#e2e8f0",
              fontSize: 13,
            }}
            cursor={{ fill: "#1e293b" }}
          />
          <Bar
            dataKey="calls"
            fill="#3b82f6"
            radius={[4, 4, 0, 0]}
            maxBarSize={40}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default CallVolumeChart;